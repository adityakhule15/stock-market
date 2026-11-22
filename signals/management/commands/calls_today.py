from __future__ import annotations

import json

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Print today's combined multi-model calls (JSON or table)."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--refresh", action="store_true", help="Run model review sync first")

    def handle(self, *args, **opts):
        from signals.consensus import consensus_from_signal
        from signals.model_review import review_signals
        from signals.models import Signal

        today = timezone.localdate()
        qs = list(
            Signal.objects.filter(created_at__date=today)
            .select_related("instrument")
            .order_by("-probability")[:50]
        )
        if opts["refresh"] and qs:
            self.stdout.write(f"Refreshing {len(qs)} signals…")
            review_signals(qs)

        rows = []
        for s in qs:
            pipe = s.pipeline or {}
            if not pipe.get("consensus") or opts["refresh"]:
                bundle = consensus_from_signal(s)
                models = bundle["models"]
                consensus = pipe.get("consensus") or bundle["consensus"]
            else:
                models = pipe.get("model_calls") or {}
                consensus = pipe.get("consensus") or {}
            rows.append(
                {
                    "id": s.id,
                    "symbol": s.instrument.symbol,
                    "side": s.side,
                    "gate": (s.gate or {}).get("result"),
                    "probability": float(s.probability),
                    "models": {
                        k: {
                            "call": v.get("call"),
                            "vote": v.get("vote"),
                            "parameters": v.get("parameters"),
                        }
                        for k, v in models.items()
                        if isinstance(v, dict) and k != "skill_checklist"
                    },
                    "skill_checklist": models.get("skill_checklist")
                    or pipe.get("skill_checklist")
                    or [],
                    "consensus": consensus,
                }
            )

        if opts["json"]:
            self.stdout.write(json.dumps({"as_of": today.isoformat(), "calls": rows}, default=str, indent=2))
            return

        self.stdout.write(f"Calls for {today} ({len(rows)})")
        for r in rows:
            cs = r.get("consensus") or {}
            self.stdout.write(
                f"{r['symbol']:12} gate={r['gate'] or '-':5} "
                f"lgbm={r['probability']:.2f} "
                f"tm={(r['models'].get('time_machine') or {}).get('call')} "
                f"kronos={(r['models'].get('kronos') or {}).get('call')} "
                f"agents={(r['models'].get('tradingagents') or {}).get('call')} "
                f"→ {cs.get('final_call') or cs.get('badge') or '—'} "
                f"score={cs.get('score')}"
            )
