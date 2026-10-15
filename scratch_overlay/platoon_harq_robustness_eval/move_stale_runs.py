#!/usr/bin/env python3
"""move_stale_runs.py -- move the runs of results/retx_campaign that the 2026-10-15 fixes make
stale out of the way, so the resumable driver (run_retx.sh) redoes exactly those. Nothing is
deleted: files go to <dest>/<stage>/<arm>/.

Which binary made a run is read from its own run CSV:
  * no column `helper_ack_suppressed`  -> before the R6 fix (commit 5bf92c4)
  * no column `hstat_rule`             -> before the selector fix (commit e5faafe)
  * no column `pos_err_m`              -> before the helper-statistics / position-error fix

A run is stale if
  1. R6 (harq_mode opt2 with a helper) made before the R6 fix: the helper was deaf (blind
     relaying);
  2. its selector is harq / harqKuo / harqMap (H4..H6) and it was made before the
     helper-statistics fix (the named helper's NACKs were recorded without its successes);
  3. its selector is map / harqMap (H3, H6) and its position error is not --posErrM (1.5 m by
     default; runs before the column used exact positions);
  4. its selector is kuo (H2) on the highway and it was made before the selector fix
     (urban P(LOS)).
Everything else (R1..R5 without a selector, static H1, kuo on the roundabout or after the
selector fix) is kept.

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


def stale_reason(r, pos_err_m):
    hs = r.get("helper_select", "off")
    sc = r.get("scenario", "")
    r6 = r.get("harq_mode") == "opt2" and r.get("helper_mode", "off") not in ("off", "none")
    if r6 and "helper_ack_suppressed" not in r:
        return "R6 before the R6 fix"
    if hs in HARQ_SELECTORS and "pos_err_m" not in r:
        return f"{hs} before the helper-statistics fix"
    if hs in ("map", "harqMap"):
        err = float(r.get("pos_err_m", 0.0) or 0.0)
        if abs(err - pos_err_m) > 1e-9:
            return f"{hs} with position error {err:g} m (want {pos_err_m:g})"
    if hs == "kuo" and sc == "highway" and "hstat_rule" not in r:
        return "kuo highway before the selector fix"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="results/retx_campaign")
    ap.add_argument("--dest", default=None,
                    help="default: <root>/pre_fix_<date-time> (a new folder per run of this script)")
    ap.add_argument("--apply", action="store_true", help="move the files (default: dry run)")
    ap.add_argument("--posErrM", type=float, default=1.5,
                    help="position error the map / harqMap runs must have (run_retx.py --posErrM)")
    a = ap.parse_args()
    root = Path(a.root)
    import time
    dest = Path(a.dest) if a.dest else root / time.strftime("pre_fix_%Y%m%d-%H%M%S")
    if not root.is_dir():
        sys.exit(f"no such directory: {root}")
    per_arm, reasons, kept = Counter(), Counter(), 0
    for run_csv in sorted(root.glob("stage*/*/*_run.csv")):
        with open(run_csv) as f:
            r = next(csv.DictReader(f), None)
        if r is None:
            continue
        why = stale_reason(r, a.posErrM)
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
