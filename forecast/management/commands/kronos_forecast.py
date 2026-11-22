from __future__ import annotations

from django.core.management.base import BaseCommand

from forecast.adapter import kronos_enabled, kronos_sidecar_installed
from forecast.services import forecast_symbol, forecast_universe


class Command(BaseCommand):
    help = "Run Kronos forecasts for symbols (local CPU sidecar)."

    def add_arguments(self, parser):
        parser.add_argument("--symbols", nargs="*", default=[], help="Symbols e.g. RELIANCE TCS")
        parser.add_argument("--universe", type=int, default=0, help="Shortlist top-N symbols")
        parser.add_argument("--interval", default="1d")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--pred-len", type=int, default=0)

    def handle(self, *args, **opts):
        if not kronos_sidecar_installed():
            self.stderr.write(self.style.ERROR("kronos sidecar not installed (.venv-kronos)"))
            return
        if not kronos_enabled():
            self.stdout.write(self.style.WARNING("KRONOS_ENABLED is off — runs will be marked error unless forced via env"))

        symbols = [s.upper().replace(".NS", "") for s in (opts["symbols"] or [])]
        if opts["universe"]:
            from marketdata.universe import shortlist_symbols

            symbols = list(dict.fromkeys(symbols + shortlist_symbols(int(opts["universe"]))))
        if not symbols:
            from marketdata.management.commands.bootstrap import BOOTSTRAP_SYMBOLS

            symbols = [s.upper().replace(".NS", "") for s in BOOTSTRAP_SYMBOLS[:15]]

        plen = int(opts["pred_len"] or 0) or None
        ok = err = 0
        for sym in symbols:
            run = forecast_symbol(
                sym,
                interval=opts["interval"],
                pred_len=plen,
                force=bool(opts["force"]),
            )
            if run.status == "ok":
                ok += 1
                self.stdout.write(
                    f"OK {run.symbol} call={run.call} p_up={(run.stats or {}).get('p_up')}"
                )
            else:
                err += 1
                self.stdout.write(self.style.WARNING(f"ERR {run.symbol}: {run.error[:120]}"))
        self.stdout.write(self.style.SUCCESS(f"done ok={ok} err={err}"))
