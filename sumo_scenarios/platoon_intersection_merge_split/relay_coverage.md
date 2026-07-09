# Downstream Coverage via the Chosen Relay

## Motivation

The relay picked by `relay_selection.py` is the vehicle with the best
**leader-side** link (LOS or NLOSv).  But the relay still has to
**forward** the groupcast to the recipients that cannot hear the
leader directly.  If those NLOS-to-leader vehicles are also NLOS to
the chosen relay, the single-relay groupcast will fail and the
protocol needs either

* a multi-hop tree,
* a different relay,
* store-and-forward,
* or out-of-band infrastructure.

This file documents the 1-hop reachability analysis.

## Algorithm

For every time-step `t`:

1. Read the chosen relay `(R, link_R, snr_R)` from
   `relay_selection.csv` (skip this step if `leader_active` is false —
   no groupcast at all).
2. Identify the **direct recipients** = every groupcast recipient
   whose `v0 → x` link is `LOS` or `NLOSv`.  (Same set as the eligible
   set in `relay_selection.py`.)
3. Identify the **indirect candidates** = every groupcast recipient
   whose `v0 → x` link is `NLOS` (i.e. blocked by a building at J1/J2
   when the leader has crossed the intersection but the trailer has
   not).  These are the vehicles that *need* the relay.
4. For each indirect candidate, look up the `R → x` link from
   `distances_classified.csv`.  Mark as **reachable via relay** if
   that link is `LOS` or `NLOSv`; otherwise **still unreachable**.
5. Record:
   * `n_recipients`        — total recipients
   * `n_direct`            — recipients with LOS/NLOSv to leader
   * `n_via_relay`         — NLOS-to-leader recipients now reachable
   * `n_unreachable`       — NLOS-to-leader recipients that the relay
     cannot reach either
   * `coverage_pct`        — `(n_direct + n_via_relay) / n_recipients × 100`
6. List the `unreachable` ids so they can be inspected later.

## Output

* `relay_coverage.csv` — one row per time-step
  (`time, relay_id, relay_link, n_recipients, n_direct, n_via_relay,
   n_unreachable, coverage_pct, unreachable_ids`).
* `relay_coverage_summary.txt` — totals, distribution, examples of
  unreachable sets.

## Edge cases

* **No leader** (leader has exited the scenario): no groupcast, no
  coverage computation.  Row recorded with `relay_id = None`,
  counts = 0.
* **No eligible relay** (gap): the groupcast can only reach the
  `n_direct` recipients.  `n_via_relay = 0`, `n_unreachable =`
  remaining NLOS-to-leader recipients.
* **No NLOS-to-leader recipients** (everyone can hear the leader):
  `n_via_relay = 0`, `n_unreachable = 0`, `coverage_pct = 100 %`.

## When this analysis matters most

The merge phase (≈ t = 3.8 s – 19.6 s) is the only window where
NLOS-to-leader recipients appear (the "leader past J1/J2 but trailer
not" geometry).  During the steady-state north-leg stretch and the
south-leg start, every recipient is LOS to the leader, so coverage
sits at 100 % even if the relay picks a poor vehicle.