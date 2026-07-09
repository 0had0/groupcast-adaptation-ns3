# platoon_intersection_merge_split

A SUMO/TraCI scenario for stress-testing the **groupcast-adaptation** controller
in a setting that combines a merge, an intersection turn, and a platoon split
in a single trajectory.

## Scenario

The network is a single 4-way L-then-reverse-L path with two intersections:

```
                       v0  v2  v4  j0  j1  v5  v7        (northbound)
                          ↑                        ↑
                  J2 ────*──── * ──── * ──── *     J1 ─── north
                          ↓                        ↓
   (eastbound)   v1  v3  v6 ──────► J2               v0  v2 …  v7     (southbound)
                          ↓
   (westbound)             j0  j1 ────► J1
```

* **J1** at (200, 200): the 8-vehicle platoon (`v0..v7`) drives north and
  turns **right**; two joiners (`j0, j1`) enter from the **west** and merge
  in.
* **J2** at (300, 200): 76.8 m later, the platoon turns **left** to go north.
  Three vehicles peel off **straight east**: `v1`, `v3`, `v6` (the 2nd, 4th,
  and 7th positions). The remaining vehicles (`v0, v2, v4, v5, v7`) plus
  the joiners (`j0, j1`) continue north on the extended 790 m north leg.

The full path of the leader (`v0`) is:

```
south leg (186.8 m) → right at J1 → east leg (76.8 m) → left at J2 → north leg (790 m)
```

## Files

| File | Purpose |
|---|---|
| `nodes.nod.xml`           | Node definitions (5 nodes for the L shape) |
| `edges.edg.xml`           | Edges with lengths (in m) |
| `connections.con.xml`     | Allowed turn movements at J1, J2 |
| `build_net.sh`            | Calls `netconvert` → `net.net.xml` |
| `routes.rou.xml`          | 8-vehicle platoon + 3 leaver routes |
| `scenario.sumocfg`        | SUMO config (120 s, FCD output) |
| `gen_trace.py`            | TraCI controller + FCD → `mob.csv` exporter |
| `plot_scenario.py`        | Validation plots + programmatic assertions |
| `net.net.xml`             | Built network (generated) |
| `fcd_output.xml`          | SUMO FCD output (generated) |
| `platoon_intersection_merge_split.mob.csv` | ns-3 mobility trace (generated) |
| `trajectory.png`          | x-y plot of all 10 vehicles (generated) |
| `speed.png`               | speed-vs-time (generated) |
| `gap.png`                 | distance from leader (generated) |
| `merge_split_events.png`  | speed with merge/leaver annotations (generated) |
| `summary.txt`             | text summary + assertion pass/fail (generated) |

## Usage

```bash
# 1. Build the network (only once)
./build_net.sh

# 2. Run the controller + simulation
python3 gen_trace.py

# 3. Validate and plot
python3 plot_scenario.py
```

## Controller

`gen_trace.py` is a custom TraCI controller that uses a **progress
coordinate** `p` (cumulative path length from the start of the south leg)
and a simple **CACC** law:

```
gap = p_pred - p_self - L
acc = K_P * (gap - IVD) + K_D * (v_pred - v_self)
v_new = v + acc * dt, clamped to [MIN_SPEED, V_CRUISE]
```

Key constants:

| Symbol        | Value | Notes |
|---------------|-------|-------|
| `V_CRUISE`    | 10 m/s (36 km/h) | Open-loop leader target |
| `IVD`         | 15 m  | Time-headway-equivalent gap (≈ 1.5 s at 10 m/s) |
| `SPACING`     | 19.5 m| `L + IVD = 4.5 + 15` |
| `MIN_SPEED`   | 2 m/s | Lowest allowed command (intersection slowdowns) |
| `K_P, K_D`    | 3.0, 1.5 | CACC gains |
| `MAX_ACCEL/DECEL` | 2.5 / -5.0 m/s² | Comfort limits |
| `V_CRUISE_BOOST` | 15 m/s | Allowed when the chain has a large gap to close *and* the successor is at SPACING or further behind.  Leavers are not boosted. |

State machine:

```
APPROACH  →  MERGE  →  SPLIT  →  CRUISE_N  →  DONE
   (south)   (J1)     (J2)       (north)
```

* **APPROACH**: platoon on south leg.
* **MERGE**:  triggered when both joiners are on `e_west_in` and approaching
  J1.  When a joiner's `p` closes to within 2 m of its predecessor and
  *both* the joiner and the slot-pred (`v3`) are past J1, the joiner is
  inserted into `platoon_order` **at the position that matches its
  geometric position in p** — typically *in front of* `v3` on `e_j1_j2`,
  since the joiner entered the east leg first.  This makes the CACC
  chain follow the actual p-space order, so `v3` ends up following the
  joiner (not `v2`, which is way ahead on the north leg).  The joiner
  is braked to `MIN_SPEED` at the slot event so it can re-accelerate
  to cruise without overshooting the slot.
* **SPLIT**: triggered when `v0` is past J1 and the joiners are slotted.
  Leavers (`v1, v3, v6`) are released as their `p > SOUTH + EAST + 1`; they
  are removed from `platoon_order` and given `V_CRUISE`.
* **CRUISE_N**: `v0` is well past J2; the rest follow and the chain
  compresses to `SPACING` on the extended north leg.

## Assertions (all passing)

`plot_scenario.py` runs the following checks against
`platoon_intersection_merge_split.mob.csv`:

1. **`no_stops`**: every vehicle's min speed ≥ 2 m/s — no vehicle stops
   during merge or split.
2. **`no_collision`**: no two vehicles closer than 0.1 m at any sampled
   time-step (SUMO guarantees no overlap; we just enforce a sane
   near-collision floor).
3. **`merge_order`**: at t=60 s, leavers are on `e_j2_east` and the rest
   are past J1 heading north.
4. **`split_order`**: at t=90 s, leavers (v1, v3, v6) are east of J2, all
   other vehicles are on `e_j2_north`.
5. **`leader_geometry`**: `v0` starts on the south leg (x≈201.6, y<200)
   and ends on the north leg (x≈301.6, y≈999.5).
6. **`j1_no_collision`**: minimum distance between the south-bound
   platoon and the west-joiner (`j0`, `j1`) is ≥ 0.5 m — no J1 collision
   during the merge.
7. **`back_slows_for_joiner`**: back-of-platoon vehicles (`v4..v7`)
   drop below 0.8 · V_CRUISE (= 8 m/s) during the merge window,
   demonstrating the merge-IVD slowdown.
8. **`joiner_maintains_ivd`**: at t=90 s, the gaps between consecutive
   vehicles in the back of the merged platoon are within ±5 m of
   `SPACING` (19.5 m).
9. **`ivd_readapts`**: at t=90 s, after the three leavers and two
   joiners have settled, every consecutive pair in the merged
   platoon (`v0, v2, j0, j1, v4, v5, v7`) is within ±5 m of
   `SPACING` — the IVD has been re-adapted after all leaver/joiner
   changes.

A summary is written to `summary.txt`.  `plot_scenario.py` exits non-zero
if any assertion fails.

## Sample run output

```
[t=0.0]   deploy j0 lp=81.7 target_p=167.3
[t=0.0]   deploy j1 lp=62.2 target_p=147.8
[t=15.5]  j0 SLOTTED  v3_p=186.9 j0_p=190.4 v4_p=155.9  order=['v0', 'v1', 'v2', 'j0', 'v3', 'v4', 'v5', 'v6', 'v7']
[t=15.8]  j1 SLOTTED  v3_p=187.3 j1_p=187.0 j0_p=191.3 v4_p=157.6  order=['v0', 'v1', 'v2', 'j0', 'j1', 'v3', 'v4', 'v5', 'v6', 'v7']
[t=16.6]  LEAVER v1 released at J2
[t=30.1]  LEAVER v3 released at J2
[t=40.9]  LEAVER v6 released at J2
```

Min speeds per vehicle:

```
v0: 10.0 m/s    v1: 2.0 m/s     v2: 2.37 m/s   v3: 3.36 m/s
v4: 5.34 m/s    v5: 2.0 m/s     v6: 2.0 m/s    v7: 2.1 m/s
j0: 2.0 m/s     j1: 2.0 m/s
```

(v0 is the only vehicle that *never* slows below 10 m/s; the others have
to brake when crossing J1, the joiners, or J2.  The `v3` min speed is
the slot-instant brake; all vehicles that slow to 2 m/s do so only
for a fraction of a second at the joiner slot or junction corner.)

## Notes for ns-3

The `platoon_intersection_merge_split.mob.csv` file is the ns-3 mobility
trace.  The `node_id` mapping is fixed:

| node_id | vehicle |
|---|---|
| 0..7    | v0..v7 |
| 8       | j0     |
| 9       | j1     |
