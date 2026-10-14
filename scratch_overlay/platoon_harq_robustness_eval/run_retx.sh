#!/usr/bin/env bash
# run_retx.sh — Driver for the groupcast retransmission × helper selection × helper scheduling campaign
# (PLAN.md).
#
# Steps: build traces check calib stageA stageB stageC topup analyze status
# Usage:
#   ./run_retx.sh build traces check calib
#   tmux new -d -s retx -c "$PWD" 'JOBS=24 ./run_retx.sh stageA stageB stageC topup analyze'
#   ./run_retx.sh status
#
# Environment variables:
#   JOBS (default 24 on server, 2 on laptop)
#   SEEDS (default 1-5)
#   BG_CARS (default 60; set to 30 for cost-reduction knob)
#   BIN (explicit binary path)

set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
REPO=$(cd ../.. && pwd)
JOBS=${JOBS:-24}
SEEDS=${SEEDS:-1-5}
BG_CARS=${BG_CARS:-60}
OUT="$HERE/results/retx_campaign"
LOG="$OUT/progress.log"
LOCK="$OUT/.lock"
mkdir -p "$OUT"

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }

find_bin() {
  if [ -n "${BIN:-}" ]; then echo "$BIN"; return; fi
  ls -t "$REPO"/ns-3-dev/build/scratch/platoon_harq_robustness_eval/ns3*-platoon_harq_robustness_eval-* \
     2>/dev/null | head -1
}

count_runs() { find "$1" -name '*_run.csv' -not -path '*/analysis*' 2>/dev/null | wc -l | tr -d ' '; }
count_failed() { grep -c 'FAILED' "$1" 2>/dev/null || true; }

status() {
  echo "Retransmission campaign in $OUT"
  echo "  Stage A runs: $(count_runs "$OUT/stageA")"
  echo "  Stage B runs: $(count_runs "$OUT/stageB")"
  echo "  Stage C runs: $(count_runs "$OUT/stageC")"
  echo "--- last log entries ($LOG):"
  tail -n 8 "$LOG" 2>/dev/null || true
  if command -v flock >/dev/null; then
    if flock -n "$LOCK" true 2>/dev/null; then echo "driver: not running"; else echo "driver: running"; fi
  fi
}

step_build() {
  log "build: setup.sh --force + ns3 build ($(git -C "$REPO" rev-parse --short HEAD))"
  (cd "$REPO" && ./setup.sh --force) || return 1
  (cd "$REPO/ns-3-dev" && ./ns3 build platoon_harq_robustness_eval) || return 1
  log "build: OK"
}

step_traces() {
  log "traces: checking/generating required traces (IVD 5, 15, 20, 30 for N 0 and N $BG_CARS, seeds $SEEDS)"
  python3 -c "
import sys, os
from pathlib import Path
sys.path.insert(0, '$HERE')
import run_campaign as rc

tdir = Path('$HERE/traces')
tdir.mkdir(parents=True, exist_ok=True)
seeds_str = '$SEEDS'
if '-' in seeds_str:
    s0, s1 = seeds_str.split('-', 1)
    seeds = list(range(int(s0), int(s1) + 1))
else:
    seeds = [int(s) for s in seeds_str.split(',')]

scenarios = ['highway', 'roundabout']
ivds = [5, 15, 20, 30]
nbg_list = [0, int('$BG_CARS')]

for sc in scenarios:
    for ivd in ivds:
        for nbg in nbg_list:
            for s in seeds:
                p = rc.trace_path(tdir, sc, ivd, nbg, s)
                if not p.exists():
                    print(f'Generating missing trace: {p.name}...')
                    _, status = rc.make_trace((tdir, sc, ivd, nbg, s))
                    if status != 'ok' and status != 'cached':
                        print(f'ERROR: trace generation failed: {status}', file=sys.stderr)
                        sys.exit(1)
print('All required traces verified/present.')
" || return 1
  log "traces: OK"
}

same_outputs() {
  python3 - "$1" "$2" <<'PY'
import csv, filecmp, glob, os, sys
a, b = sys.argv[1], sys.argv[2]
skip = {"helper_select", "helper_shadow", "hsel_cam_age_ms_mean", "hsel_cam_age_ms_p95",
        "helper_share_m4", "wall_s", "sel_ops_mean", "sel_cpu_us_mean",
        "helper_assigned", "helper_drop_rate"}
bad = []
for f in sorted(glob.glob(a + "_*.csv")):
    suf = f[len(a):]
    g = b + suf
    if suf in ("_hsel.csv",):
        continue
    if not os.path.exists(g):
        bad.append(f"missing {suf}")
    elif suf == "_run.csv":
        ra = next(csv.DictReader(open(f))); rb = next(csv.DictReader(open(g)))
        d = [k for k in ra if k not in skip and ra[k] != rb.get(k)]
        if d:
            bad.append(f"run: {d[:5]}")
    elif not filecmp.cmp(f, g, shallow=False):
        bad.append(f"differs {suf}")
print("identical" if not bad else "DIFFERENT " + "; ".join(bad))
sys.exit(1 if bad else 0)
PY
}

step_check() {
  local real
  real=$(find_bin)
  [ -n "$real" ] && [ -x "$real" ] || { log "check: no binary found"; return 1; }
  log "check: testing binary $real"

  local test_bin
  test_bin=$(ls -t "$REPO"/ns-3-dev/build/scratch/platoon_harq_robustness_eval/ns3*-test_helper_select-* 2>/dev/null | head -1)
  if [ -n "$test_bin" ] && [ -x "$test_bin" ]; then
    log "check: running helper_select unit tests ($test_bin)"
    "$test_bin" || { log "check: helper_select unit tests FAILED"; return 1; }
  fi

  local smoke="$OUT/smoke"
  rm -rf "$smoke" && mkdir -p "$smoke"
  local tr="$HERE/traces/rbt_ivd30_n0_s1.csv.gz"
  local net="--sumoNet=$HERE/sumo/roundabout/roundabout.net.xml"

  # Base flags
  local base=(--dedicatedCarrier=true --leaderSps=true --spsReselectOnMisfit=true
              --slDynExclusion=true --slProjectAnnounced=true --slProbResourceKeep=0.8
              --rriList=0,10,20,50,100,200,300,400,500,600,700,800,900,1000
              --ns3::LteRlcUm::ReorderingTimer=0ms)

  log "check: running shadow identity test (ref vs shadow)"
  "$real" "${base[@]}" --scenario=roundabout --mobility="$tr" "$net" --traffic=periodic1tb \
      --seed=1 --run=1 --maxTime=15 --helperMode=leaderRes --helper=4 --collCond=true \
      --out="$smoke/ref" > "$smoke/ref.log" 2>&1 || return 1

  "$real" "${base[@]}" --scenario=roundabout --mobility="$tr" "$net" --traffic=periodic1tb \
      --seed=1 --run=1 --maxTime=15 --helperMode=leaderRes --helper=4 --collCond=true \
      --helperSelect=map --helperSelectShadow=true \
      --out="$smoke/shadow" > "$smoke/shadow.log" 2>&1 || return 1

  same_outputs "$smoke/ref" "$smoke/shadow" | tee -a "$LOG" || return 1

  log "check: testing Option 2 leader (R3)"
  "$real" "${base[@]}" --scenario=roundabout --mobility="$tr" "$net" --traffic=periodic1tb \
      --seed=1 --run=1 --maxTime=15 --retx=R3 --out="$smoke/opt2" > "$smoke/opt2.log" 2>&1 || return 1

  log "check: testing Option 2 helper (R6 + S1 + map)"
  "$real" "${base[@]}" --scenario=roundabout --mobility="$tr" "$net" --traffic=periodic1tb \
      --seed=1 --run=1 --maxTime=15 --retx=R6 --helperSched=S1 --helperSelect=map \
      --out="$smoke/r6" > "$smoke/r6.log" 2>&1 || return 1

  log "check: testing HARQ statistics selector (H4)"
  "$real" "${base[@]}" --scenario=roundabout --mobility="$tr" "$net" --traffic=periodic1tb \
      --seed=1 --run=1 --maxTime=15 --retx=R6 --helperSched=S1 --helperSelect=harq \
      --out="$smoke/h4" > "$smoke/h4.log" 2>&1 || return 1

  log "check: testing HARQ + map selector (H6)"
  "$real" "${base[@]}" --scenario=roundabout --mobility="$tr" "$net" --traffic=periodic1tb \
      --seed=1 --run=1 --maxTime=15 --retx=R6 --helperSched=S1 --helperSelect=harqMap \
      --out="$smoke/h6" > "$smoke/h6.log" 2>&1 || return 1

  log "check: testing aperiodic1tb SB-SPS (rri 20)"
  "$real" "${base[@]}" --scenario=roundabout --mobility="$tr" "$net" --traffic=aperiodic1tb \
      --seed=1 --run=1 --maxTime=15 --aperiodicRri=20 --out="$smoke/ap20" > "$smoke/ap20.log" 2>&1 || return 1

  log "check: verifying run CSV flags and metrics"
  python3 -c "
import csv, glob, sys
for f in sorted(glob.glob('$smoke/*_run.csv')):
    with open(f) as fh:
        r = next(csv.DictReader(fh))
        assert abs(float(r['sl_slot_frac']) - 1.0) < 1e-4, f'sl_slot_frac in {f}'
        assert int(float(r['tb_max_b'])) == 1333, f'tb_max_b in {f}'
        assert int(float(r['leader_pkt_multi_tb_unqueued'])) == 0, f'multi_tb in {f}'
        assert int(float(r['relay_by_unchosen'])) == 0, f'relay_by_unchosen in {f}'
print('All smoke run CSV flags verified.')
" || return 1

  log "check: OK — all smoke tests and invariants passed."
}

step_calib() {
  local real
  real=$(find_bin)
  log "calib: running calibration pilots (20s per scenario x bg level) to measure CBR"
  local cdir="$OUT/calib"
  python3 "$HERE/run_retx.py" --stage calib --bin "$real" --results "$cdir" --jobs "$JOBS" --maxTime 20 || return 1

  log "calib: measured CBR summary:"
  python3 -c "
import csv, glob, os
print(f'{\"Scenario\":<12} {\"Level\":<10} {\"Target CBR\":<12} {\"Measured CBR\":<14} {\"Leader CBR\":<12}')
print('-' * 62)
for f in sorted(glob.glob('$cdir/**/*_run.csv', recursive=True)):
    with open(f) as fh:
        r = next(csv.DictReader(fh))
        sc = r['scenario']
        bg = 'none' if int(float(r['nbg'])) == 0 else ('moderate' if float(r['bg_flows_per_car']) < 2.5 else 'high')
        target = '0.0' if bg == 'none' else ('~0.30' if bg == 'moderate' else '~0.60')
        cbr = float(r['cbr_occupied'])
        cbr_l = float(r['cbr_leader'])
        print(f'{sc:<12} {bg:<10} {target:<12} {cbr:<14.4f} {cbr_l:<12.4f}')
" | tee -a "$LOG"
  log "calib: OK"
}

step_stageA() {
  local real
  real=$(find_bin)
  log "stageA: starting Retransmission Strategies campaign"
  python3 "$HERE/run_retx.py" --stage A --bin "$real" --results "$OUT/stageA" --jobs "$JOBS" --seeds "$SEEDS" || return 1
  log "stageA: completed"
}

step_stageB() {
  local real
  real=$(find_bin)
  log "stageB: starting Helper Selection campaign"
  python3 "$HERE/run_retx.py" --stage B --bin "$real" --results "$OUT/stageB" --jobs "$JOBS" --seeds "$SEEDS" || return 1
  log "stageB: completed"
}

step_stageC() {
  local real
  real=$(find_bin)
  log "stageC: starting Helper Scheduling campaign"
  python3 "$HERE/run_retx.py" --stage C --bin "$real" --results "$OUT/stageC" --jobs "$JOBS" --seeds "$SEEDS" || return 1
  log "stageC: completed"
}

# ---------------------------------------------------------------------------
# CLI dispatcher
# ---------------------------------------------------------------------------
STEPS=("$@")
[ ${#STEPS[@]} -eq 0 ] && STEPS=(check)

for step in "${STEPS[@]}"; do
  case "$step" in
    build)  step_build ;;
    traces) step_traces ;;
    check)  step_check ;;
    calib)  step_calib ;;
    stageA) step_stageA ;;
    stageB) step_stageB ;;
    stageC) step_stageC ;;
    status) status ;;
    *) echo "Unknown step: $step" >&2; exit 1 ;;
  esac
done
