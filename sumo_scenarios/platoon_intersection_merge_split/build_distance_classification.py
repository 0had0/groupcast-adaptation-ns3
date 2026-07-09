#!/usr/bin/env python3
"""Generate NxN distance + channel-condition (LOS / NLOS / NLOSv) CSV.

For every time-step in the FCD trace, for every ordered pair (i, j) of
nodes we record:

  * `distance_m`   — Euclidean distance between the two vehicles
  * `link`         — "LOS", "NLOS" or "NLOSv"
  * `reason`       — short tag explaining the classification
  * `pathloss_db`  — V2V_URBAN (3GPP TR 37.885 Table 6.2.1-1) path loss
  * `rx_power_dbm` — TX power minus path loss
  * `interference_dbm` — sum of all *other* transmitters' RX power at this RX
  * `noise_dbm`    — thermal noise floor + noise figure
  * `sinr_db`      — rx_power_dbm − 10·log10(interference_mW + noise_mW)

The classification follows the geometric NLOSv module in
`scratch_overlay/common/channel/three-gpp-geometric-nlosv.{h,cc}`
which decorates an underlying 3GPP channel-condition model with a
deterministic NLOSv flag whenever the straight-line TX→RX path is
blocked by another vehicle's AABB (Liang–Barsky segment-vs-AABB).

The "NLOS" classification is the one we add here: outside the route
edges there are building footprints at the four corners of each
intersection (J1 at (201.6, 200), J2 at (300, 200)).  If the TX→RX
segment passes through any of those footprints the link is NLOS.  In
practice this is exactly the "leader has passed J1 / J2 but the
trailer has not yet reached the intersection" case the user asked
about — the line cuts through the building on the inside corner of
the intersection.

We additionally add a coarse road-segment check: if the two vehicles
are on clearly different road segments whose straight-line connection
crosses a building, force NLOS.  The road-segment check is redundant
with the building AABB test but makes the reason labels more readable.

Output:
  * `distances_classified.csv` — long-format (one row per ordered pair
    per time-step) with the columns above plus `tx`, `rx`, `time`,
    `tx_x`, `tx_y`, `rx_x`, `rx_y`, `tx_v`, `rx_v`.
"""
import math
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
CSV_IN  = HERE / "platoon_intersection_merge_split.mob.csv"
CSV_OUT = HERE / "distances_classified.csv"

# ── V2V_URBAN path loss + receiver-side SINR model ───────────────────────
# Mirrors `ThreeGppV2vUrbanPropagationLossModel` in
# `ns-3-dev/src/propagation/model/three-gpp-v2v-propagation-loss-model.cc`
# (3GPP TR 37.885 v15.2.0 Table 6.2.1-1).
#
#   LOS:   PL = 38.77 + 16.7·log10(d_3D) + 18.2·log10(f_GHz)
#   NLOS:  PL = 36.85 + 30.0·log10(d_3D) + 18.9·log10(f_GHz)
#   NLOSv: PL = PL_LOS + additional_vehicle_blockage
#
# For NLOSv we use the *median* of the additional log-normal blockage
# (i.e. mu_a), which is the deterministic central case.  At typical
# 802.11p antenna heights (1.5 m) and a Type-1/2 blocker (1.6 m) we
# fall in "Case 2" (both antennas below the blocker), giving
#
#   mu_a(d) = 9 + max(0, 15·log10(d_3D) − 41)        [dB]
#
# (Case 3 — mixed antenna heights — would be mu_a − 4 dB; we keep the
# conservative Case 2 here.  Truck blockers 3.0 m would also land in
# Case 2.)
FREQ_HZ            = 5.9e9    # ITS-G5 band (matches nlosv_model_eval.cc)
TX_POWER_DBM       = 23.0     # 200 mW
BANDWIDTH_HZ       = 10e6     # 802.11p 10 MHz channel
NOISE_FIGURE_DB    = 9.0      # typical V2V receiver NF
THERMAL_NOISE_DBM  = -174.0 + 10.0 * math.log10(BANDWIDTH_HZ)
NOISE_FLOOR_DBM    = THERMAL_NOISE_DBM + NOISE_FIGURE_DB   # ≈ −95 dBm

# Use inf as a sentinel for the self-link (distance = 0).  Pandas
# / numpy can store +inf as a float.
INF = float("inf")
NEG_INF = float("-inf")


def pathloss_db(distance_m, condition):
    """V2V_URBAN path loss in dB for a 2D scenario (z = 0 → d_3D = d_2D).

    `distance_m` is the Euclidean distance between TX and RX.
    `condition` is one of "LOS", "NLOS", "NLOSv".
    """
    if distance_m <= 0.0:
        return 0.0
    d = distance_m
    f_ghz = FREQ_HZ / 1e9
    if condition == "LOS":
        return 38.77 + 16.7 * math.log10(d) + 18.2 * math.log10(f_ghz)
    if condition == "NLOS":
        return 36.85 + 30.0 * math.log10(d) + 18.9 * math.log10(f_ghz)
    if condition == "NLOSv":
        pl_los = 38.77 + 16.7 * math.log10(d) + 18.2 * math.log10(f_ghz)
        # Case 2: both antennas below blocker height (1.6 m)
        mu_a = 9.0 + max(0.0, 15.0 * math.log10(d) - 41.0)
        return pl_los + mu_a
    raise ValueError(f"unknown condition {condition!r}")


def sinr_db_from_powers(signal_dbm, interference_mw, noise_dbm):
    """Compute SINR (dB) given signal in dBm and interference in mW."""
    noise_mw = 10.0 ** (noise_dbm / 10.0)
    if math.isinf(signal_dbm):
        return INF
    if interference_mw <= 0.0:
        # Only noise
        return signal_dbm - noise_dbm
    return signal_dbm - 10.0 * math.log10(interference_mw + noise_mw)

# ── Network geometry (mirrors nodes.nod.xml) ────────────────────────────
J1 = (201.6, 200.0)   # south-east of the L
J2 = (300.0, 200.0)   # north-west of the reverse-L

# Building footprints at the 4 inside corners of each intersection.
# These are the rectangular patches a real city-block would occupy on
# the inside corner of the junction.  When the TX→RX segment passes
# through any of these the link is NLOS.  Size 15 m × 15 m is a
# reasonable city-block corner.
BUILDING_HALF = 7.5
BUILDINGS = []
for cx, cy in (J1, J2):
    # cx, cy is the centre of the intersection.  The four building
    # centres are offset by (BUILDING_HALF + ~2 m road half-width) so
    # they sit just outside the intersection footprint.
    OFFSET = BUILDING_HALF + 2.0
    for dx, dy in ((-OFFSET, -OFFSET), (OFFSET, -OFFSET),
                   (-OFFSET,  OFFSET), (OFFSET,  OFFSET)):
        BUILDINGS.append((cx + dx, cy + dy))

# AABB of every building (Liang–Barsky needs half-extents).
def get_building_aabbs():
    return [(cx, cy, BUILDING_HALF, BUILDING_HALF) for cx, cy in BUILDINGS]

# ── Vehicle AABB (used for NLOSv blocker test) ──────────────────────────
VEH_HALF_LEN = 2.25   # 4.5 m / 2
VEH_HALF_WID = 1.0    # 2.0 m / 2

# ── Liang–Barsky segment-vs-AABB (copied verbatim from the C++ module) ──
def segment_intersects_aabb(x1, y1, x2, y2, cx, cy, hl, hw):
    dx = x2 - x1
    dy = y2 - y1
    state = [0.0, 1.0]   # [t_enter, t_exit]

    def clip(p, q):
        if abs(p) < 1e-12:
            return q >= 0.0
        r = q / p
        if p < 0.0:
            state[0] = max(state[0], r)
        else:
            state[1] = min(state[1], r)
        return state[0] <= state[1]

    return (clip(-dx, x1 - (cx - hl)) and
            clip( dx, (cx + hl) - x1) and
            clip(-dy, y1 - (cy - hw)) and
            clip( dy, (cy + hw) - y1) and
            state[0] <= state[1])

# ── Per-vehicle "which road segment is this?" classification ────────────
# Used purely to produce a human-readable `reason` label.
def segment_of(x, y):
    if abs(x - 201.6) < 1.5 and 0.0 <= y <= 200.0:
        return "south_leg"
    if abs(x - 301.6) < 1.5 and 200.0 <= y <= 1000.0:
        return "north_leg"
    if abs(y - 198.4) < 1.5 and 0.0 <= x <= 200.0:
        return "west_approach"
    if abs(y - 198.4) < 1.5 and 200.0 <= x <= 300.0:
        return "east_leg"
    if abs(y - 198.4) < 1.5 and 300.0 <= x <= 500.0:
        return "leaver_east"
    if 200.0 <= x <= 214.0 and 187.0 <= y <= 200.0:
        return "J1_internal"
    if 289.0 <= x <= 315.0 and 195.0 <= y <= 211.0:
        return "J2_internal"
    return "transition"

# ── Core link classifier ────────────────────────────────────────────────
def classify_link(tx_x, tx_y, rx_x, rx_y, blockers, building_aabbs):
    """Return (label, reason) for the TX→RX link."""
    # Self-link is meaningless — distance 0, mark LOS
    if tx_x == rx_x and tx_y == rx_y:
        return "LOS", "self"

    # 1. NLOSv: any *other* vehicle's AABB intersects the segment.
    #    The blocker AABBs are pre-computed once per frame in `blockers`
    #    and exclude the TX/RX pair.
    for (bx, by, bl, bw) in blockers:
        if segment_intersects_aabb(tx_x, tx_y, rx_x, rx_y,
                                   bx, by, bl, bw):
            return "NLOSv", f"blocker@({bx:.1f},{by:.1f})"

    # 2. NLOS: any building AABB intersects the segment.
    for (bx, by, bl, bw) in building_aabbs:
        if segment_intersects_aabb(tx_x, tx_y, rx_x, rx_y,
                                   bx, by, bl, bw):
            seg_tx = segment_of(tx_x, tx_y)
            seg_rx = segment_of(rx_x, rx_y)
            # The common "leader passed J1 / J2 but trailer hasn't"
            # case shows up as a NLOS hit on the J1 or J2 building.
            if "J1" in (seg_tx, seg_rx) or (seg_tx in ("east_leg", "north_leg")
                                            and seg_rx == "south_leg"):
                return "NLOS", f"building@J1 leader-past-J1 trailer-not"
            if "J2" in (seg_tx, seg_rx) or (seg_tx in ("north_leg", "leaver_east")
                                            and seg_rx in ("south_leg", "east_leg")):
                return "NLOS", f"building@J2 leader-past-J2 trailer-not"
            return "NLOS", f"building@({bx:.1f},{by:.1f})"

    return "LOS", "clear"


# ── Main: walk every frame, every ordered pair ──────────────────────────
def main():
    df = pd.read_csv(CSV_IN, sep=",")
    df.columns = [c.strip().lstrip("#").strip() for c in df.columns]
    df["node_id"] = df["node_id"].astype(int)

    building_aabbs = get_building_aabbs()
    rows = []

    for t, g in df.groupby("time"):
        g = g.set_index("node_id")
        ids = sorted(g.index.tolist())
        # Pre-compute every vehicle's AABB (used as potential blockers
        # for every pair in this frame).
        veh_aabbs = {nid: (g.loc[nid, "x"], g.loc[nid, "y"],
                           VEH_HALF_LEN, VEH_HALF_WID)
                     for nid in ids}
        # First pass: classify every (i, j) pair and cache the
        # (link, reason, distance, pathloss, rx_power) tuple.  We also
        # accumulate the per-rx list of (tx, rx_power) for the
        # interference sum in the second pass.
        pair_info = {}                 # (i, j) -> (label, reason, d, pl_db, rx_p_dbm)
        rx_powers = {nid: [] for nid in ids}
        for i in ids:
            ix, iy, iv = g.loc[i, "x"], g.loc[i, "y"], g.loc[i, "speed"]
            for j in ids:
                jx, jy, jv = g.loc[j, "x"], g.loc[j, "y"], g.loc[j, "speed"]
                d = math.hypot(jx - ix, jy - iy)
                # Blockers = all vehicles except i and j themselves.
                blockers = [veh_aabbs[k] for k in ids if k != i and k != j]
                label, reason = classify_link(ix, iy, jx, jy,
                                              blockers, building_aabbs)
                pl_db = pathloss_db(d, label)
                # Self-link: 0 m → 0 dB path loss (signal == TX power).
                if i == j:
                    rx_p_dbm = TX_POWER_DBM
                else:
                    rx_p_dbm = TX_POWER_DBM - pl_db
                pair_info[(i, j)] = (label, reason, d, pl_db, rx_p_dbm)
                rx_powers[j].append((i, rx_p_dbm))

        # Second pass: per RX, sum interference from all *other* TX in
        # linear mW and emit the rows.
        for j in ids:
            interf_mw = 0.0
            for (i, rx_p_dbm) in rx_powers[j]:
                if i == j:
                    continue
                interf_mw += 10.0 ** (rx_p_dbm / 10.0)
            interf_dbm = (10.0 * math.log10(interf_mw)
                          if interf_mw > 0.0 else NEG_INF)
            for i in ids:
                label, reason, d, pl_db, rx_p_dbm = pair_info[(i, j)]
                if i == j:
                    # Self-link: no interference from the RX itself,
                    # and the signal is "perfect" (path loss = 0).
                    interf_dbm_this = NEG_INF
                    sinr = INF
                else:
                    interf_dbm_this = interf_dbm
                    sinr = sinr_db_from_powers(rx_p_dbm, interf_mw,
                                               NOISE_FLOOR_DBM)
                # SNR (no interference) for reference.
                if i == j:
                    snr = INF
                else:
                    snr = rx_p_dbm - NOISE_FLOOR_DBM
                rows.append({
                    "time":             round(float(t), 1),
                    "tx":               int(i),
                    "rx":               int(j),
                    "tx_x":             round(float(g.loc[i, "x"]), 2),
                    "tx_y":             round(float(g.loc[i, "y"]), 2),
                    "tx_v":             round(float(g.loc[i, "speed"]), 2),
                    "rx_x":             round(float(g.loc[j, "x"]), 2),
                    "rx_y":             round(float(g.loc[j, "y"]), 2),
                    "rx_v":             round(float(g.loc[j, "speed"]), 2),
                    "distance_m":       round(d, 2),
                    "link":             label,
                    "reason":           reason,
                    "pathloss_db":      round(pl_db, 2),
                    "rx_power_dbm":     round(rx_p_dbm, 2),
                    "noise_dbm":        round(NOISE_FLOOR_DBM, 2),
                    "interference_dbm": (None if math.isinf(interf_dbm_this)
                                          and interf_dbm_this < 0
                                          else round(interf_dbm_this, 2)),
                    "snr_db":           (None if math.isinf(snr)
                                          else round(snr, 2)),
                    "sinr_db":          (None if math.isinf(sinr)
                                          else round(sinr, 2)),
                })

    out = pd.DataFrame(rows)
    out.to_csv(CSV_OUT, index=False)
    avg_per_frame = len(out) / df.time.nunique()
    print(f"Wrote {CSV_OUT}  ({len(out):,} rows, {df.time.nunique()} frames, "
          f"avg {avg_per_frame:.1f} pairs/frame)")
    # Per-class counts
    counts = out["link"].value_counts()
    print("Per-class counts:")
    for k, v in counts.items():
        print(f"  {k:6s}: {v:>9,}")
    # Top reasons
    print("\nTop reasons per class:")
    for cls in ("NLOSv", "NLOS", "LOS"):
        sub = out[out.link == cls]
        if len(sub) == 0:
            continue
        print(f"  {cls}:")
        for reason, n in sub.reason.value_counts().head(6).items():
            print(f"    {n:>7,}  {reason}")

    # SINR / RX-power summary (excluding self-links)
    print("\nV2V_URBAN link-budget summary "
          f"(f={FREQ_HZ/1e9:.1f} GHz, "
          f"Ptx={TX_POWER_DBM:.0f} dBm, "
          f"BW={BANDWIDTH_HZ/1e6:.0f} MHz, "
          f"NF={NOISE_FIGURE_DB:.0f} dB → noise floor {NOISE_FLOOR_DBM:.1f} dBm):")
    real = out[out.tx != out.rx].copy()
    # path loss per class
    print("  Mean pathloss (dB):")
    for cls in ("LOS", "NLOS", "NLOSv"):
        sub = real[real.link == cls]
        if len(sub):
            print(f"    {cls:6s}: {sub.pathloss_db.mean():6.1f}  "
                  f"(min {sub.pathloss_db.min():.1f}, max {sub.pathloss_db.max():.1f}, "
                  f"N={len(sub):,})")
    # interference / SINR
    sinr = real["sinr_db"].dropna()
    snr = real["snr_db"].dropna()
    print(f"  Interference (dBm): mean {real.interference_dbm.mean():.1f}, "
          f"min {real.interference_dbm.min():.1f}, max {real.interference_dbm.max():.1f}")
    print(f"  SNR (dB, no interferers): mean {snr.mean():.1f}, "
          f"median {snr.median():.1f}, "
          f"p10 {snr.quantile(0.10):.1f}, p90 {snr.quantile(0.90):.1f}")
    print(f"  SINR (dB, all-tx-concurrent interference): mean {sinr.mean():.1f}, "
          f"median {sinr.median():.1f}, "
          f"p10 {sinr.quantile(0.10):.1f}, p90 {sinr.quantile(0.90):.1f}, "
          f"min {sinr.min():.1f}, max {sinr.max():.1f}")
    # Per-class SINR
    print("  Per-class SINR (dB):")
    for cls in ("LOS", "NLOS", "NLOSv"):
        sub = real[real.link == cls]["sinr_db"].dropna()
        if len(sub):
            print(f"    {cls:6s}: mean {sub.mean():6.1f}  median {sub.median():6.1f}  "
                  f"min {sub.min():.1f}  N={len(sub):,}")


if __name__ == "__main__":
    main()
