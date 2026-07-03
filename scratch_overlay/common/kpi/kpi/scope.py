"""
scope.py — Receiver-set abstractions.

A Scope answers the question: "given a transmitter, who are its intended
receivers?" Different metrics use different scopes:

  - AllScope        : every other UE in the topology (no filtering)
  - GeographicScope : UEs whose distance from the TX falls in any Range
  - IntendedScope   : the application's intended receiver set (Topology-derived)
  - PartitionedScope: per-hop partitioning — leader→front-half, relay→back-half

The Scope is one of the args passed to a KpiContext; each KPI spec picks which
scope it wants to use via the `scope` argument.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Set, TYPE_CHECKING

if TYPE_CHECKING:
    from .topology import Topology
    from .range import Ranges


class Scope(ABC):
    """Abstract: who is in the receiver set for a given TX?"""

    name: str = "abstract"

    @abstractmethod
    def receivers(self, tx_node: int, topology: "Topology") -> Set[int]:
        """Return the set of node IDs that are 'receivers' for tx_node."""
        ...

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"


class AllScope(Scope):
    """All other UEs in the topology. Used for broadcast-style metrics."""

    name = "all"

    def receivers(self, tx_node: int, topology: "Topology") -> Set[int]:
        return topology.all_nodes - {tx_node}


class GeographicScope(Scope):
    """UEs within range of the TX. Used for PRR Type 1 (broadcast, geographic).

    The range filter is configurable via the Ranges container — no hardcoded
    320m anywhere.
    """

    name = "geographic"

    def receivers(self, tx_node: int, topology: "Topology") -> Set[int]:
        out: Set[int] = set()
        for rx in topology.all_nodes - {tx_node}:
            if topology.ranges.in_any_range(topology.distance(tx_node, rx)):
                out.add(rx)
        return out


class IntendedScope(Scope):
    """The application's intended receiver set (Topology-derived).

    For relay arms this is partitioned per-hop: leader's intended set = front-half,
    relay's intended set = back-half. For broadcast arms (none/adapt) this is
    all other UEs.
    """

    name = "intended"

    def receivers(self, tx_node: int, topology: "Topology") -> Set[int]:
        return set(topology.intended_for(tx_node))


class PartitionedScope(Scope):
    """Same as IntendedScope but explicitly partitioned for relay hops.

    This is the default for arms with relay nodes (sameL2/fixed/broadcast).
    """

    name = "partitioned"

    def receivers(self, tx_node: int, topology: "Topology") -> Set[int]:
        return set(topology.intended_for(tx_node))


# ---------------------------------------------------------------------------
# Resolution from CLI string
# ---------------------------------------------------------------------------

SCOPES = {
    "all": AllScope,
    "geographic": GeographicScope,
    "intended": IntendedScope,
    "partitioned": PartitionedScope,
}


def resolve(scope_name: str, topology: "Topology") -> Scope:
    """Instantiate a Scope by name.

    Special name "auto": choose based on topology
      - if topology has relay nodes → PartitionedScope (per-hop)
      - else                    → AllScope (broadcast)
    """
    if scope_name == "auto":
        return PartitionedScope() if topology.relay_nodes else AllScope()
    cls = SCOPES.get(scope_name)
    if cls is None:
        raise ValueError(
            f"Unknown scope '{scope_name}'. Known: {list(SCOPES.keys())} or 'auto'"
        )
    return cls()


if __name__ == "__main__":
    # Smoke test with a minimal Topology stub.
    from types import SimpleNamespace

    class FakeTopo:
        all_nodes = {0, 1, 2, 3}
        relay_nodes = set()
        intended_for = staticmethod(lambda tx: {0,1,2,3} - {tx})
        distance = staticmethod(lambda a, b: 50.0)
        ranges = SimpleNamespace(in_any_range=lambda d: d <= 200.0)

    topo = FakeTopo()
    assert AllScope().receivers(0, topo) == {1, 2, 3}
    assert GeographicScope().receivers(0, topo) == {1, 2, 3}
    assert IntendedScope().receivers(0, topo) == {1, 2, 3}

    print("scope.py smoke test passed.")