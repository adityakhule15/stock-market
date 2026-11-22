from django.urls import include, path
from rest_framework.routers import DefaultRouter

from dashboard import api

router = DefaultRouter()
router.register(r"signals", api.SignalViewSet, basename="signal")
router.register(r"positions", api.PositionViewSet, basename="position")
router.register(r"models", api.ModelViewSet, basename="model")

urlpatterns = [
    # Before router so "stream" is not captured as position pk
    path("positions/stream/", api.positions_stream, name="positions_stream"),
    path("", include(router.urls)),
    path("quotes/<str:symbol>/", api.quotes, name="quotes"),
    path("option-chain/", api.option_chain, name="option_chain"),
    path("advisor/start/", api.advisor_start, name="advisor_start"),
    path("advisor/chat/", api.advisor_chat, name="advisor_chat"),
    path("advisor/ai-chat/", api.advisor_ai_chat, name="advisor_ai_chat"),
    path("advisor/ai-status/", api.advisor_ai_status, name="advisor_ai_status"),
    path("advisor/tomorrow/", api.advisor_tomorrow, name="advisor_tomorrow"),
    path("performance/", api.performance, name="performance"),
    path("analysis/", api.analysis, name="analysis"),
    path("health/", api.health, name="health"),
    path("orb/", api.orb_today, name="orb"),
    path("kill-switch/", api.set_kill_switch, name="kill_switch"),
    path("portfolio/reset-life/", api.reset_life, name="reset_life"),
    path("settings/fno-watchlist/", api.fno_watchlist, name="fno_watchlist"),
    path("settings/indstocks-token/", api.indstocks_token, name="indstocks_token"),
    path("settings/live-refresh/", api.live_refresh, name="live_refresh"),
    path("settings/agents/", api.agents_settings, name="agents_settings"),
    path("settings/models/", api.models_settings, name="models_settings"),
    path("calls/today/", api.calls_today, name="calls_today"),
    path("calls/refresh/", api.calls_refresh, name="calls_refresh"),
    path("research/run/", api.research_run, name="research_run"),
    path("research/runs/", api.research_runs, name="research_runs"),
    path("journal/day-book/", api.day_book, name="day_book"),
    path("journal/retrain/", api.retrain_now, name="retrain_now"),
    path("journal/export.csv", api.journal_export_csv, name="journal_export_csv"),
]
