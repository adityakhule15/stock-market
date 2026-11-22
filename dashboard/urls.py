from django.urls import path

from dashboard import views

urlpatterns = [
    path("", views.signals_page, name="signals"),
    path("advisor/", views.advisor_page, name="advisor"),
    path("partials/signals/", views.signals_partial, name="signals_partial"),
    path("blocked/", views.blocked_page, name="blocked"),
    path("positions/", views.positions_page, name="positions"),
    path("journal/", views.journal_page, name="journal"),
    path("journal/weekly/", views.journal_weekly_page, name="journal_weekly"),
    path("insights/", views.insights_page, name="insights"),
    path("performance/", views.performance_page, name="performance"),
    path("models/", views.models_page, name="models"),
    path("backtest/", views.backtest_page, name="backtest"),
    path("settings/", views.settings_page, name="settings"),
]
