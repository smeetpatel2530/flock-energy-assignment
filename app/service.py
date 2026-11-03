"""Application logic between the HTTP layer and the portal client.

Meter metadata (nameplate, network position, location) comes from the portal's bulk export
and is held in memory for `cache_ttl_seconds`: one export replaces hundreds of per-meter
requests against a rate-limited portal. Consumption is fetched live per request because it
is per-meter data that the export does not include.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from app.models import Consumption, Meter, MeterPage, Network, Transformer
from app.network import build_network
from app.portal.client import UrjaPortalClient
from app.portal.errors import PortalError, PortalNotFoundError
from app.portal.parsing import parse_consumption, parse_meter, parse_transformer

logger = logging.getLogger(__name__)

T = TypeVar("T")


class MeterNotFoundError(Exception):
    def __init__(self, meter_id: str) -> None:
        super().__init__(f"Meter {meter_id} not found")
        self.meter_id = meter_id


@dataclass(frozen=True)
class MeterFilters:
    q: str | None = None
    install_status: str | None = None
    install_type: str | None = None
    phase_type: str | None = None
    make: str | None = None
    dt_code: str | None = None
    feeder_code: str | None = None

    def matches(self, meter: Meter) -> bool:
        feeder = next((n.code for n in meter.network if n.level == "feeder"), None)
        checks = [
            (self.install_status, meter.install_status),
            (self.install_type, meter.install_type),
            (self.phase_type, meter.phase_type),
            (self.make, meter.make),
            (self.dt_code, meter.dt_code),
            (self.feeder_code, feeder),
        ]
        if any(wanted is not None and (actual or "").lower() != wanted.lower() for wanted, actual in checks):
            return False
        if self.q:
            needle = self.q.lower()
            return needle in meter.meter_id.lower() or needle in meter.serial_number.lower()
        return True


class TimedCache(Generic[T]):
    """Holds one loaded value for `ttl` seconds. If a refresh fails, keeps serving the stale value."""

    def __init__(self, loader: Callable[[], T], ttl: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._loader = loader
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._value: T | None = None
        self._loaded_at = 0.0

    def get(self) -> T:
        with self._lock:
            if self._value is not None and self._clock() - self._loaded_at < self._ttl:
                return self._value
            try:
                self._value = self._loader()
                self._loaded_at = self._clock()
            except PortalError:
                if self._value is None:
                    raise
                logger.warning("Portal refresh failed; serving stale data", exc_info=True)
            return self._value


class MeterService:
    def __init__(self, client: UrjaPortalClient, cache_ttl_seconds: float) -> None:
        self._client = client
        self._meters = TimedCache(self._load_meters, cache_ttl_seconds)
        self._transformers = TimedCache(self._load_transformers, cache_ttl_seconds)

    def list_meters(self, filters: MeterFilters, page: int, page_size: int) -> MeterPage:
        matching = [m for m in self._meters.get().values() if filters.matches(m)]
        start = (page - 1) * page_size
        return MeterPage(items=matching[start : start + page_size], total=len(matching), page=page, page_size=page_size)

    def get_meter(self, meter_id: str) -> Meter:
        meter = self._meters.get().get(meter_id)
        if meter is None:
            raise MeterNotFoundError(meter_id)
        return meter

    def get_consumption(self, meter_id: str) -> Consumption:
        try:
            rows = self._client.get_energy(meter_id)
        except PortalNotFoundError:
            raise MeterNotFoundError(meter_id) from None
        return parse_consumption(meter_id, rows)

    def get_network(self) -> Network:
        # Rebuilt per call from the cached snapshot: ~400 meters takes milliseconds.
        return build_network(list(self._meters.get().values()))

    def list_transformers(self) -> list[Transformer]:
        return self._transformers.get()

    def _load_meters(self) -> dict[str, Meter]:
        meters = [parse_meter(record) for record in self._client.export_meters()]
        logger.info("Loaded %d meters from portal export", len(meters))
        return {m.meter_id: m for m in sorted(meters, key=lambda m: m.meter_id)}

    def _load_transformers(self) -> list[Transformer]:
        return [parse_transformer(record) for record in self._client.list_transformers()]
