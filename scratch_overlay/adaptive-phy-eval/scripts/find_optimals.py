#!/usr/bin/env python3
"""
Find optimal configurations and compute static vs oracle gap.
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

# Paths
ADAPTIVE_PHY_EVAL_DIR = Path("/Users/hadih/Documents/groupcast-adaptation-ns3/ns-3-dev/scratch/adaptive-phy-eval")
RESULTS_DIR = ADAPTIVE_PHY_EVAL_DIR / "results"
SUMMARY_DIR = RESULTS_DIR / "summary"


def load_summary_csvs() -> Dict:
    """Load all summary CSVs."""
    
    summaries = {}
    
    if not SUMMARY_DIR.exists():
        print(f"Warning: Summary directory not found: {SUMMARY_DIR}")
        return summaries
    
    for csv_file in SUMMARY_DIR.glob("*_summary.csv"):
        scenario = csv_file.stem.replace("_summary", "")
        summaries[scenario] = []
        
        with open(csv_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                summaries[scenario].append({
                    "condition": row["condition"],
                    "mu": int(row["mu"]),
                    "w_prbs": int(row["w_prbs"]),
                    "mcs": int(row["mcs"]),
                    "group_prr_mean": float(row["group_prr_mean"]),
                })
    
    return summaries


def find_per_condition_optimal(rows: List[Dict]) -> Dict:
    """Find optimal config per condition."""
    
    by_condition = defaultdict(list)
    for row in rows:
        by_condition[row["condition"]].append(row)
    
    optimal = {}
    for cond, cond_rows in by_condition.items():
        best = max(cond_rows, key=lambda r: r["group_prr_mean"])
        optimal[cond] = best
    
    return optimal


def find_best_static(rows: List[Dict]) -> Dict:
    """Find single config that maximizes min(GroupPRR) across all conditions."""
    
    # Group by condition
    by_condition = defaultdict(list)
    for row in rows:
        by_condition[row["condition"]].append(row)
    
    # For each config, compute min(GroupPRR) across all conditions
    config_results = defaultdict(lambda: {"min_prr": 1.0})
    
    for row in rows:
        key = (row["mu"], row["w_prbs"], row["mcs"])
        config_results[key]["min_prr"] = min(
            config_results[key]["min_prr"],
            row["group_prr_mean"]
        )
    
    # Find config maximizing min(GroupPRR)
    best_config = None
    best_min_prr = -1
    for config, data in config_results.items():
        if data["min_prr"] > best_min_prr:
            best_min_prr = data["min_prr"]
            best_config = config
    
    if best_config:
        return {
            "mu": best_config[0],
            "w_prbs": best_config[1],
            "mcs": best_config[2],
            "group_prr": best_min_prr,
        }
    return {}


def compute_gaps(summaries: Dict) -> List[Dict]:
    """Compute optimal vs static gaps."""
    
    results = []
    
    for scenario, rows in summaries.items():
        # Find per-condition optimal
        per_condition_optimal = find_per_condition_optimal(rows)
        
        # Find best static
        best_static = find_best_static(rows)
        
        # Compute gap for each condition
        for cond, opt in per_condition_optimal.items():
            gap = opt["group_prr_mean"] - best_static["group_prr"]
            
            results.append({
                "scenario": scenario,
                "condition": cond,
                "optimal_mu": opt["mu"],
                "optimal_w": opt["w_prbs"],
                "optimal_mcs": opt["mcs"],
                "group_prr_at_optimal": opt["group_prr_mean"],
                "best_static_mu": best_static["mu"],
                "best_static_w": best_static["w_prbs"],
                "best_static_mcs": best_static["mcs"],
                "group_prr_at_best_static": best_static["group_prr"],
                "prr_gap": gap,
            })
    
    return results


def print_results(gaps: List[Dict]) -> None:
    """Print results in human-readable format."""
    
    # Group by scenario
    by_scenario = defaultdict(list)
    for row in gaps:
        by_scenario[row["scenario"]].append(row)
    
    for scenario, rows in by_scenario.items():
        print(f"\n=== SCENARIO {scenario.upper()} ===")
        print(f"{'Condition':<12} | {'Optimal Config':<20} | {'PRR':<8} | {'Static Config':<20} | {'Static PRR':<8} | {'Gap':<8}")
        print("-" * 100)
        
        for row in rows:
            opt_cfg = f"μ={row['optimal_mu']}, W={row['optimal_w']}, MCS={row['optimal_mcs']}"
            static_cfg = f"μ={row['best_static_mu']}, W={row['best_static_w']}, MCS={row['best_static_mcs']}"
            
            print(f"{row['condition']:<12} | {opt_cfg:<20} | {row['group_prr_at_optimal']:<8.4f} | {static_cfg:<20} | {row['group_prr_at_best_static']:<8.4f} | {row['prr_gap']:<+8.4f}")
        
        # Verdict
        avg_gap = sum(r["prr_gap"] for r in rows) / len(rows)
        max_gap = max(r["prr_gap"] for r in rows)
        
        if max_gap > 0.01:
            verdict = "VERDICT: Optimal config SHIFTS with condition. Dynamic reconfiguration justified."
        else:
            verdict = "VERDICT: Static config dominates. No significant gain from adaptation."
        
        print("-" * 100)
        print(f"Average gap: {avg_gap:+.4f}, Max gap: {max_gap:+.4f}")
        print(verdict)


def main():
    parser = argparse.ArgumentParser(description="Find optimal configs")
    args = parser.parse_args()
    
    print("Loading summaries...")
    summaries = load_summary_csvs()
    
    if not summaries:
        print("No summary CSVs found. Run aggregate_results.py first.")
        return
    
    print(f"Loaded {len(summaries)} scenarios")
    
    print("\nComputing gaps...")
    gaps = compute_gaps(summaries)
    
    # Write final CSV
    out_path = RESULTS_DIR / "all_scenarios_optimal_configs.csv"
    with open(out_path, 'w') as f:
        writer = csv.DictWriter(f, fieldnames=[
            "scenario", "condition", "optimal_mu", "optimal_w", "optimal_mcs",
            "group_prr_at_optimal", "best_static_mu", "best_static_w", 
            "best_static_mcs", "group_prr_at_best_static", "prr_gap"
        ])
        writer.writeheader()
        for row in gaps:
            writer.writerow(row)
    
    print(f"\nWrote {out_path}")
    
    # Print human-readable
    print_results(gaps)


if __name__ == "__main__":
    main()