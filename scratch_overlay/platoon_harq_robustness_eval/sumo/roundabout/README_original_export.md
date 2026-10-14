# Synthetic roundabout scenario (constant density + RSU traces)

Self-contained SUMO scenario: a **4-arm roundabout** with **4 lanes per arm
(2 in / 2 out)**, 1 km long arms, buildings in the middle of the roundabout and
along the arms, and **TraCI traces at 1 ms resolution** for five traffic
densities, each with a static **RSU** marker whose distance to every vehicle is
recorded at every simulation step.

Everything here is generated from scratch — no OpenStreetMap data is used any
more (`map(1).osm` is left in the folder but unused).

## Files

| file | role |
|---|---|
| `build_network.py` | writes the plain-XML network, runs netconvert, injects the buildings, writes the routes |
| `run_density_sim.py` | TraCI run: density control + RSU + 1 ms trace recording, one CSV per density |
| `roundabout.net.xml` | the network (roads, junction internals, **98 buildings**) |
| `roundabout.rou.xml` | 12 routes: every arm in → a *different* arm out, around the ring |
| `buildings.csv` | the building polygons (also embedded in the net file) |
| `traces/plot_snapshot.py` | renders one snapshot (network + buildings + vehicles + RSU) |
| `traces/traces_density<D>.csv.gz` | **one trace file per density** (9, 16, 20, 50, 80) |
| `traces/density_summary.csv` | density / speed / delay / arrivals per scenario |
| `traces/run.log` | console log with the progress bars |

## 1. Network

```bash
export SUMO_HOME=/path/to/sumo
python build_network.py
```

* **Roundabout**: circle of radius 25 m approximated by 12 chords, 2 lanes,
  30 km/h, one-way **counter-clockwise** (right-hand traffic), priority 10.
* **Arms**: north / east / south / west, **1 km** from the roundabout centre,
  2 lanes **in** (right half) + 2 lanes **out** (left half) = **4 lanes**,
  50 km/h, priority 1 → entering traffic gives way to the ring.
* **Connections** are written explicitly: arm → ring (entry), ring → ring
  (straight on), ring → arm (exit). No U-turns, no wrong-way entries.
* **Buildings**: 2 blocks in the middle of the roundabout and 96 blocks along
  both sides of the four arms. SUMO 1.26 has no building import, so they are
  injected as `<type id="building.N"><polygon .../></type>` — exactly the
  representation netconvert uses for OSM building shapes — and they are drawn by
  `sumo-gui`.
* **Routes**: 12 routes `{src}2{dst}` (4 entry arms × 3 exit arms), each
  `arm_in → ring segments → other arm_out`, so no vehicle ever leaves through
  the arm it entered.

## 2. Simulation

```bash
python run_density_sim.py                       # 9 16 20 50 80, dt = 1/1000 s
python run_density_sim.py --densities 20 --dt 0.01 --record-from 60   # quick test
python run_density_sim.py --no-rsu              # reference run without the marker
```

### Density control

The *area of interest* is a circle of **RADIUS = 500 m** around the roundabout
centre (0.785 km²). "Density 20" means **20 vehicles are present in that area at
all times**:

* every `CONTROL_DT` (0.1 s) the area is counted and, if it is below target,
  new vehicles are inserted at the **outer end of a random 1 km arm** with
  `departSpeed=max` and a random position on that arm,
* vehicles that reach the end of their route leave the network and are
  replaced, so the population keeps cycling,
* if the count ever exceeds the target, the vehicle closest to the area border
  is taken out again,
* the first **90 s** are the *cold start*: the spawned vehicles need ≈ 70 s to
  cover their 1 km approach, so traces are recorded from t = 90 s
  (`--record-from`), which gives the constant-density state.

Measured control accuracy (1 ms runs): in-area count stays within ±1 vehicle of
the target for every density.

### The RSU

* id `RSU`, vType `RSU` (2.0 × 0.6 m, `speedFactor 0`, `maxSpeed 0.01`),
  inserted with `traci.vehicle.add()` on the **inbound carriageway of the north
  arm** with a lateral offset of **+14 m** (lane 0 of `N_in` is at x = -4.8 m,
  the carriageway ends at x = +6.4 m) → the marker stands **outside the road, on
  its left side**.
* **Position: (9.07, 32.60) m** — at the **junction of the roundabout and the
  northern road** (the give-way line, end of the `N_in` edge), 11.8 m from the
  junction node (0, 25). The trace files were produced with the marker 120 m
  further north; the distances were afterwards recomputed for this position
  without re-running the simulation (see below).
* It is pinned: its position is checked every step from the TraCI subscription
  and restored with `traci.vehicle.moveTo()` if SUMO nudges it.
* Vehicles still slow down slightly next to it (SUMO has no concept of a purely
  decorative roadside object): with d = 9 the mean speed is 12.3 m/s with the
  RSU and 12.4 m/s without, mean time loss 3.2 s vs 1.1 s. Use `--no-rsu` for
  reference runs.

## 3. Traces

`traces/traces_density<D>.csv.gz` — one row per (simulation step, vehicle),
sampled every **1/1000 s**, for every vehicle inside the area of interest
(`--all-vehicles` records the whole network):

| column | TraCI variable |
|---|---|
| `step`, `time_s`, `phase`, `density_veh_per_area` | bookkeeping |
| `veh_id`, `is_rsu` | the RSU is flagged with `is_rsu = 1` |
| **`dist_xy_m`**, `dist_3d_m`, `bearing_from_rsu_deg` | **distance of this vehicle to the RSU** (ground plane, 3D, bearing) |
| `x`, `y`, `z` | `VAR_POSITION3D` |
| `speed_mps`, `speed_lat_mps`, `accel_mps2`, `slope` | `VAR_SPEED`, `VAR_SPEED_LAT`, `VAR_ACCELERATION`, `VAR_SLOPE` |
| `angle_deg` | `VAR_ANGLE` |
| `road_id`, `lane_id`, `lane_index`, `lane_pos_m`, `lateral_m` | `VAR_ROAD_ID`, `VAR_LANE_ID`, `VAR_LANE_INDEX`, `VAR_LANEPOSITION`, `VAR_LANEPOSITION_LAT` |
| `route_id`, `route_index` | `VAR_ROUTE_ID`, `VAR_ROUTE_INDEX` |
| `lane_change_mode`, `signals`, `stop_state` | `VAR_LANECHANGE_MODE`, `VAR_SIGNALS`, `VAR_STOPSTATE` |
| `person_number`, `waiting_time_s`, `accum_waiting_s`, `time_loss_s` | `VAR_PERSON_NUMBER`, `VAR_WAITING_TIME`, `VAR_ACCUMULATED_WAITING_TIME`, `VAR_TIMELOSS` |
| `distance_m`, `speed_factor`, `min_gap_m`, `allowed_speed_mps` | `VAR_DISTANCE`, `VAR_SPEED_FACTOR`, `VAR_MINGAP`, `VAR_ALLOWED_SPEED` |
| `vtype`, `depart_delay_s`, `in_area` | `VAR_TYPE`, `VAR_DEPART_DELAY`, bookkeeping |

Performance: every vehicle is subscribed to the position only, and the full
variable set is subscribed/unsubscribed while it is inside the area — otherwise
the payload per step would be dominated by vehicles on the 500 m long approach.

## 3b. Moving the RSU without re-running the simulation

`traces/move_rsu.py` rewrites the RSU distances of **existing** trace files:

```bash
python traces/move_rsu.py                 # to the junction (9.07, 32.60)
python traces/move_rsu.py --x 0 --y 25    # anywhere else
python traces/move_rsu.py --dry-run
```

Every row stores the vehicle position `(x, y, z)` of that simulation step, so the
new distance is the plain Euclidean distance to the new anchor
(`dist_xy_m`, `dist_3d_m`, `bearing_from_rsu_deg` are rewritten; the `RSU` row
itself is set to the new anchor with distance 0). The traffic is **not** touched:
the vehicle trajectories in the files are exactly the ones that were simulated.
The script is idempotent (it always reads the stored coordinates) and each
`traces_density*.meta.json` keeps the original position under `rsu_previous`.

## 4. Visualisation

```bash
uv run --project .. python traces/plot_snapshot.py --density 20 --time 120
uv run --project .. python traces/plot_snapshot.py --density 50 --zoom 250 --lanes
sumo-gui -n roundabout.net.xml          # look at the network and the buildings
```

The plot shows the SUMO road geometry and the building polygons, all vehicles
with the same circular marker (coloured by speed), the RSU as a large star and
the RSU→vehicle distance lines.

## 5. Results (seed 42, 1 ms traces, 60 s recorded warm phase each)

| density [veh/area] | veh/km² | in-area min/mean/max | mean speed | mean time loss | trace rows | file |
|---:|---:|---:|---:|---:|---:|---|
|  9 |  11.5 |  8 /  9.0 /  9 | 12.3 m/s |  3.2 s |   599 511 | `traces_density9.csv.gz` (14 MB) |
| 16 |  20.4 | 15 / 16.0 / 17 | 12.5 m/s |  2.3 s | 1 019 145 | `traces_density16.csv.gz` (24 MB) |
| 20 |  25.5 | 19 / 20.0 / 21 | 11.6 m/s |  3.7 s | 1 258 857 | `traces_density20.csv.gz` (31 MB) |
| 50 |  63.7 | 48 / 49.9 / 51 | 11.4 m/s |  7.5 s | 3 058 413 | `traces_density50.csv.gz` (77 MB) |
| 80 | 101.9 | 79 / 80.0 / 80 |  0.6 m/s | 67.6 s | 4 860 222 | `traces_density80.csv.gz` (50 MB) |

![summary](traces/density_summary.png)

* **Density control** holds the target within ±1 vehicle for every density.
* **d = 80 is beyond the capacity of the junction**: the 2-lane ring fills up, the
  circulating stream is interrupted and the roundabout **gridlocks** — at
  t = 140 s all 80 vehicles in the area stand still (mean speed 0.00 m/s, the
  snapshot `traces/snapshot_density80_gridlock.png` shows the ring and the four
  approaches blocked back to ~100 m from the centre). Keeping 80 vehicles inside
  a 500 m radius circle requires ≈4800 veh/h through the roundabout, i.e.
  ≈1200 veh/h per arm, which is at/above the give-way capacity of a 25 m radius,
  2-lane roundabout. The critical density is between 50 and 80 veh/area.
* Snapshots (all drawn with the RSU at its new position at the junction):
  `traces/snapshot_density9_t120.png` (whole network),
  `traces/snapshot_density9_zoom.png` (roundabout detail, 260 m window),
  `traces/snapshot_density20_t130.png`, `traces/snapshot_density20_zoom.png`,
  `traces/snapshot_density50_zoom.png` (fluid flow) and
  `traces/snapshot_density80_gridlock.png` (gridlock: the ring is full and all
  four approaches are blocked back to ~100 m from the centre).

## Notes / gotchas

* `traci.vehicle.subscribe()` takes **one** object id per call, not a list.
* The `VAR_DEPARTED_VEHICLES_IDS` / `VAR_ARRIVED_VEHICLES_IDS` lists are **per
  step**: reading them with a coarser interval silently misses vehicles.
* A vehicle that reached the end of its route is already out of the network —
  calling `traci.vehicle.remove()` on it desynchronises the TraCI connection.
* `VAR_ROUTE`, `VAR_ARRIVALLANE` and `VAR_EDGE_ID` are not available for
  vehicles in SUMO 1.26 (`unsupported variable`).
* Lateral offsets beyond the lane are accepted, which is what puts the RSU
  outside the carriageway.
