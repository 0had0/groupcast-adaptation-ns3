#!/bin/bash
# Grid'5000 stage job: runs genie stages on the reserved node with one simulation per core.
#   scripts/g5k/stage.sh "load"            scripts/g5k/stage.sh "period hmiss"        scripts/g5k/stage.sh "psfch1of2"
# Submitted by scripts/g5k/submit.sh. Refuses to start unless setup.sh passed on this cluster (out/g5k_preflight_ok):
# the binary is built with native optimizations for the setup node's CPU. Resumable: finished runs are skipped, so a
# job cut by its walltime is simply submitted again.
set -u
source "$(dirname "$0")/env.sh"
STAGES="$1"
CLUSTER=$(hostname | cut -d- -f1)
[ -f "$G5K_OK" ] || { echo "no $G5K_OK: run scripts/g5k/setup.sh first"; exit 8; }
read -r OKC _ < "$G5K_OK"
[ "$OKC" = "$CLUSTER" ] || { echo "built and checked on $OKC, this node is $CLUSTER: run on $OKC"; exit 8; }
cd "$G5K_STUDY" || exit 2
echo "$(date '+%F %T') job ${OAR_JOB_ID:-?} on $(hostname) ($(nproc) cores): stages [$STAGES]" >> out/genie_campaign.log
bash scripts/genie_stages.sh "$(nproc)" "$STAGES" >> out/genie_campaign.log 2>&1
