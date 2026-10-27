#!/bin/bash
# Density sensitivity sweep on the remote: arms R1 / R3 / R6, background 15 / 30 / 45 cars at the
# moderate per-car load (1.67 flows / car), highway + roundabout (W and E exit, curve window),
# periodic1tb + aperiodic1tb, 10 seeds (540 runs). 0 and 60 cars come from out/full (none / moderate).
#   1. (optional) update the checkout:   --pull BRANCH
#   2. (optional) setup.sh --force; build the simulator (always, ninja is a no-op when up to date)
#   3. generate the missing ivd30 n15 / n30 / n45 traces (hwy, rbt, rbtE; needs SUMO)
#   4. study.py run --profile density in the background (nohup, resumable), then the notebook
# Usage: launch_density.sh [--pull BRANCH] [--jobs N] [--seeds 1-10] [--setup] [--gen-jobs N]
#                          [--out DIR] [--only REGEX] [--full-dir DIR]
#        (--jobs / --gen-jobs default: every core, nproc)
#          --out DIR       output folder (default out/density), log out/<basename DIR>_run.log; the
#                          commit launched is written to DIR/COMMIT
#          --only REGEX    passed to study.py (regex on "arm/tag")
#          --full-dir DIR  full campaign whose 0 / 60-car cells the notebook adds (default out/full)
#        launch_density.sh --smoke [--jobs N]   # check the whole chain first (foreground, ~minutes):
#            traces for seed 1 / 15 cars, R6 on every scenario x traffic with short runs, consistency
#            checks, notebook; output out/density_smoke, nothing of the real sweep is touched
#        launch_density.sh [--out DIR] --stop
# Progress: ./monitor_study.sh density
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
EVAL="$HERE/.."
NCPU=$(nproc 2>/dev/null || sysctl -n hw.ncpu)
JOBS=$NCPU; SEEDS=1-10; PULL=""; SETUP=0; STOP=0; GENJOBS=$NCPU; SMOKE=0
OUT=out/density; ONLY=""; FULLDIR_ARG=out/full
while [ $# -gt 0 ]; do
  case "$1" in
    --pull) PULL="$2"; shift 2;;
    --jobs) JOBS="$2"; shift 2;;
    --seeds) SEEDS="$2"; shift 2;;
    --setup) SETUP=1; shift;;
    --gen-jobs) GENJOBS="$2"; shift 2;;
    --stop) STOP=1; shift;;
    --smoke) SMOKE=1; shift;;
    --out) OUT="$2"; shift 2;;
    --only) ONLY="$2"; shift 2;;
    --full-dir) FULLDIR_ARG="$2"; shift 2;;
    *) echo "unknown option $1"; exit 2;;
  esac
done
case "$OUT" in /*) OUTABS="$OUT";; *) OUTABS="$HERE/$OUT";; esac
OUTABS="${OUTABS%/}"
LOG="out/$(basename "$OUTABS")_run.log"
ONLYARG=(); [ -n "$ONLY" ] && ONLYARG=(--only "$ONLY")
# processes of this output folder only (study.py and the simulators get its absolute path)
STUDY_PAT="study.py run --profile density .*--out $OUTABS( |\$)"
SIM_PAT="ns3.42-platoon_harq_robustness_eval-.*--out=$OUTABS/"
if [ "$STOP" = 1 ]; then
  pkill -f "$STUDY_PAT" && echo "stopped study.py ($OUTABS)"
  pkill -f "build_density_notebook.py .*--density-dir $OUTABS"
  pkill -f "$SIM_PAT" && echo "stopped simulator processes"
  sleep 2
  pgrep -fl "$STUDY_PAT|$SIM_PAT" || echo "nothing running for $OUTABS"
  exit 0
fi
PROFILE=density; DENS="15 30 45"
if [ "$SMOKE" = 1 ]; then PROFILE=density_smoke; DENS="15"; SEEDS=1; fi
if [ "$SMOKE" = 0 ] && pgrep -f "$STUDY_PAT" > /dev/null; then
  echo "a density sweep is already running into $OUTABS: launch_density.sh --out $OUT --stop first"; exit 7
fi

cd "$REPO" || exit 2
if [ -n "$PULL" ]; then
  git fetch origin && git checkout "$PULL" && git pull --ff-only origin "$PULL" || exit 3
fi
echo "commit $(git rev-parse --short HEAD) ($(git rev-parse --abbrev-ref HEAD))"
if [ "$SETUP" = 1 ]; then
  ./setup.sh --force || exit 4
fi
ninja -C ns-3-dev/cmake-cache scratch_platoon_harq_robustness_eval_platoon_harq_robustness_eval || exit 5

mkdir -p "$HERE/out/traces_log"
# ---- traces (git-ignored): generate the missing ones, GENJOBS in parallel; each has its own log in
# out/traces_log/; a failure is retried once on its own (no parallel neighbours), then reported
SEEDLIST=$(python3 -c "import sys; sys.path.insert(0, '$HERE'); import study; print(*study.parse_seeds('$SEEDS'))")
gen_cmd() {   # $1 = hwy | rbt | rbtE, $2 = cars, $3 = seed
  case $1 in
    hwy)  A="--scenario highway";;
    rbt)  A="--scenario roundabout";;
    rbtE) A="--scenario roundabout --rbt-exit E";;
  esac
  echo "python3 $EVAL/gen_mobility.py $A --ivd 30 --nbg $2 --seed $3 --out $EVAL/traces/$1_ivd30_n$2_s$3.csv.gz"
}
TODO=()
for N in $DENS; do
  for S in $SEEDLIST; do
    for P in hwy rbt rbtE; do
      [ -f "$EVAL/traces/${P}_ivd30_n${N}_s${S}.csv.gz" ] || TODO+=("$P $N $S")
    done
  done
done
echo "traces to generate: ${#TODO[@]} ($GENJOBS in parallel)"
export -f gen_cmd; export EVAL HERE
run_gen() {   # $1 $2 $3 as gen_cmd; prints FAILED <name> on failure
  local name="$1_ivd30_n$2_s$3"
  $(gen_cmd "$1" "$2" "$3") > "$HERE/out/traces_log/$name.log" 2>&1 || echo "FAILED $1 $2 $3"
}
export -f run_gen
if [ ${#TODO[@]} -gt 0 ]; then
  FAILED=$(printf '%s\n' "${TODO[@]}" | xargs -P "$GENJOBS" -L 1 bash -c 'run_gen "$0" "$1" "$2"')
  if [ -n "$FAILED" ]; then
    echo "$FAILED" | sed 's/^/  /'; echo "retrying those one at a time"
    STILL=""
    while read -r _ P N S; do
      R=$(run_gen "$P" "$N" "$S")
      if [ -n "$R" ]; then
        STILL="$STILL ${P}_n${N}_s${S}"
        echo "FAILED again: ${P}_ivd30_n${N}_s${S} -- last lines of out/traces_log/${P}_ivd30_n${N}_s${S}.log:"
        grep -v "is not known\|Retrying in\|Could not connect to TraCI" "$HERE/out/traces_log/${P}_ivd30_n${N}_s${S}.log" | tail -8 | sed 's/^/    /'
      else
        echo "ok on retry: ${P}_ivd30_n${N}_s${S}"
      fi
    done <<< "$FAILED"
    [ -n "$STILL" ] && { echo "trace generation failed"; exit 8; }
  fi
fi
# background population actually reached in the area (target vs mean)
python3 - "$EVAL/traces" "$DENS" "$SEEDLIST" <<'PYEOF'
import json, sys, os
d, dens, seeds = sys.argv[1], sys.argv[2].split(), sys.argv[3].split()
bad = []
for n in dens:
    for s in seeds:
        for p in ('hwy', 'rbt', 'rbtE'):
            j = os.path.join(d, f'{p}_ivd30_n{n}_s{s}.json')
            m = json.load(open(j))['meta'] if os.path.exists(j) else {}
            if m and abs(m.get('bg_in_area_mean', 0) - int(n)) > 0.1 * int(n):
                bad.append(f"{p} n{n} s{s}: mean {m['bg_in_area_mean']} vs target {n}")
print('background in area: ' + ('all within 10 % of the target' if not bad else 'OFF TARGET:\n  ' + '\n  '.join(bad)))
PYEOF

cd "$HERE" || exit 2
mkdir -p out
unset MAGIC_HELPER_ID NUM_MEMBERS
if [ "$SMOKE" = 1 ]; then
  echo "smoke: $(python3 study.py list --profile density_smoke | wc -l) short runs, $JOBS in parallel"
  python3 study.py run --profile density_smoke --jobs "$JOBS" > out/density_smoke_run.log 2>&1
  tail -8 out/density_smoke_run.log
  python3 - <<'PYEOF' || { echo "SMOKE FAILED"; exit 9; }
import glob, json, sys, pandas as pd
ok = True
for f in sorted(glob.glob('out/density_smoke/R6/*_run.csv*')):
    tag = f.split('/')[-1].split('_run.csv')[0]
    r = pd.read_csv(f).iloc[0]
    cmd = json.load(open(f'out/density_smoke/R6/{tag}.cmd.json'))
    win = any(c.startswith('--kpiStart') for c in cmd)
    p = pd.read_csv(f'out/density_smoke/R6/{tag}_pkt.csv')
    bent = (p.dist_m < p.member * 35 - .5).mean()
    good = r.pkts > 0 and (win == tag.startswith('rbt')) and '--bgFlowsPerCar=1.67' in cmd
    ok &= bool(good)
    print(f"{'ok  ' if good else 'BAD '} {tag:34s} pkts {int(r.pkts):5d}  PRR {r.prr_mean:.3f}  curve window {win}  "
          f"bent-link packets {100 * bent:.0f} %  helper reclaims {int(r.helper_reclaim)}")
n = len(glob.glob('out/density_smoke/R6/*_run.csv*'))
print(f'{n}/6 runs'); sys.exit(0 if ok and n == 6 else 1)
PYEOF
  [ -d out/full ] && FULLDIR=out/full || FULLDIR=out/remote_full
  python3 build_density_notebook.py --full-dir "$FULLDIR" --density-dir out/density_smoke > out/density_smoke_nb.log 2>&1 \
    && echo "notebook ok (DENSITY_RESULTS.ipynb from $FULLDIR + out/density_smoke)" \
    || { tail -5 out/density_smoke_nb.log; echo "SMOKE FAILED (notebook)"; exit 9; }
  echo "SMOKE PASSED"
  exit 0
fi
echo "planned runs: $(python3 study.py list --profile density --seeds "$SEEDS" ${ONLYARG[@]+"${ONLYARG[@]}"} | wc -l), $JOBS in parallel"
mkdir -p "$OUTABS"
git -C "$REPO" rev-parse HEAD > "$OUTABS/COMMIT"
echo "output $OUTABS (commit $(cut -c1-12 "$OUTABS/COMMIT")), notebook with the full cells of $FULLDIR_ARG"
ONLYQ=""; [ -n "$ONLY" ] && ONLYQ="--only $(printf '%q' "$ONLY")"
nohup bash -c "python3 study.py run --profile density --seeds $SEEDS --jobs $JOBS --out $OUTABS $ONLYQ && \
               python3 build_density_notebook.py --full-dir $FULLDIR_ARG --density-dir $OUTABS" \
      >> "$LOG" 2>&1 &
echo "running in background (pid $!). Progress: ./monitor_study.sh density --log $LOG"
