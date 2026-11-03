"""Public API behaviour: real routes and service, fake portal client."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.portal.errors import PortalAuthError, PortalNotFoundError, PortalTimeoutError, PortalUnavailableError
from app.service import MeterService
from tests.fixtures import HALF_HOURLY_ROWS, export_record


class FakePortalClient:
    def __init__(self, meters=None, energy=None, error=None):
        self.meters = meters if meters is not None else []
        self.energy = energy or {}
        self.error = error
        self.export_calls = 0

    def export_meters(self):
        self.export_calls += 1
        if self.error:
            raise self.error
        return self.meters

    def list_transformers(self):
        return [{"code": "DT-001", "name": "Malviya Nagar DT 1", "feederCode": "F-001", "capacityKva": 100}]

    def get_energy(self, meter_id):
        if self.error:
            raise self.error
        if meter_id not in self.energy:
            raise PortalNotFoundError(meter_id)
        return self.energy[meter_id]


def api(portal):
    service = MeterService(portal, cache_ttl_seconds=600)
    return TestClient(create_app(service))


@pytest.fixture
def portal():
    feeder_one = export_record()["hierarchy"] | {"feeder": {"code": "F-001", "name": "Feeder 1"}}
    return FakePortalClient(
        meters=[
            export_record("J100002", serialNo="AL28136", installStatus="Faulty", make="Genus"),
            export_record("J100001"),
            export_record("J100003", serialNo="SE11111", phaseType="three", hierarchy=feeder_one),
        ],
        energy={"J100001": HALF_HOURLY_ROWS},
    )


def test_list_meters_is_sorted_and_paginated(portal):
    with api(portal) as client:
        body = client.get("/meters", params={"page_size": 2}).json()

    assert body["total"] == 3
    assert [m["meter_id"] for m in body["items"]] == ["J100001", "J100002"]


def test_list_meters_filters(portal):
    with api(portal) as client:
        assert [m["meter_id"] for m in client.get("/meters?install_status=faulty").json()["items"]] == ["J100002"]
        assert [m["meter_id"] for m in client.get("/meters?make=genus").json()["items"]] == ["J100002"]
        assert [m["meter_id"] for m in client.get("/meters?feeder_code=F-001").json()["items"]] == ["J100003"]
        assert [m["meter_id"] for m in client.get("/meters?q=se111").json()["items"]] == ["J100003"]


def test_meter_snapshot_is_cached_between_requests(portal):
    with api(portal) as client:
        client.get("/meters")
        client.get("/meters/J100001")

    assert portal.export_calls == 1


def test_get_meter(portal):
    with api(portal) as client:
        response = client.get("/meters/J100001")

    assert response.status_code == 200
    body = response.json()
    assert body["dt_code"] == "DT-002"
    assert body["network"][0] == {"level": "zone", "code": "Z-02", "name": "Jaipur Zone 2"}


def test_unknown_meter_is_404(portal):
    with api(portal) as client:
        response = client.get("/meters/J999999")

    assert response.status_code == 404
    assert response.json()["error"] == "meter_not_found"


def test_consumption(portal):
    with api(portal) as client:
        body = client.get("/meters/J100001/consumption").json()

    assert body["interval_minutes"] == 30
    assert body["readings"][0]["timestamp"] == "2026-06-23T23:30:00"
    assert body["readings"][1]["kwh_consumed"] == 0.42


def test_consumption_for_unknown_meter_is_404(portal):
    with api(portal) as client:
        assert client.get("/meters/J999999/consumption").status_code == 404


def test_transformers(portal):
    with api(portal) as client:
        assert client.get("/transformers").json()[0]["capacity_kva"] == 100.0


def test_network(portal):
    with api(portal) as client:
        body = client.get("/network").json()

    assert body["zones"][0]["meter_count"] == 3
    assert {"codes_under_many_parents", "codes_with_many_names", "missing_values"} <= body["data_quality"].keys()


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 0}, {"page_size": 201}])
def test_invalid_pagination_is_422(portal, params):
    with api(portal) as client:
        assert client.get("/meters", params=params).status_code == 422


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (PortalAuthError("bad creds"), 502, "portal_auth_failed"),
        (PortalUnavailableError("down"), 503, "portal_unavailable"),
        (PortalTimeoutError("slow"), 504, "portal_timeout"),
    ],
)
def test_portal_failures_map_to_gateway_errors(error, status, code):
    with api(FakePortalClient(error=error)) as client:
        response = client.get("/meters")

    assert response.status_code == status
    assert response.json()["error"] == code


def test_stale_snapshot_is_served_when_refresh_fails(portal):
    service = MeterService(portal, cache_ttl_seconds=0)  # every request tries to refresh
    with TestClient(create_app(service)) as client:
        assert client.get("/meters").status_code == 200
        portal.error = PortalUnavailableError("down")
        response = client.get("/meters")

    assert response.status_code == 200
    assert response.json()["total"] == 3
