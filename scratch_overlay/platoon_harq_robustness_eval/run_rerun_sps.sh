#!/usr/bin/env bash
# run_rerun_sps.sh -- full re-run of the study with standard Mode-2 grant types:
#   * periodic traffic (leader periodic1/periodic2, background periodic1) on SB-SPS, with the
#     TS 38.321 sec. 5.22.1.2 reselection trigger when an RLC SDU does not fit the grant
#     (instead of CTTC's segmentation across periods);
#   * aperiodic traffic (leader aperiodic1, background aperiodic1, helper relays) on dynamic
#     grants;
#   * standard-like SPS configuration: sl-ProbResourceKeep 0.8, a 14-entry
#     sl-ResourceReservePeriodList (stack default: 27 entries, more than RRC allows);
#   * TS 38.214 sec. 8.1.4 sensing fixes: exclusion for dynamic grants (D1) and projection of
#     announced reservations of dynamic and current-period retransmissions (D2).
#
# REQUIRES the code that implements the options
#   --spsReselectOnMisfit  --slDynExclusion  --slProjectAnnounced  --slProbResourceKeep  --rriList
#   (and the existing --leaderSps)
# and writes the run-CSV columns
#   leader_grant (sps|dynamic, effective), sps_reselect_on_misfit, sl_dyn_exclusion,
#   sl_project_announced, sl_prob_resource_keep, rri_list.
# The `check` step refuses to start the campaign otherwise.
#
# Usage (server, inside tmux; the script is resumable -- just relaunch the same command):
#   tmux new -d -s rerun -c "$PWD" 'JOBS=24 ./run_rerun_sps.sh'
#   ./run_rerun_sps.sh status                # progress, from any shell, at any time
#   ./run_rerun_sps.sh [step ...]            # steps: build check campaign variants coop analyze
#                                            # default: check campaign variants coop analyze
#   `build` (not in the default) runs ./setup.sh --force (reinstalls patches/ into the
#   submodules -- local submodule edits are overwritten) and rebuilds the program.
#
# Environment: JOBS (default 24), TAG (default sps -> results/*_sps), BIN (ns-3 binary;
# default: newest ns3*-platoon_harq_robustness_eval-* in the build tree).
#
# Matrix (same traces and seeds as the earlier campaigns):
#   campaign  2 scenarios x IVD {5,15,30} x 3 traffic x N {0,15,30,45,60} x seeds 1-3  = 270
#   variants  IVD 30, N {0,45,60}: dedicated + blind, 3 traffic, seeds 1-3 (108) and
#             MCS 14 + dedicated, periodic1/2, seeds 4-10 (168); MCS 14 seeds 1-3 = campaign
#                                                                                      = 276
#   coop      arms none leaderRes ownRes split additive x cells roundabout:{0,45,60}
#             highway:{45,60} (IVD 30) x periodic1/2 x seeds 1-10, helper 4, --coll-cond  = 500
#   (not re-run: MCS 5/9 -- open truncated-packet bug -- and the helper sweep.)
# Rough cost on the server at 24 jobs: campaign ~3 h, variants ~4.5 h, coop ~6-7 h.

set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
REPO=$(cd ../.. && pwd)
JOBS=${JOBS:-24}
TAG=${TAG:-sps}
OUT="$HERE/results/rerun_$TAG"
CAMP="$HERE/results/campaign_$TAG"
VAR="$HERE/results/variants_$TAG"
COOP="$HERE/results/coop_$TAG"
ANA="$HERE/results/analysis_$TAG"
LOG="$OUT/progress.log"
WRAP="$OUT/ns3_$TAG"
mkdir -p "$OUT"

# sl-ResourceReservePeriodList: 14 of the <= 16 entries RRC allows (TS 38.331), with the
# leader's 10 ms (periodic2) and 100 ms (periodic1); sl-ProbResourceKeep 0.8 (its maximum)
RRI_LIST=0,10,20,50,100,200,300,400,500,600,700,800,900,1000
PROB_KEEP=0.8
NEW_FLAGS=(--leaderSps=true --spsReselectOnMisfit=true --slDynExclusion=true --slProjectAnnounced=true
           --slProbResourceKeep=$PROB_KEEP --rriList=$RRI_LIST)
REQUIRED_OPTS=(leaderSps spsReselectOnMisfit slDynExclusion slProjectAnnounced slProbResourceKeep rriList)
COOP_ARMS=(none leaderRes ownRes split additive)
COOP_CELLS=(roundabout:0 roundabout:45 roundabout:60 highway:45 highway:60)
SEEDS_4_10=(4 5 6 7 8 9 10)
SEEDS_1_10=(1 2 3 4 5 6 7 8 9 10)
EXP_CAMPAIGN=270
EXP_VARIANTS=276
EXP_COOP=500

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }
count_runs() { find "$1" -name '*_run.csv' 2>/dev/null | wc -l | tr -d ' '; }
count_failed() { cat "$1"/progress.log "$1"/*/progress.log 2>/dev/null | grep -c 'FAILED' || true; }
pct() { awk -v a="$1" -v b="$2" 'BEGIN{printf "%5.1f%%", (b>0?100*a/b:0)}'; }

find_bin() {
  if [ -n "${BIN:-}" ]; then echo "$BIN"; return; fi
  ls -t "$REPO"/ns-3-dev/build/scratch/platoon_harq_robustness_eval/ns3*-platoon_harq_robustness_eval-* \
     2>/dev/null | head -1
}

status() {
  echo "rerun '$TAG' in $HERE"
  printf '  %-9s %4s / %-4s %s  failed: %s\n' campaign "$(count_runs "$CAMP")" $EXP_CAMPAIGN \
    "$(pct "$(count_runs "$CAMP")" $EXP_CAMPAIGN)" "$(count_failed "$CAMP")"
  printf '  %-9s %4s / %-4s %s  failed: %s\n' variants "$(count_runs "$VAR")" $EXP_VARIANTS \
    "$(pct "$(count_runs "$VAR")" $EXP_VARIANTS)" "$(count_failed "$VAR")"
  printf '  %-9s %4s / %-4s %s  failed: %s\n' coop "$(count_runs "$COOP")" $EXP_COOP \
    "$(pct "$(count_runs "$COOP")" $EXP_COOP)" "$(count_failed "$COOP")"
  echo "--- last steps ($LOG):"
  tail -n 6 "$LOG" 2>/dev/null
  for d in "$CAMP" "$VAR" "$COOP"; do
    f=$(ls -t "$d"/progress.log "$d"/*/progress.log 2>/dev/null | head -1)
    [ -n "$f" ] && { echo "--- $f:"; tail -n 2 "$f"; }
  done
  if command -v flock >/dev/null; then
    if flock -n "$OUT/.lock" true 2>/dev/null; then echo "driver: not running"; else echo "driver: running"; fi
  fi
}

# Every run-CSV under $1 must carry the new flags; leader_grant must be sps for periodic and
# dynamic for aperiodic traffic; t-Reordering 0.
verify_dir() {
  python3 - "$1" "$PROB_KEEP" "$RRI_LIST" <<'PY'
import csv, glob, sys
root = sys.argv[1]
need = {"sps_reselect_on_misfit": 1, "sl_dyn_exclusion": 1, "sl_project_announced": 1,
        "rlc_reordering_ms": 0, "sl_prob_resource_keep": float(sys.argv[2])}
want_rri = sys.argv[3].replace(",", ":")
n = bad = 0
missing = set()
for f in sorted(glob.glob(f"{root}/**/*_run.csv", recursive=True)):
    with open(f) as fh:
        for r in csv.DictReader(fh):
            n += 1
            for k, v in need.items():
                if k not in r:
                    missing.add(k)
                elif float(r[k]) != v:
                    bad += 1
                    print(f"MISMATCH {f}: {k}={r[k]} (want {v})")
            if "rri_list" not in r:
                missing.add("rri_list")
            elif r["rri_list"] != want_rri:
                bad += 1
                print(f"MISMATCH {f}: rri_list={r['rri_list']} (want {want_rri})")
            g, tr = r.get("leader_grant"), r.get("traffic", "")
            if g is None:
                missing.add("leader_grant")
            elif tr and g != ("sps" if tr.startswith("periodic") else "dynamic"):
                bad += 1
                print(f"MISMATCH {f}: leader_grant={g} for traffic {tr}")
print(f"verified {n} runs under {root}: {bad} mismatches; missing columns: "
      f"{', '.join(sorted(missing)) or 'none'}")
sys.exit(1 if (bad or missing or n == 0) else 0)
PY
}

provenance() {
  local real=$1
  {
    echo "date: $(date '+%F %T')   host: $(hostname)   jobs: $JOBS   tag: $TAG"
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
    echo "# generated by run_rerun_sps.sh: the study binary + the standard-grant flags"
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
  if [ ${#missing[@]} -gt 0 ]; then
    log "check: the binary lacks --${missing[*]} -- implement and build the standard-grant code first"
    return 1
  fi
  command -v python3 >/dev/null || { log "check: python3 missing"; return 1; }
  python3 -c 'import pandas, numpy, matplotlib, scipy' 2>/dev/null \
    || { log "check: python packages missing (pandas numpy matplotlib scipy)"; return 1; }
  [ -n "${SUMO_HOME:-}" ] || log "check: WARNING SUMO_HOME unset (needed only for missing traces)"
  log "check: disk $(df -h "$HERE" | awk 'NR==2{print $4" free"}')$(command -v free >/dev/null && free -g | awk '/Mem:/{print ", RAM "$7" GB available"}')"
  make_wrapper "$real"
  provenance "$real"
  log "check: wrapper $WRAP"

  # smoke runs: short, one per grant type and scenario, then the CSV check
  local smoke="$OUT/smoke"
  rm -rf "$smoke"; mkdir -p "$smoke"
  local cases=(
    "roundabout traces/rbt_ivd30_n0_s1.csv.gz periodic1"
    "roundabout traces/rbt_ivd30_n0_s1.csv.gz periodic2"
    "roundabout traces/rbt_ivd30_n0_s1.csv.gz aperiodic1"
    "highway traces/hwy_ivd30_n45_s1.csv.gz periodic2"
  )
  local pids=() ran=0
  for c in "${cases[@]}"; do
    read -r sc tr traffic <<<"$c"
    [ -f "$tr" ] || { log "check: smoke case skipped, trace $tr missing"; continue; }
    local net="--sumoNet=$HERE/sumo/roundabout/roundabout.net.xml"
    [ "$sc" = roundabout ] || net="--scenario=$sc"   # harmless repeat for the highway
    local tag="${sc}_${traffic}"
    ( cd "$smoke" && "$WRAP" --scenario="$sc" --mobility="$HERE/$tr" "$net" \
        --traffic="$traffic" --kpiStart=3 --maxTime=15 --out="$smoke/$tag" > "$smoke/$tag.log" 2>&1 ) &
    pids+=($!); ran=$((ran + 1))
  done
  [ $ran -gt 0 ] || { log "check: no smoke case could run (traces missing)"; return 1; }
  local rc=0
  for p in "${pids[@]}"; do wait "$p" || rc=1; done
  [ $rc -eq 0 ] || { log "check: a smoke run failed -- see $smoke/*.log"; return 1; }
  verify_dir "$smoke" | tee -a "$LOG"
  [ "${PIPESTATUS[0]}" -eq 0 ] || { log "check: smoke CSVs do not carry the expected flags"; return 1; }
  log "check: OK ($ran smoke runs)"
}

ensure_wrapper() {
  if [ ! -x "$WRAP" ]; then
    log "no wrapper yet -- running check first"
    step_check || return 1
  fi
}

step_campaign() {
  ensure_wrapper || return 1
  log "campaign: start ($(count_runs "$CAMP")/$EXP_CAMPAIGN done)"
  python3 run_campaign.py --seeds 1 2 3 --jobs "$JOBS" --results "$CAMP" --bin "$WRAP" || return 1
  verify_dir "$CAMP" | tail -1 | tee -a "$LOG"; [ "${PIPESTATUS[0]}" -eq 0 ] || return 1
  log "campaign: done ($(count_runs "$CAMP")/$EXP_CAMPAIGN, failed $(count_failed "$CAMP"))"
}

step_variants() {
  ensure_wrapper || return 1
  [ "$(count_runs "$CAMP")" -ge $EXP_CAMPAIGN ] || { log "variants: campaign incomplete (baseline cells needed)"; return 1; }
  log "variants: start ($(count_runs "$VAR")/$EXP_VARIANTS done)"
  python3 run_variants.py --variants mcs14 dedicated blind --jobs "$JOBS" \
      --results "$VAR" --baseline-dir "$CAMP" --bin "$WRAP" || return 1
  python3 run_variants.py --variants mcs14 dedicated --traffic periodic1 periodic2 \
      --seeds "${SEEDS_4_10[@]}" --jobs "$JOBS" --results "$VAR" --baseline-dir "$CAMP" --bin "$WRAP" || return 1
  verify_dir "$VAR" | tail -1 | tee -a "$LOG"; [ "${PIPESTATUS[0]}" -eq 0 ] || return 1
  log "variants: done ($(count_runs "$VAR")/$EXP_VARIANTS, failed $(count_failed "$VAR"))"
}

step_coop() {
  ensure_wrapper || return 1
  log "coop: start ($(count_runs "$COOP")/$EXP_COOP done)"
  python3 run_coop.py --arms "${COOP_ARMS[@]}" --cells "${COOP_CELLS[@]}" --coll-cond \
      --helper 4 --jobs "$JOBS" --results "$COOP" --bin "$WRAP" || return 1
  verify_dir "$COOP" | tail -1 | tee -a "$LOG"; [ "${PIPESTATUS[0]}" -eq 0 ] || return 1
  log "coop: done ($(count_runs "$COOP")/$EXP_COOP, failed $(count_failed "$COOP"))"
}

step_analyze() {
  local rc=0
  log "analyze: campaign -> $ANA"
  python3 analyze.py --results "$CAMP" --out "$ANA" > "$OUT/analyze_campaign.txt" 2>&1 || { log "analyze: campaign FAILED"; rc=1; }
  log "analyze: variants"
  python3 run_variants.py --analyze-only --seeds "${SEEDS_1_10[@]}" --results "$VAR" --baseline-dir "$CAMP" \
      > "$OUT/analyze_variants.txt" 2>&1 || { log "analyze: variants FAILED"; rc=1; }
  log "analyze: coop"
  python3 run_coop.py --analyze-only --arms "${COOP_ARMS[@]}" --cells "${COOP_CELLS[@]}" --results "$COOP" \
      > "$OUT/analyze_coop.txt" 2>&1 || { log "analyze: coop FAILED"; rc=1; }
  python3 analyze_coop_followup.py footprint --rbt "$COOP" --hwy "$COOP" \
      > "$OUT/analyze_footprint.txt" 2>&1 || { log "analyze: footprint FAILED (check the script's cell filters)"; rc=1; }
  log "analyze: done (outputs in $ANA, $VAR/analysis, $COOP/analysis, logs in $OUT/analyze_*.txt)"
  return $rc
}

# ---------------------------------------------------------------------------------------------
if [ "${1:-}" = status ]; then status; exit 0; fi

STEPS=("$@")
[ ${#STEPS[@]} -eq 0 ] && STEPS=(check campaign variants coop analyze)

# one driver at a time (flock on Linux; skipped where unavailable)
if command -v flock >/dev/null; then
  exec 9>"$OUT/.lock"
  flock -n 9 || { echo "another run_rerun_sps.sh is active (lock $OUT/.lock)"; exit 1; }
fi
exec > >(tee -a "$OUT/stdout.log") 2>&1

log "=== start: steps ${STEPS[*]}, jobs $JOBS, tag $TAG"
T0=$(date +%s)
for s in "${STEPS[@]}"; do
  t=$(date +%s)
  log "--- step $s"
  case "$s" in
    build) step_build ;;
    check) step_check ;;
    campaign) step_campaign ;;
    variants) step_variants ;;
    coop) step_coop ;;
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
