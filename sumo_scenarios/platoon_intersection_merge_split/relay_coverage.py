#!/usr/bin/env python3
"""Downstream-coverage analysis for the chosen relay.

Given the relay picked by `relay_selection.py` at every simulation
time-step, this script computes the **1-hop reachable set**:

  * `direct`     — recipients with LOS or NLOSv to the leader
  * `via_relay`  — NLOS-to-leader recipients reachable via the relay
                   (LOS or NLOSv from relay to recipient)
  * `unreachable`— NLOS-to-leader recipients that the relay also
                   cannot reach (would need a multi-hop tree or a
                   different relay)

The output `relay_coverage.csv` has one row per time-step.
A human-readable summary is written to `relay_coverage_summary.txt`.

Algorithm details live in `relay_coverage.md`.
"""
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
LINKS_CSV = HERE / "distances_classified.csv"
RELAY_CSV = HERE / "relay_selection.csv"
OUT_CSV   = HERE / "relay_coverage.csv"
SUMMARY   = HERE / "relay_coverage_summary.txt"

# Scenario constants — kept in sync with relay_selection.py.
LEADER_ID = 0

# A "usable" link is one the receiver can decode with reasonable
# probability (LOS, or NLOSv which still passes with the 3 dB extra
# shadowing).  NLOS is excluded.
USABLE_LINKS = ("LOS", "NLOSv")


# ── Helpers ─────────────────────────────────────────────────────────────
def compute_coverage_at_time(t, df_t, relay_row):
    """Return coverage row for time `t`.

    `df_t`     : distances_classified rows for this time
    `relay_row`: matching row from relay_selection.csv (pd.Series)
    """
    leader_active = bool(relay_row["leader_active"])
    relay_id = (int(relay_row["relay_id"])
                if pd.notna(relay_row["relay_id"]) else None)

    # Active vehicles at this time, keyed by node id.
    rx_view = df_t[["rx", "rx_x", "rx_y"]].drop_duplicates(subset=["rx"])
    veh = {int(r["rx"]): (float(r["rx_x"]), float(r["rx_y"]))
           for _, r in rx_view.iterrows()}

    # Pre-index the two link tables we'll need (leader → *, relay → *).
    leader_rows = df_t[df_t.tx == LEADER_ID].set_index("rx")
    relay_rows  = (df_t[df_t.tx == relay_id].set_index("rx")
                   if relay_id is not None else None)

    n_recipients = 0
    n_direct = 0
    n_via_relay = 0
    n_unreachable = 0
    unreachable_ids = []

    if leader_active:
        # Reuse the eligibility predicate from relay_selection.py to
        # keep the two scripts in sync.
        from relay_selection import is_recipient
        for nid in veh:
            x, y = veh[nid]
            if not is_recipient(nid, x, y):
                continue
            n_recipients += 1
            if nid not in leader_rows.index:
                continue
            row = leader_rows.loc[nid]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            link = str(row["link"])
            if link in USABLE_LINKS:
                n_direct += 1
                continue
            # NLOS to leader — check whether the relay can forward.
            if relay_rows is not None and nid in relay_rows.index:
                rrow = relay_rows.loc[nid]
                if isinstance(rrow, pd.DataFrame):
                    rrow = rrow.iloc[0]
                rlink = str(rrow["link"])
            else:
                rlink = None
            if rlink in USABLE_LINKS:
                n_via_relay += 1
            else:
                n_unreachable += 1
                unreachable_ids.append(int(nid))

    coverage_pct = ((n_direct + n_via_relay) / n_recipients * 100.0
                    if n_recipients > 0 else float("nan"))

    return {
        "time":            float(t),
        "leader_active":   leader_active,
        "relay_id":        relay_id,
        "relay_link":      (None if pd.isna(relay_row["relay_link"])
                            else str(relay_row["relay_link"])),
        "n_recipients":    n_recipients,
        "n_direct":        n_direct,
        "n_via_relay":     n_via_relay,
        "n_unreachable":   n_unreachable,
        "coverage_pct":    coverage_pct,
        "unreachable_ids": ";".join(str(i) for i in unreachable_ids),
    }


# ── Main ────────────────────────────────────────────────────────────────
def main():
    df_links = pd.read_csv(LINKS_CSV)
    df_relay = pd.read_csv(RELAY_CSV)
    # Group the link table once for O(1) lookup by time.
    links_by_t = {float(t): g for t, g in df_links.groupby("time")}

    rows = []
    for _, relay_row in df_relay.iterrows():
        t = float(relay_row["time"])
        df_t = links_by_t.get(t, df_links.iloc[0:0])
        rows.append(compute_coverage_at_time(t, df_t, relay_row))

    out = pd.DataFrame(rows)
    out.to_csv(OUT_CSV, index=False)

    # ── Summary printout ─────────────────────────────────────────────────
    n_total       = len(out)
    n_leader_gone = int((~out.leader_active).sum())
    n_full        = int((out.coverage_pct == 100).sum())
    n_partial     = int(((out.coverage_pct < 100) &
                         (out.coverage_pct.notna())).sum())
    n_gap         = int(out.coverage_pct.isna().sum())
    cov_active = out[out.leader_active].coverage_pct.dropna()
    mean_cov = cov_active.mean() if len(cov_active) else float("nan")

    print(f"Wrote {OUT_CSV}  ({n_total:,} time-steps)")
    print(f"  Leader active               : {n_total - n_leader_gone:,}")
    print(f"  Leader exited               : {n_leader_gone:,}")
    print(f"  100 % coverage              : {n_full:,}")
    print(f"  Partial coverage            : {n_partial:,}")
    print(f"  No-coverage (leader gone)   : {n_gap:,}")
    print(f"  Mean coverage (active only) : {mean_cov:.2f} %")

    unr = out[out.n_unreachable > 0]
    if not unr.empty:
        print(f"\nFrames where the single relay cannot reach everyone:")
        print(f"  Total frames                : {len(unr):,}")
        print(f"  Max unreachable recipients  : "
              f"{int(out.n_unreachable.max())}")
        print(f"  Mean unreachable            : "
              f"{out.n_unreachable.mean():.2f}")
        worst = int(out.n_unreachable.max())
        worst_rows = out[out.n_unreachable == worst].head(5)
        print(f"  Worst frames (n_unreachable = {worst}):")
        for _, r in worst_rows.iterrows():
            rid = ("v" + str(int(r.relay_id))
                   if pd.notna(r.relay_id) else "(no relay)")
            print(f"    t={r.time:6.1f}s  relay={rid:>5s}  "
                  f"unreachable=[{r.unreachable_ids}]")

    # Persist the same summary to disk.
    with SUMMARY.open("w") as f:
        write = f.write
        write(f"# Relay downstream-coverage summary  ({n_total} time-steps)\n")
        write(f"# Input:  {LINKS_CSV.name}, {RELAY_CSV.name}\n")
        write(f"# Output: {OUT_CSV.name}\n\n")
        write(f"Leader active               : "
              f"{n_total - n_leader_gone:,}\n")
        write(f"Leader exited               : {n_leader_gone:,}\n")
        write(f"100 % coverage              : {n_full:,}\n")
        write(f"Partial coverage            : {n_partial:,}\n")
        write(f"No-coverage (leader gone)   : {n_gap:,}\n")
        write(f"Mean coverage (active only) : {mean_cov:.2f} %\n")
        if not unr.empty:
            write("\nFrames where the single relay cannot reach everyone:\n")
            write(f"  Total frames                : {len(unr):,}\n")
            write(f"  Max unreachable recipients  : "
                  f"{int(out.n_unreachable.max())}\n")
            write(f"  Mean unreachable            : "
                  f"{out.n_unreachable.mean():.2f}\n")
            worst = int(out.n_unreachable.max())
            worst_rows = out[out.n_unreachable == worst].head(5)
            write(f"  Worst frames (n_unreachable = {worst}):\n")
            for _, r in worst_rows.iterrows():
                rid = ("v" + str(int(r.relay_id))
                       if pd.notna(r.relay_id) else "(no relay)")
                write(f"    t={r.time:6.1f}s  relay={rid:>5s}  "
                      f"unreachable=[{r.unreachable_ids}]\n")
    print(f"\nWrote {SUMMARY}")


if __name__ == "__main__":
    main()