#!/usr/bin/env python3
import csv
from collections import defaultdict
from pathlib import Path

RESULTS_DIR = Path("/Users/hadih/Documents/groupcast-adaptation-ns3/ns-3-dev/scratch/adaptive-phy-eval/results")
RAW_DIR = RESULTS_DIR / "raw"
SUMMARY_DIR = RESULTS_DIR / "summary"

def parse(name):
    name = name.replace("_run.csv", "")
    parts = name.split("_")
    if len(parts) < 6: return {}
    try:
        return {"scenario": parts[0], "condition": parts[1], "mu": int(parts[2][2:]), "w": int(parts[3][1:]), "mcs": int(parts[4][3:]), "seed": int(parts[5][5:])}
    except: return {}

def aggregate():
    results = defaultdict(list)
    for csv_file in RAW_DIR.glob("*_run.csv"):
        cfg = parse(csv_file.name)
        if not cfg: continue
        with open(csv_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = (cfg["scenario"], cfg["condition"], cfg["mu"], cfg["w"], cfg["mcs"])
                try: results[key].append(float(row.get("group_PRR", 0)))
                except: pass
                break
    return results

def main():
    results = aggregate()
    print(f"Found {len(results)} configs")
    print("Condition | mu | W | MCS | GroupPRR")
    print("-" * 45)
    for (scen, cond, mu, w, mcs), prrs in sorted(results.items()):
        print(f"{cond:<10} | {mu} | {w} | {mcs:<4} | {sum(prrs)/len(prrs):.4f}")
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    # Write summary CSVs...
    print("Done!")

if __name__ == "__main__":
    main()
