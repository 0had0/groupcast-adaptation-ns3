#!/bin/bash
# Progress of the genie campaign (launch_genie.sh on ease-rtx, scripts/g5k/ on Grid'5000): done / total per stage, failures, running simulators, rate, ETA.
# Usage: monitor_genie.sh [--watch SECONDS]
cd "$(dirname "$0")/.." || exit 2   # harq_helper_study/
WATCH=0; [ "${1:-}" = "--watch" ] && WATCH="${2:-300}"
show() {
python3 - <<'EOF'
import glob, os, re, time
STAGES = [  # name, total, folders holding <run>_run.csv[.gz], stage log
    ('load', 900, ['out/v3_genie/R4_bsc_genie', 'out/v3_genie/R4_bsc_fbs_genie', 'out/v3_genie/R6_genie'], 'out/genie_load.log'),
    ('hmiss', 900, ['out/helperrx_genie/controlled/*'], 'out/genie_hmiss.log'),
    ('timing', 810, ['out/timing_genie/*'], 'out/genie_timing.log'),
    ('period', 240, ['out/psfch_genie/*'], 'out/genie_period.log'),
    ('psfch', 1320, ['out/psfch_err_genie/*'], 'out/genie_psfch*.log'),
]
now = time.time()
print(time.strftime('%F %T'))
tot_done = tot = 0
recent = 0
for name, total, dirs, log in STAGES:
    files = [f for d in dirs for f in glob.glob(os.path.join(d, '*_run.csv')) + glob.glob(os.path.join(d, '*_run.csv.gz'))]
    n = len(files)
    recent += sum(now - os.path.getmtime(f) < 3600 for f in files)
    fails = 0
    for lg in glob.glob(log):
        txt = open(lg, errors='replace').read()
        fails += len(re.findall(r'^FAIL', txt, re.M)) + len(re.findall(r'exit +-?[1-9]\d* ', txt))
    tot_done += n; tot += total
    print(f'  {name:7} {n:5d}/{total:<5d} {100 * n / total:5.1f} %   failures in log: {fails}')
print(f'  all     {tot_done:5d}/{tot:<5d} {100 * tot_done / tot:5.1f} %   finished in the last hour: {recent}', end='')
print(f'   ETA ~{(tot - tot_done) / recent:.1f} h at that rate' if recent else '')
st = 'out/genie_campaign.log'
if os.path.exists(st):
    print('  ' + '\n  '.join(open(st).read().strip().splitlines()[-3:]))
EOF
if command -v oarstat > /dev/null; then   # Grid'5000: the simulators run on the nodes of the genie-* OAR jobs
  echo "  OAR jobs:"; oarstat -u 2>/dev/null | grep -E 'genie-' | sed 's/^/    /' || echo "    none"
  echo "  home quota: $(quota 2>/dev/null | awk 'NR==4 {printf "%.1f GB used of %.0f GB (soft)", $1/1e6, $2/1e6}')"
else
  echo "  simulators running: $(pgrep -f 'ns3.42-platoon_harq_robustness_eval-.*--helperSelect=schedule' | wc -l | tr -d ' ')   disk free: $(df -h . | awk 'NR==2 {print $4}')"
fi
}
if [ "$WATCH" -gt 0 ]; then
  while true; do clear; show; sleep "$WATCH"; done
else
  show
fi
