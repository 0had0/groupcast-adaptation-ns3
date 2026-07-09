# Relay Selection for V2V Groupcast

## Context

In the `platoon_intersection_merge_split` scenario the leader (`v0`,
`node_id = 0`) periodically broadcasts a groupcast message intended for
every member of the platoon (and for joiners while they are
approaching, and for leavers until they peel off).  In a dense urban
grid some vehicles have their line-of-sight to the leader blocked by
buildings at the intersections.  These NLOS links have very low SNR
(median ≈ −34 dB in our run) and would not deliver the groupcast
reliably.

A **relay** is a single intermediate vehicle that:

1. Has a usable link to the leader — **LOS** or **NLOSv** (NLOS is
   excluded).
2. Forwards the leader's message to the recipients that cannot hear
   the leader directly.

This file documents the per-time-step algorithm that picks that relay
from the `(time, tx, rx)` table produced by
`build_distance_classification.py`.

## Per-time-step algorithm

For each simulation time `t` (one row per ordered `(tx, rx)` pair in
`distances_classified.csv`):

1. **Identify the leader**: `v0` (`node_id = 0`).
2. **Active vehicles** at `t` are the union of all `tx` and `rx`
   values for that row group.
3. **Recipients** = active vehicles, minus
   - the leader itself (no self-relay),
   - leavers (`v1`, `v3`, `v6`) that have already departed — i.e.
     `x > 320 m` **and** `y < 195 m` (on the east leg well past J2),
   - off-map vehicles (`x > 510 m` or `y > 1010 m`).
   Joiners (`j0` = `8`, `j1` = `9`) are recipients from the moment
   they appear on the west approach; once merged they stay in the
   platoon.
4. For every recipient, look up the leader's `(v0 → recipient)`
   record at time `t` and read `link`, `snr_db`, `distance_m`.
5. **Eligible vehicles** = recipients whose leader-link is `LOS` or
   `NLOSv`.  `NLOS` is excluded.
6. **Pick the relay** as the eligible vehicle with the highest SNR
   to the leader.  Ties are broken by preferring `LOS` over
   `NLOSv` (more capacity headroom).

When there are zero eligible vehicles the relay is set to `None` and
the timestamp is reported as a **coverage gap** — the groupcast would
need an out-of-band mechanism (e.g. store-and-forward, infrastructure
relay) to reach the platoon in that frame.

## Why this metric

* **SNR** is monotonically related to distance for any single link
  condition and combines path-loss + receiver noise into one number.
* **Highest SNR** ⇒ most reliable decode ⇒ best chance to forward
  correctly to every downstream vehicle in one transmission.
* **LOS preferred over NLOSv at equal SNR** — NLOSv still carries the
  additional stochastic vehicle-blockage tail; LOS is deterministic.

## Output

`relay_selection.csv` — one row per time-step, with columns:

| column | meaning |
|---|---|
| `time` | simulation time (s) |
| `n_active` | vehicles present in the scenario at `time` |
| `n_recipients` | active vehicles that should receive the groupcast |
| `n_eligible` | recipients with LOS / NLOSv link to leader |
| `relay_id` | chosen relay (None ⇒ coverage gap) |
| `relay_link` | LOS or NLOSv |
| `relay_snr_db` | SNR of leader → relay |
| `relay_distance_m` | Euclidean distance from leader |
| `alternates` | `;`-separated `id(link,snr dB)` list of other eligible vehicles (SNR-desc) |

`relay_selection_summary.txt` — printable summary (counts per relay,
counts per link class, coverage gaps, per-segment relay picks).

## Edge cases

* **Self-link** (leader ↔ leader): `distance_m = 0`, `pathloss_db =
  0`, `snr_db = +inf`.  Filtered out by step 3.
* **Joiner not yet in the scenario** (no FCD record at this `time`):
  not active, hence not a recipient.
* **Multiple eligible vehicles with identical SNR**: tie broken by
  link class (LOS first), then by `id` (stable order).
* **Coverage gap** (no eligible relay): reported as `relay_id =
  None`; the algorithm does not silently fall back to NLOS.