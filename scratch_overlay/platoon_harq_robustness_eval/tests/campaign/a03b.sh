#!/bin/bash
# a03b.sh -- TEST_PLAN decision D-7 (A03b): enable the stock NR test suites and run them.  NOT run automatically.
#
# THIS SCRIPT RECONFIGURES THE SHARED CMAKE CACHE (ns-3-dev/cmake-cache).  Run it only when no build and no simulation is running, after the
# user has approved it.  Procedure (TEST_PLAN section 0.1 D-7):
#   1. snapshot  grep -E '^(NS3_|CMAKE_BUILD_TYPE)' CMakeCache.txt   before and after; only  NS3_TESTS:BOOL=OFF -> ON may change (else stop)
#   2. rebuild the production binary and the tests
#   3. re-run the DET run-set and require byte-identical outputs against the pre-reconfigure DET (kept as out/DET_pre)
#   4. run nr-test-sidelink-harq, nr-sl-sensing, nr-sl-sci-headers and the sl-multi-lc-* suites; write out/A03b/summary.csv (read by A03)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
TESTS="$(cd "$HERE/.." && pwd)"
REPO="$(cd "$TESTS/../../.." && pwd)"
OUT="$TESTS/out/A03b"
mkdir -p "$OUT"
if pgrep -f "ns3.42-.*-default|ninja" > /dev/null; then echo "a build or a simulation is running: abort"; exit 2; fi
cd "$REPO/ns-3-dev" || exit 2
grep -E '^(NS3_|CMAKE_BUILD_TYPE)' cmake-cache/CMakeCache.txt | sort > "$OUT/cache_before.txt"
[ -d "$TESTS/out/DET" ] && [ ! -d "$TESTS/out/DET_pre" ] && mv "$TESTS/out/DET" "$TESTS/out/DET_pre"
./ns3 configure --enable-examples --disable-werror --enable-tests > "$OUT/configure.log" 2>&1 || { echo "configure failed"; exit 3; }
grep -E '^(NS3_|CMAKE_BUILD_TYPE)' cmake-cache/CMakeCache.txt | sort > "$OUT/cache_after.txt"
if ! diff <(grep -v '^NS3_TESTS' "$OUT/cache_before.txt") <(grep -v '^NS3_TESTS' "$OUT/cache_after.txt") > "$OUT/cache_diff_other.txt"; then
  echo "STOP: cache flags other than NS3_TESTS changed, see $OUT/cache_diff_other.txt"; exit 4
fi
grep '^NS3_TESTS' "$OUT/cache_after.txt" | grep -q ':BOOL=ON' || { echo "STOP: NS3_TESTS is not ON"; exit 4; }
ninja -C cmake-cache scratch_platoon_harq_robustness_eval_platoon_harq_robustness_eval test-runner > "$OUT/build.log" 2>&1 || { echo "build failed"; exit 5; }
python3 "$HERE/run_matrix.py" --runset DET --jobs 4 > "$OUT/det_rerun.log" 2>&1
python3 - "$TESTS/out/DET_pre" "$TESTS/out/DET" > "$OUT/det_compare.txt" <<'PY'
import sys, os, gzip, glob
pre, post = sys.argv[1], sys.argv[2]
def rd(p):
    if os.path.exists(p): return open(p, 'rb').read()
    if os.path.exists(p + '.gz'): return gzip.open(p + '.gz', 'rb').read()
    return None
bad = 0; n = 0
for arm in sorted(os.listdir(pre)):
    d = os.path.join(pre, arm)
    if not os.path.isdir(d): continue
    for f in sorted(glob.glob(d + '/*_*.csv*')):
        base = os.path.basename(f).replace('.gz', '')
        a = rd(os.path.join(d, base)); b = rd(os.path.join(post, arm, base))
        if base.endswith('_run.csv') and a and b:
            # wall-clock columns differ legitimately
            ha, *ra = a.decode().splitlines(); hb, *rb = b.decode().splitlines()
            cols = ha.split(',')
            skip = {cols.index(c) for c in ('wall_s', 'sel_cpu_us_mean') if c in cols}
            fa = ra[0].split(','); fb = rb[0].split(',')
            same = ha == hb and all(x == y for i, (x, y) in enumerate(zip(fa, fb)) if i not in skip)
        else:
            same = (a == b)
        n += 1
        if not same: bad += 1; print('DIFF', arm, base)
print('files', n, 'different', bad)
PY
echo "suite,status,note" > "$OUT/summary.csv"
for s in nr-test-sidelink-harq nr-sl-sensing nr-sl-sci-headers sl-multi-lc-dyn-gcast-harq sl-multi-lc-dyn-gcast-no-harq sl-multi-lc-dyn-gcast-blind \
         sl-multi-lc-prio-dyn sl-multi-lc-prio-sps sl-multi-lc-prio-bcast sl-multi-lc-prio-gcast sl-multi-lc-prio-uni sl-multi-lc-rri; do
  ./test.py --no-build --suite="$s" > "$OUT/$s.log" 2>&1
  if grep -q "PASS" "$OUT/$s.log"; then st=PASS; else st=FAIL; fi
  echo "$s,$st,\"$(grep -E 'FAIL|PASS' "$OUT/$s.log" | head -1)\"" >> "$OUT/summary.csv"
done
echo done
