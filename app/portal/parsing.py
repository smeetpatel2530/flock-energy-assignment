"""Map raw portal payloads into public models, absorbing the portal's quirks.

Quirks handled here (see PROTOCOL.md): numbers sent as strings, DD/MM/YYYY timestamps,
blank hierarchy codes/names, cumulative energy registers, duplicate or empty energy rows.
"""

from collections import Counter
from datetime import datetime
from typing import Any

from app.models import Consumption, Location, Meter, NetworkNode, Reading, Transformer
from app.portal.errors import PortalResponseError

NETWORK_LEVELS = ("zone", "circle", "division", "subdivision", "substation", "feeder", "dt")
PORTAL_TIMESTAMP_FORMAT = "%d/%m/%Y %H:%M"


def parse_meter(record: dict[str, Any]) -> Meter:
    """Map one record from the portal's bulk export."""
    meter_id = _text(record.get("meterId"))
    if meter_id is None:
        raise PortalResponseError("Export record without a meterId")

    hierarchy = record.get("hierarchy") or {}
    network = [
        NetworkNode(
            level=level,
            code=_text((hierarchy.get(level) or {}).get("code")),
            name=_text((hierarchy.get(level) or {}).get("name")),
        )
        for level in NETWORK_LEVELS
    ]

    geo = record.get("geo") or {}
    latitude, longitude = _number(geo.get("lat")), _number(geo.get("lng"))
    location = Location(latitude=latitude, longitude=longitude) if None not in (latitude, longitude) else None

    return Meter(
        meter_id=meter_id,
        serial_number=_text(record.get("serialNo")) or "",
        make=_text(record.get("make")) or "",
        phase_type=_slug(record.get("phaseType")),
        install_status=_slug(record.get("installStatus")),
        install_type=_slug(record.get("installType")),
        dt_code=_text(record.get("dtCode")),
        location=location,
        network=network,
    )


def parse_consumption(meter_id: str, rows: list[dict[str, Any]]) -> Consumption:
    """Turn the portal's cumulative register rows into ordered readings with per-interval deltas."""
    by_timestamp: dict[datetime, dict[str, Any]] = {}
    skipped = 0
    for row in rows:
        timestamp = _timestamp(row.get("timestamp"))
        kwh = _number(row.get("kwh"))
        # Rows without a timestamp or kWh value are unusable; for duplicate timestamps keep
        # the first usable row (observed duplicates are trailing rows with blank values).
        if timestamp is None or kwh is None or timestamp in by_timestamp:
            skipped += 1
            continue
        by_timestamp[timestamp] = {"kwh": kwh, "kvah": _number(row.get("kvah")), "volt": _number(row.get("voltR"))}

    readings: list[Reading] = []
    previous_kwh: float | None = None
    for timestamp in sorted(by_timestamp):
        values = by_timestamp[timestamp]
        consumed = None if previous_kwh is None else round(values["kwh"] - previous_kwh, 3)
        readings.append(
            Reading(
                timestamp=timestamp,
                kwh_register=values["kwh"],
                kvah_register=values["kvah"],
                voltage_r=values["volt"],
                kwh_consumed=consumed,
            )
        )
        previous_kwh = values["kwh"]

    return Consumption(
        meter_id=meter_id,
        interval_minutes=_typical_interval_minutes(readings),
        period_start=readings[0].timestamp if readings else None,
        period_end=readings[-1].timestamp if readings else None,
        total_kwh_consumed=(
            round(readings[-1].kwh_register - readings[0].kwh_register, 3) if len(readings) > 1 else None
        ),
        skipped_rows=skipped,
        readings=readings,
    )


def parse_transformer(record: dict[str, Any]) -> Transformer:
    code = _text(record.get("code"))
    if code is None:
        raise PortalResponseError("Transformer record without a code")
    return Transformer(
        code=code,
        name=_text(record.get("name")) or "",
        feeder_code=_text(record.get("feederCode")),
        capacity_kva=_number(record.get("capacityKva")),
    )


def _typical_interval_minutes(readings: list[Reading]) -> int | None:
    gaps = [
        int((later.timestamp - earlier.timestamp).total_seconds() // 60)
        for earlier, later in zip(readings, readings[1:])
    ]
    return Counter(gaps).most_common(1)[0][0] if gaps else None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _timestamp(value: Any) -> datetime | None:
    try:
        return datetime.strptime(str(value), PORTAL_TIMESTAMP_FORMAT)
    except ValueError:
        return None


def _slug(value: Any) -> str:
    """'CT Operated' -> 'ct_operated', 'Installed' -> 'installed'."""
    return "_".join((_text(value) or "unknown").lower().split())
