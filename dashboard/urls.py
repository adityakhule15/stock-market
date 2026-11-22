from django.urls import path
from django.views.generic import RedirectView

from dashboard import views

urlpatterns = [
    path("", views.signals_page, name="signals"),
    path("results/", views.results_page, name="results"),
    path("calls/", RedirectView.as_view(url="/results/?tab=votes", permanent=False), name="calls"),
    path("advisor/", views.advisor_page, name="advisor"),
    path("partials/signals/", views.signals_partial, name="signals_partial"),
    path("blocked/", RedirectView.as_view(url="/results/?tab=blocked", permanent=False), name="blocked"),
    path("positions/", views.positions_page, name="positions"),
    path("journal/", views.journal_page, name="journal"),
    path("journal/weekly/", views.journal_weekly_page, name="journal_weekly"),
    path("insights/", RedirectView.as_view(url="/results/?tab=practice", permanent=False), name="insights"),
    path("performance/", RedirectView.as_view(url="/results/?tab=practice", permanent=False), name="performance"),
    path("models/", RedirectView.as_view(url="/results/?tab=model", permanent=False), name="models"),
    path("backtest/", RedirectView.as_view(url="/results/?tab=backtest", permanent=False), name="backtest"),
    path("settings/", views.settings_page, name="settings"),
    path("research/report/<int:run_id>/", views.research_report, name="research_report"),
]
