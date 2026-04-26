#!/usr/bin/env bash
# setup.sh — assemble the full source tree from submodules + patches + tarballs.
#
# Run this ONCE after cloning the parent repo on a new machine.
# It is idempotent: safe to re-run on a machine that is already set up,
# but will ABORT if uncaptured edits are detected in any submodule
# (pass --force to discard them).
#
# Usage:
#   ./setup.sh          # normal run
#   ./setup.sh --force  # skip safeguard and overwrite any local edits
set -euo pipefail

FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

log()  { echo "[setup] $*"; }
die()  { echo "[setup] ERROR: $*" >&2; exit 1; }
ok()   { echo "[setup] OK: $*"; }

# Pinned commit SHAs
SHA_NS3="c90c13b8310a813cf4eaf67a2c90df497bbd1965"
SHA_NR="321566011c1a49ed8e722c3edc9a659604fe1dda"
SHA_NETANIM="9f8c0829512ffafe56e5ff424d72c91c10ca4e8f"
SHA_SUMO="4fc6bb35f499858cb91dde604495a4d3ef4baaef"

BRANCH_NS3="v2x-lte-dev"
BRANCH_NR="nr-v2x-dev"

# ---------------------------------------------------------------------------
# 0. Sanity checks
# ---------------------------------------------------------------------------
[[ -f "$ROOT/.gitmodules" ]] || die ".gitmodules not found — are you in the project root?"
[[ -d "$ROOT/.git" ]] || die "Not a git repository root."
command -v git >/dev/null || die "git not found."

# ---------------------------------------------------------------------------
# 1b. Safeguard: detect edits in already-initialized submodules that are NOT
#     captured in the patch files.
# ---------------------------------------------------------------------------
check_submodule_clean() {
    local sub="$1"
    local patch="$2"

    [[ -d "$ROOT/$sub/.git" ]] || return 0   # not yet initialized → skip

    if [[ -f "$ROOT/$patch" ]]; then
        # Try to reverse-apply the patch in --check mode (dry-run).
        # If it fails, the working tree has drifted beyond what the patch captures.
        if ! git -C "$ROOT/$sub" apply -R --check "$ROOT/$patch" 2>/dev/null; then
            if [[ $FORCE -eq 0 ]]; then
                echo ""
                echo "[setup] ABORT: Uncaptured edits detected in '$sub'."
                echo "        The working tree has drifted beyond what 'patches/${patch#patches/}' captures."
                echo "        Run:  scripts/regen-patches.sh && git add patches/ && git commit"
                echo "        Then re-run setup.sh.  Or pass --force to discard the edits."
                exit 1
            else
                log "WARNING: uncaptured edits in $sub — overwriting (--force)."
            fi
        fi
    else
        # No patch file — check for any working-tree diff at all.
        local diff
        diff="$(git -C "$ROOT/$sub" diff HEAD 2>/dev/null)"
        if [[ -n "$diff" ]]; then
            if [[ $FORCE -eq 0 ]]; then
                echo ""
                echo "[setup] ABORT: $sub has uncommitted changes but no patch file captures them."
                echo "        Run:  scripts/regen-patches.sh && git add patches/ && git commit"
                echo "        Then re-run setup.sh.  Or pass --force to discard."
                exit 1
            else
                log "WARNING: uncaptured edits in $sub — overwriting (--force)."
            fi
        fi
    fi
}

check_extras_clean() {
    local manifest="$ROOT/patches/ns-3-dev/extras/manifest.sha256"
    [[ -d "$ROOT/ns-3-dev/.git" ]] || return 0
    [[ -f "$manifest" ]] || return 0

    local current
    current="$(cd "$ROOT/ns-3-dev" && \
        find src/traci src/vehicle-visualizer -type f 2>/dev/null | sort | \
        xargs shasum -a 256 2>/dev/null || true)"

    local stored
    stored="$(cat "$manifest")"

    if [[ "$current" != "$stored" ]]; then
        if [[ $FORCE -eq 0 ]]; then
            echo ""
            echo "[setup] ABORT: ns-3-dev/src/traci or src/vehicle-visualizer has changed"
            echo "        beyond what the tarballs in patches/ns-3-dev/extras/ capture."
            echo "        Run:  scripts/regen-patches.sh && git add patches/ && git commit"
            echo "        Then re-run setup.sh.  Or pass --force to discard."
            exit 1
        else
            log "WARNING: uncaptured changes in ns-3-dev untracked dirs — overwriting (--force)."
        fi
    fi
}

log "Checking for uncaptured edits in already-initialized submodules..."
check_submodule_clean "netanim"          "patches/netanim/local.patch"
check_submodule_clean "sumo"             "patches/sumo/local.patch"
check_submodule_clean "ns-3-dev"         "patches/ns-3-dev/local.patch"
check_submodule_clean "ns-3-dev/contrib/nr" "patches/nr/local.patch"
check_extras_clean
log "Safeguard passed."

# ---------------------------------------------------------------------------
# 2. Initialize outer submodules (not recursive — nr is handled separately)
# ---------------------------------------------------------------------------
log "Initializing outer submodules (netanim, sumo, ns-3-dev)..."
git submodule update --init -- netanim sumo ns-3-dev
ok "Outer submodules checked out."

# ---------------------------------------------------------------------------
# 3. Pin ns-3-dev to the correct branch + commit
# ---------------------------------------------------------------------------
log "Checking out ns-3-dev on branch $BRANCH_NS3 at $SHA_NS3..."
git -C "$ROOT/ns-3-dev" fetch origin "$BRANCH_NS3" 2>/dev/null || true
git -C "$ROOT/ns-3-dev" checkout "$BRANCH_NS3" 2>/dev/null || \
    git -C "$ROOT/ns-3-dev" checkout -b "$BRANCH_NS3" --track "origin/$BRANCH_NS3" 2>/dev/null || true
git -C "$ROOT/ns-3-dev" reset --hard "$SHA_NS3"
ok "ns-3-dev at $(git -C "$ROOT/ns-3-dev" rev-parse --short HEAD) on $(git -C "$ROOT/ns-3-dev" rev-parse --abbrev-ref HEAD)"

# ---------------------------------------------------------------------------
# 4. Clone / update ns-3-dev/contrib/nr directly.
#    (Cannot be a parent gitlink because ns-3-dev/ is itself a gitlink,
#     which blocks further index entries under that path.  setup.sh owns
#     the pinning instead.)
# ---------------------------------------------------------------------------
NR_DIR="$ROOT/ns-3-dev/contrib/nr"
NR_URL="https://gitlab.com/cttc-lena/nr.git"
log "Setting up ns-3-dev/contrib/nr..."

if [[ -d "$NR_DIR/.git" ]]; then
    log "  contrib/nr already cloned — fetching..."
    git -C "$NR_DIR" fetch origin 2>/dev/null || true
else
    # Remove any non-git content left by ns-3-dev checkout
    rm -rf "$NR_DIR"
    mkdir -p "$ROOT/ns-3-dev/contrib"
    log "  Cloning $NR_URL..."
    git clone "$NR_URL" "$NR_DIR"
fi

# Silence dirty-status noise in ns-3-dev.
# On a fresh submodule clone, ns-3-dev/.git is a *file* pointing to the parent's
# .git/modules/ns-3-dev/ — resolve the real git dir with git rev-parse.
NS3_GIT_DIR="$(git -C "$ROOT/ns-3-dev" rev-parse --git-dir)"
# git rev-parse returns a relative path when inside a worktree; make it absolute
[[ "$NS3_GIT_DIR" = /* ]] || NS3_GIT_DIR="$ROOT/ns-3-dev/$NS3_GIT_DIR"
EXCLUDE="$NS3_GIT_DIR/info/exclude"
mkdir -p "$(dirname "$EXCLUDE")"
grep -qF "contrib/nr" "$EXCLUDE" 2>/dev/null || echo "contrib/nr" >> "$EXCLUDE"

# Pin to pinned branch + commit
git -C "$NR_DIR" checkout "$BRANCH_NR" 2>/dev/null || \
    git -C "$NR_DIR" checkout -b "$BRANCH_NR" --track "origin/$BRANCH_NR" 2>/dev/null || true
git -C "$NR_DIR" reset --hard "$SHA_NR"
ok "ns-3-dev/contrib/nr at $(git -C "$NR_DIR" rev-parse --short HEAD) on $(git -C "$NR_DIR" rev-parse --abbrev-ref HEAD)"

# ---------------------------------------------------------------------------
# 5. Pin outer submodules to exact commits and apply patches
# ---------------------------------------------------------------------------
apply_patch() {
    local sub="$1"
    local patch="$ROOT/$2"
    [[ -f "$patch" ]] || return 0
    git -C "$ROOT/$sub" apply --check "$patch" || die "Patch check failed for $sub — see above."
    git -C "$ROOT/$sub" apply "$patch"
    ok "Applied patch to $sub"
}

log "Pinning netanim at $SHA_NETANIM..."
git -C "$ROOT/netanim" reset --hard "$SHA_NETANIM"
apply_patch "netanim" "patches/netanim/local.patch"

log "Pinning sumo at $SHA_SUMO..."
git -C "$ROOT/sumo" reset --hard "$SHA_SUMO"
apply_patch "sumo" "patches/sumo/local.patch"

log "Applying ns-3-dev tracked-file patch (if any)..."
apply_patch "ns-3-dev" "patches/ns-3-dev/local.patch"

log "Applying nr patch..."
apply_patch "ns-3-dev/contrib/nr" "patches/nr/local.patch"

# ---------------------------------------------------------------------------
# 6. Extract ns-3-dev untracked-directory contributions from tarballs
# ---------------------------------------------------------------------------
log "Extracting ns-3-dev contribution tarballs..."
for tarball in "$ROOT/patches/ns-3-dev/extras/"*.tar.gz; do
    [[ -f "$tarball" ]] || continue
    tar -xzf "$tarball" -C "$ROOT/ns-3-dev"
    ok "Extracted $(basename "$tarball") into ns-3-dev/"
done

# ---------------------------------------------------------------------------
# 7. Symlink scratch_overlay dirs into ns-3-dev/scratch/
# ---------------------------------------------------------------------------
log "Linking scratch_overlay into ns-3-dev/scratch/..."
for src in "$ROOT/scratch_overlay"/*/; do
    name="$(basename "$src")"
    target="$ROOT/ns-3-dev/scratch/$name"
    rel="../../scratch_overlay/$name"  # relative from ns-3-dev/scratch/

    if [[ -L "$target" ]]; then
        # Already a symlink — check it points to the right place
        current="$(readlink "$target")"
        if [[ "$current" == "$rel" ]]; then
            log "  $name: symlink already correct"
            continue
        else
            log "  $name: updating symlink ($current → $rel)"
            rm "$target"
        fi
    elif [[ -d "$target" ]]; then
        die "ns-3-dev/scratch/$name is a real directory — cannot replace with symlink. Remove it manually."
    fi

    ln -s "$rel" "$target"
    ok "  ns-3-dev/scratch/$name → $rel"
done

# ---------------------------------------------------------------------------
# 8. Install parent-repo pre-commit hook
# ---------------------------------------------------------------------------
log "Configuring git hooks path..."
git -C "$ROOT" config core.hooksPath scripts/hooks
ok "git hooks path set to scripts/hooks"

# ---------------------------------------------------------------------------
# 9. Verification block
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo " VERIFICATION"
echo "============================================================"

check_sha() {
    local sub="$1" expected="$2"
    local actual
    actual="$(git -C "$ROOT/$sub" rev-parse HEAD)"
    if [[ "$actual" == "$expected" ]]; then
        echo "  PASS  $sub  @ ${actual:0:12}"
    else
        echo "  FAIL  $sub  expected ${expected:0:12} got ${actual:0:12}"
    fi
}

check_sha "netanim"              "$SHA_NETANIM"
check_sha "sumo"                 "$SHA_SUMO"
check_sha "ns-3-dev"             "$SHA_NS3"
check_sha "ns-3-dev/contrib/nr"  "$SHA_NR"

echo ""
echo "  ns-3-dev branch: $(git -C "$ROOT/ns-3-dev" rev-parse --abbrev-ref HEAD)"
echo "  nr branch:       $(git -C "$ROOT/ns-3-dev/contrib/nr" rev-parse --abbrev-ref HEAD)"
echo ""

for name in groupcast_eval adaptive-phy-eval; do
    lnk="$ROOT/ns-3-dev/scratch/$name"
    if [[ -L "$lnk" ]]; then
        echo "  PASS  ns-3-dev/scratch/$name → $(readlink "$lnk")"
    else
        echo "  FAIL  ns-3-dev/scratch/$name is not a symlink"
    fi
done

for d in src/traci src/vehicle-visualizer; do
    if [[ -d "$ROOT/ns-3-dev/$d" ]]; then
        echo "  PASS  ns-3-dev/$d exists"
    else
        echo "  FAIL  ns-3-dev/$d missing"
    fi
done

echo ""
echo "Setup complete.  To build:"
echo "  ./build.sh"
