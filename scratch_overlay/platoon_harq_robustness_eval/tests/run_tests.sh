#!/bin/bash
# run_tests.sh -- launch script of the platoon_harq_robustness_eval test suite (TEST_PLAN.md section 10).
#
# Usage: tests/run_tests.sh [--profile quick|pilot|reduced|full] [--jobs N] [--stage STAGE[,STAGE...]] [--only REGEX] [--resume] [--no-notebook]
# Stages (default all, in this order):
#   env       unset MAGIC_HELPER_ID / NUM_MEMBERS; print machine, git revisions, the NR diff stat; assert BASE_FLAGS == run_retx.py
#   build     ninja: production binary, every test executable, the NR examples used by A03 (never ./ns3 configure)
#   unit      U-type tests: A01, A02, D01 + the existing test_helper_select
#   synth     DET first (then A06), then the other synthetic run-sets via campaign/run_matrix.py
#   campaign  CORE and SPSDYN of the profile via campaign/run_matrix.py (resumable, gzip, RSS log)
#   check     all W and C tests (A03-A06, B*, C*, D02, D03, E*, F*, G*), JSON into tests/results/
#   notebook  python3 tests/notebook/build_notebook.py && jupyter nbconvert --to notebook --execute --inplace tests/TEST_RESULTS.ipynb
# Exit code: 0 if no FAIL, 1 otherwise; prints a summary table (id, status, #checks FAIL/XFAIL/PASS/INFO/SKIP).
#
# Hard rules: no ./ns3 configure here (the optional A03b reconfigure is tests/campaign/a03b.sh, run by hand after approval); every simulation runs
# with MAGIC_HELPER_ID and NUM_MEMBERS unset (run_matrix.py wraps the binary in `env -u ...`).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
NS3="$REPO/ns-3-dev"
BIN="$NS3/build/scratch/platoon_harq_robustness_eval/tests"
PROFILE=quick
JOBS=4
STAGES="env,build,unit,synth,campaign,check,notebook"
ONLY=""
NOTEBOOK=1
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2;;
    --jobs) JOBS="$2"; shift 2;;
    --stage) STAGES="$2"; shift 2;;
    --only) ONLY="$2"; shift 2;;
    --resume) shift;;
    --no-notebook) NOTEBOOK=0; shift;;
    -h|--help) sed -n 2,17p "$0"; exit 0;;
    *) echo "unknown argument $1"; exit 2;;
  esac
done
unset MAGIC_HELPER_ID NUM_MEMBERS
export PTEST_ROOT="$HERE"
mkdir -p "$HERE/out/logs" "$HERE/results"
has_stage() { [[ ",$STAGES," == *",$1,"* ]]; }
log() { echo "[$(date +%H:%M:%S)] $*"; }
TESTS_UNIT="A01_rrc_sci_compliance A02_psfch_timing D01_kuo_objective"
TESTS_CHECK="A03_nr_examples_regression A04_patch_gating A05_env_guard A06_determinism B01_blind_workflow B02_option1_workflow B03_option2_workflow B04_feedback_binding B05_harq_process_management B06_sps_harq_interaction C01_helper_signalling C02_relay_gating C03_option2_helper_silence C04_relay_combining C05_option1_helper_nack D02_selection_invariants D03_selection_accounting E01_kpi_recompute E02_pir E03_channel_variation_per_rx E04_background_interference E05_traffic_classes E06_spectrum_usage_nodes F01_sbsps_behaviour F02_dynamic_behaviour F03_sps_vs_dynamic F04_retx_resource_constraints G01_blind_effectiveness G02_harq_upper_bound G03_spectrum_gain_prr_parity G04_option2_helper_protocol_scale G05_helper_reliability_worst_rx G06_distance_link_conditions G07_harq_tradeoffs G08_silent_helper_gap"

run_test() {
  local t="$1"
  if [ -n "$ONLY" ] && ! [[ "$t" =~ $ONLY ]]; then return 0; fi
  local exe="$BIN/ns3.42-test_${t}-default"
  [ -x "$exe" ] || { echo "missing $exe"; return 1; }
  "$exe"
}

if has_stage env; then
  { log "stage env"
    echo "machine: $(uname -a)"; echo "cpus: $(sysctl -n hw.ncpu 2>/dev/null)  mem: $(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1073741824 )) GB"
    echo "repo rev: $(git -C "$REPO" rev-parse --short HEAD)  branch: $(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
    echo "contrib/nr rev: $(git -C "$NS3/contrib/nr" rev-parse --short HEAD 2>/dev/null)"
    echo "contrib/nr diff: $(git -C "$NS3/contrib/nr" diff --stat 2>/dev/null | tail -1)"
    echo "MAGIC_HELPER_ID=${MAGIC_HELPER_ID-unset} NUM_MEMBERS=${NUM_MEMBERS-unset}"
    python3 "$HERE/campaign/runsets.py" "$PROFILE" || { echo "BASE_FLAGS assertion failed"; exit 3; }
  } 2>&1 | tee "$HERE/out/logs/env.log"
  [ "${PIPESTATUS[0]}" -eq 0 ] || exit 3
fi

if has_stage build; then
  log "stage build" | tee "$HERE/out/logs/build.log"
  TARGETS="scratch_platoon_harq_robustness_eval_platoon_harq_robustness_eval"
  for t in $TESTS_UNIT $TESTS_CHECK; do TARGETS="$TARGETS scratch_platoon_harq_robustness_eval_test_$t"; done
  TARGETS="$TARGETS scratch_platoon_harq_robustness_eval_test_helper_select sl-harq-example sl-multi-lc-example"
  ( cd "$NS3" && touch "$HERE/CMakeLists.txt" && ninja -C cmake-cache $TARGETS ) >> "$HERE/out/logs/build.log" 2>&1 || { tail -20 "$HERE/out/logs/build.log"; echo "build failed"; exit 4; }
  tail -2 "$HERE/out/logs/build.log"
fi

if has_stage unit; then
  log "stage unit" | tee "$HERE/out/logs/unit.log"
  for t in $TESTS_UNIT; do run_test "$t" >> "$HERE/out/logs/unit.log" 2>&1; echo "$t exit $?" | tee -a "$HERE/out/logs/unit.log"; done
  "$NS3/build/scratch/platoon_harq_robustness_eval/ns3.42-test_helper_select-default" >> "$HERE/out/logs/unit.log" 2>&1; echo "test_helper_select exit $?" | tee -a "$HERE/out/logs/unit.log"
fi

if has_stage synth; then
  log "stage synth" | tee "$HERE/out/logs/synth.log"
  # DET first: A06 decides whether the campaign needs diag-off duplicates
  python3 "$HERE/campaign/run_matrix.py" --runset DET --jobs "$JOBS" >> "$HERE/out/logs/synth.log" 2>&1
  run_test A06_determinism >> "$HERE/out/logs/synth.log" 2>&1
  grep -E "campaign_needs_diag_off_duplicates" "$HERE/results/A06_determinism.json" | head -1 | tee -a "$HERE/out/logs/synth.log"
  python3 "$HERE/campaign/run_matrix.py" --runset SYN_NEAR,SYN_MARGINAL,SYN_DTX,SYN_MCR,SYN_SPS,SYN_P2,SYN_HM,MAGIC,SYN_DIST --jobs "$JOBS" >> "$HERE/out/logs/synth.log" 2>&1
  tail -3 "$HERE/out/logs/synth.log"
fi

if has_stage campaign; then
  log "stage campaign (profile $PROFILE)" | tee "$HERE/out/logs/campaign.log"
  python3 "$HERE/campaign/run_matrix.py" --runset CORE,SPSDYN,RBT_RING --profile "$PROFILE" --jobs "$JOBS" >> "$HERE/out/logs/campaign.log" 2>&1
  tail -3 "$HERE/out/logs/campaign.log"
fi

if has_stage check; then
  log "stage check" | tee "$HERE/out/logs/check.log"
  for t in $TESTS_CHECK; do run_test "$t" >> "$HERE/out/logs/check.log" 2>&1; done
fi

summary() {
python3 - "$HERE/results" <<'PY'
import json, glob, sys, os
rows = []
for f in sorted(glob.glob(os.path.join(sys.argv[1], '*.json'))):
    try:
        j = json.load(open(f))
    except Exception:
        continue
    if 'checks' not in j: continue
    c = {k: 0 for k in ('FAIL', 'XFAIL', 'PASS', 'INFO', 'SKIP')}
    for ch in j['checks']: c[ch['status']] += 1
    rows.append((j['id'], j['name'], j['status'], c))
print('%-4s %-34s %-6s %5s %5s %5s %5s %5s' % ('id', 'name', 'status', 'FAIL', 'XFAIL', 'PASS', 'INFO', 'SKIP'))
bad = 0
for i, n, s, c in rows:
    print('%-4s %-34s %-6s %5d %5d %5d %5d %5d' % (i, n, s, c['FAIL'], c['XFAIL'], c['PASS'], c['INFO'], c['SKIP']))
    bad += c['FAIL']
print('total FAIL checks:', bad)
sys.exit(1 if bad else 0)
PY
}

if has_stage notebook && [ "$NOTEBOOK" -eq 1 ]; then
  log "stage notebook" | tee "$HERE/out/logs/notebook.log"
  ( cd "$HERE" && python3 notebook/build_notebook.py && python3 -m jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=900 TEST_RESULTS.ipynb ) >> "$HERE/out/logs/notebook.log" 2>&1 || { tail -15 "$HERE/out/logs/notebook.log"; echo "notebook failed"; }
fi

log "summary"
summary
exit $?
