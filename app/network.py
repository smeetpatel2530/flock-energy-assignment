"""Build the network hierarchy from meters' individual paths, and report its inconsistencies.

The portal has no hierarchy endpoint; every meter just carries its own zone -> DT path, and
those paths disagree with each other (PROTOCOL.md section 9). Rather than guess which parent
link is "right", a node is identified by its whole path from the zone. That is correct
whether codes are unique only within their parent or the data is simply inconsistent; the
inconsistencies are reported separately so consumers can decide what to trust.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from app.models import (
    CodeUnderManyParents,
    CodeWithManyNames,
    Meter,
    MissingValues,
    Network,
    NetworkDataQuality,
    NetworkNode,
    NetworkTreeNode,
)
from app.portal.parsing import NETWORK_LEVELS


@dataclass
class _Branch:
    node: NetworkNode
    meter_count: int = 0
    children: dict[tuple[str | None, str | None], "_Branch"] = field(default_factory=dict)

    def to_model(self) -> NetworkTreeNode:
        return NetworkTreeNode(
            level=self.node.level,
            code=self.node.code,
            name=self.node.name,
            meter_count=self.meter_count,
            children=[child.to_model() for child in _sorted(self.children.values())],
        )


def build_network(meters: list[Meter]) -> Network:
    zones: dict[tuple[str | None, str | None], _Branch] = {}
    for meter in meters:
        level_children = zones
        for node in meter.network:
            # Same code under the same parent path = same node, even if some records lack its
            # name. Nodes with no code fall back to their name.
            key = (node.code, None) if node.code else (None, node.name)
            branch = level_children.setdefault(key, _Branch(node))
            if branch.node.name is None and node.name is not None:
                branch.node = node
            branch.meter_count += 1
            level_children = branch.children

    return Network(
        zones=[zone.to_model() for zone in _sorted(zones.values())],
        data_quality=_data_quality(meters),
    )


def _data_quality(meters: list[Meter]) -> NetworkDataQuality:
    parents: dict[tuple[str, str], set[str | None]] = defaultdict(set)
    names: dict[tuple[str, str], set[str]] = defaultdict(set)
    missing_code: dict[str, int] = defaultdict(int)
    missing_name: dict[str, int] = defaultdict(int)

    for meter in meters:
        previous: NetworkNode | None = None
        for node in meter.network:
            if node.code is None:
                missing_code[node.level] += 1
            if node.name is None:
                missing_name[node.level] += 1
            if node.code is not None:
                if node.name is not None:
                    names[(node.level, node.code)].add(node.name)
                if previous is not None:
                    parents[(node.level, node.code)].add(previous.code)
            previous = node

    order = {level: i for i, level in enumerate(NETWORK_LEVELS)}

    def by_level_then_code(key: tuple[str, str]) -> tuple[int, str]:
        return order[key[0]], key[1]

    return NetworkDataQuality(
        codes_under_many_parents=[
            CodeUnderManyParents(level=level, code=code, parent_codes=sorted(found, key=lambda c: c or ""))
            for (level, code), found in sorted(parents.items(), key=lambda kv: by_level_then_code(kv[0]))
            if len(found) > 1
        ],
        codes_with_many_names=[
            CodeWithManyNames(level=level, code=code, names=sorted(found))
            for (level, code), found in sorted(names.items(), key=lambda kv: by_level_then_code(kv[0]))
            if len(found) > 1
        ],
        missing_values=[
            MissingValues(level=level, meters_missing_code=missing_code[level], meters_missing_name=missing_name[level])
            for level in NETWORK_LEVELS
            if missing_code[level] or missing_name[level]
        ],
    )


def _sorted(branches) -> list[_Branch]:
    return sorted(branches, key=lambda b: (b.node.code or "", b.node.name or ""))
