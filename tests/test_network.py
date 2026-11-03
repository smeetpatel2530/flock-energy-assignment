import copy

from app.network import build_network
from app.portal.parsing import parse_meter
from tests.fixtures import export_record


def meter(meter_id, **levels):
    """A meter whose hierarchy is the fixture default with some levels overridden."""
    record = export_record(meter_id)
    record["hierarchy"] = copy.deepcopy(record["hierarchy"]) | levels
    return parse_meter(record)


def descend(node, *codes):
    for code in codes:
        node = next(c for c in node.children if c.code == code)
    return node


def test_tree_counts_meters_at_every_level():
    network = build_network([meter("J1"), meter("J2"), meter("J3", dt={"code": "DT-099", "name": "Other DT"})])

    zone = network.zones[0]
    assert (zone.code, zone.meter_count) == ("Z-02", 3)
    feeder = descend(zone, "C-02", "D-02", "SD-02", "SS-02", "F-002")
    assert [(dt.code, dt.meter_count) for dt in feeder.children] == [("DT-002", 2), ("DT-099", 1)]
    assert feeder.children[0].children == []


def test_code_reused_under_two_parents_becomes_two_nodes_and_is_reported():
    network = build_network([
        meter("J1"),
        meter("J2", circle={"code": "C-04", "name": "Circle 4"}),  # same D-02, different circle
    ])

    zone = network.zones[0]
    assert descend(zone, "C-02", "D-02").meter_count == 1
    assert descend(zone, "C-04", "D-02").meter_count == 1
    conflicts = {(c.level, c.code): c.parent_codes for c in network.data_quality.codes_under_many_parents}
    assert conflicts[("division", "D-02")] == ["C-02", "C-04"]


def test_missing_name_does_not_split_a_node_and_is_counted():
    network = build_network([meter("J1"), meter("J2", circle={"code": "C-02", "name": ""})])

    circles = network.zones[0].children
    assert [(c.code, c.name, c.meter_count) for c in circles] == [("C-02", "Circle 2", 2)]
    missing = {m.level: m for m in network.data_quality.missing_values}
    assert missing["circle"].meters_missing_name == 1
    assert missing["circle"].meters_missing_code == 0


def test_missing_code_keeps_the_named_node():
    network = build_network([meter("J1", circle={"code": "", "name": "Circle 6"})])

    circle = network.zones[0].children[0]
    assert (circle.code, circle.name) == (None, "Circle 6")
    assert network.data_quality.missing_values[0].meters_missing_code == 1


def test_one_code_with_two_names_is_reported():
    network = build_network([meter("J1"), meter("J2", dt={"code": "DT-002", "name": "Old Mansarovar Xfmr"})])

    assert [(c.code, c.names) for c in network.data_quality.codes_with_many_names] == [
        ("DT-002", ["Mansarovar DT 2", "Old Mansarovar Xfmr"])
    ]


def test_clean_data_has_no_quality_findings():
    quality = build_network([meter("J1"), meter("J2")]).data_quality

    assert quality.codes_under_many_parents == []
    assert quality.codes_with_many_names == []
    assert quality.missing_values == []
