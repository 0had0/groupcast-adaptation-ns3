"""
csi_kpi_schema.py
-----------------
Canonical column definitions for csi_groupcast_eval per-run CSV files and the
aggregated Parquet sweep output.  Import this from run_csi_sweep.py and from
analysis notebooks so the schema stays in one place.
"""

# Columns written by the C++ csi_kpi module (per-rx section rows).
PER_RX_COLS = [
    "section",
    "rx_node", "port",
    "expected", "rx_count", "rx_bytes",
    "prr",
    "pir_mean_s", "pir_p90_s", "pir_p95_s", "pir_n_gaps",
    "throughput_kbps",
    "is_edge",
    "harq_ack", "harq_nack",
    "distance_m",
]

# Columns written by the C++ csi_kpi module (run-level section row).
RUN_COLS = [
    "section",
    "group_prr", "gprr",
    "mcs_mean", "fb_reporters_mean",
    "harq_ack_total", "harq_nack_total", "harq_timeout",
    "n_mcs_decisions",
]

# Sweep-level metadata injected by run_csi_sweep.py into every row.
SWEEP_META_COLS = [
    "stem",
    "scheme", "reporter", "adapter",
    "scenario",
    "n_ues", "speed_mps", "ivd_m", "mcr_m",
    "feedback_k",
    "mu", "bw_prbs",
    "seed",
]

# Full per-rx output columns in the Parquet (meta + per_rx C++ cols, minus "section").
PARQUET_PER_RX_COLS = SWEEP_META_COLS + [c for c in PER_RX_COLS if c != "section"]

# Full run-level output columns in the Parquet (meta + run C++ cols, minus "section").
PARQUET_RUN_COLS = SWEEP_META_COLS + [c for c in RUN_COLS if c != "section"]
