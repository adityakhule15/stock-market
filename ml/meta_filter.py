"""Meta-model filter — learns which setups the user loses on; promote + gate."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from config import trading_config as cfg

logger = logging.getLogger(__name__)

META_NAME = "meta_filter"

FEATURE_KEYS = (
    "hour",
    "prob",
    "news_flag",
    "regime_high_vol",
    "regime_trend",
    "setup_orb",
    "setup_flag",
    "setup_oversold",
    "is_fno",
    "followed_plan",
    "had_mistakes",
    "mistake_chased",
    "mistake_ignored_sl",
    "mistake_oversized",
    "mistake_revenge",
)


def _feat_from_review(r) -> list[float]:
    pos = r.position
    sig = pos.signal
    hour = pos.opened_at.hour if pos.opened_at else 12
    prob = float(sig.probability) if sig else 0.5
    pipe = (sig.pipeline if sig else {}) or {}
    news_flag = 1.0 if (pipe.get("news") or {}).get("action") in {"warn", "block"} else 0.0
    regime = r.regime_snapshot or {}
    tag = regime.get("tag", "range")
    setup = ((pipe.get("scanner") or {}).get("setup") or "") if pipe else ""
    is_fno = 1.0 if sig and ("fno" in (sig.strategy or "") or sig.product == "NRML") else 0.0
    tags = {str(t).lower() for t in (r.mistake_tags or [])}
    # Exclude "followed correctly" from mistake count
    real_mistakes = {t for t in tags if t != "followed_signal_correctly"}
    return [
        float(hour),
        prob,
        news_flag,
        1.0 if tag == "high_vol" else 0.0,
        1.0 if tag == "trend" else 0.0,
        1.0 if setup == "orb" else 0.0,
        1.0 if setup == "bull_flag" else 0.0,
        1.0 if setup == "oversold_bounce" else 0.0,
        is_fno,
        1.0 if r.followed_plan else 0.0,
        1.0 if real_mistakes else 0.0,
        1.0 if "chased" in tags else 0.0,
        1.0 if "ignored_sl" in tags else 0.0,
        1.0 if "oversized" in tags else 0.0,
        1.0 if "revenge_trade" in tags else 0.0,
    ]


def features_from_order(order: dict, *, regime: dict | None = None) -> list[float]:
    """Live feature vector for an order dict (pre-trade; mistake flags = 0)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
    now = datetime.now(IST)
    pipe = order.get("pipeline") or {}
    news_flag = 1.0 if (pipe.get("news") or {}).get("action") in {"warn", "block"} else 0.0
    tag = (regime or {}).get("tag", "range")
    setup = ((pipe.get("scanner") or {}).get("setup") or order.get("strategy") or "")
    is_fno = 1.0 if (
        "fno" in str(order.get("strategy") or "")
        or str(order.get("product") or "").upper() == "NRML"
        or order.get("is_option")
    ) else 0.0
    return [
        float(now.hour),
        float(order.get("probability") or 0.5),
        news_flag,
        1.0 if tag == "high_vol" else 0.0,
        1.0 if tag == "trend" else 0.0,
        1.0 if setup == "orb" else 0.0,
        1.0 if setup == "bull_flag" else 0.0,
        1.0 if setup == "oversold_bounce" else 0.0,
        is_fno,
        1.0,  # assume plan followed until proven otherwise
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    ]


def _rows_from_reviews(min_n: int | None = None) -> tuple[np.ndarray, np.ndarray, list[dict]] | None:
    from portfolio.models import TradeReview

    min_n = min_n if min_n is not None else cfg.RETRAIN_MIN_SAMPLES
    reviews = list(
        TradeReview.objects.select_related("position", "position__signal").order_by("created_at")
    )
    reviews = [r for r in reviews if not getattr(r.position, "is_dummy", False)]
    if len(reviews) < min_n:
        return None
    X, y, meta = [], [], []
    for r in reviews:
        pos = r.position
        sig = pos.signal
        prob = float(sig.probability) if sig else 0.5
        feat = _feat_from_review(r)
        label = 1 if float(pos.pnl) > 0 else 0
        X.append(feat)
        y.append(label)
        meta.append({"review_id": r.id, "pnl": float(pos.pnl), "prob": prob})
    return np.asarray(X, dtype=float), np.asarray(y, dtype=int), meta


def calibration_report(buckets: int = 5) -> dict[str, Any]:
    built = _rows_from_reviews(min_n=1)
    if not built:
        return {"n": 0, "buckets": [], "warning": "No reviews yet"}
    _, y, meta = built
    n = len(y)
    if n < 5:
        return {"n": n, "buckets": [], "warning": f"Sample size {n} — need more trades"}
    probs = np.array([m["prob"] for m in meta])
    edges = np.linspace(0, 1, buckets + 1)
    out = []
    for i in range(buckets):
        lo, hi = edges[i], edges[i + 1]
        mask = (probs >= lo) & (probs < hi if i < buckets - 1 else probs <= hi)
        if not mask.any():
            continue
        out.append(
            {
                "prob_lo": round(float(lo), 2),
                "prob_hi": round(float(hi), 2),
                "n": int(mask.sum()),
                "suggested_prob": round(float(probs[mask].mean()), 3),
                "actual_win_rate": round(float(y[mask].mean()), 3),
            }
        )
    return {
        "n": n,
        "buckets": out,
        "warning": None if n >= cfg.RETRAIN_MIN_SAMPLES else f"n={n} < {cfg.RETRAIN_MIN_SAMPLES}",
    }


def rule_level_stats() -> dict[str, Any]:
    from collections import defaultdict

    from portfolio.models import TradeReview

    by_setup: dict[str, list[float]] = defaultdict(list)
    by_hour: dict[int, list[float]] = defaultdict(list)
    for r in TradeReview.objects.select_related("position", "position__signal"):
        pos = r.position
        sig = pos.signal
        setup = "unknown"
        if sig and sig.pipeline:
            setup = (sig.pipeline.get("scanner") or {}).get("setup") or sig.strategy or "unknown"
        elif sig:
            setup = sig.strategy or "unknown"
        win = 1.0 if float(pos.pnl) > 0 else 0.0
        by_setup[setup].append(win)
        by_hour[pos.opened_at.hour if pos.opened_at else 12].append(win)

    def _summ(d: dict) -> list[dict]:
        rows = []
        for k, vals in d.items():
            rows.append({"key": k, "n": len(vals), "win_rate": round(sum(vals) / len(vals), 3)})
        rows.sort(key=lambda x: -x["n"])
        return rows

    return {"by_setup": _summ(by_setup), "by_hour": _summ(by_hour)}


def train_meta_candidate(min_n: int | None = None) -> dict[str, Any]:
    """Fit a small logistic filter. Returns candidate metrics + fitted clf when OK."""
    min_n = min_n if min_n is not None else cfg.RETRAIN_MIN_SAMPLES
    built = _rows_from_reviews(min_n=min_n)
    if not built:
        return {
            "promoted": False,
            "reason": f"need ≥{min_n} reviewed trades",
            "n": 0,
        }
    X, y, meta = built
    n = len(y)
    split = max(int(n * 0.7), n - max(5, n // 5))
    Xtr, Xte = X[:split], X[split:]
    ytr, yte = y[:split], y[split:]
    if len(yte) < 3 or len(np.unique(ytr)) < 2:
        return {"promoted": False, "reason": "insufficient class variety for OOS", "n": n}

    try:
        from sklearn.linear_model import LogisticRegression

        clf = LogisticRegression(max_iter=200)
        clf.fit(Xtr, ytr)
        te_acc = float(clf.score(Xte, yte))
        maj = int(np.bincount(ytr).argmax())
        base = float((yte == maj).mean())
        beats = te_acc > base + 0.02
        # Refit on all data for the artifact we may promote
        clf_full = LogisticRegression(max_iter=200)
        clf_full.fit(X, y)
        return {
            "promoted": False,
            "candidate_ok": beats,
            "n": n,
            "oos_acc": round(te_acc, 3),
            "baseline_acc": round(base, 3),
            "reason": "beats baseline" if beats else "does not beat baseline — keep champion",
            "feature_keys": list(FEATURE_KEYS),
            "_clf": clf_full,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("meta filter train failed")
        return {"promoted": False, "reason": str(exc), "n": n}


def maybe_promote_meta(min_n: int | None = None) -> dict[str, Any]:
    """Train; if candidate beats baseline, save + promote via registry as meta_filter."""
    from ml import registry

    min_n = min_n if min_n is not None else cfg.RETRAIN_MIN_SAMPLES
    result = train_meta_candidate(min_n=min_n)
    clf = result.pop("_clf", None)
    if not (result.get("candidate_ok") and result.get("n", 0) >= min_n and clf is not None):
        result["promoted"] = False
        return result

    bundle = {
        "algo": "logistic_meta",
        "params": {"max_iter": 200},
        "clf": clf,
        "feature_keys": list(FEATURE_KEYS),
    }
    metrics = {
        "n": result["n"],
        "oos_acc": result.get("oos_acc"),
        "baseline_acc": result.get("baseline_acc"),
        "train_start": None,
        "train_end": None,
    }
    mv = registry.save(bundle, metrics, name=META_NAME, status="candidate")
    registry.promote(mv)
    result["promoted"] = True
    result["status"] = "champion"
    result["version"] = mv.version
    logger.info("Promoted meta_filter v%s (n=%s oos=%s)", mv.version, result["n"], result.get("oos_acc"))
    return result


def load_meta_champion() -> dict | None:
    from ml import registry

    mv = registry.champion(META_NAME)
    if mv is None:
        return None
    try:
        return registry.load(mv)
    except Exception:  # noqa: BLE001
        logger.exception("failed to load meta champion")
        return None


def predict_meta_win_prob(order: dict, *, regime: dict | None = None) -> float | None:
    """P(win) from champion meta filter, or None if unavailable."""
    bundle = load_meta_champion()
    if not bundle or "clf" not in bundle:
        return None
    clf = bundle["clf"]
    keys = bundle.get("feature_keys") or FEATURE_KEYS
    feat = features_from_order(order, regime=regime)
    # Pad/trim if feature schema drifted
    if len(feat) < len(keys):
        feat = feat + [0.0] * (len(keys) - len(feat))
    elif len(feat) > len(keys):
        feat = feat[: len(keys)]
    try:
        proba = clf.predict_proba(np.asarray([feat], dtype=float))[0]
        # class 1 = win
        classes = list(getattr(clf, "classes_", [0, 1]))
        if 1 in classes:
            return float(proba[classes.index(1)])
        return float(proba[-1])
    except Exception:  # noqa: BLE001
        logger.exception("meta predict failed")
        return None
