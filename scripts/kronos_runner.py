#!/usr/bin/env python3
"""Kronos sidecar runner — JSON stdin → JSON stdout.

Runs under `.venv-kronos` (CPU torch) so torch stays out of Django / TradingAgents.

Request (stdin JSON):
  {
    "action": "ping" | "predict",
    "bars": [{"ts": "...", "open":.., "high":.., "low":.., "close":.., "volume":..}],
    "y_timestamps": ["..."],          # future bar timestamps (len == pred_len)
    "pred_len": 5,
    "lookback": 400,                  # optional trim of history
    "paths": 32,                      # independent sample paths
    "batch_chunk": 8,                 # paths per predict_batch call
    "T": 1.0,
    "top_p": 0.9,
    "top_k": 0,
    "seed": 19,
    "model_id": "NeoQuasar/Kronos-small",
    "tokenizer_id": "NeoQuasar/Kronos-Tokenizer-base",
    "max_context": 512,
    "device": "cpu",
    "hf_cache": "data/kronos/hf_cache"
  }

Response paths: list of N lists of OHLCV dicts (one per future bar).
Library progress goes to stderr; only one JSON object is written to stdout.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any


def _ok(payload: dict[str, Any]) -> int:
    sys.stdout.write(json.dumps({"ok": True, **payload}, default=str))
    sys.stdout.write("\n")
    return 0


def _fail(message: str, **extra: Any) -> int:
    sys.stdout.write(json.dumps({"ok": False, "error": message, **extra}, default=str))
    sys.stdout.write("\n")
    return 1


def _load_request() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("empty stdin — expected JSON request")
    return json.loads(raw)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_import_path() -> Path:
    root = _repo_root() / "third_party" / "kronos"
    if not root.exists():
        raise FileNotFoundError(f"vendored Kronos missing at {root}")
    path = str(root)
    if path not in sys.path:
        sys.path.insert(0, path)
    return root


def action_ping(req: dict[str, Any]) -> int:
    try:
        _ensure_import_path()
        import torch  # noqa: F401
        from model import Kronos, KronosPredictor, KronosTokenizer  # noqa: F401

        return _ok(
            {
                "action": "ping",
                "torch": True,
                "kronos_import": True,
                "device": req.get("device") or "cpu",
            }
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc), traceback=traceback.format_exc()[-2000:])


def _build_df(bars: list[dict[str, Any]], lookback: int | None):
    import pandas as pd

    if not bars:
        raise ValueError("bars is empty")
    rows = []
    for b in bars:
        rows.append(
            {
                "timestamps": pd.Timestamp(b["ts"]),
                "open": float(b["open"]),
                "high": float(b["high"]),
                "low": float(b["low"]),
                "close": float(b["close"]),
                "volume": float(b.get("volume") or 0.0),
            }
        )
    df = pd.DataFrame(rows).sort_values("timestamps").reset_index(drop=True)
    if lookback and lookback > 0 and len(df) > lookback:
        df = df.iloc[-lookback:].reset_index(drop=True)
    if "amount" not in df.columns:
        df["amount"] = df["volume"] * df[["open", "high", "low", "close"]].mean(axis=1)
    return df


def _get_predictor(req: dict[str, Any]):
    import torch

    _ensure_import_path()
    from model import Kronos, KronosPredictor, KronosTokenizer

    cache = req.get("hf_cache") or str(_repo_root() / "data" / "kronos" / "hf_cache")
    Path(cache).mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", cache)
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", cache)
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", os.getenv("KRONOS_OFFLINE", "0"))

    model_id = req.get("model_id") or "NeoQuasar/Kronos-small"
    tokenizer_id = req.get("tokenizer_id") or "NeoQuasar/Kronos-Tokenizer-base"
    device = req.get("device") or "cpu"
    max_context = int(req.get("max_context") or 512)

    tokenizer = KronosTokenizer.from_pretrained(tokenizer_id, cache_dir=cache)
    model = Kronos.from_pretrained(model_id, cache_dir=cache)
    predictor = KronosPredictor(
        model, tokenizer, device=device, max_context=max_context
    )
    # Force deterministic-ish sampling when seed provided
    seed = req.get("seed")
    if seed is not None:
        torch.manual_seed(int(seed))
    return predictor, model_id, tokenizer_id, device, max_context


def action_predict(req: dict[str, Any]) -> int:
    import pandas as pd

    bars = req.get("bars") or []
    y_raw = req.get("y_timestamps") or []
    pred_len = int(req.get("pred_len") or len(y_raw) or 5)
    lookback = req.get("lookback")
    lookback_i = int(lookback) if lookback else None
    n_paths = max(1, int(req.get("paths") or req.get("sample_count") or 32))
    chunk = max(1, int(req.get("batch_chunk") or 8))
    T = float(req.get("T") or 1.0)
    top_p = float(req.get("top_p") or 0.9)
    top_k = int(req.get("top_k") or 0)

    if len(y_raw) != pred_len:
        raise ValueError(
            f"y_timestamps length ({len(y_raw)}) must equal pred_len ({pred_len})"
        )

    df = _build_df(bars, lookback_i)
    x_timestamp = pd.to_datetime(df["timestamps"])
    y_timestamp = pd.to_datetime(pd.Series(y_raw))
    hist = df[["open", "high", "low", "close", "volume", "amount"]].copy()

    predictor, model_id, tokenizer_id, device, max_context = _get_predictor(req)

    # Upstream averages sample_count internally. Repeat the series N times with
    # sample_count=1 so each batch item is an independent path we can keep.
    path_dfs: list[Any] = []
    remaining = n_paths
    while remaining > 0:
        batch_n = min(chunk, remaining)
        df_list = [hist] * batch_n
        x_list = [x_timestamp] * batch_n
        y_list = [y_timestamp] * batch_n
        preds = predictor.predict_batch(
            df_list,
            x_list,
            y_list,
            pred_len=pred_len,
            T=T,
            top_k=top_k,
            top_p=top_p,
            sample_count=1,
            verbose=False,
        )
        path_dfs.extend(preds)
        remaining -= batch_n

    paths_out: list[list[dict[str, Any]]] = []
    for pdf in path_dfs:
        rows = []
        for ts, row in pdf.iterrows():
            rows.append(
                {
                    "ts": pd.Timestamp(ts).isoformat(),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume") or 0.0),
                    "amount": float(row.get("amount") or 0.0),
                }
            )
        paths_out.append(rows)

    last_close = float(hist["close"].iloc[-1])
    return _ok(
        {
            "action": "predict",
            "model_id": model_id,
            "tokenizer_id": tokenizer_id,
            "device": device,
            "max_context": max_context,
            "lookback": int(len(hist)),
            "pred_len": pred_len,
            "paths_n": len(paths_out),
            "T": T,
            "top_p": top_p,
            "top_k": top_k,
            "seed": req.get("seed"),
            "last_close": last_close,
            "paths": paths_out,
        }
    )


def main() -> int:
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    try:
        req = _load_request()
    except Exception as exc:  # noqa: BLE001
        return _fail(f"invalid request: {exc}")

    action = str(req.get("action") or "ping").lower()
    try:
        if action == "ping":
            return action_ping(req)
        if action == "predict":
            return action_predict(req)
        return _fail(f"unknown action: {action}")
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc), traceback=traceback.format_exc()[-3000:])


if __name__ == "__main__":
    raise SystemExit(main())
