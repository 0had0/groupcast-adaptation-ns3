#!/usr/bin/env bash
# regen-patches.sh — regenerate all patches and tarballs from current submodule working trees.
# Run from the project root.  No commits needed inside submodules — working-tree diffs are the patch.
#
# Usage:
#   scripts/regen-patches.sh           # regenerate everything
#   scripts/regen-patches.sh --check   # exit 1 if any patch/tarball would change (CI guard)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CHECK_MODE=0
[[ "${1:-}" == "--check" ]] && CHECK_MODE=1

CHANGED=0

log()  { echo "[regen-patches] $*"; }
warn() { echo "[regen-patches] WARN: $*" >&2; }

# ---------------------------------------------------------------------------
# Helper: write a git diff patch for tracked-file mods in a submodule.
# If the diff is empty, remove the patch file (no stale empties).
# ---------------------------------------------------------------------------
regen_patch() {
    local sub="$1"          # path relative to ROOT
    local out="$2"          # output patch file relative to ROOT
    local paths=("${@:3}")  # optional specific paths to diff; empty = whole tree

    if [[ ! -d "$ROOT/$sub" ]]; then
        warn "Submodule $sub not found — skipping."
        return
    fi

    local tmp
    tmp="$(mktemp)"
    if [[ ${#paths[@]} -gt 0 ]]; then
        git -C "$ROOT/$sub" diff HEAD -- "${paths[@]}" > "$tmp"
    else
        git -C "$ROOT/$sub" diff HEAD > "$tmp"
    fi

    if [[ ! -s "$tmp" ]]; then
        # Empty diff
        if [[ -f "$ROOT/$out" ]]; then
            if [[ $CHECK_MODE -eq 1 ]]; then
                log "CHECK FAIL: $out should be deleted (diff is now empty)"
                CHANGED=1
            else
                rm -f "$ROOT/$out"
                log "$out removed (diff is empty)"
                CHANGED=1
            fi
        fi
        rm -f "$tmp"
        return
    fi

    if [[ -f "$ROOT/$out" ]] && diff -q "$tmp" "$ROOT/$out" > /dev/null 2>&1; then
        log "$out unchanged"
        rm -f "$tmp"
        return
    fi

    if [[ $CHECK_MODE -eq 1 ]]; then
        log "CHECK FAIL: $out is stale"
        CHANGED=1
        rm -f "$tmp"
        return
    fi

    mkdir -p "$(dirname "$ROOT/$out")"
    mv "$tmp" "$ROOT/$out"
    log "$out updated"
    CHANGED=1
}

# ---------------------------------------------------------------------------
# Helper: create a reproducible tarball for an untracked directory.
# Uses --sort=name and forces mtime=epoch so content is the only variable.
# ---------------------------------------------------------------------------
regen_tarball() {
    local sub="$1"      # submodule path
    local reldir="$2"   # directory inside submodule (e.g. src/traci)
    local out="$3"      # output .tar.gz relative to ROOT

    if [[ ! -d "$ROOT/$sub/$reldir" ]]; then
        warn "$sub/$reldir not found — skipping tarball."
        return
    fi

    local tmp
    tmp="$(mktemp).tar.gz"
    # macOS tar doesn't support --sort or --mtime, use find+sort pipeline with GNU tar if available
    if command -v gtar &> /dev/null; then
        gtar --sort=name --mtime='@0' --owner=0 --group=0 --numeric-owner \
             -czf "$tmp" -C "$ROOT/$sub" "$reldir"
    else
        # Fallback: macOS bsdtar — no --sort/--mtime; use reproducible workaround via find
        (cd "$ROOT/$sub" && find "$reldir" -print0 | sort -z | \
            tar --no-mac-metadata -czf "$tmp" --null -T -)
    fi

    if [[ -f "$ROOT/$out" ]] && diff -q "$tmp" "$ROOT/$out" > /dev/null 2>&1; then
        log "$out unchanged"
        rm -f "$tmp"
        return
    fi

    if [[ $CHECK_MODE -eq 1 ]]; then
        log "CHECK FAIL: $out is stale"
        CHANGED=1
        rm -f "$tmp"
        return
    fi

    mkdir -p "$(dirname "$ROOT/$out")"
    mv "$tmp" "$ROOT/$out"
    log "$out updated"
    CHANGED=1
}

# ---------------------------------------------------------------------------
# Helper: write manifest of sha256 hashes for a directory (for setup.sh safeguard).
# ---------------------------------------------------------------------------
regen_manifest() {
    local sub="$1"
    local dirs=("${@:2}")   # dirs inside $sub to hash
    local out="patches/ns-3-dev/extras/manifest.sha256"

    local tmp
    tmp="$(mktemp)"
    for d in "${dirs[@]}"; do
        if [[ -d "$ROOT/$sub/$d" ]]; then
            (cd "$ROOT/$sub" && find "$d" -type f | sort | xargs shasum -a 256 2>/dev/null) >> "$tmp"
        fi
    done

    if [[ -f "$ROOT/$out" ]] && diff -q "$tmp" "$ROOT/$out" > /dev/null 2>&1; then
        log "$out unchanged"
        rm -f "$tmp"
        return
    fi

    if [[ $CHECK_MODE -eq 1 ]]; then
        log "CHECK FAIL: $out is stale"
        CHANGED=1
        rm -f "$tmp"
        return
    fi

    mv "$tmp" "$ROOT/$out"
    log "$out updated"
    CHANGED=1
}

# ---------------------------------------------------------------------------
# Regenerate patches
# ---------------------------------------------------------------------------
log "Regenerating patches..."

regen_patch "netanim"          "patches/netanim/local.patch"
regen_patch "sumo"             "patches/sumo/local.patch"
regen_patch "ns-3-dev"         "patches/ns-3-dev/local.patch"
regen_patch "ns-3-dev/contrib/nr" "patches/nr/local.patch"

# ---------------------------------------------------------------------------
# Regenerate tarballs + manifest for untracked ns-3-dev contributions
# ---------------------------------------------------------------------------
log "Regenerating tarballs for ns-3-dev contributions..."

regen_tarball "ns-3-dev" "src/traci"             "patches/ns-3-dev/extras/traci.tar.gz"
regen_tarball "ns-3-dev" "src/vehicle-visualizer" "patches/ns-3-dev/extras/vehicle-visualizer.tar.gz"
regen_manifest "ns-3-dev" "src/traci" "src/vehicle-visualizer"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
if [[ $CHECK_MODE -eq 1 ]]; then
    if [[ $CHANGED -eq 1 ]]; then
        echo ""
        echo "CHECK FAILED: patches are stale. Run scripts/regen-patches.sh and commit the result."
        exit 1
    else
        log "All patches are up-to-date."
    fi
else
    if [[ $CHANGED -eq 1 ]]; then
        echo ""
        echo "Done. Stage the updated patches in the parent repo:"
        echo "  git add patches/ && git commit -m 'chore: update patches'"
    else
        log "Nothing changed."
    fi
fi
