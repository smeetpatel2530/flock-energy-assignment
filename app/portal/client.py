"""Transport-level client for the Urja Meter Ops portal.

Knows how to log in, keep the session alive, respect the portal's rate limit and sign
bulk-export requests. Returns the portal's raw JSON payloads; mapping them into our public
models is done in `app.portal.parsing`. See PROTOCOL.md for the observed behaviour this
code relies on.
"""

import hashlib
import hmac
import logging
import re
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.portal.errors import (
    PortalAuthError,
    PortalNotFoundError,
    PortalResponseError,
    PortalSignatureError,
    PortalTimeoutError,
    PortalUnavailableError,
)

logger = logging.getLogger(__name__)

SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")
EXPORT_PATH = "/portal/export"
# The export ignores `page` and always returns every meter, but the portal UI sends
# page=1, so we do the same to stay on the code path the portal itself uses.
EXPORT_QUERY = "page=1"


def sign_request(method: str, path: str, query: str, timestamp: str, secret: str) -> str:
    """Reproduce the portal's export signature: hex HMAC-SHA256 over method, path, query, timestamp."""
    message = "\n".join([method, path, query, timestamp])
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


class UrjaPortalClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout_seconds: float = 15.0,
        min_request_interval_seconds: float = 0.5,
        max_rate_limit_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._username = username
        self._password = password
        self._min_interval = min_request_interval_seconds
        self._max_rate_limit_retries = max_rate_limit_retries
        self._sleep = sleep
        self._http = httpx.Client(base_url=base_url, timeout=timeout_seconds, transport=transport)
        # One request at a time: the portal rate-limits per client, so concurrency buys nothing.
        self._lock = threading.RLock()
        self._logged_in = False
        self._last_request_at = 0.0

    def close(self) -> None:
        self._http.close()

    # ---- public operations -------------------------------------------------

    def export_meters(self) -> list[dict[str, Any]]:
        """All meters with nameplate, hierarchy and geo, via the signed bulk export."""
        with self._lock:
            secret = self._signing_secret()
            try:
                payload = self._get_json(EXPORT_PATH, EXPORT_QUERY, self._export_headers(secret))
            except PortalSignatureError:
                # The secret may have rotated (not observed, but cheap to handle): refetch once.
                logger.info("Export signature rejected; refetching signing secret")
                secret = self._signing_secret()
                payload = self._get_json(EXPORT_PATH, EXPORT_QUERY, self._export_headers(secret))
        return _require_list(payload, "data")

    def list_transformers(self) -> list[dict[str, Any]]:
        """All distribution transformers, walking the portal's 20-per-page listing."""
        transformers: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = self._get_json("/portal/dts", f"page={page}")
            rows = _require_list(payload, "data")
            transformers.extend(rows)
            total = payload.get("total")
            if not rows or not isinstance(total, int) or len(transformers) >= total:
                return transformers
            page += 1

    def get_energy(self, meter_id: str) -> list[dict[str, Any]]:
        """Raw energy register readings for one meter."""
        # The ID goes into the portal URL; refuse anything that could change the path or
        # query (e.g. "..", "?"), so callers cannot steer our session to other portal URLs.
        if not SAFE_ID.fullmatch(meter_id):
            raise PortalNotFoundError(f"Not a valid meter ID: {meter_id!r}")
        payload = self._get_json(f"/portal/meters/{meter_id}/energy")
        return _require_list(payload, "data")

    # ---- session -----------------------------------------------------------

    def login(self) -> None:
        with self._lock:
            logger.info("Logging in to portal")
            # The portal's login form is a SvelteKit form action. With this header it answers
            # JSON ({"type": "redirect"} on success, {"type": "failure"} otherwise) and sets
            # the HttpOnly session cookie, which httpx keeps in its cookie jar. Origin is
            # required: SvelteKit's CSRF check rejects form POSTs without a same-site Origin
            # (403 "Cross-site POST form submissions are forbidden").
            response = self._send(
                "POST",
                "/login",
                data={"email": self._username, "password": self._password},
                headers={
                    "accept": "application/json",
                    "x-sveltekit-action": "true",
                    "origin": str(self._http.base_url).rstrip("/"),
                },
            )
            result = _json(response)
            if result.get("type") != "redirect":
                self._logged_in = False
                raise PortalAuthError("Portal rejected the configured credentials")
            self._logged_in = True

    def _signing_secret(self) -> str:
        payload = self._get_json("/portal/keys")
        secret = payload.get("data", {}).get("signingSecret") if isinstance(payload, dict) else None
        if not isinstance(secret, str) or not secret:
            raise PortalResponseError("Portal /portal/keys did not return a signing secret")
        return secret

    def _export_headers(self, secret: str) -> Callable[[], dict[str, str]]:
        # A factory, because each retry needs a fresh timestamp (stale ones are rejected).
        def build() -> dict[str, str]:
            timestamp = str(int(time.time()))
            signature = sign_request("GET", EXPORT_PATH, EXPORT_QUERY, timestamp, secret)
            return {"x-timestamp": timestamp, "x-signature": signature}

        return build

    # ---- HTTP plumbing -----------------------------------------------------

    def _get_json(
        self,
        path: str,
        query: str = "",
        headers_factory: Callable[[], dict[str, str]] | None = None,
    ) -> Any:
        """GET a /portal JSON endpoint, handling login, session expiry and rate limiting."""
        url = f"{path}?{query}" if query else path
        with self._lock:
            if not self._logged_in:
                self.login()
            relogged = False
            rate_limit_retries = 0
            while True:
                headers = headers_factory() if headers_factory else None
                response = self._send("GET", url, headers=headers)

                if response.status_code == 401:
                    error = _error_code(response)
                    if error == "signature_invalid":
                        raise PortalSignatureError("Portal rejected the export signature")
                    if relogged:
                        raise PortalAuthError("Portal rejected a freshly created session")
                    logger.info("Portal session missing or expired; logging in again")
                    self.login()
                    relogged = True
                    continue

                if response.status_code == 429:
                    if rate_limit_retries >= self._max_rate_limit_retries:
                        raise PortalUnavailableError("Portal is rate limiting requests")
                    # No Retry-After header is sent; back off exponentially (2s, 4s, 8s...).
                    delay = 2.0 * (2**rate_limit_retries)
                    rate_limit_retries += 1
                    logger.warning("Portal rate limit hit; retrying in %.0fs", delay)
                    self._sleep(delay)
                    continue

                if response.status_code == 404:
                    raise PortalNotFoundError(f"Portal returned 404 for {path}")
                if response.status_code >= 500:
                    raise PortalUnavailableError(f"Portal returned HTTP {response.status_code}")
                if response.status_code != 200:
                    raise PortalResponseError(f"Unexpected HTTP {response.status_code} from {path}")
                return _json(response)

    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self._throttle()
        try:
            return self._http.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise PortalTimeoutError("Portal did not respond in time") from exc
        except httpx.TransportError as exc:
            raise PortalUnavailableError(f"Could not reach portal: {exc}") from exc

    def _throttle(self) -> None:
        wait = self._last_request_at + self._min_interval - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        self._last_request_at = time.monotonic()


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise PortalResponseError("Portal returned a non-JSON response") from exc


def _error_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("error") if isinstance(body, dict) else None


def _require_list(payload: Any, key: str) -> list[dict[str, Any]]:
    value = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(value, list):
        raise PortalResponseError(f"Portal response is missing a '{key}' list")
    return value
