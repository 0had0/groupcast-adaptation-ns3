#!/bin/bash
# Submit the genie campaign on Grid'5000 (run on the site frontend: it only calls oarsub).
#   scripts/g5k/submit.sh setup                      # build + preflight on one node (about 1 h), now
#   scripts/g5k/submit.sh campaign [--now] [--walltime H]
#        five jobs on five nodes of $CLUSTER, one per group of stages (about 1700 core-hours in all):
#          genie-load    load (900 runs)                         genie-timing  timing (810 runs)
#          genie-small   period + hmiss (240 + 900 runs)         genie-psfch1  psfch shard 1/2 (660 runs)
#                                                                genie-psfch2  psfch shard 2/2 (660 runs)
#        default: night jobs (-t night: they start at 19:00 on weekdays, or at once at night / weekends), walltime 13 h
#        (weekday nights end at 09:00: the usage policy forbids crossing it). --now: start as soon as nodes are free,
#        walltime must then end before 19:00 on a weekday (usage policy); give --walltime.
#        A job cut by its walltime loses nothing: submit the campaign again, finished runs are skipped.
#   scripts/g5k/submit.sh stages "STAGES" [--now] [--walltime H]   # one job for the given stages (e.g. "psfch2of2")
# Cluster: $CLUSTER (default paradoxe: 52 cores / node), queue: $QUEUE (default: default).
set -u
source "$(dirname "$0")/env.sh"
CLUSTER="${CLUSTER:-paradoxe}"
# OAR queue, always named: at Rennes jobs without -q land in the production (Abaca) queue (p1..p4), where the
# default-queue clusters (paradoxe, parasilo) do not exist. QUEUE=abaca with an Abaca cluster: no night / day rules,
# jobs up to the user's priority limit (p2: 96 h); the build must then be redone on that cluster (setup).
QUEUE="${QUEUE:-default}"
cd "$G5K_STUDY" || exit 2
mkdir -p out/oar
what="${1:-}"; shift || true
NOW=0; WALL=13; STAGES=""
[ "$what" = stages ] && { STAGES="$1"; shift; }
while [ $# -gt 0 ]; do
  case "$1" in
    --now) NOW=1; shift;;
    --walltime) WALL="$2"; shift 2;;
    *) echo "unknown option $1"; exit 2;;
  esac
done
sub() {   # name, walltime, command...
  local name="$1" wall="$2"; shift 2
  local t=(); [ "$NOW" = 0 ] && [ "$QUEUE" = default ] && t=(-t night)
  oarsub -q "$QUEUE" -n "$name" -p "cluster='$CLUSTER'" -l "host=1,walltime=$wall" "${t[@]}" \
    -O "out/oar/%jobid%_$name.out" -E "out/oar/%jobid%_$name.err" "$@" 2>&1 | grep -v '^\[ADMISSION RULE\]'
}
case "$what" in
  setup)
    NOW=1; sub genie-setup 1:30 "$G5K_STUDY/scripts/g5k/setup.sh";;
  campaign)
    [ -f "$G5K_OK" ] || { echo "run 'submit.sh setup' first (no $G5K_OK)"; exit 8; }
    sub genie-load   "$WALL" "$G5K_STUDY/scripts/g5k/stage.sh load"
    sub genie-timing "$WALL" "$G5K_STUDY/scripts/g5k/stage.sh timing"
    sub genie-small  "$WALL" "$G5K_STUDY/scripts/g5k/stage.sh 'period hmiss'"
    sub genie-psfch1 "$WALL" "$G5K_STUDY/scripts/g5k/stage.sh psfch1of2"
    sub genie-psfch2 "$WALL" "$G5K_STUDY/scripts/g5k/stage.sh psfch2of2";;
  stages)
    [ -f "$G5K_OK" ] || { echo "run 'submit.sh setup' first (no $G5K_OK)"; exit 8; }
    sub "genie-$(echo "$STAGES" | tr -c 'a-z0-9\n' '-')" "$WALL" "$G5K_STUDY/scripts/g5k/stage.sh '$STAGES'";;
  *) sed -n 2,18p "$0"; exit 2;;
esac
echo "check the jobs: oarstat -u     usage policy: usagepolicycheck -t     progress: scripts/monitor_genie.sh"
