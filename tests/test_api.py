import pytest
from django.urls import reverse
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_health_endpoint():
    client = APIClient()
    resp = client.get("/api/health/")
    assert resp.status_code == 200
    assert "kill_switch" in resp.data


@pytest.mark.django_db
def test_signals_list():
    client = APIClient()
    resp = client.get("/api/signals/?status=new")
    assert resp.status_code == 200
