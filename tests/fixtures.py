"""Portal payloads shaped exactly like those observed on the live portal (see PROTOCOL.md)."""


def export_record(meter_id="J100001", **overrides):
    record = {
        "meterId": meter_id,
        "serialNo": "GE84132",
        "make": "L&T",
        "phaseType": "single",
        "installStatus": "Installed",
        "installType": "CT Operated",
        "build": "legacy",
        "dtCode": "DT-002",
        "geo": {"lat": 26.822136543835608, "lng": 75.90718190602279},
        "hierarchy": {
            "zone": {"code": "Z-02", "name": "Jaipur Zone 2"},
            "circle": {"code": "C-02", "name": "Circle 2"},
            "division": {"code": "D-02", "name": "Division 2"},
            "subdivision": {"code": "SD-02", "name": "Subdivision 2"},
            "substation": {"code": "SS-02", "name": "Substation 2"},
            "feeder": {"code": "F-002", "name": "Feeder 2"},
            "dt": {"code": "DT-002", "name": "Mansarovar DT 2"},
        },
    }
    record.update(overrides)
    return record


HALF_HOURLY_ROWS = [
    {"timestamp": "24/06/2026 00:00", "kwh": "42594.47", "kvah": "46002.02", "voltR": "230"},
    {"timestamp": "23/06/2026 23:30", "kwh": "42594.05", "kvah": "46001.58", "voltR": "231"},
    {"timestamp": "24/06/2026 00:30", "kwh": "42594.88", "kvah": "46002.47", "voltR": "225"},
]

# Observed on J100195: a trailing duplicate timestamp with blank kWh and voltage.
DAILY_ROWS_WITH_DUPLICATE = [
    {"timestamp": "29/06/2026 00:00", "kwh": "24519.45", "kvah": "26481.01", "voltR": "228"},
    {"timestamp": "30/06/2026 00:00", "kwh": "24530.81", "kvah": "26493.27", "voltR": "240"},
    {"timestamp": "30/06/2026 00:00", "kwh": "", "kvah": "26493.27", "voltR": ""},
]
