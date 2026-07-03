#!/usr/bin/env python3
"""
tests/test_join_per_run.py
==========================
Tests for join_per_run.py — registry-based combine of per-KPI CSVs with
the per-cell ``*_summary.csv``.

Run with:
    cd scratch_overlay/common/kpi
    python3 -m unittest tests.test_join_per_run -v

Layout produced by ``kpi_postprocess.py`` (registry-based):
    <run_dir>/
      <runId>_summary.csv
      <runId>_prr_type2.csv
      <runId>_aoi.csv
      <runId>_throughput.csv
      <runId>_harq.csv
      <runId>_ch_occupancy.csv
"""
import csv
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from join_per_run import combine_run, join_all, split_seed_tag, strip_seed_tag


def _write_csv(path: Path, rows: list, fieldnames: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _make_summary(run_dir: Path, run_id: str):
    _write_csv(
        run_dir / f"{run_id}_summary.csv",
        [{
            "runId": run_id, "seed": 1, "numUEs": 5,
            "speedKmh": 100, "ivd": 29, "mcrMeters": 115.5,
            "harqOption": 1, "traffic": "onoff",
            "mu": 1, "numSubchannels": 12, "bwPRBs": 20,
            "mcs": 9, "psfchPeriod": 4, "simTime": 7.0,
        }],
        ["runId", "seed", "numUEs", "speedKmh", "ivd", "mcrMeters",
         "harqOption", "traffic", "mu", "numSubchannels", "bwPRBs",
         "mcs", "psfchPeriod", "simTime"],
    )


def _write_run(
    root: Path,
    run_id: str,
    *,
    n_link_rows: int = 3,
) -> Path:
    """Write a synthetic per-run dir under *root* with per-KPI CSVs."""
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _make_summary(run_dir, run_id)

    # PRR Type 2: per-link (tx_node_id, rx_node_id, prr_mean, prr_worst).
    _write_csv(
        run_dir / f"{run_id}_prr_type2.csv",
        [
            {"tx_node_id": 0, "rx_node_id": i + 1,
             "intended_set_size": 3, "n_tx": 100, "n_rx_success": 99 - i,
             "prr_mean": (99 - i) / 100.0,
             "prr_worst": 0.5, "prr_best": 1.0}
            for i in range(n_link_rows)
        ],
        ["tx_node_id", "rx_node_id", "intended_set_size", "n_tx",
         "n_rx_success", "prr_mean", "prr_worst", "prr_best"],
    )
    # AoI: per-link.
    _write_csv(
        run_dir / f"{run_id}_aoi.csv",
        [
            {"tx_node_id": 0, "rx_node_id": i + 1,
             "n_samples": 100, "aoi_mean_ms": 5.0 + i,
             "aoi_p5_ms": 2.0, "aoi_p95_ms": 50.0 + i,
             "aoi_max_ms": 60.0, "aoi_min_ms": 1.0}
            for i in range(n_link_rows)
        ],
        ["tx_node_id", "rx_node_id", "n_samples",
         "aoi_mean_ms", "aoi_p5_ms", "aoi_p95_ms", "aoi_max_ms", "aoi_min_ms"],
    )
    # Throughput: per-link.
    _write_csv(
        run_dir / f"{run_id}_throughput.csv",
        [
            {"tx_node_id": 0, "rx_node_id": i + 1,
             "n_rx": 99 - i, "bytes_rx": 30000, "thput_kbps": 200.0 - i}
            for i in range(n_link_rows)
        ],
        ["tx_node_id", "rx_node_id", "n_rx", "bytes_rx", "thput_kbps"],
    )
    # HARQ: per-rx_node_id.
    _write_csv(
        run_dir / f"{run_id}_harq.csv",
        [
            {"rx_node_id": i + 1, "harq_ack": 100, "harq_nack": 1,
             "harq_total": 101, "harq_nack_rate": 0.01}
            for i in range(n_link_rows)
        ],
        ["rx_node_id", "harq_ack", "harq_nack", "harq_total", "harq_nack_rate"],
    )
    # Channel occupancy: per-tx_node_id.
    _write_csv(
        run_dir / f"{run_id}_ch_occupancy.csv",
        [
            {"tx_node_id": 0, "n_grants": 100, "subch_slots_used": 100,
             "subch_slots_total": 600, "ch_occupancy": 0.167,
             "subch_slots_per_pkt": 2.0}
        ],
        ["tx_node_id", "n_grants", "subch_slots_used",
         "subch_slots_total", "ch_occupancy", "subch_slots_per_pkt"],
    )
    return run_dir


class TestSeedTagHelpers(unittest.TestCase):
    def test_splits_single_digit_seed(self):
        clean, seed = split_seed_tag("N10_s100_ivd29_mcr115p5_admit_4_h1_onoff_seed1")
        self.assertEqual(clean, "N10_s100_ivd29_mcr115p5_admit_4_h1_onoff")
        self.assertEqual(seed, 1)

    def test_splits_multi_digit_seed(self):
        clean, seed = split_seed_tag("N20_s140_admit_4_h2_seed42")
        self.assertEqual(clean, "N20_s140_admit_4_h2")
        self.assertEqual(seed, 42)

    def test_no_seed_suffix_returns_none(self):
        clean, seed = split_seed_tag("N10_s100_admit_4_h1_onoff")
        self.assertEqual(seed, None)

    def test_strip_seed_tag(self):
        self.assertEqual(
            strip_seed_tag("N10_s100_admit_seed3"),
            "N10_s100_admit",
        )


class TestCombineRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.run_dir = _write_run(self.root, "N10_s100_ivd29_mcr115p5_seed1")

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_dataframe(self):
        df = combine_run(self.run_dir)
        # 3 link rows.
        self.assertEqual(len(df), 3)

    def test_has_summary_columns(self):
        df = combine_run(self.run_dir)
        self.assertIn("runId", df.columns)
        self.assertIn("seed", df.columns)
        self.assertIn("numUEs", df.columns)
        # seed-stripped runId.
        self.assertTrue(
            df["runId"].iloc[0].endswith("mcr115p5"),
            f"expected seed-stripped runId, got {df['runId'].iloc[0]}"
        )

    def test_merges_kpis_with_kpi_id_prefix(self):
        df = combine_run(self.run_dir)
        self.assertIn("prr_type2_prr_mean", df.columns)
        self.assertIn("aoi_aoi_mean_ms", df.columns)
        self.assertIn("throughput_thput_kbps", df.columns)
        self.assertIn("harq_harq_ack", df.columns)
        self.assertIn("ch_occupancy_ch_occupancy", df.columns)

    def test_missing_summary_raises(self):
        # Remove the summary file.
        (self.run_dir / "N10_s100_ivd29_mcr115p5_seed1_summary.csv").unlink()
        with self.assertRaises(FileNotFoundError):
            combine_run(self.run_dir)


class TestJoinAll(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for rid in ["armA_seed1", "armA_seed2", "armB_seed1"]:
            _write_run(self.root, rid)

    def tearDown(self):
        self.tmp.cleanup()

    def test_walks_and_concatenates(self):
        out = self.root / "all_runs.csv"
        n = join_all(self.root, out)
        self.assertEqual(n, 3)
        # 3 runs × 3 link rows each.
        import pandas as pd
        df = pd.read_csv(out)
        self.assertEqual(len(df), 9)

    def test_run_id_strips_seed(self):
        out = self.root / "all_runs.csv"
        join_all(self.root, out)
        import pandas as pd
        df = pd.read_csv(out)
        # armA appears 6 times (2 seeds × 3 rows), armB 3 times.
        self.assertEqual((df["runId"] == "armA").sum(), 6)
        self.assertEqual((df["runId"] == "armB").sum(), 3)

    def test_empty_dir(self):
        empty = self.root / "empty"
        empty.mkdir()
        out = empty / "out.csv"
        n = join_all(empty, out)
        self.assertEqual(n, 0)


class TestRunDirsFiltering(unittest.TestCase):
    def test_filters_to_dirs_with_csvs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # A run dir with per-KPI CSVs.
            _write_run(root, "good_seed1")
            # A bare subdirectory with only summary.
            (root / "partial_seed1").mkdir()
            _make_summary(root / "partial_seed1", "partial_seed1")
            out = root / "out.csv"
            n = join_all(root, out)
            self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()