#!/bin/bash
# Grid'5000 setup job (run on a reserved node, never on the frontend): configure + build the simulator (optimized,
# native for this node's CPU: run the campaign on the same cluster), report the node environment, run the preflight.
#   oarsub -p paradoxe -l host=1,walltime=1:30 -O out/oar/%jobid%.out -E out/oar/%jobid%.err scripts/g5k/setup.sh
# The preflight: R6_genie, 3 routes, 0 cars, seed 1 must reproduce out/genie_preflight_ref (laptop runs) bit for bit
# (every _run.csv column except wall time). On success it writes out/g5k_preflight_ok (cluster, commit, date); the
# stage jobs refuse to start without it.
set -u
source "$(dirname "$0")/env.sh"
CLUSTER=$(hostname | cut -d- -f1)
echo "== node $(hostname) ($(nproc) cores, $(free -g | awk '/Mem/ {print $2}') GB), $(cat /etc/debian_version), $(g++ --version | head -1)"
rm -f "$G5K_OK"
cd "$G5K_REPO/ns-3-dev" || exit 2
echo "== configure $(date '+%F %T')"
./ns3 configure --build-profile=optimized --disable-werror --disable-examples --disable-tests -- \
  -DCMAKE_PREFIX_PATH="$G5K_DEPS/usr" -DGSL_ROOT_DIR="$G5K_DEPS/usr" \
  -DSQLite3_INCLUDE_DIR="$G5K_DEPS/usr/include" -DSQLite3_LIBRARY=/usr/lib/x86_64-linux-gnu/libsqlite3.so.0 \
  > "$G5K_STUDY/out/g5k_configure.log" 2>&1 || { tail -30 "$G5K_STUDY/out/g5k_configure.log"; echo "configure FAILED"; exit 3; }
grep -E 'Eigen|GNU Scientific|SQLite|Build profile|Native' "$G5K_STUDY/out/g5k_configure.log" | head -10
echo "== build $(date '+%F %T')"
ninja -C cmake-cache -j "$(nproc)" scratch_platoon_harq_robustness_eval_platoon_harq_robustness_eval \
  scratch_platoon_harq_robustness_eval_test_helper_select > "$G5K_STUDY/out/g5k_build.log" 2>&1 \
  || { grep -E 'error|FAILED' -A3 "$G5K_STUDY/out/g5k_build.log" | head -40; echo "build FAILED"; exit 4; }
BIN=build/scratch/platoon_harq_robustness_eval/ns3.42-platoon_harq_robustness_eval-optimized
ls -la "$BIN" || exit 4
echo "== libraries not found:"; ldd "$BIN" | grep 'not found' || echo "   none"
echo "== unit tests $(date '+%F %T')"
(cd /tmp && "$G5K_REPO/ns-3-dev/build/scratch/platoon_harq_robustness_eval/ns3.42-test_helper_select-optimized" | tail -2) || { echo "unit tests FAILED"; exit 5; }
cd "$G5K_STUDY" || exit 2
echo "== preflight $(date '+%F %T')"
rm -rf out/genie_preflight
python3 scripts/study.py run --profile v3_genie --seeds 1 --only '^R6_genie/(hwy|rbt|rbtE)_ivd30_periodic1tb_none_s1$' \
  --jobs 3 --out out/genie_preflight > out/genie_preflight.log 2>&1 || { tail -5 out/genie_preflight.log; echo "preflight runs FAILED"; exit 6; }
python3 - <<'EOF' || { echo "preflight FAILED: this build does not reproduce the laptop's genie runs"; exit 6; }
import glob, os, sys
import pandas as pd
bad = 0
refs = sorted(glob.glob('out/genie_preflight_ref/*_run.csv'))
if len(refs) != 3:
    sys.exit('need 3 references in out/genie_preflight_ref')
for ref in refs:
    a, b = pd.read_csv(ref), pd.read_csv(os.path.join('out/genie_preflight/R6_genie', os.path.basename(ref)))
    d = [c for c in a.columns if c in b and c not in ('wall_s', 'sel_cpu_us_mean') and str(a[c][0]) != str(b[c][0])]
    print(f"  {os.path.basename(ref)}: {'identical' if not d else 'DIFFERS in ' + ', '.join(d[:10])}")
    bad += bool(d)
sys.exit(1 if bad else 0)
EOF
echo "$CLUSTER $(git -C "$G5K_REPO" rev-parse --short HEAD 2>/dev/null || echo no-git) $(date '+%F %T')" > "$G5K_OK"
echo "== preflight passed: $(cat "$G5K_OK")"
