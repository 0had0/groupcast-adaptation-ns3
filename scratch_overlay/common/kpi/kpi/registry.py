"""
registry.py — KPI spec registry: the plugin architecture.

A KPI spec is a small class that knows:
  - its id, title, spec reference (e.g. 3GPP TR 37.885 §5.2.1)
  - what columns its output rows have
  - how to compute its values from a KpiContext

Adding a new KPI = create one file in kpis/ that uses @register.
No edits to core code required.

KpiContext holds the shared per-DB state (DataCache, Topology, Outcomes, etc.)
so each KPI just reads what it needs.
"""
from __future__ import annotations

import argparse
import csv
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, List, Optional, Tuple

from .data import DataCache
from .outcomes import Outcomes
from .range import Ranges
from .scope import Scope, resolve
from .topology import Topology


# ---------------------------------------------------------------------------
# Output column + result types
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class KpiOutputColumn:
    name: str
    unit: str = ""
    description: str = ""

    def __str__(self) -> str:
        return f"{self.name} ({self.unit})" if self.unit else self.name


@dataclass
class KpiResult:
    """One output file from a KPI computation.

    `rows` is a list of dicts keyed by column name (from output_columns).
    `filename` is the relative filename (under out_dir) WITHOUT the tag prefix.
    """
    rows: List[Dict[str, Any]]
    filename: str

    def write(self, out_dir: Path, tag: str) -> Path:
        """Write to out_dir/<tag>_<self.filename>.csv. Returns the path."""
        path = out_dir / f"{tag}_{self.filename}"
        if not self.rows:
            # Still write a header so downstream consumers see the schema.
            return path
        cols = list(self.rows[0].keys())
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in self.rows:
                w.writerow(r)
        return path


# Convenience aliases for clarity at call sites.
class ScalarResult(KpiResult):
    """One row per (tx, rx, flow) aggregate or similar flat aggregation."""

    def __init__(self, rows: List[Dict[str, Any]], filename: str):
        super().__init__(rows=rows, filename=filename)


class BinResult(KpiResult):
    """Rows aggregated by distance bin (20m wide per spec)."""

    def __init__(self, rows: List[Dict[str, Any]], filename: str):
        super().__init__(rows=rows, filename=filename)


class CdfResult(KpiResult):
    """One row per per-packet or per-link sample (CDF source)."""

    def __init__(self, rows: List[Dict[str, Any]], filename: str):
        super().__init__(rows=rows, filename=filename)


class TimeSeriesResult(KpiResult):
    """One row per time window."""

    def __init__(self, rows: List[Dict[str, Any]], filename: str):
        super().__init__(rows=rows, filename=filename)


# ---------------------------------------------------------------------------
# KpiContext — what every KPI sees
# ---------------------------------------------------------------------------
@dataclass
class KpiContext:
    db_path: Path
    out_dir: Path
    seed: int
    run: int
    t_start: float
    t_end: float
    topology: Topology
    outcomes: Outcomes
    cache: DataCache
    ranges: Ranges
    scope_mode: str
    args: argparse.Namespace
    scope: Scope = field(init=False)

    def __post_init__(self):
        self.scope = resolve(self.scope_mode, self.topology)


# ---------------------------------------------------------------------------
# KpiBase + KpiSpec + @register
# ---------------------------------------------------------------------------
class KpiBase(ABC):
    """Base class for KPI implementations.

    Subclass and implement `compute(ctx) -> List[KpiResult]`. Then use
    `@register(...)` to declare metadata. The class is auto-instantiated
    by the registry.
    """

    spec: "KpiSpec"

    @abstractmethod
    def compute(self, ctx: KpiContext) -> List[KpiResult]:
        ...


@dataclass(frozen=True)
class KpiSpec:
    id: str
    title: str
    spec_ref: str
    output_kind: str                       # "scalar" | "bin" | "cdf" | "timeseries"
    output_columns: Tuple[KpiOutputColumn, ...]
    requires: Tuple[str, ...] = ()
    cli_args: Tuple[str, ...] = ()
    klass: type = KpiBase                   # subclass of KpiBase

    def run(self, ctx: KpiContext) -> List[KpiResult]:
        instance = self.klass()
        return instance.compute(ctx)


def register(id: str, *, title: str, spec_ref: str, output_kind: str,
             output_columns: Tuple[KpiOutputColumn, ...],
             requires: Tuple[str, ...] = (),
             cli_args: Tuple[str, ...] = ()):
    """Decorator to register a KpiBase subclass in the global registry."""
    def wrap(klass):
        spec = KpiSpec(
            id=id, title=title, spec_ref=spec_ref,
            output_kind=output_kind, output_columns=output_columns,
            requires=requires, cli_args=cli_args, klass=klass,
        )
        klass.spec = spec
        KpiRegistry.register(spec)
        return klass
    return wrap


class KpiRegistry:
    """Global registry of KpiSpec objects, populated via @register decorator."""
    _specs: ClassVar[Dict[str, KpiSpec]] = {}

    @classmethod
    def register(cls, spec: KpiSpec) -> KpiSpec:
        if spec.id in cls._specs:
            raise ValueError(f"Duplicate KPI id: {spec.id}")
        cls._specs[spec.id] = spec
        return spec

    @classmethod
    def all(cls) -> Dict[str, KpiSpec]:
        return dict(cls._specs)

    @classmethod
    def get(cls, kpi_id: str) -> Optional[KpiSpec]:
        return cls._specs.get(kpi_id)

    @classmethod
    def selected(cls, names: List[str]) -> List[KpiSpec]:
        """Resolve KPI id list, ignoring missing ones but reporting them."""
        out: List[KpiSpec] = []
        missing: List[str] = []
        for name in names:
            spec = cls._specs.get(name)
            if spec is None:
                missing.append(name)
            else:
                out.append(spec)
        if missing:
            import sys
            print(f"[WARN] Unknown KPI ids ignored: {missing}", file=sys.stderr)
        return out

    @classmethod
    def clear(cls) -> None:
        """Reset the registry (for tests)."""
        cls._specs.clear()


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    @register("test_kpi", title="Test KPI", spec_ref="local",
              output_kind="scalar",
              output_columns=(KpiOutputColumn("foo", "", "demo"),))
    class TestKpi(KpiBase):
        def compute(self, ctx):
            return [ScalarResult([{"foo": 42}], filename="test_kpi.csv")]

    print("Registered KPIs:", list(KpiRegistry.all().keys()))
    spec = KpiRegistry.get("test_kpi")
    print("Found:", spec.id, spec.title)
    KpiRegistry.clear()
    print("registry.py smoke test passed.")