#!/usr/bin/env bash
# run_local_h.sh -- laptop test of the helper-selection strategies (stage B arms of
# run_retx.py: R4 / R5 with H1..H3, R6 with H1..H6) with NO background traffic and ONE seed.
#
#   ./run_local_h.sh                 # run (resumable: completed runs are skipped), then summarise
#   ./run_local_h.sh summary         # only the summary of what is there
#
# Environment (defaults in brackets):
#   JOBS [2]            parallel runs (the laptop is crash-prone: keep it low)
#   SEED [1]            the seed
#   SCENARIOS [highway,roundabout]   IVDS [15,30]   TRAFFICS [periodic1tb,periodic2tb,aperiodic1tb]
#   H4_RULES [nearest]  extra H4 rules, e.g. nearest,nearestAhead,nearestReliable,reliableAhead
#   KUO_OBJECTIVES [mean]  add maxmin variants with mean,min
#   H4_CAND_MAX_FAIL [0.5]  POS_ERR_M [1.5]  ARM_FILTER [all]   BIN [newest local build]
#   OUT [results/retx_local_h]
# Highway runs are cut to 53 s by run_retx.py (KPIs from 3 s), the roundabout runs its trace.
# Uses the traces in traces/ (all N = 0, seed 1 traces exist; a missing one is generated with
# SUMO). Outputs: $OUT/stageB/<arm>/<tag>_*.csv, $OUT/summary_h.csv (+ printed table).

set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
OUT=${OUT:-$HERE/results/retx_local_h}
JOBS=${JOBS:-2}
SEED=${SEED:-1}

find_bin() {
  if [ -n "${BIN:-}" ]; then echo "$BIN"; return; fi
  ls -t "$REPO"/ns-3-dev/build/scratch/platoon_harq_robustness_eval/ns3*-platoon_harq_robustness_eval-* \
     2>/dev/null | head -1
}

summary() {
  python3 - "$OUT" <<'PY'
import glob, os, sys
import pandas as pd
out = sys.argv[1]
rows = []
for f in glob.glob(os.path.join(out, "stageB", "*", "*_run.csv")):
    r = pd.read_csv(f).iloc[0]
    rows.append(dict(arm=os.path.basename(os.path.dirname(f)), scenario=r.scenario,
                     ivd=r.ivd_m, traffic=r.traffic, worst_prr=r.prr_min, gprr=r.prr_mean,
                     tx_per_pkt=r.tx_per_pkt, relays=r.get("coop_sent", 0),
                     helper_changes_per_s=r.get("helper_changes_per_s", 0)))
if not rows:
    print("no completed runs yet in", out); sys.exit(0)
D = pd.DataFrame(rows)
D.to_csv(os.path.join(out, "summary_h_runs.csv"), index=False)
S = (D.groupby(["scenario", "arm"])[["worst_prr", "gprr", "tx_per_pkt", "helper_changes_per_s"]]
       .mean().round(3))
S["cells"] = D.groupby(["scenario", "arm"]).size()
S.to_csv(os.path.join(out, "summary_h.csv"))
pd.set_option("display.width", 160)
print(f"{len(D)} runs; mean over IVD x traffic cells (N = 0, one seed: no CIs)")
print(S.to_string())
PY
}

if [ "${1:-}" = summary ]; then summary; exit 0; fi

BINP=$(find_bin)
[ -n "$BINP" ] && [ -x "$BINP" ] || { echo "no local binary: cd ns-3-dev && ./ns3 build platoon_harq_robustness_eval"; exit 1; }
[ "$(uname)" = Darwin ] && export DYLD_LIBRARY_PATH="$REPO/ns-3-dev/build/lib${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
mkdir -p "$OUT"
echo "binary: $BINP"
echo "out:    $OUT   jobs: $JOBS   seed: $SEED   (no background traffic)"

python3 "$HERE/run_retx.py" --stage B --bin "$BINP" --results "$OUT/stageB" --jobs "$JOBS" \
    --seeds "$SEED" --bg none \
    --scenarios "${SCENARIOS:-highway,roundabout}" --ivds "${IVDS:-15,30}" \
    --traffics "${TRAFFICS:-periodic1tb,periodic2tb,aperiodic1tb}" \
    --h4Rules "${H4_RULES:-nearest}" --h4CandMaxFail "${H4_CAND_MAX_FAIL:-0.5}" \
    --kuoObjectives "${KUO_OBJECTIVES:-mean}" --posErrM "${POS_ERR_M:-1.5}" \
    --armFilter "${ARM_FILTER:-}"
rc=$?
summary
exit $rc
