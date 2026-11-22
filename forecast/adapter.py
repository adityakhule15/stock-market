"""Kronos sidecar config + bar packing + IPC (mirrors research.adapter)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from django.conf import settings

from config import trading_config as cfg

logger = logging.getLogger(__name__)


def project_path(*parts: str) -> Path:
    return Path(settings.BASE_DIR).joinpath(*parts)


def kronos_enabled() -> bool:
    env = os.getenv("KRONOS_ENABLED", "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    if env in {"0", "false", "no", "off"}:
        return False
    try:
        from portfolio.models import PortfolioSettings

        ps = PortfolioSettings.objects.first()
        if ps is not None:
            stored = getattr(ps, "models_config", None) or {}
            if isinstance(stored, dict) and "kronos_enabled" in stored:
                return bool(stored.get("kronos_enabled"))
    except Exception:  # noqa: BLE001
        pass
    return bool(getattr(cfg, "KRONOS_ENABLED", False))


def resolve_kronos_python() -> Path:
    override = os.getenv("KRONOS_PYTHON", "").strip()
    if override:
        return Path(override)
    win = project_path(".venv-kronos", "Scripts", "python.exe")
    nix = project_path(".venv-kronos", "bin", "python")
    if win.exists():
        return win
    if nix.exists():
        return nix
    return win if os.name == "nt" else nix


def resolve_kronos_runner() -> Path:
    override = os.getenv("KRONOS_RUNNER", "").strip()
    if override:
        p = Path(override)
        return p if p.is_absolute() else project_path(override)
    return project_path("scripts", "kronos_runner.py")


def kronos_sidecar_installed() -> bool:
    return resolve_kronos_python().exists() and resolve_kronos_runner().exists()


def load_kronos_settings() -> dict[str, Any]:
    model_size = "small"
    try:
        from portfolio.models import PortfolioSettings

        ps = PortfolioSettings.objects.first()
        stored = getattr(ps, "models_config", None) or {} if ps else {}
        if isinstance(stored, dict) and stored.get("kronos_model_size"):
            model_size = str(stored["kronos_model_size"])
    except Exception:  # noqa: BLE001
        stored = {}

    size = (os.getenv("KRONOS_MODEL_SIZE") or model_size or "small").lower()
    model_map = {
        "mini": ("NeoQuasar/Kronos-mini", "NeoQuasar/Kronos-Tokenizer-2k", 2048),
        "small": ("NeoQuasar/Kronos-small", "NeoQuasar/Kronos-Tokenizer-base", 512),
        "base": ("NeoQuasar/Kronos-base", "NeoQuasar/Kronos-Tokenizer-base", 512),
    }
    default_model, default_tok, default_ctx = model_map.get(size, model_map["small"])

    paths_n = int(
        os.getenv("KRONOS_PATHS")
        or (stored.get("kronos_paths") if isinstance(stored, dict) else None)
        or getattr(cfg, "KRONOS_PATHS", 32)
    )
    return {
        "enabled": kronos_enabled(),
        "model_size": size,
        "model_id": os.getenv("KRONOS_MODEL_ID") or default_model,
        "tokenizer_id": os.getenv("KRONOS_TOKENIZER_ID") or default_tok,
        "max_context": int(os.getenv("KRONOS_MAX_CONTEXT") or default_ctx),
        "paths": paths_n,
        "lookback": int(os.getenv("KRONOS_LOOKBACK") or getattr(cfg, "KRONOS_LOOKBACK", 400)),
        "pred_len": int(os.getenv("KRONOS_PRED_LEN") or getattr(cfg, "KRONOS_PRED_LEN", 5)),
        "T": float(os.getenv("KRONOS_T") or getattr(cfg, "KRONOS_T", 1.0)),
        "top_p": float(os.getenv("KRONOS_TOP_P") or getattr(cfg, "KRONOS_TOP_P", 0.9)),
        "seed": int(os.getenv("KRONOS_SEED") or getattr(cfg, "KRONOS_SEED", 19)),
        "timeout_sec": int(
            os.getenv("KRONOS_TIMEOUT_SEC") or getattr(cfg, "KRONOS_TIMEOUT_SEC", 300)
        ),
        "batch_chunk": int(os.getenv("KRONOS_BATCH_CHUNK") or 8),
        "hf_cache": str(
            Path(
                os.getenv("HF_HOME")
                or os.getenv("HUGGINGFACE_HUB_CACHE")
                or project_path("data", "kronos", "hf_cache")
            )
        ),
        "device": os.getenv("KRONOS_DEVICE") or "cpu",
    }


def ensure_hf_cache() -> Path:
    p = Path(load_kronos_settings()["hf_cache"])
    p.mkdir(parents=True, exist_ok=True)
    return p


def bars_to_payload(df: pd.DataFrame, *, lookback: int | None = None) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    work = df.copy()
    if not isinstance(work.index, pd.DatetimeIndex):
        if "ts" in work.columns:
            work = work.set_index(pd.to_datetime(work["ts"]))
        elif "timestamps" in work.columns:
            work = work.set_index(pd.to_datetime(work["timestamps"]))
    work = work.sort_index()
    if lookback and lookback > 0:
        work = work.iloc[-lookback:]
    out: list[dict[str, Any]] = []
    for ts, row in work.iterrows():
        out.append(
            {
                "ts": pd.Timestamp(ts).isoformat(),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]) if "volume" in work.columns else 0.0,
            }
        )
    return out


def next_session_timestamps(
    last_ts: datetime | pd.Timestamp,
    pred_len: int,
    *,
    interval: str = "1d",
) -> list[str]:
    """Generate future NSE-ish timestamps for Kronos y_timestamp."""
    ts = pd.Timestamp(last_ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("Asia/Kolkata")
    else:
        ts = ts.tz_convert("Asia/Kolkata")

    out: list[pd.Timestamp] = []
    cur = ts
    if interval in {"1d", "d", "day"}:
        while len(out) < pred_len:
            cur = cur + pd.Timedelta(days=1)
            if cur.weekday() < 5:  # Mon-Fri
                # NSE daily bar conventionally stamped near close
                stamp = cur.normalize() + pd.Timedelta(hours=15, minutes=30)
                out.append(stamp)
    else:
        # Intraday 5m: step forward 5 minutes within session 09:15–15:10
        while len(out) < pred_len:
            cur = cur + pd.Timedelta(minutes=5)
            t = cur.time()
            if cur.weekday() >= 5:
                # jump to next Monday 09:15
                days = (7 - cur.weekday()) % 7 or 7
                cur = (cur + pd.Timedelta(days=days)).normalize() + pd.Timedelta(
                    hours=9, minutes=15
                )
                continue
            if t < datetime.strptime("09:15", "%H:%M").time():
                cur = cur.normalize() + pd.Timedelta(hours=9, minutes=15)
                continue
            if t > datetime.strptime("15:10", "%H:%M").time():
                cur = (cur + pd.Timedelta(days=1)).normalize() + pd.Timedelta(
                    hours=9, minutes=15
                )
                continue
            out.append(cur)
    return [t.isoformat() for t in out]


def sidecar_env() -> dict[str, str]:
    env = os.environ.copy()
    cache = str(ensure_hf_cache())
    env["HF_HOME"] = cache
    env["HUGGINGFACE_HUB_CACHE"] = cache
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["PYTHONPATH"] = str(project_path("third_party", "kronos")) + os.pathsep + env.get(
        "PYTHONPATH", ""
    )
    return env


def invoke_kronos(request: dict[str, Any], *, timeout_sec: int | None = None) -> dict[str, Any]:
    python = resolve_kronos_python()
    runner = resolve_kronos_runner()
    if not runner.exists():
        return {"ok": False, "error": f"runner missing: {runner}", "badge": "kronos_unavailable"}
    if not python.exists():
        return {
            "ok": False,
            "error": (
                "kronos sidecar not installed — create .venv-kronos and "
                f"pip install -r requirements-kronos.txt (expected {python})"
            ),
            "badge": "kronos_unavailable",
        }

    settings_k = load_kronos_settings()
    timeout = int(timeout_sec or settings_k["timeout_sec"] or 300)
    payload = json.dumps(request, default=str).encode("utf-8")
    try:
        proc = subprocess.run(
            [str(python), str(runner)],
            input=payload,
            capture_output=True,
            timeout=timeout,
            env=sidecar_env(),
            cwd=str(project_path()),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s", "timeout": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    stdout = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
    if not stdout:
        return {
            "ok": False,
            "error": stderr or f"empty runner output (exit {proc.returncode})",
            "stderr": stderr[-2000:],
        }
    line = stdout.splitlines()[-1]
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": "invalid JSON from kronos runner",
            "stdout": stdout[-2000:],
            "stderr": stderr[-2000:],
        }
    if not isinstance(data, dict):
        return {"ok": False, "error": "runner returned non-object JSON"}
    if "ok" not in data:
        data["ok"] = proc.returncode == 0
    if stderr:
        data.setdefault("stderr", stderr[-2000:])
    return data


def today_ist() -> date:
    from django.utils import timezone

    return timezone.localdate()
