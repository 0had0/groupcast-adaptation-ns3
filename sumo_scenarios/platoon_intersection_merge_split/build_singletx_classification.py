#!/usr/bin/env python3
"""Single-TX, multi-frequency distance + SINR CSV.

Mirrors `build_distance_classification.py` but with two changes:

  1. **Single transmitter.**  Only the leader (v0) is on the air, so
     interference is zero and SINR == SNR.  Every other vehicle is
     silent — they're treated as geometry (AABB blockers for NLOSv)
     but they don't radiate.

  2. **Two frequency profiles.**  3GPP path-loss constants and a
     different receiver noise floor per profile:

        5.9 GHz   — TR 37.885 V2V Urban (10 MHz ch., NF 9 dB)
        mmWave 28 — TR 38.901 UMi Street Canyon (100 MHz ch., NF 10 dB)

     The geometry-based LOS / NLOSv / NLOS classification is identical
     to the existing script; only the dB conversion changes.

Outputs:
  * `distances_classified_singletx_5.9ghz.csv`  (5.9 GHz, 1 TX)
  * `distances_classified_singletx_mmwave.csv`  (28 GHz, 1 TX)

Both CSVs have the same schema as `distances_classified.csv`
(time, tx, rx, …, pathloss_db, rx_power_dbm, noise_dbm, snr_db,
sinr_db) so downstream tools (relay_selection, plot_sinr_all_rx, …)
can be pointed at either file by changing the path argument.
"""
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
CSV_IN = HERE / "platoon_intersection_merge_split.mob.csv"

# ── Network geometry (mirrors nodes.nod.xml) ────────────────────────────
J1 = (201.6, 200.0)
J2 = (300.0, 200.0)
BUILDING_HALF = 7.5
OFFSET = BUILDING_HALF + 2.0
BUILDINGS = []
for cx, cy in (J1, J2):
    for dx, dy in ((-OFFSET, -OFFSET), (OFFSET, -OFFSET),
                   (-OFFSET,  OFFSET), (OFFSET,  OFFSET)):
        BUILDINGS.append((cx + dx, cy + dy))


def get_building_aabbs():
    return [(cx, cy, BUILDING_HALF, BUILDING_HALF) for cx, cy in BUILDINGS]


VEH_HALF_LEN = 2.25
VEH_HALF_WID = 1.0


# ── Frequency profiles ─────────────────────────────────────────────────
@dataclass(frozen=True)
class Profile:
    """One (frequency, bandwidth, NF, path-loss-formula) configuration."""
    name: str
    freq_hz: float
    bw_hz: float
    nf_db: float
    # V2V Urban / UMi path-loss constants — see the docstring header.
    # PL = A + B·log10(d) + F·log10(f_GHz)
    los_a: float
    los_b: float
    los_f: float
    nlos_a: float
    nlos_b: float
    nlos_f: float
    # NLOSv additional vehicle-blockage median (dB).  Sub-6 GHz uses
    # a distance-dependent log-normal median (TR 37.885 Case 2),
    # mmWave uses a flat 20 dB constant (typical IEEE 802.11ad
    # vehicle-shadowing measurement).
    nlosv_mode: str = "tr37885"   # or "flat"
    nlosv_flat_db: float = 0.0
    # TX power (dBm) — 200 mW for both
    tx_power_dbm: float = 23.0

    @property
    def noise_dbm(self):
        return -174.0 + 10.0 * math.log10(self.bw_hz) + self.nf_db


PROFILES = {
    "5.9ghz": Profile(
        name="5.9GHz",
        freq_hz=5.9e9,
        bw_hz=10e6,
        nf_db=9.0,
        # TR 37.885 Table 6.2.1-1 V2V Urban
        los_a=38.77, los_b=16.7, los_f=18.2,
        nlos_a=36.85, nlos_b=30.0, nlos_f=18.9,
        nlosv_mode="tr37885",
    ),
    "mmwave": Profile(
        name="mmWave28",
        freq_hz=28e9,
        bw_hz=100e6,
        nf_db=10.0,
        # TR 38.901 Table 7.4.1-1 UMi Street Canyon (line-of-sight)
        los_a=32.4,  los_b=21.0, los_f=20.0,
        # TR 38.901 Table 7.4.1-1 UMi NLOS (option 1; dominates LOS
        # beyond the break point for 28 GHz)
        nlos_a=22.4, nlos_b=35.3, nlos_f=21.3,
        # Flat 20 dB vehicle-blockage median (typical 28 GHz
        # in-vehicle / car-shadowing measurement, e.g. IEEE 802.11ad).
        nlosv_mode="flat", nlosv_flat_db=20.0,
    ),
}


def pathloss_db(profile: Profile, distance_m: float, condition: str) -> float:
    """Frequency-aware path loss in dB (2-D scenario, z = 0)."""
    if distance_m <= 0.0:
        return 0.0
    d = distance_m
    f_ghz = profile.freq_hz / 1e9
    if condition == "LOS":
        return (profile.los_a
                + profile.los_b * math.log10(d)
                + profile.los_f * math.log10(f_ghz))
    if condition == "NLOS":
        return (profile.nlos_a
                + profile.nlos_b * math.log10(d)
                + profile.nlos_f * math.log10(f_ghz))
    if condition == "NLOSv":
        pl_los = (profile.los_a
                  + profile.los_b * math.log10(d)
                  + profile.los_f * math.log10(f_ghz))
        if profile.nlosv_mode == "tr37885":
            # TR 37.885 Case 2: both antennas below blocker height.
            mu_a = 9.0 + max(0.0, 15.0 * math.log10(d) - 41.0)
            return pl_los + mu_a
        if profile.nlosv_mode == "flat":
            return pl_los + profile.nlosv_flat_db
        raise ValueError(profile.nlosv_mode)
    raise ValueError(f"unknown condition {condition!r}")


# ── Geometry classifier (identical to build_distance_classification.py)
def segment_intersects_aabb(x1, y1, x2, y2, cx, cy, hl, hw):
    dx = x2 - x1
    dy = y2 - y1
    state = [0.0, 1.0]

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


def classify_link(tx_x, tx_y, rx_x, rx_y, blockers, building_aabbs):
    if tx_x == rx_x and tx_y == rx_y:
        return "LOS", "self"
    for (bx, by, bl, bw) in blockers:
        if segment_intersects_aabb(tx_x, tx_y, rx_x, rx_y, bx, by, bl, bw):
            return "NLOSv", f"blocker@({bx:.1f},{by:.1f})"
    for (bx, by, bl, bw) in building_aabbs:
        if segment_intersects_aabb(tx_x, tx_y, rx_x, rx_y, bx, by, bl, bw):
            seg_tx = segment_of(tx_x, tx_y)
            seg_rx = segment_of(rx_x, rx_y)
            if "J1" in (seg_tx, seg_rx) or (seg_tx in ("east_leg", "north_leg")
                                            and seg_rx == "south_leg"):
                return "NLOS", "building@J1 leader-past-J1 trailer-not"
            if "J2" in (seg_tx, seg_rx) or (seg_tx in ("north_leg", "leaver_east")
                                            and seg_rx in ("south_leg", "east_leg")):
                return "NLOS", "building@J2 leader-past-J2 trailer-not"
            return "NLOS", f"building@({bx:.1f},{by:.1f})"
    return "LOS", "clear"


# ── Main ────────────────────────────────────────────────────────────────
def build_for_profile(profile: Profile, df: pd.DataFrame) -> pd.DataFrame:
    """Run the per-frame classification + per-link path loss + SINR for
    a single frequency profile, single-TX (leader only).

    SINR == SNR because no interferers are on the air.
    """
    building_aabbs = get_building_aabbs()
    rows = []

    for t, g in df.groupby("time"):
        g = g.set_index("node_id")
        ids = sorted(g.index.tolist())
        veh_aabbs = {nid: (g.loc[nid, "x"], g.loc[nid, "y"],
                           VEH_HALF_LEN, VEH_HALF_WID)
                     for nid in ids}

        # First pass: classify and compute per-pair pathloss / rx_power.
        pair_info = {}
        for i in ids:
            ix, iy, iv = g.loc[i, "x"], g.loc[i, "y"], g.loc[i, "speed"]
            for j in ids:
                jx, jy, jv = g.loc[j, "x"], g.loc[j, "y"], g.loc[j, "speed"]
                d = math.hypot(jx - ix, jy - iy)
                blockers = [veh_aabbs[k] for k in ids
                            if k != i and k != j]
                label, reason = classify_link(ix, iy, jx, jy,
                                              blockers, building_aabbs)
                pl_db = pathloss_db(profile, d, label)
                if i == j:
                    rx_p_dbm = profile.tx_power_dbm
                else:
                    rx_p_dbm = profile.tx_power_dbm - pl_db
                pair_info[(i, j)] = (label, reason, d, pl_db, rx_p_dbm)

        # Second pass: per (time, rx, tx) write the row.  Single-TX
        # means interference = 0 so SINR = SNR.
        for j in ids:
            for i in ids:
                label, reason, d, pl_db, rx_p_dbm = pair_info[(i, j)]
                if i == j:
                    interf_dbm = NEG_INF
                    snr = INF
                    sinr = INF
                else:
                    interf_dbm = NEG_INF        # no other TXs on the air
                    snr = rx_p_dbm - profile.noise_dbm
                    sinr = snr                  # SINR == SNR
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
                    "noise_dbm":        round(profile.noise_dbm, 2),
                    "interference_dbm": (None
                                         if math.isinf(interf_dbm)
                                            and interf_dbm < 0
                                         else round(interf_dbm, 2)),
                    "snr_db":           (None if math.isinf(snr)
                                          else round(snr, 2)),
                    "sinr_db":          (None if math.isinf(sinr)
                                          else round(sinr, 2)),
                })

    return pd.DataFrame(rows)


# Sentinels.
INF = float("inf")
NEG_INF = float("-inf")


def main():
    df = pd.read_csv(CSV_IN, sep=",")
    df.columns = [c.strip().lstrip("#").strip() for c in df.columns]
    df["node_id"] = df["node_id"].astype(int)

    print(f"Loaded {CSV_IN.name}: {len(df):,} rows, "
          f"{df.time.nunique()} frames\n")

    for key, profile in PROFILES.items():
        out_path = HERE / f"distances_classified_singletx_{key}.csv"
        out = build_for_profile(profile, df)
        out.to_csv(out_path, index=False)
        # Per-class path-loss summary, mirroring build_distance_classification.py.
        real = out[out.tx != out.rx]
        print(f"=== {profile.name}   (single-TX, leader only) ===")
        print(f"  freq  = {profile.freq_hz/1e9:.1f} GHz")
        print(f"  BW    = {profile.bw_hz/1e6:.0f} MHz")
        print(f"  NF    = {profile.nf_db:.0f} dB")
        print(f"  noise = {profile.noise_dbm:.1f} dBm")
        print(f"  P_tx  = {profile.tx_power_dbm:.0f} dBm")
        print(f"  Wrote {out_path.name}  ({len(out):,} rows, "
              f"{df.time.nunique()} frames × avg {len(out)/df.time.nunique():.1f} pairs/frame)")
        print("  Per-class mean pathloss (dB):")
        for cls in ("LOS", "NLOS", "NLOSv"):
            sub = real[real.link == cls]
            if len(sub):
                print(f"    {cls:6s}: {sub.pathloss_db.mean():6.1f}  "
                      f"(min {sub.pathloss_db.min():.1f}, max {sub.pathloss_db.max():.1f}, "
                      f"N={len(sub):,})")
        snr = real["sinr_db"].dropna()
        print(f"  SINR (dB, single-TX): mean {snr.mean():.1f}, "
              f"median {snr.median():.1f}, "
              f"p10 {snr.quantile(0.10):.1f}, p90 {snr.quantile(0.90):.1f}, "
              f"min {snr.min():.1f}, max {snr.max():.1f}")
        print("  Per-class SINR (dB):")
        for cls in ("LOS", "NLOS", "NLOSv"):
            sub = real[real.link == cls]["sinr_db"].dropna()
            if len(sub):
                print(f"    {cls:6s}: mean {sub.mean():6.1f}  median {sub.median():6.1f}  "
                      f"min {sub.min():.1f}  N={len(sub):,}")
        print()


if __name__ == "__main__":
    main()