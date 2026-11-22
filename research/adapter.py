"""Django-friendly TradingAgents config + ticker/portfolio builders + IPC."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

from django.conf import settings

from config import trading_config as cfg

logger = logging.getLogger(__name__)

INDEX_MAP = {
    "NIFTY": "^NSEI",
    "NIFTY50": "^NSEI",
    "^NSEI": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "^NSEBANK": "^NSEBANK",
    "INDIAVIX": "^INDIAVIX",
    "^INDIAVIX": "^INDIAVIX",
}

FNO_UNDERLYING = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
}


def project_path(*parts: str) -> Path:
    return Path(settings.BASE_DIR).joinpath(*parts)


# Process-local kill after Windows Application Control / DLL failures
_AGENTS_HARD_DISABLED = False
_AGENTS_HARD_REASON = ""


def disable_agents_for_process(reason: str) -> None:
    """Stop further TradingAgents attempts this process (DLL / App Control blocks)."""
    global _AGENTS_HARD_DISABLED, _AGENTS_HARD_REASON
    if _AGENTS_HARD_DISABLED:
        return
    _AGENTS_HARD_DISABLED = True
    _AGENTS_HARD_REASON = (reason or "agents unavailable")[:240]
    logger.warning("Research agents disabled for this session: %s", _AGENTS_HARD_REASON)


def agents_hard_disabled_reason() -> str:
    return _AGENTS_HARD_REASON if _AGENTS_HARD_DISABLED else ""


def agents_enabled() -> bool:
    """Master switch: env wins if set; else PortfolioSettings.agents_config; else trading_config."""
    if _AGENTS_HARD_DISABLED:
        return False
    env = os.getenv("AGENTS_ENABLED", "").strip().lower()
    if env in {"0", "false", "no", "off"}:
        return False
    if env in {"1", "true", "yes", "on"}:
        # Still respect process hard-disable after DLL / App Control failure
        return not _AGENTS_HARD_DISABLED
    try:
        from portfolio.models import PortfolioSettings

        ps = PortfolioSettings.objects.first()
        if ps is not None:
            stored = getattr(ps, "agents_config", None) or {}
            if isinstance(stored, dict) and "enabled" in stored:
                return bool(stored.get("enabled")) and not _AGENTS_HARD_DISABLED
    except Exception:  # noqa: BLE001
        pass
    return bool(getattr(cfg, "AGENTS_ENABLED", False))


def ensure_agents_off_in_settings(*, reason: str = "") -> None:
    """Persist agents disabled so UI + next boot stay quiet."""
    try:
        from portfolio.models import PortfolioSettings

        ps = PortfolioSettings.get_solo()
        cfg_a = dict(ps.agents_config or {})
        if cfg_a.get("enabled"):
            cfg_a["enabled"] = False
            if reason:
                cfg_a["auto_disabled_reason"] = reason[:200]
            ps.agents_config = cfg_a
            ps.save(update_fields=["agents_config", "updated_at"])
    except Exception:  # noqa: BLE001
        pass


def resolve_ta_python() -> Path:
    """Return the TradingAgents sidecar interpreter.

    Never falls back to Django's ``sys.executable`` — that silently imports
    against the wrong pandas pin and surfaces as ``No module named
    'tradingagents'``. Missing sidecar → clear error in ``invoke_runner``.
    """
    override = os.getenv("TA_PYTHON", "").strip()
    if override:
        return Path(override)
    win = project_path(".venv-ta", "Scripts", "python.exe")
    nix = project_path(".venv-ta", "bin", "python")
    if win.exists():
        return win
    if nix.exists():
        return nix
    # Prefer the platform-expected path so the missing-venv error is actionable.
    return win if os.name == "nt" else nix


def ta_sidecar_installed() -> bool:
    python = resolve_ta_python()
    return python.exists()


def resolve_ta_runner() -> Path:
    override = os.getenv("TA_RUNNER", "").strip()
    if override:
        p = Path(override)
        return p if p.is_absolute() else project_path(override)
    return project_path("scripts", "ta_runner.py")


def load_agents_settings() -> dict[str, Any]:
    """Merge trading_config defaults + env + optional PortfolioSettings.agents_config."""
    analysts = getattr(cfg, "AGENTS_SELECTED_ANALYSTS", ("market", "social", "news", "fundamentals"))
    if isinstance(analysts, str):
        analysts = tuple(a.strip() for a in analysts.split(",") if a.strip())

    out: dict[str, Any] = {
        "enabled": agents_enabled(),
        "top_n": int(os.getenv("AGENTS_TOP_N") or getattr(cfg, "AGENTS_TOP_N", 3)),
        "daily_call_cap": int(
            os.getenv("AGENTS_DAILY_CALL_CAP") or getattr(cfg, "AGENTS_DAILY_CALL_CAP", 6)
        ),
        "daily_cost_cap_inr": float(
            os.getenv("AGENTS_DAILY_COST_CAP_INR")
            or getattr(cfg, "AGENTS_DAILY_COST_CAP_INR", 0)
            or 0
        ),
        "timeout_sec": int(
            os.getenv("AGENTS_RUN_TIMEOUT_SEC") or getattr(cfg, "AGENTS_RUN_TIMEOUT_SEC", 600)
        ),
        "retry_budget": int(
            os.getenv("AGENTS_RETRY_BUDGET") or getattr(cfg, "AGENTS_RETRY_BUDGET", 1)
        ),
        "max_debate_rounds": int(
            os.getenv("AGENTS_MAX_DEBATE_ROUNDS")
            or getattr(cfg, "AGENTS_MAX_DEBATE_ROUNDS", 1)
        ),
        "max_risk_rounds": int(
            os.getenv("AGENTS_MAX_RISK_ROUNDS") or getattr(cfg, "AGENTS_MAX_RISK_ROUNDS", 1)
        ),
        "selected_analysts": list(analysts),
        "checkpoint_enabled": _env_bool(
            "AGENTS_CHECKPOINT_ENABLED",
            bool(getattr(cfg, "AGENTS_CHECKPOINT_ENABLED", True)),
        ),
        "holding_period_days": int(
            os.getenv("AGENTS_HOLDING_PERIOD_DAYS")
            or getattr(cfg, "AGENTS_HOLDING_PERIOD_DAYS", 5)
        ),
        "llm_provider": _default_provider(),
    }

    # UI overrides stored on PortfolioSettings
    try:
        from portfolio.models import PortfolioSettings

        ps = PortfolioSettings.objects.first()
        if ps is not None:
            stored = getattr(ps, "agents_config", None) or {}
            if isinstance(stored, dict) and stored:
                out.update({k: v for k, v in stored.items() if v is not None})
                if "enabled" in stored:
                    out["enabled"] = bool(stored["enabled"])
    except Exception:  # noqa: BLE001
        pass

    env_analysts = os.getenv("AGENTS_SELECTED_ANALYSTS", "").strip()
    if env_analysts:
        out["selected_analysts"] = [a.strip() for a in env_analysts.split(",") if a.strip()]

    return out


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _default_provider() -> str:
    env = os.getenv("TRADINGAGENTS_LLM_PROVIDER", "").strip()
    if env:
        return env
    if os.getenv("DEEPSEEK_API_KEY"):
        return "deepseek"
    if os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"):
        return "google"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return "ollama"


def ensure_data_dirs() -> dict[str, str]:
    results = Path(
        os.getenv("TRADINGAGENTS_RESULTS_DIR")
        or project_path("artifacts", "research")
    )
    cache = Path(
        os.getenv("TRADINGAGENTS_CACHE_DIR")
        or project_path("data", "tradingagents", "cache")
    )
    memory = Path(
        os.getenv("TRADINGAGENTS_MEMORY_LOG_PATH")
        or project_path("data", "tradingagents", "memory", "trading_memory.md")
    )
    results.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    memory.parent.mkdir(parents=True, exist_ok=True)
    if not memory.exists():
        memory.write_text("# TradingAgents memory log\n", encoding="utf-8")
    return {
        "results_dir": str(results.resolve()),
        "data_cache_dir": str(cache.resolve()),
        "memory_log_path": str(memory.resolve()),
    }


def build_ta_config(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Config overlays passed to the sidecar (paths + debate rounds + provider)."""
    paths = ensure_data_dirs()
    agents = load_agents_settings()
    provider = agents.get("llm_provider") or _default_provider()

    cfg_out: dict[str, Any] = {
        **paths,
        "llm_provider": provider,
        "max_debate_rounds": int(agents["max_debate_rounds"]),
        "max_risk_discuss_rounds": int(agents["max_risk_rounds"]),
        "checkpoint_enabled": bool(agents["checkpoint_enabled"]),
        "holding_period_days": int(agents["holding_period_days"]),
        # Default India benchmark; never leave None (upstream falls back to SPY).
        "benchmark_ticker": os.getenv("TRADINGAGENTS_BENCHMARK_TICKER") or "^NSEI",
        # Prefer India-relevant macro queries when not overridden upstream
        "output_language": os.getenv("TRADINGAGENTS_OUTPUT_LANGUAGE") or "English",
    }

    # Model defaults by provider (cheap experiments)
    if provider == "deepseek":
        cfg_out.setdefault("quick_think_llm", os.getenv("TRADINGAGENTS_QUICK_THINK_LLM") or "deepseek-chat")
        cfg_out.setdefault("deep_think_llm", os.getenv("TRADINGAGENTS_DEEP_THINK_LLM") or "deepseek-chat")
    elif provider == "google":
        model = os.getenv("TRADINGAGENTS_QUICK_THINK_LLM") or os.getenv("GEMINI_MODEL") or "gemini-2.0-flash"
        cfg_out.setdefault("quick_think_llm", model)
        cfg_out.setdefault(
            "deep_think_llm",
            os.getenv("TRADINGAGENTS_DEEP_THINK_LLM") or model,
        )
        # StockScope often uses GEMINI_API_KEY; TradingAgents expects GOOGLE_API_KEY
        if os.getenv("GEMINI_API_KEY") and not os.getenv("GOOGLE_API_KEY"):
            os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]
    elif provider == "ollama":
        cfg_out.setdefault("quick_think_llm", os.getenv("TRADINGAGENTS_QUICK_THINK_LLM") or "llama3.1")
        cfg_out.setdefault("deep_think_llm", os.getenv("TRADINGAGENTS_DEEP_THINK_LLM") or "llama3.1")
        cfg_out.setdefault(
            "backend_url",
            os.getenv("TRADINGAGENTS_LLM_BACKEND_URL")
            or os.getenv("OLLAMA_BASE_URL")
            or "http://localhost:11434/v1",
        )

    if agents.get("quick_think_llm"):
        cfg_out["quick_think_llm"] = agents["quick_think_llm"]
    if agents.get("deep_think_llm"):
        cfg_out["deep_think_llm"] = agents["deep_think_llm"]

    if extra:
        cfg_out.update({k: v for k, v in extra.items() if v is not None})
    return cfg_out


def to_yahoo_ticker(symbol: str, *, fno_underlying: bool = False) -> str:
    """Map StockScope symbols to Yahoo tickers (.NS / .BO / indices)."""
    raw = (symbol or "").strip().upper()
    if not raw:
        raise ValueError("empty symbol")

    # Strip option-ish noise; F&O tickets pass underlying explicitly
    base = raw.split()[0]
    if base in INDEX_MAP:
        return INDEX_MAP[base]
    if fno_underlying and base in FNO_UNDERLYING:
        return FNO_UNDERLYING[base]

    if base.endswith(".NS") or base.endswith(".BO"):
        return base
    if base.startswith("^"):
        return base

    # Equity default: NSE
    return f"{base}.NS"


def yahoo_ticker_candidates(symbol: str) -> list[str]:
    primary = to_yahoo_ticker(symbol)
    out = [primary]
    if primary.endswith(".NS"):
        out.append(primary[:-3] + ".BO")
    return out


def build_portfolio_context(*, known: bool = True) -> dict[str, Any] | None:
    """Build PortfolioContext dict from StockScope wallet.

    - known=True + flat book → positions=[] (agents know you are flat)
    - known=False → None (omit portfolio; agents have no holdings knowledge)
    Never includes broker tokens.
    """
    if not known:
        return None
    try:
        from portfolio.models import PortfolioSettings, Position
    except Exception:  # noqa: BLE001
        return None

    ps = PortfolioSettings.get_solo()
    positions_out: list[dict[str, Any]] = []
    for pos in Position.objects.filter(status="open", is_dummy=False).select_related(
        "instrument"
    ):
        sym = pos.instrument.symbol
        try:
            y = to_yahoo_ticker(sym)
        except ValueError:
            y = sym
        positions_out.append(
            {
                "ticker": y,
                "quantity": float(pos.qty),
                "average_price": float(pos.entry_price),
            }
        )
    return {
        "cash": float(ps.cash),
        "currency": "INR",
        "positions": positions_out,
    }


def sidecar_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("TRADINGAGENTS_NO_UPDATE_CHECK", "1")
    paths = ensure_data_dirs()
    env["TRADINGAGENTS_RESULTS_DIR"] = paths["results_dir"]
    env["TRADINGAGENTS_CACHE_DIR"] = paths["data_cache_dir"]
    env["TRADINGAGENTS_MEMORY_LOG_PATH"] = paths["memory_log_path"]
    # Mirror GEMINI → GOOGLE for TA clients
    if env.get("GEMINI_API_KEY") and not env.get("GOOGLE_API_KEY"):
        env["GOOGLE_API_KEY"] = env["GEMINI_API_KEY"]
    return env


def invoke_runner(request: dict[str, Any], *, timeout_sec: int | None = None) -> dict[str, Any]:
    """Call scripts/ta_runner.py via the TA interpreter. Never imports tradingagents here."""
    python = resolve_ta_python()
    runner = resolve_ta_runner()
    if not runner.exists():
        return {"ok": False, "error": f"runner missing: {runner}"}
    if not python.exists():
        return {
            "ok": False,
            "error": (
                "agents sidecar not installed — create .venv-ta and "
                "pip install -r requirements-tradingagents.txt "
                f"(expected {python})"
            ),
            "badge": "agents_unavailable",
        }

    agents = load_agents_settings()
    timeout = int(timeout_sec or agents["timeout_sec"] or 600)
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
            "stderr": stderr,
        }
    # Last JSON line wins (ignore any log noise)
    line = stdout.splitlines()[-1]
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": "invalid JSON from runner",
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


def provider_keys_present() -> bool:
    provider = _default_provider()
    if provider == "ollama":
        return True
    if provider == "chatgpt":
        # Auth file may exist; treat as present if configured
        return True
    key_map = {
        "openai": "OPENAI_API_KEY",
        "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        "anthropic": "ANTHROPIC_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "xai": "XAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "qwen": ("DASHSCOPE_API_KEY", "DASHSCOPE_CN_API_KEY"),
        "glm": ("ZHIPU_API_KEY", "ZHIPU_CN_API_KEY"),
        "minimax": ("MINIMAX_API_KEY", "MINIMAX_CN_API_KEY"),
        "mistral": "MISTRAL_API_KEY",
        "kimi": "MOONSHOT_API_KEY",
        "groq": "GROQ_API_KEY",
        "nvidia": "NVIDIA_API_KEY",
        "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
        "bedrock": "AWS_ACCESS_KEY_ID",
        "azure": "AZURE_OPENAI_API_KEY",
    }
    keys = key_map.get(provider, ())
    if isinstance(keys, str):
        keys = (keys,)
    return any(os.getenv(k) for k in keys)


def today_ist() -> date:
    from django.utils import timezone

    return timezone.localdate()
