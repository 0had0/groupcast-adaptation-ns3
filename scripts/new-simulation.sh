#!/usr/bin/env bash
# scripts/new-simulation.sh <name>
#
# Creates a new NR-V2X simulation scratch directory from the eval_template,
# registers it with the ns-3-dev build system via a symlink, and prints the
# first build/run command pair.
#
# Usage:
#   ./scripts/new-simulation.sh my_new_eval
#
# Output:
#   scratch_overlay/my_new_eval/          ← source dir (git-tracked)
#   ns-3-dev/scratch/my_new_eval         ← symlink into ns-3-dev build tree
#
# After running:
#   cd ns-3-dev
#   ./ns3 build scratch/my_new_eval/my_new_eval
#   ./ns3 run  scratch/my_new_eval/my_new_eval -- --help

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE_DIR="$REPO_ROOT/scratch_overlay/eval_template"
NS3_SCRATCH="$REPO_ROOT/ns-3-dev/scratch"

usage() {
  echo "Usage: $0 <simulation-name>"
  echo "  <simulation-name>  snake_case name, e.g. my_new_eval"
  exit 1
}

[[ $# -eq 1 ]] || usage
NAME="$1"

DEST="$REPO_ROOT/scratch_overlay/$NAME"
LINK="$NS3_SCRATCH/$NAME"

if [[ -e "$DEST" ]]; then
  echo "ERROR: $DEST already exists. Aborting." >&2
  exit 1
fi

# ── Copy template ────────────────────────────────────────────────────────────
cp -r "$TEMPLATE_DIR" "$DEST"

# Rename eval_template.cc → <name>.cc
mv "$DEST/eval_template.cc" "$DEST/${NAME}.cc"

# Patch the .cc: update the comment header and the NS_LOG_COMPONENT name.
perl -i \
  -pe "s|eval_template/eval_template\.cc|${NAME}/${NAME}.cc|g;" \
  -pe "s|NS_LOG_COMPONENT_DEFINE\(\"EvalTemplate\"\)|NS_LOG_COMPONENT_DEFINE(\"${NAME}\")|g;" \
  -pe "s|kpiDb = \"eval_template\"|kpiDb = \"${NAME}\"|g;" \
  -pe "s|Results → \" << dbPath|Results → \" << dbPath|g;" \
  "$DEST/${NAME}.cc"

# Patch CMakeLists.txt: replace eval_template with the new name everywhere.
perl -i \
  -pe "s|eval_template|${NAME}|g;" \
  "$DEST/CMakeLists.txt"

# ── Symlink into ns-3-dev/scratch ────────────────────────────────────────────
if [[ -L "$LINK" ]]; then
  echo "WARNING: symlink $LINK already exists — removing old link."
  rm "$LINK"
fi
ln -sfn "../../scratch_overlay/${NAME}" "$LINK"

echo ""
echo "✅  New simulation created: $NAME"
echo ""
echo "Source directory : $DEST"
echo "ns-3 scratch link: $LINK"
echo ""
echo "Next steps:"
echo "  cd $REPO_ROOT/ns-3-dev"
echo "  ./ns3 build scratch/${NAME}/${NAME}"
echo "  ./ns3 run  scratch/${NAME}/${NAME} -- --help"
echo ""
echo "Module reference: scratch_overlay/common/README.md"
