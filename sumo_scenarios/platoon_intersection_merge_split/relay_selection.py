#!/usr/bin/env python3
"""Per-time-step relay selection for the V2V groupcast scenario.

For every simulation time `t`, this script reads the per-pair link
classification from `distances_classified.csv` (produced by
`build_distance_classification.py`) and selects the **best relay** for
the leader (`v0`, `node_id = 0`) to use when forwarding its groupcast.

A vehicle is eligible to be the relay iff:
  * it is a groupcast recipient at this time (active, not departed
    leaver, not the leader itself);
  * its leader-link is LOS or NLOSv (NLOS is excluded).

Among the eligible set the script picks the vehicle with the highest
SNR to the leader (tie-broken by preferring LOS over NLOSv, then by
ascending node id).

Output:
  * `relay_selection.csv`         one row per time-step
  * `relay_selection_summary.txt` human-readable summary

Algorithm and edge cases are documented in `relay_selection.md`.
"""
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
CSV_IN       = HERE / "distances_classified.csv"
CSV_OUT      = HERE / "relay_selection.csv"
SUMMARY_OUT  = HERE / "relay_selection_summary.txt"

# ── Scenario constants (mirror the SUMO route) ──────────────────────────
LEADER_ID = 0                       # v0
LEAVERS   = frozenset({1, 3, 6})    # v1, v3, v6 — peel off east at J2
JOINERS   = frozenset({8, 9})       # j0, j1

# Leavers "have departed" once they are on the east leg, well past J2.
# J2 is at (300, 200); the leaver exit lane heads east at y ≈ 198.4.
# We use an x-threshold rather than y because the leaver's lane sits
# at y=198.4 — the same y as the leader's east-bound approach — so a
# y-cutoff cannot disambiguate.
LEAVER_DEPARTED_X = 310.0   # > 300 = past the J2 corner

# Off-map clamp — vehicles that have left the network entirely.
X_MAX, Y_MAX = 510.0, 1010.0

# Consecutive-frame tolerance for grouping gap runs (seconds).
# 100 ms frame interval + a small float-rounding margin.
FRAME_DT = 0.1
DT_TOL   = 0.15


# ── Eligibility predicate ───────────────────────────────────────────────
def is_recipient(nid: int, x: float, y: float) -> bool:
    """Return True iff `nid` at (x, y) should receive the leader's
    groupcast at the current time-step.

    Filtering rules (see `relay_selection.md` for justification):
      * nid == LEADER_ID    → False   (no self-relay)
      * off-map             → False   (x > X_MAX or y > Y_MAX)
      * departed leaver     → False   (x > LEAVER_DEPARTED_X)
      * otherwise           → True
    """
    if nid == LEADER_ID:
        return False
    if x > X_MAX or y > Y_MAX or x < -5 or y < -5:
        return False
    if nid in LEAVERS and x > LEAVER_DEPARTED_X:
        return False
    return True


# ── Relay selection per time-step ───────────────────────────────────────
def select_relay_at_time(t: float, df_t: pd.DataFrame) -> dict:
    """Return one row's worth of relay-selection results for time `t`.

    `df_t` is the subset of `distances_classified.csv` for this time.
    """
    # Active vehicles at time t (use either tx or rx side — the trace is
    # square, but be defensive in case a vehicle only appears on one side).
    rx_view = df_t[["rx", "rx_x", "rx_y"]].drop_duplicates(subset=["rx"])
    tx_view = df_t[["tx", "tx_x", "tx_y"]].drop_duplicates(subset=["tx"])
    veh = {}
    for _, r in rx_view.iterrows():
        veh[int(r["rx"])] = (float(r["rx_x"]), float(r["rx_y"]))
    for _, r in tx_view.iterrows():
        nid = int(r["tx"])
        if nid not in veh:
            veh[nid] = (float(r["tx_x"]), float(r["tx_y"]))

    n_active = len(veh)

    # Pre-index the leader's outgoing rows for O(1) lookup.  If the
    # leader has already left the scenario the lookup table is empty
    # and we report no groupcast at all (no need to relay).
    leader_rows = df_t[df_t.tx == LEADER_ID].set_index("rx")
    leader_active = LEADER_ID in veh

    candidates = []        # list of {id, link, snr_db, distance_m}
    n_recipients = 0

    if leader_active:
        for nid, (x, y) in veh.items():
            if not is_recipient(nid, x, y):
                continue
            n_recipients += 1
            if nid not in leader_rows.index:
                continue
            row = leader_rows.loc[nid]
            # The slice may yield one row, but be defensive.
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            link = str(row["link"])
            if link not in ("LOS", "NLOSv"):
                continue                          # NLOS → not eligible
            candidates.append({
                "id":         int(nid),
                "link":       link,
                "snr_db":     float(row["snr_db"]),
                "distance_m": float(row["distance_m"]),
            })

    # Sort: highest SNR first; tie-break: LOS over NLOSv; then by id.
    candidates.sort(key=lambda c: (-c["snr_db"],
                                   0 if c["link"] == "LOS" else 1,
                                   c["id"]))

    best = candidates[0] if candidates else None
    alternates = candidates[1:]

    return {
        "time":             float(t),
        "n_active":         int(n_active),
        "n_recipients":     int(n_recipients),
        "n_eligible":       len(candidates),
        "leader_active":    leader_active,
        "relay_id":         best["id"] if best else None,
        "relay_link":       best["link"] if best else None,
        "relay_snr_db":     best["snr_db"] if best else None,
        "relay_distance_m": best["distance_m"] if best else None,
        "alternates":       alternates,
    }


# ── Helpers ─────────────────────────────────────────────────────────────
def contiguous_runs(times, dt_tol=DT_TOL):
    """Group `times` into contiguous runs where consecutive elements are
    within `dt_tol` seconds of each other.  Returns a list of
    (start, end, n_frames).
    """
    if len(times) == 0:
        return []
    times = sorted(times)
    runs = []
    run_start = times[0]
    run_end = times[0]
    for t in times[1:]:
        if 0.0 <= t - run_end <= dt_tol:
            run_end = t
        else:
            runs.append((run_start, run_end,
                         int(round((run_end - run_start) / FRAME_DT)) + 1))
            run_start = run_end = t
    runs.append((run_start, run_end,
                 int(round((run_end - run_start) / FRAME_DT)) + 1))
    return runs


def print_run(label, runs, stream):
    if not runs:
        stream(f"  (none)\n")
        return
    for t0, t1, n in runs:
        stream(f"  {label}: {t0:6.1f} – {t1:6.1f} s  "
               f"({n} frames, {n * FRAME_DT:.1f} s)\n")


# ── Main ────────────────────────────────────────────────────────────────
def main():
    df = pd.read_csv(CSV_IN)

    # Build one summary row per time-step.
    rows = []
    for t, g in df.groupby("time"):
        rows.append(select_relay_at_time(float(t), g))

    summary = pd.DataFrame([{
        "time":             r["time"],
        "n_active":         r["n_active"],
        "n_recipients":     r["n_recipients"],
        "n_eligible":       r["n_eligible"],
        "leader_active":    r["leader_active"],
        "relay_id":         r["relay_id"],
        "relay_link":       r["relay_link"],
        "relay_snr_db":     r["relay_snr_db"],
        "relay_distance_m": r["relay_distance_m"],
        "alternates":       ";".join(
            f"{a['id']}({a['link']},{a['snr_db']:.1f}dB,"
            f"{a['distance_m']:.1f}m)"
            for a in r["alternates"]
        ),
    } for r in rows])
    summary.to_csv(CSV_OUT, index=False)

    # ── Console + text summary ──────────────────────────────────────────
    n_total      = len(summary)
    n_with_relay = int(summary.relay_id.notna().sum())
    n_gap        = n_total - n_with_relay
    n_no_leader  = int((~summary.leader_active).sum())

    print(f"Wrote {CSV_OUT}  ({n_total:,} time-steps)")
    print(f"  Steps with a relay       : {n_with_relay:,}")
    print(f"  Steps without a relay    : {n_gap:,}")
    print(f"     ↳ of which leader gone: {n_no_leader:,}")
    print(f"     ↳ of which no eligible: {n_gap - n_no_leader:,}")

    print("\nRelay selection counts (which vehicle gets picked as relay):")
    for nid, n in summary.relay_id.value_counts(dropna=False).items():
        label = f"v{int(nid)}" if pd.notna(nid) else "(none — gap)"
        print(f"  {label:>10s}: {n:>5d}")

    print("\nRelay link quality:")
    for link, n in summary.relay_link.value_counts(dropna=False).items():
        label = link if pd.notna(link) else "(gap)"
        print(f"  {label:>6s}: {n:>5d}")

    # Per-segment relay timeline (helps see merge / split transitions).
    # Coalesce consecutive identical (relay_id, relay_link) entries so a
    # long "no-relay" stretch prints as one entry with its duration.
    # Note: NaN != NaN in pandas, so normalise the key first.
    print("\nRelay timeline (one entry per run of identical relay):")
    def _key(r):
        rid = None if pd.isna(r.relay_id) else int(r.relay_id)
        link = None if pd.isna(r.relay_link) else str(r.relay_link)
        return (rid, link)
    runs = []
    run_start = None
    run_key = None
    run_snr = run_dist = run_n = None
    for _, r in summary.iterrows():
        key = _key(r)
        if key != run_key:
            if run_key is not None:
                runs.append((run_start, r.time - FRAME_DT,
                             run_key[0], run_key[1],
                             run_snr, run_dist, run_n))
            run_start = r.time
            run_key = key
            run_snr  = (None if pd.isna(r.relay_snr_db)
                        else float(r.relay_snr_db))
            run_dist = (None if pd.isna(r.relay_distance_m)
                        else float(r.relay_distance_m))
            run_n    = int(r.n_eligible)
    # close the final run
    if run_key is not None:
        runs.append((run_start, summary.time.iloc[-1],
                     run_key[0], run_key[1],
                     run_snr, run_dist, run_n))

    for t0, t1, rid, link, snr, dist, n_elig in runs:
        label = f"v{rid}" if rid is not None else "(gap)"
        snr_s  = f"{snr:6.1f}" if snr is not None else "  nan "
        dist_s = f"{dist:6.1f}" if dist is not None else "  nan "
        dur = (t1 - t0) + FRAME_DT
        print(f"  t={t0:6.1f} – {t1:6.1f} s  ({dur:5.1f}s)  "
              f"relay={label:>5s}  link={str(link):>5s}  "
              f"snr={snr_s} dB  dist={dist_s} m  "
              f"n_elig={n_elig}")

    # Coverage gaps split by reason (no-leader vs no-eligible).
    no_leader_rows = summary[~summary.leader_active]
    no_elig_rows   = summary[summary.leader_active & summary.relay_id.isna()]
    no_leader_runs = contiguous_runs(no_leader_rows.time.tolist())
    no_elig_runs   = contiguous_runs(no_elig_rows.time.tolist())

    print(f"\nCoverage gaps — leader not in scenario "
          f"({len(no_leader_rows):,} frames):")
    print_run("no-leader", no_leader_runs, lambda s: print(s, end=""))

    print(f"\nCoverage gaps — leader present but no eligible relay "
          f"({len(no_elig_rows):,} frames):")
    print_run("no-eligible", no_elig_runs, lambda s: print(s, end=""))

    # Persist the same summary to disk so it's easy to grep later.
    with SUMMARY_OUT.open("w") as f:
        out = lambda s: f.write(s)
        out(f"# Relay selection summary  ({n_total} time-steps)\n")
        out(f"# Input:  {CSV_IN.name}\n")
        out(f"# Output: {CSV_OUT.name}\n")
        out(f"# Leader: v0 (id={LEADER_ID}); leavers={sorted(LEAVERS)}; "
            f"joiners={sorted(JOINERS)}\n\n")
        out(f"Time-steps with a relay       : {n_with_relay:,}\n")
        out(f"Time-steps without a relay    : {n_gap:,}\n")
        out(f"  ↳ of which leader gone      : {n_no_leader:,}\n")
        out(f"  ↳ of which no eligible relay: {n_gap - n_no_leader:,}\n\n")
        out("Relay selection counts:\n")
        for nid, n in summary.relay_id.value_counts(dropna=False).items():
            label = f"v{int(nid)}" if pd.notna(nid) else "(none — gap)"
            out(f"  {label:>10s}: {n:>5d}\n")
        out("\nRelay link quality:\n")
        for link, n in summary.relay_link.value_counts(dropna=False).items():
            label = link if pd.notna(link) else "(gap)"
            out(f"  {label:>6s}: {n:>5d}\n")
        out("\nRelay timeline (one entry per run of identical relay):\n")
        for t0, t1, rid, link, snr, dist, n_elig in runs:
            label = f"v{rid}" if rid is not None else "(gap)"
            snr_s  = f"{snr:6.1f}" if snr is not None else "  nan "
            dist_s = f"{dist:6.1f}" if dist is not None else "  nan "
            dur = (t1 - t0) + FRAME_DT
            out(f"  t={t0:6.1f} – {t1:6.1f} s  ({dur:5.1f}s)  "
                f"relay={label:>5s}  link={str(link):>5s}  "
                f"snr={snr_s} dB  dist={dist_s} m  "
                f"n_elig={n_elig}\n")
        out(f"\nCoverage gaps — leader not in scenario "
            f"({len(no_leader_rows):,} frames):\n")
        print_run("no-leader", no_leader_runs, out)
        out(f"\nCoverage gaps — leader present but no eligible relay "
            f"({len(no_elig_rows):,} frames):\n")
        print_run("no-eligible", no_elig_runs, out)
    print(f"\nWrote {SUMMARY_OUT}")


if __name__ == "__main__":
    main()