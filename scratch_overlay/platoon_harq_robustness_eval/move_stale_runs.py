#!/usr/bin/env python3
"""move_stale_runs.py -- move the runs of results/retx_campaign that the 2026-10-15 fixes make
stale out of the way, so the resumable driver (run_retx.sh) redoes exactly those. Nothing is
deleted: files go to <dest>/<stage>/<arm>/.

Which binary made a run is read from its own run CSV:
  * no column `helper_ack_suppressed`  -> before the R6 fix (commit 5bf92c4)
  * no column `hstat_rule`             -> before the selector fix (this commit)

A run is stale if
  1. R6 (harq_mode opt2 with a helper) made before the R6 fix: the helper was deaf (blind
     relaying);
  2. made before the selector fix and its selector is
     - harq / harqKuo / harqMap (H4..H6): feedback statistics (first transmission only),
       H4 tie tolerance;
     - kuo (H2) on the highway: P(LOS) was the urban formula;
     - with --strict also map (H3) on the highway (scores proved unchanged in pilots: the
       in-lane platoon blocks every non-adjacent link, adjacent links succeed with P ~ 1).
Everything else (R1..R5 static, kuo / map on the roundabout, map on the highway) is kept.

  python3 move_stale_runs.py results/retx_campaign            # dry run: counts per arm
  python3 move_stale_runs.py results/retx_campaign --apply    # move them
"""

import argparse
import csv
import shutil
import sys
from collections import Counter
from pathlib import Path

HARQ_SELECTORS = {"harq", "harqKuo", "harqMap", "harqWorstNearest"}


def stale_reason(r, strict):
    hs = r.get("helper_select", "off")
    sc = r.get("scenario", "")
    r6 = r.get("harq_mode") == "opt2" and r.get("helper_mode", "off") not in ("off", "none")
    if r6 and "helper_ack_suppressed" not in r:
        return "R6 before the R6 fix"
    if "hstat_rule" not in r:
        if hs in HARQ_SELECTORS:
            return f"{hs} before the selector fix"
        if hs == "kuo" and sc == "highway":
            return "kuo highway before the selector fix"
        if strict and hs == "map" and sc == "highway":
            return "map highway before the selector fix (--strict)"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="results/retx_campaign")
    ap.add_argument("--dest", default=None, help="default: <root>/pre_fix_20261015")
    ap.add_argument("--apply", action="store_true", help="move the files (default: dry run)")
    ap.add_argument("--strict", action="store_true", help="also highway map runs")
    a = ap.parse_args()
    root = Path(a.root)
    dest = Path(a.dest) if a.dest else root / "pre_fix_20261015"
    if not root.is_dir():
        sys.exit(f"no such directory: {root}")
    per_arm, reasons, kept = Counter(), Counter(), 0
    for run_csv in sorted(root.glob("stage*/*/*_run.csv")):
        if dest in run_csv.parents:
            continue
        with open(run_csv) as f:
            r = next(csv.DictReader(f), None)
        if r is None:
            continue
        why = stale_reason(r, a.strict)
        if why is None:
            kept += 1
            continue
        arm_dir = run_csv.parent
        tag = run_csv.name[: -len("_run.csv")]
        per_arm[f"{arm_dir.parent.name}/{arm_dir.name}"] += 1
        reasons[why] += 1
        if a.apply:
            out = dest / arm_dir.parent.name / arm_dir.name
            out.mkdir(parents=True, exist_ok=True)
            for f in list(arm_dir.glob(tag + "_*")) + list(arm_dir.glob(tag + ".log")):
                shutil.move(str(f), str(out / f.name))
    for k, v in sorted(per_arm.items()):
        print(f"  {k:45s} {v:6d} stale runs")
    print("reasons:", dict(reasons))
    print(f"stale {sum(per_arm.values())}, kept {kept}; "
          + (f"moved to {dest}" if a.apply else "dry run (add --apply to move)"))


if __name__ == "__main__":
    main()
