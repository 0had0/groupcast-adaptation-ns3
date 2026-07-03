#!/usr/bin/env python3
"""
build analysis_new.ipynb for relay_storm_eval that consumes the new
kpi/ pipeline output (kpi_results/sweep_aggregate.csv, kpi_results/storm_indicator.csv).
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "analysis.ipynb"           # legacy notebook
DST = HERE / "analysis_new.ipynb"       # new notebook

# Column-name remap for the new pipeline's {kpi_id}_{col}_agg_{stat} convention.
# Legacy (agg_fixed_sweep) used bare names; new pipeline prefixes each KPI id.
COL_REMAP = {
    # PRR (legacy type1 → new type2; "prr_worst" → min over RX of prr_mean)
    "prr_mean_agg_mean":   "prr_type2_prr_mean_agg_mean",
    "prr_mean_agg_min":    "prr_type2_prr_mean_agg_min",
    "prr_mean_agg_max":    "prr_type2_prr_mean_agg_max",
    "prr_mean_agg_p5":     "prr_type2_prr_mean_agg_p5",
    "prr_mean_agg_p95":    "prr_type2_prr_mean_agg_p95",
    "prr_worst_agg_mean":  "prr_type2_prr_mean_agg_min",   # worst-RX ≡ min over RX
    "prr_worst_agg_min":   "prr_type2_prr_mean_agg_min",
    "prr_worst_agg_max":   "prr_type2_prr_mean_agg_min",
    "prr_worst_agg_p5":    "prr_type2_prr_mean_agg_p5",
    "prr_worst_agg_p95":   "prr_type2_prr_mean_agg_p95",
    # AoI
    "aoi_mean_ms_agg_mean": "aoi_aoi_mean_ms_agg_mean",
    "aoi_mean_ms_agg_p5":  "aoi_aoi_mean_ms_agg_p5",
    "aoi_mean_ms_agg_p95": "aoi_aoi_mean_ms_agg_p95",
    # Storm (legacy had `storm_ratio` and `hop1_retx` flat; new prefixes both with `storm_`)
    "storm_ratio_agg_mean": "storm_storm_ratio_agg_mean",
    "hop1_retx_agg_mean":   "storm_hop1_retx_agg_mean",
    # HARQ
    "harq_nack_rate_agg_mean": "harq_harq_nack_rate_agg_mean",
    # Channel occupancy (legacy 'ch_occupancy' → new 'ch_occupancy_ch_occupancy')
    "ch_occupancy_agg_mean": "ch_occupancy_ch_occupancy_agg_mean",
    # PSR (legacy `psr_agg_*` → new `psr_psr_agg_*`)
    "psr_agg_mean": "psr_psr_agg_mean",
    "psr_agg_min":  "psr_psr_agg_min",
    "psr_agg_max":  "psr_psr_agg_max",
    "psr_agg_p5":   "psr_psr_agg_p5",
    "psr_agg_p95":  "psr_psr_agg_p95",
    # relay forwarding / leader TX counts (only in sweep_aggregate.csv path; we
    # don't have these in the new pipeline — drop them via filter below).
    "relay_decoded_agg_mean":   None,
    "relay_forwarded_agg_mean": None,
    "leader_tx_total_agg_mean": None,
}

# storm_indicator.csv column remap
STORM_COL_REMAP = {
    "storm_ratio_hop1":   "storm_ratio",
    "baseline_hop1_retx": "baseline_hop1_retx_mean",
    "relay_hop1_retx":    "relay_hop1_retx_mean",
}


def remap_cols(src: str, colmap: dict) -> str:
    """Replace legacy column names with new pipeline names. Drops unknown ones."""
    out = src
    # Sort by length desc so longer matches come first (avoid partial replacement)
    for old in sorted(colmap.keys(), key=len, reverse=True):
        new = colmap[old]
        if new is None:
            # Drop references.  Conservative: replace standalone identifiers only.
            pattern = re.compile(r"\b" + re.escape(old) + r"\b")
            out = pattern.sub("None  # dropped", out)
        else:
            pattern = re.compile(r"\b" + re.escape(old) + r"\b")
            out = pattern.sub(new, out)
    return out


def remap_storm_indicator(src: str) -> str:
    return remap_cols(src, STORM_COL_REMAP)


def remap_load_cell(src: str) -> str:
    """Rewrite the data-loading cell so the results dir defaults to kpi_results/."""
    # Find the candidate-paths list and replace it with kpi_results-first.
    # We do a structural rewrite rather than a regex so the rest stays clean.
    new = src
    # Replace the CANDIDATES list — put kpi_results first.
    new = re.sub(
        r"CANDIDATES\s*=\s*\[.*?\]",
        "CANDIDATES = [HERE / '../../kpi_results',\n"
        "              HERE / 'kpi_results',\n"
        "              Path('/Users/hadih/Documents/groupcast-adaptation-ns3/kpi_results')]",
        new, flags=re.DOTALL,
    )
    # The legacy file is `sweep_aggregate.csv` — but the new pipeline also writes
    # the same filename (the postprocessor kept the convention), so the rest of
    # the cell works as-is.
    return new


with open(SRC) as f:
    nb = json.load(f)

# Walk through the cells, rewriting code-cell sources as needed.
new_cells = []
for c in nb["cells"]:
    if c["cell_type"] != "code":
        new_cells.append(c)
        continue
    src = "".join(c["source"])

    # The data-loading cell (cell 2) is identified by its distinctive
    # `agg = pd.read_csv(...)` line; we rewrite its path lookup and remap cols.
    if "agg   = pd.read_csv(RESULTS" in src:
        src = remap_load_cell(src)

    # Remap legacy column references throughout.
    src = remap_cols(src, COL_REMAP)
    src = remap_storm_indicator(src)

    c["source"] = src.splitlines(keepends=True)
    new_cells.append(c)

# Update the title cell.
if new_cells and new_cells[0]["cell_type"] == "markdown":
    src0 = "".join(new_cells[0]["source"])
    src0 = src0.replace(
        "Relay ReTX Storm Analysis — NR-V2X Sidelink Platoon",
        "Relay ReTX Storm Analysis — NR-V2X Sidelink Platoon (new kpi/ pipeline)",
    )
    new_cells[0]["source"] = src0.splitlines(keepends=True)

nb["cells"] = new_cells

with open(DST, "w") as f:
    json.dump(nb, f, indent=1)

print(f"Wrote {DST} ({len(new_cells)} cells)")
