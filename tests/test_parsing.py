from datetime import datetime

import pytest

from app.portal.errors import PortalResponseError
from app.portal.parsing import parse_consumption, parse_meter, parse_transformer
from tests.fixtures import DAILY_ROWS_WITH_DUPLICATE, HALF_HOURLY_ROWS, export_record


def test_parse_meter_maps_export_record_to_public_model():
    meter = parse_meter(export_record())

    assert meter.meter_id == "J100001"
    assert meter.serial_number == "GE84132"
    assert meter.install_status == "installed"
    assert meter.install_type == "ct_operated"
    assert meter.location.latitude == pytest.approx(26.8221365)
    assert [n.level for n in meter.network] == [
        "zone", "circle", "division", "subdivision", "substation", "feeder", "dt",
    ]
    assert meter.network[-1].code == "DT-002"
    assert meter.network[-1].name == "Mansarovar DT 2"


def test_parse_meter_turns_blank_hierarchy_values_into_null():
    record = export_record()
    record["hierarchy"]["circle"] = {"code": "", "name": "Circle 6"}
    record["hierarchy"]["feeder"] = {"code": "F-003", "name": ""}

    network = {n.level: n for n in parse_meter(record).network}

    assert network["circle"].code is None and network["circle"].name == "Circle 6"
    assert network["feeder"].code == "F-003" and network["feeder"].name is None


def test_parse_meter_tolerates_missing_geo():
    assert parse_meter(export_record(geo=None)).location is None


def test_parse_meter_rejects_record_without_id():
    with pytest.raises(PortalResponseError):
        parse_meter(export_record(meterId=""))


def test_consumption_sorts_readings_and_computes_interval_deltas():
    result = parse_consumption("J100001", HALF_HOURLY_ROWS)

    assert [r.timestamp for r in result.readings] == [
        datetime(2026, 6, 23, 23, 30), datetime(2026, 6, 24, 0, 0), datetime(2026, 6, 24, 0, 30),
    ]
    assert [r.kwh_consumed for r in result.readings] == [None, 0.42, 0.41]
    assert result.readings[0].kwh_register == 42594.05
    assert result.readings[0].voltage_r == 231.0
    assert result.interval_minutes == 30
    assert result.total_kwh_consumed == 0.83
    assert result.skipped_rows == 0


def test_consumption_drops_duplicate_blank_rows():
    result = parse_consumption("J100195", DAILY_ROWS_WITH_DUPLICATE)

    assert len(result.readings) == 2
    assert result.skipped_rows == 1
    assert result.readings[-1].kwh_register == 24530.81
    assert result.interval_minutes == 1440


def test_consumption_with_no_rows_is_empty_not_an_error():
    result = parse_consumption("J100001", [])

    assert result.readings == []
    assert result.interval_minutes is None
    assert result.total_kwh_consumed is None
    assert result.period_start is None


def test_parse_transformer():
    dt = parse_transformer({"code": "DT-001", "name": "Malviya Nagar DT 1", "feederCode": "F-001", "capacityKva": 100})

    assert dt.capacity_kva == 100.0
    assert dt.feeder_code == "F-001"
