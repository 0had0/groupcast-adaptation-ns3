#!/usr/bin/env bash
# env.sh — activate the project shell environment.
# Source this file (do NOT execute it):
#   source env.sh   OR   . env.sh
#
# Sets up:
#   - SUMO binaries (sumo, sumo-gui, netconvert, …) from sumo/bin/
#   - NetAnim binary from netanim/build/
#   - Python .venv activated via uv

# Guard against being executed instead of sourced
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "ERROR: source this script, don't execute it."
    echo "  source env.sh"
    exit 1
fi

_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# SUMO
# ---------------------------------------------------------------------------
_SUMO_BIN="$_ROOT/sumo/bin"
if [[ -d "$_SUMO_BIN" ]]; then
    export SUMO_HOME="$_ROOT/sumo"
    export PATH="$_SUMO_BIN:$PATH"
    echo "[env] SUMO: $SUMO_HOME"
else
    echo "[env] WARNING: sumo/bin not found — run ./build.sh or build sumo first."
fi

# ---------------------------------------------------------------------------
# NetAnim
# ---------------------------------------------------------------------------
_NETANIM_BIN="$_ROOT/netanim/build"
if [[ -f "$_NETANIM_BIN/netanim" ]]; then
    export PATH="$_NETANIM_BIN:$PATH"
    echo "[env] NetAnim: $_NETANIM_BIN/netanim"
else
    echo "[env] WARNING: netanim/build/netanim not found — build netanim first (qmake && make in netanim/)."
fi

# ---------------------------------------------------------------------------
# Python .venv via uv
# ---------------------------------------------------------------------------
_VENV="$_ROOT/.venv"
if [[ ! -d "$_VENV" ]]; then
    if command -v uv &>/dev/null; then
        echo "[env] No .venv found — creating with uv..."
        uv venv "$_VENV"
    else
        echo "[env] WARNING: .venv not found and uv is not installed."
    fi
fi

if [[ -f "$_VENV/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$_VENV/bin/activate"
    echo "[env] Python venv: $VIRTUAL_ENV ($(python --version 2>&1))"
fi

unset _ROOT _SUMO_BIN _NETANIM_BIN _VENV
