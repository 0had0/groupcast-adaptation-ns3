#!/bin/bash
# Grid'5000 bootstrap, on the site frontend (file operations only: git and tar, no compilation):
#   1. clone the repository (or fast-forward an existing clone) on branch coop-arms into ~/groupcast-adaptation-ns3
#   2. ./setup.sh --minimal: ns-3-dev + nr pinned and patched, scratch links for this study only (no sumo / netanim)
#   3. unpack the campaign inputs (out/ is not in git): v3 command templates, genie schedules, preflight references
# Usage (the script is in the repo, so the first time fetch it from GitHub or run the three steps by hand):
#   bash bootstrap.sh [~/genie_inputs_g5k.tar.gz]
set -eu
REPO_URL="${REPO_URL:-https://github.com/0had0/groupcast-adaptation-ns3.git}"
BRANCH="${BRANCH:-coop-arms}"
ROOT="${G5K_REPO:-$HOME/groupcast-adaptation-ns3}"
INPUTS="${1:-$HOME/genie_inputs_g5k.tar.gz}"
if [ -d "$ROOT/.git" ]; then
  git -C "$ROOT" fetch origin "$BRANCH"
  git -C "$ROOT" checkout "$BRANCH"
  git -C "$ROOT" merge --ff-only "origin/$BRANCH"
else
  [ -e "$ROOT" ] && { echo "$ROOT exists and is not a git clone: move it away first"; exit 2; }
  git clone --branch "$BRANCH" "$REPO_URL" "$ROOT"
fi
echo "repository at $(git -C "$ROOT" rev-parse --short HEAD) ($BRANCH)"
(cd "$ROOT" && ./setup.sh --minimal)
STUDY="$ROOT/scratch_overlay/platoon_harq_robustness_eval/harq_helper_study"
mkdir -p "$STUDY/out"
if [ -f "$INPUTS" ]; then
  tar xzf "$INPUTS" -C "$STUDY/out"
  echo "inputs: $(ls "$STUDY"/out/genie_probe_all/R3_probe/*_genie.csv | wc -l) schedules, $(ls "$STUDY"/out/v3/R6/*.cmd.json | wc -l) R6 templates, $(ls "$STUDY"/out/genie_preflight_ref | wc -l) preflight references"
else
  echo "no inputs archive at $INPUTS: copy genie_inputs_g5k.tar.gz there and re-run"; exit 3
fi
echo "next: cd $STUDY && scripts/g5k/submit.sh setup"
