"""Portal client behaviour against a fake portal (httpx.MockTransport), no network needed."""

import httpx
import pytest

from app.portal.client import UrjaPortalClient, sign_request
from app.portal.errors import PortalAuthError, PortalNotFoundError, PortalTimeoutError, PortalUnavailableError

LOGIN_OK = {"type": "redirect", "status": 303, "location": "/meters"}
LOGIN_FAILED = {"type": "failure", "status": 401, "data": "[{\"error\":1},\"Invalid email or password.\"]"}
UNAUTHORIZED = {"error": "unauthorized", "message": "A valid session is required."}
SECRET = "test-secret-not-real"


class FakePortal:
    """Scripted portal: maps a path to a list of responses served in order (last one repeats)."""

    def __init__(self, routes):
        self.routes = {path: list(responses) for path, responses in routes.items()}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.routes[request.url.path]
        status, body = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(status, json=body)

    def paths(self):
        return [r.url.path for r in self.requests]


def make_client(portal, **kwargs):
    sleeps = []
    client = UrjaPortalClient(
        "https://portal.test", "user@example.test", "pw",
        min_request_interval_seconds=0, transport=httpx.MockTransport(portal), sleep=sleeps.append, **kwargs,
    )
    return client, sleeps


def test_signature_matches_portal_browser_implementation():
    # Expected value computed with the portal's own WebCrypto code in the browser.
    assert sign_request("GET", "/portal/export", "page=1", "1700000000", SECRET) == (
        "bc5c2c81a65c44edd8eba6081dd95d461286bfd71d3f6131f9958bf1725d2ac3"
    )


def test_logs_in_lazily_with_sveltekit_form_action():
    portal = FakePortal({"/login": [(200, LOGIN_OK)], "/portal/meters/J1/energy": [(200, {"data": []})]})
    client, _ = make_client(portal)

    client.get_energy("J1")

    login = portal.requests[0]
    assert login.method == "POST" and login.url.path == "/login"
    assert login.headers["x-sveltekit-action"] == "true"
    assert login.headers["origin"] == "https://portal.test"  # SvelteKit CSRF check
    assert b"email=user%40example.test" in login.content


def test_bad_credentials_raise_auth_error():
    portal = FakePortal({"/login": [(200, LOGIN_FAILED)]})
    client, _ = make_client(portal)

    with pytest.raises(PortalAuthError):
        client.get_energy("J1")


def test_expired_session_triggers_one_relogin_and_retry():
    portal = FakePortal({
        "/login": [(200, LOGIN_OK)],
        "/portal/meters/J1/energy": [(200, {"data": []}), (401, UNAUTHORIZED), (200, {"data": [{"kwh": "1"}]})],
    })
    client, _ = make_client(portal)
    client.get_energy("J1")

    assert client.get_energy("J1") == [{"kwh": "1"}]
    assert portal.paths().count("/login") == 2


def test_session_rejected_after_relogin_is_auth_error():
    portal = FakePortal({"/login": [(200, LOGIN_OK)], "/portal/meters/J1/energy": [(401, UNAUTHORIZED)]})
    client, _ = make_client(portal)

    with pytest.raises(PortalAuthError):
        client.get_energy("J1")


def test_rate_limit_backs_off_then_succeeds():
    portal = FakePortal({
        "/login": [(200, LOGIN_OK)],
        "/portal/meters/J1/energy": [(429, {"error": "rate_limited"}), (429, {"error": "rate_limited"}), (200, {"data": []})],
    })
    client, sleeps = make_client(portal)

    assert client.get_energy("J1") == []
    assert sleeps == [2.0, 4.0]


def test_rate_limit_gives_up_after_max_retries():
    portal = FakePortal({"/login": [(200, LOGIN_OK)], "/portal/meters/J1/energy": [(429, {"error": "rate_limited"})]})
    client, sleeps = make_client(portal, max_rate_limit_retries=2)

    with pytest.raises(PortalUnavailableError):
        client.get_energy("J1")
    assert len(sleeps) == 2


def test_unknown_meter_is_not_found():
    portal = FakePortal({"/login": [(200, LOGIN_OK)], "/portal/meters/NOPE/energy": [(404, {"error": "not_found"})]})
    client, _ = make_client(portal)

    with pytest.raises(PortalNotFoundError):
        client.get_energy("NOPE")


@pytest.mark.parametrize("meter_id", ["..", "../keys", "J1?x=1", "J1/geo", ""])
def test_unsafe_meter_ids_never_reach_the_portal(meter_id):
    portal = FakePortal({"/login": [(200, LOGIN_OK)]})
    client, _ = make_client(portal)

    with pytest.raises(PortalNotFoundError):
        client.get_energy(meter_id)
    assert portal.requests == []


def test_timeout_is_reported_as_timeout():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    client, _ = make_client(timeout)
    with pytest.raises(PortalTimeoutError):
        client.login()


def test_export_signs_request_with_secret_from_keys_endpoint():
    portal = FakePortal({
        "/login": [(200, LOGIN_OK)],
        "/portal/keys": [(200, {"data": {"signingSecret": SECRET}})],
        "/portal/export": [(200, {"data": [{"meterId": "J100000"}], "total": 1})],
    })
    client, _ = make_client(portal)

    assert client.export_meters() == [{"meterId": "J100000"}]
    export = portal.requests[-1]
    assert export.url.query == b"page=1"
    expected = sign_request("GET", "/portal/export", "page=1", export.headers["x-timestamp"], SECRET)
    assert export.headers["x-signature"] == expected


def test_export_refetches_secret_once_when_signature_rejected():
    portal = FakePortal({
        "/login": [(200, LOGIN_OK)],
        "/portal/keys": [(200, {"data": {"signingSecret": "old"}}), (200, {"data": {"signingSecret": SECRET}})],
        "/portal/export": [(401, {"error": "signature_invalid"}), (200, {"data": [], "total": 0})],
    })
    client, _ = make_client(portal)

    assert client.export_meters() == []
    assert portal.paths().count("/portal/keys") == 2


def test_transformers_walks_all_pages():
    portal = FakePortal({
        "/login": [(200, LOGIN_OK)],
        "/portal/dts": [
            (200, {"data": [{"code": "DT-001"}], "total": 2, "page": 1, "pageSize": 1}),
            (200, {"data": [{"code": "DT-002"}], "total": 2, "page": 2, "pageSize": 1}),
        ],
    })
    client, _ = make_client(portal)

    assert [t["code"] for t in client.list_transformers()] == ["DT-001", "DT-002"]
