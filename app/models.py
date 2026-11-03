"""Public API models. These are our contract; they deliberately do not mirror portal payloads."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

NetworkLevel = Literal["zone", "circle", "division", "subdivision", "substation", "feeder", "dt"]


class NetworkNode(BaseModel):
    level: NetworkLevel
    code: str | None = Field(description="Node code, e.g. 'F-001'. Null when the portal has none.")
    name: str | None = Field(description="Display name. Null when the portal has none.")


class Location(BaseModel):
    latitude: float
    longitude: float


class Meter(BaseModel):
    meter_id: str = Field(examples=["J100001"])
    serial_number: str = Field(examples=["GE84132"])
    make: str = Field(examples=["L&T"])
    phase_type: str = Field(description="Observed values: single, three.", examples=["single"])
    install_status: str = Field(
        description="Observed values: installed, faulty, decommissioned.", examples=["installed"]
    )
    install_type: str = Field(
        description="Observed values: ct_operated, whole_current.", examples=["ct_operated"]
    )
    dt_code: str | None = Field(description="Distribution transformer the meter is fed from.")
    location: Location | None
    network: list[NetworkNode] = Field(
        description="Position in the network, ordered from zone down to distribution transformer."
    )


class MeterPage(BaseModel):
    items: list[Meter]
    total: int
    page: int
    page_size: int


class Reading(BaseModel):
    timestamp: datetime = Field(
        description="Local time as reported by the portal (no timezone given; assumed Asia/Kolkata)."
    )
    kwh_register: float = Field(description="Cumulative active-energy register (kWh).")
    kvah_register: float | None = Field(description="Cumulative apparent-energy register (kVAh).")
    voltage_r: float | None = Field(description="R-phase voltage (V).")
    kwh_consumed: float | None = Field(
        description="kWh consumed since the previous reading. Null for the first reading."
    )


class Consumption(BaseModel):
    meter_id: str
    interval_minutes: int | None = Field(
        description="Typical spacing between readings: 30 or 1440 have been observed. "
        "Null if fewer than two readings."
    )
    period_start: datetime | None
    period_end: datetime | None
    total_kwh_consumed: float | None = Field(
        description="kWh consumed between the first and last reading."
    )
    skipped_rows: int = Field(
        description="Portal rows dropped because they were duplicated or had no usable kWh value."
    )
    readings: list[Reading]


class Transformer(BaseModel):
    code: str = Field(examples=["DT-001"])
    name: str = Field(examples=["Malviya Nagar DT 1"])
    feeder_code: str | None = Field(examples=["F-001"])
    capacity_kva: float | None = Field(examples=[100])


class NetworkTreeNode(BaseModel):
    level: NetworkLevel
    code: str | None
    name: str | None
    meter_count: int = Field(description="Meters at or below this node.")
    children: list["NetworkTreeNode"] = Field(
        description="Child nodes. Empty at DT level; use GET /meters?dt_code=... for its meters."
    )


class CodeUnderManyParents(BaseModel):
    level: NetworkLevel
    code: str
    parent_codes: list[str | None] = Field(description="Distinct parent codes seen for this code.")


class CodeWithManyNames(BaseModel):
    level: NetworkLevel
    code: str
    names: list[str]


class MissingValues(BaseModel):
    level: NetworkLevel
    meters_missing_code: int
    meters_missing_name: int


class NetworkDataQuality(BaseModel):
    codes_under_many_parents: list[CodeUnderManyParents] = Field(
        description="Codes that appear under more than one parent. The portal does not say whether "
        "codes are unique only within their parent or the links are inconsistent."
    )
    codes_with_many_names: list[CodeWithManyNames]
    missing_values: list[MissingValues] = Field(description="Only levels with at least one gap are listed.")


class Network(BaseModel):
    zones: list[NetworkTreeNode] = Field(
        description="Hierarchy built from every meter's own path. A node is identified by its full "
        "path from the zone, so a code reused under two parents appears as two nodes."
    )
    data_quality: NetworkDataQuality


class ErrorResponse(BaseModel):
    error: str = Field(examples=["meter_not_found"])
    message: str
