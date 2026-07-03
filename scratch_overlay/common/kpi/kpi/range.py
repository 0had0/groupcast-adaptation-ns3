"""
range.py — Scalable geographic range abstractions.

Encapsulates distance ranges and bin specifications. No hardcoded 320m, 150m,
or 20m values anywhere in KPI code — every range comes from CLI arguments.

CLI syntax (via `Ranges.from_cli`):

    "320"              → one range: (0, 320)
    "150,320"          → two ranges: (0, 150), (0, 320)
    "20:500/20"        → bin spec: 26 bins of width 20m, span [20, 500)
    "320,20:500/20"    → both: ranges + bins
    ""                 → empty (no ranges, no bins)

Bin spec format: "<lo>:<hi>/<width>" with lo=0 default and hi=lo+26*width default.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class RangeSpec:
    """Single distance range (a, b] in meters.

    Open at a, closed at b — matches the 3GPP TR 37.885 convention
    ("distance in the range (a, b)").
    """
    a: float
    b: float

    def contains(self, d: float) -> bool:
        return self.a < d <= self.b


@dataclass(frozen=True)
class BinSpec:
    """Distance bins of equal width over [lo, hi).

    bin_for(d) returns the bin index (0-based) for a distance d.
    Distances outside [lo, hi) clamp to bin -1 or N.
    """
    width: float
    lo: float
    hi: float

    @property
    def n_bins(self) -> int:
        """Number of bins in the spec."""
        import math
        return int(round((self.hi - self.lo) / self.width))

    def bin_for(self, d: float) -> int:
        """Return bin index for distance d. Returns -1 if d < lo, n_bins if d >= hi."""
        if d < self.lo:
            return -1
        idx = int((d - self.lo) / self.width)
        if idx >= self.n_bins:
            return self.n_bins  # sentinel: out of range
        return idx

    def bin_edges(self, idx: int) -> Tuple[float, float]:
        """Return (lo, hi) edges for bin index idx."""
        return (self.lo + idx * self.width, self.lo + (idx + 1) * self.width)

    def iter_bins(self) -> List[Tuple[int, float, float]]:
        """Iterate over all bins as (idx, lo, hi) tuples."""
        return [(i, *self.bin_edges(i)) for i in range(self.n_bins)]


@dataclass(frozen=True)
class Ranges:
    """Container for one or more RangeSpecs plus optional BinSpec.

    All distances in meters.
    """
    ranges: Tuple[RangeSpec, ...] = ()
    bins: Optional[BinSpec] = None

    @classmethod
    def empty(cls) -> "Ranges":
        return cls(ranges=(), bins=None)

    @classmethod
    def from_cli(cls, s: str, bin_spec: Optional[str] = None) -> "Ranges":
        """Parse CLI string into Ranges.

        Args:
            s: comma-separated range upper bounds, e.g. "320" or "150,320"
               → ranges (0, 320), (0, 150)
            bin_spec: optional bin spec like "20:500/20"

        Returns:
            Ranges instance
        """
        ranges: List[RangeSpec] = []
        if s and s.strip():
            for token in s.split(","):
                token = token.strip()
                if not token:
                    continue
                upper = float(token)
                if upper <= 0:
                    raise ValueError(f"Range upper bound must be positive, got {upper}")
                ranges.append(RangeSpec(a=0.0, b=upper))
        bins = BinSpec.from_cli(bin_spec) if bin_spec else None
        return cls(ranges=tuple(ranges), bins=bins)

    def in_any_range(self, d: float) -> bool:
        """True if d falls within ANY of the range specs."""
        if not self.ranges:
            return True  # no range filter = all distances
        return any(r.contains(d) for r in self.ranges)

    def in_range_idx(self, d: float) -> int:
        """Index of the range spec containing d, or -1 if none."""
        for i, r in enumerate(self.ranges):
            if r.contains(d):
                return i
        return -1

    def bin_for(self, d: float) -> int:
        """Bin index for d (delegates to self.bins). Returns -1 if no bins."""
        if self.bins is None:
            return -1
        return self.bins.bin_for(d)

    def iter_bins(self) -> List[Tuple[int, float, float]]:
        """Iterate over bins if defined, else empty."""
        if self.bins is None:
            return []
        return self.bins.iter_bins()


# BinSpec.from_cli is defined as a method below to keep the class self-contained.


def _parse_bin_spec(s: str) -> BinSpec:
    """Parse "<lo>:<hi>/<width>" → BinSpec. See module docstring."""
    if "/" not in s:
        raise ValueError(
            f"Invalid bin spec '{s}': expected format '<lo>:<hi>/<width>'"
        )
    range_part, width_part = s.split("/", 1)
    width = float(width_part)
    if width <= 0:
        raise ValueError(f"Bin width must be positive, got {width}")
    if ":" in range_part:
        lo_s, hi_s = range_part.split(":", 1)
        lo = float(lo_s) if lo_s else 0.0
        hi = float(hi_s)
    else:
        # "<hi>/<width>" — lo defaults to 0
        hi = float(range_part)
        lo = 0.0
    if hi <= lo:
        raise ValueError(f"Bin hi ({hi}) must be > lo ({lo})")
    return BinSpec(width=width, lo=lo, hi=hi)


# Attach as a classmethod
BinSpec.from_cli = classmethod(lambda cls, s: _parse_bin_spec(s) if s else None)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Range spec
    r = Ranges.from_cli("320")
    assert r.in_any_range(100.0)
    assert r.in_any_range(320.0)
    assert not r.in_any_range(320.001)

    # List of ranges
    r = Ranges.from_cli("150,320")
    assert r.in_any_range(100.0)   # in (0, 150]
    assert r.in_any_range(200.0)   # in (0, 320]
    assert not r.in_any_range(400.0)

    # Bin spec
    b = BinSpec.from_cli("20:500/20")
    assert b.n_bins == 24  # (500-20)/20 = 24 bins: [20,40), [40,60), ..., [480,500)
    assert b.bin_for(30.0) == 0  # [20, 40)
    assert b.bin_for(232.0) == 10  # [220, 240)
    assert b.bin_for(15.0) == -1  # below lo
    assert b.bin_for(600.0) == 24  # above hi (sentinel)

    # Combined
    r = Ranges.from_cli("320", bin_spec="20:500/20")
    assert r.in_any_range(100.0)
    assert r.bins is not None
    print("range.py smoke test passed.")