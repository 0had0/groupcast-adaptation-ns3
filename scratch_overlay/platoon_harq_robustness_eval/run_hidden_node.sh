#!/usr/bin/env bash
# run_hidden_node.sh -- hidden-node test of the helper on leader-chosen resources
# (README "Hidden-node test"): sensing gap leader / helper at every leader selection and at
# every use of a leader-selected resource, PRR vs gap, oracle IUC arm (upper bound).
#
# Every run uses the SB-SPS reference configuration (the NEW_FLAGS of run_rerun_sps.sh),
# periodic1, --collCond, --hiddenNode, and one of the sensing thresholds (--slThresRsrp).
# Arms none / leaderRes / ownRes / leaderResIuc (run_hidden_node.py).
#
# Usage (server, inside tmux; resumable -- relaunch the same command):
#   tmux new -d -s hn -c "$PWD" 'JOBS=24 ./run_hidden_node.sh check stageA analyzeA'
#   ./run_hidden_node.sh status                 # progress, from any shell
#   ./run_hidden_node.sh [step ...]             # steps: build check stageA analyzeA
#                                               #        sweep1 sweep2 analyze
#   Stage B only after a stage-A go and the user's OK, with the thresholds that produced the
#   go and the cells stage A showed to be flat dropped, e.g.
#   THRESHOLDS="-128" DROP="highway:60:45" JOBS=24 ./run_hidden_node.sh sweep1 sweep2 analyze
#   (no default steps: the stages are run explicitly.)
#
# Environment: JOBS (24), TAG (hn -> results/hidden_hn, driver log results/rerun_hn/),
# THRESHOLDS ("-128 -110"), DROP (cells scenario:ivd:N to leave out of the sweeps), BIN.
#
# Matrix (run_hidden_node.py --dry-run prints counts and a time estimate):
#   stageA  rbt IVD30 N45, hwy IVD30 N45, hwy IVD60 N45 x thresholds x seeds 1-3 x
#           (none + {leaderRes, ownRes, leaderResIuc} x helpers {4, 7})         = 126 runs
#   sweep1  N {0,15,30,45,60} x {highway, roundabout} x IVD 30 x helper 4 x 4 arms x 10 seeds
#   sweep2  N 45 x {highway, roundabout} x helpers {2,4,7} x IVD {30,60} x 3 arms x 10
#           seeds, + none per scenario x IVD x threshold; shared runs and stage-A seeds reused
# Rough cost on the server at 24 jobs: stage A ~2 h; sweeps ~8 h per threshold.

set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
REPO=$(cd ../.. && pwd)
JOBS=${JOBS:-24}
TAG=${TAG:-hn}
THRESHOLDS=${THRESHOLDS:-"-128 -110"}
DROP=${DROP:-}
OUT="$HERE/results/rerun_$TAG"
RES="$HERE/results/hidden_$TAG"
LOG="$OUT/progress.log"
WRAP="$OUT/ns3_$TAG"
mkdir -p "$OUT"

# the SB-SPS reference configuration (run_rerun_sps.sh)
RRI_LIST=0,10,20,50,100,200,300,400,500,600,700,800,900,1000
PROB_KEEP=0.8
NEW_FLAGS=(--leaderSps=true --spsReselectOnMisfit=true --slDynExclusion=true --slProjectAnnounced=true
           --slProbResourceKeep=$PROB_KEEP --rriList=$RRI_LIST)
REQUIRED_OPTS=(leaderSps spsReselectOnMisfit slDynExclusion slProjectAnnounced slProbResourceKeep rriList
               slThresRsrp hiddenNode)

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }
count_runs() { find "$1" -name '*_run.csv' 2>/dev/null | wc -l | tr -d ' '; }
count_failed() { cat "$1"/progress.log 2>/dev/null | grep -c 'FAILED' || true; }
drop_args() { [ -n "$DROP" ] && echo "--drop $DROP"; }

find_bin() {
  if [ -n "${BIN:-}" ]; then echo "$BIN"; return; fi
  ls -t "$REPO"/ns-3-dev/build/scratch/platoon_harq_robustness_eval/ns3*-platoon_harq_robustness_eval-* \
     2>/dev/null | head -1
}

status() {
  echo "hidden-node test '$TAG' in $HERE (results $RES)"
  echo "  runs present: $(count_runs "$RES"), failed lines: $(count_failed "$RES")"
  for st in stageA sweep1 sweep2; do
    # shellcheck disable=SC2046
    echo "  $(python3 run_hidden_node.py --stage $st --thresholds $THRESHOLDS $(drop_args) \
              --results "$RES" --jobs "$JOBS" --dry-run 2>/dev/null | head -1)"
  done
  echo "--- last steps ($LOG):"
  tail -n 6 "$LOG" 2>/dev/null
  [ -f "$RES/progress.log" ] && { echo "--- $RES/progress.log:"; tail -n 2 "$RES/progress.log"; }
  if command -v flock >/dev/null; then
    if flock -n "$OUT/.lock" true 2>/dev/null; then echo "driver: not running"; else echo "driver: running"; fi
  fi
}

# Every run-CSV under $1 (arm = folder, tag ..._h<helper>_th<-thr>_s<seed>): the SB-SPS
# flags, t-Reordering 0, hidden_node = 1, coll_cond = 1, the tag's helper and threshold,
# the folder's helper mode, every hidden-node check 0.
verify_dir() {
  python3 - "$1" "$PROB_KEEP" "$RRI_LIST" <<'PY'
import csv, glob, os, re, sys
root = sys.argv[1]
need = {"sps_reselect_on_misfit": 1, "sl_dyn_exclusion": 1, "sl_project_announced": 1,
        "rlc_reordering_ms": 0, "sl_prob_resource_keep": float(sys.argv[2]),
        "hidden_node": 1, "coll_cond": 1}
must0 = ["hn_chk_sel_in_l", "hn_chk_age0_unexplained", "hn_chk_gap_mismatch", "hn_chk_link",
         "hn_chk_unlinked_uses", "hn_chk_iuc_sel_in_h"]
want_rri = sys.argv[3].replace(",", ":")
n = bad = 0
missing = set()
for f in sorted(glob.glob(f"{root}/**/*_run.csv", recursive=True)):
    arm = os.path.basename(os.path.dirname(f))
    m = re.search(r"_h(\d+)_th(\d+)_s\d+_run\.csv$", f)
    with open(f) as fh:
        for r in csv.DictReader(fh):
            n += 1
            want = dict(need)
            if m:
                want["helper"] = int(m.group(1))
                want["sl_thres_rsrp"] = -int(m.group(2))
            for k in must0:
                want[k] = 0
            for k, v in want.items():
                if k not in r:
                    missing.add(k)
                elif float(r[k]) != v:
                    bad += 1
                    print(f"MISMATCH {f}: {k}={r[k]} (want {v})")
            if r.get("rri_list") != want_rri:
                bad += 1
                print(f"MISMATCH {f}: rri_list={r.get('rri_list')} (want {want_rri})")
            if r.get("leader_grant") != "sps":
                bad += 1
                print(f"MISMATCH {f}: leader_grant={r.get('leader_grant')} (want sps)")
            if r.get("helper_mode") != arm:
                bad += 1
                print(f"MISMATCH {f}: helper_mode={r.get('helper_mode')} (folder {arm})")
print(f"verified {n} runs under {root}: {bad} mismatches; missing columns: "
      f"{', '.join(sorted(missing)) or 'none'}")
sys.exit(1 if (bad or missing or n == 0) else 0)
PY
}

provenance() {
  local real=$1
  {
    echo "date: $(date '+%F %T')   host: $(hostname)   jobs: $JOBS   tag: $TAG   thresholds: $THRESHOLDS   drop: ${DROP:--}"
    echo "branch: $(git -C "$REPO" rev-parse --abbrev-ref HEAD)  commit: $(git -C "$REPO" rev-parse --short HEAD)"
    echo "nr submodule: $(git -C "$REPO/ns-3-dev/contrib/nr" describe --tags --always 2>/dev/null)"
    echo "study folder changes:"; git -C "$REPO" status --short -- "$HERE" | sed 's/^/  /'
    echo "nr working-tree changes:"; git -C "$REPO/ns-3-dev/contrib/nr" status --short 2>/dev/null | sed 's/^/  /'
    echo "binary: $real"
    echo "binary mtime: $(date -r "$real" '+%F %T' 2>/dev/null || stat -c %y "$real")"
    echo "binary sha256: $( (sha256sum "$real" 2>/dev/null || shasum -a 256 "$real") | cut -d' ' -f1)"
    echo "extra flags: ${NEW_FLAGS[*]}"
  } >> "$OUT/provenance.txt"
}

make_wrapper() {
  local real=$1
  {
    echo '#!/usr/bin/env bash'
    echo "# generated by run_hidden_node.sh: the study binary + the SB-SPS reference flags"
    printf 'exec %q "$@"' "$real"
    printf ' %q' "${NEW_FLAGS[@]}"
    echo
  } > "$WRAP"
  chmod +x "$WRAP"
}

step_build() {
  log "build: setup.sh --force + ns3 build ($(git -C "$REPO" rev-parse --abbrev-ref HEAD) @ $(git -C "$REPO" rev-parse --short HEAD))"
  (cd "$REPO" && ./setup.sh --force) || return 1
  (cd "$REPO/ns-3-dev" && ./ns3 build platoon_harq_robustness_eval) || return 1
}

step_check() {
  local real
  real=$(find_bin)
  [ -n "$real" ] && [ -x "$real" ] || { log "check: no ns-3 binary found (build first or set BIN)"; return 1; }
  log "check: binary $real"
  local help
  help=$("$real" --PrintHelp 2>&1) || true
  local missing=()
  for o in "${REQUIRED_OPTS[@]}"; do
    grep -q -- "--$o" <<<"$help" || missing+=("$o")
  done
  grep -q -- "leaderResIuc" <<<"$help" || missing+=("helperMode=leaderResIuc")
  if [ ${#missing[@]} -gt 0 ]; then
    log "check: the binary lacks --${missing[*]} -- build the hidden-node code first"
    return 1
  fi
  command -v python3 >/dev/null || { log "check: python3 missing"; return 1; }
  python3 -c 'import pandas, numpy, matplotlib, scipy' 2>/dev/null \
    || { log "check: python packages missing (pandas numpy matplotlib scipy)"; return 1; }
  [ -n "${SUMO_HOME:-}" ] || log "check: WARNING SUMO_HOME unset (needed to generate missing traces)"
  log "check: disk $(df -h "$HERE" | awk 'NR==2{print $4" free"}')$(command -v free >/dev/null && free -g | awk '/Mem:/{print ", RAM "$7" GB available"}')"
  make_wrapper "$real"
  provenance "$real"
  log "check: wrapper $WRAP"

  # smoke runs (15 s): every arm, both thresholds, both helpers; then the CSV checks
  local smoke="$OUT/smoke"
  rm -rf "$smoke"; mkdir -p "$smoke"
  local cases=(
    "none roundabout 30 45 4 -128"
    "leaderRes roundabout 30 45 7 -110"
    "leaderResIuc highway 30 45 4 -128"
    "ownRes highway 30 45 7 -110"
  )
  local pids=() ran=0
  for c in "${cases[@]}"; do
    read -r arm sc ivd nbg h thr <<<"$c"
    local short=hwy; [ "$sc" = roundabout ] && short=rbt
    local tr="$HERE/traces/${short}_ivd${ivd}_n${nbg}_s1.csv.gz"
    [ -f "$tr" ] || { log "check: smoke case skipped, trace $tr missing"; continue; }
    local net=(); [ "$sc" = roundabout ] && net=(--sumoNet="$HERE/sumo/roundabout/roundabout.net.xml")
    local tag="${short}_ivd${ivd}_periodic1_n${nbg}_h${h}_th${thr#-}_s1"
    mkdir -p "$smoke/$arm"
    ( cd "$smoke/$arm" && "$WRAP" --scenario="$sc" --mobility="$tr" "${net[@]}" --traffic=periodic1 \
        --helperMode="$arm" --helper="$h" --slThresRsrp="$thr" --hiddenNode=true --collCond=true \
        --kpiStart=3 --maxTime=15 --ns3::LteRlcUm::ReorderingTimer=0ms --out="$smoke/$arm/$tag" \
        > "$smoke/$arm/$tag.log" 2>&1 ) &
    pids+=($!); ran=$((ran + 1))
  done
  [ $ran -gt 0 ] || { log "check: no smoke case could run (traces missing)"; return 1; }
  local rc=0
  for p in "${pids[@]}"; do wait "$p" || rc=1; done
  [ $rc -eq 0 ] || { log "check: a smoke run failed -- see $smoke/*/*.log"; return 1; }
  verify_dir "$smoke" | tee -a "$LOG"
  [ "${PIPESTATUS[0]}" -eq 0 ] || { log "check: smoke CSVs fail the flag / hidden-node checks"; return 1; }
  python3 analyze_hidden_node.py stageA --results "$smoke" --out "$smoke/analysis" > "$smoke/analyze.txt" 2>&1 \
    || { log "check: analyze_hidden_node.py failed on the smoke runs -- see $smoke/analyze.txt"; return 1; }
  log "check: OK ($ran smoke runs, analysis runs)"
}

ensure_wrapper() {
  if [ ! -x "$WRAP" ]; then
    log "no wrapper yet -- running check first"
    step_check || return 1
  fi
}

run_stage() {
  local st=$1
  ensure_wrapper || return 1
  # shellcheck disable=SC2046
  log "$st: $(python3 run_hidden_node.py --stage "$st" --thresholds $THRESHOLDS $(drop_args) \
              --results "$RES" --jobs "$JOBS" --dry-run | head -1)"
  # shellcheck disable=SC2046
  python3 run_hidden_node.py --stage "$st" --thresholds $THRESHOLDS $(drop_args) --jobs "$JOBS" \
      --results "$RES" --bin "$WRAP" || return 1
  verify_dir "$RES" | tail -1 | tee -a "$LOG"; [ "${PIPESTATUS[0]}" -eq 0 ] || return 1
  log "$st: done ($(count_runs "$RES") runs in $RES, failed lines $(count_failed "$RES"))"
}

step_analyze_a() {
  log "analyzeA: -> $RES/analysis_A"
  python3 analyze_hidden_node.py stageA --results "$RES" > "$OUT/analyze_stageA.txt" 2>&1 \
    || { log "analyzeA: FAILED (see $OUT/analyze_stageA.txt)"; return 1; }
  log "analyzeA: $(grep '^VERDICT' "$OUT/analyze_stageA.txt")"
}

step_analyze() {
  log "analyze: -> $RES/analysis"
  python3 analyze_hidden_node.py full --results "$RES" > "$OUT/analyze_full.txt" 2>&1 \
    || { log "analyze: FAILED (see $OUT/analyze_full.txt)"; return 1; }
  log "analyze: done ($OUT/analyze_full.txt, $RES/analysis/verdict.txt)"
}

# ---------------------------------------------------------------------------------------------
if [ "${1:-}" = status ]; then status; exit 0; fi

STEPS=("$@")
[ ${#STEPS[@]} -eq 0 ] && { echo "give the steps explicitly (build check stageA analyzeA sweep1 sweep2 analyze)"; exit 1; }

# one driver at a time (flock on Linux; skipped where unavailable)
if command -v flock >/dev/null; then
  exec 9>"$OUT/.lock"
  flock -n 9 || { echo "another run_hidden_node.sh is active (lock $OUT/.lock)"; exit 1; }
fi
exec > >(tee -a "$OUT/stdout.log") 2>&1

log "=== start: steps ${STEPS[*]}, jobs $JOBS, tag $TAG, thresholds $THRESHOLDS, drop ${DROP:--}"
T0=$(date +%s)
for s in "${STEPS[@]}"; do
  t=$(date +%s)
  log "--- step $s"
  case "$s" in
    build) step_build ;;
    check) step_check ;;
    stageA|sweep1|sweep2) run_stage "$s" ;;
    analyzeA) step_analyze_a ;;
    analyze) step_analyze ;;
    *) log "unknown step $s"; false ;;
  esac
  rc=$?
  el=$(( $(date +%s) - t ))
  if [ $rc -ne 0 ]; then
    log "--- step $s FAILED after $((el / 3600))h$(((el % 3600) / 60))m -- fix and relaunch (completed runs are kept)"
    exit $rc
  fi
  log "--- step $s ok in $((el / 3600))h$(((el % 3600) / 60))m"
done
el=$(( $(date +%s) - T0 ))
log "=== all done in $((el / 3600))h$(((el % 3600) / 60))m"
status | tee -a "$LOG"
