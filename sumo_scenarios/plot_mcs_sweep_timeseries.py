#!/usr/bin/env python3
"""
plot_mcs_sweep_timeseries.py
Time-series KPI plots with one coloured line per MCS (7, 10, 12, 14, 20).

Layout  : 5 rows × 3 cols  (metric × scenario)
Lines   : one colour per MCS — solid = worst RX, dashed = p5 RX
Shading : band between p5 and worst for each MCS
Channel : TX-side metrics (rows 3-4) — single line per MCS (mean ± CI band)

Metrics
-------
  Row 0 — PRR
  Row 1 — PIR  [ms]
  Row 2 — Throughput [kbps]
  Row 3 — Channel occupancy [%]  (TX-side)
  Row 4 — Wasted subchannel equivalents  (TX-side)
"""

from pathlib import Path
import sqlite3
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.cm as cm
from matplotlib.lines import Line2D
import warnings
warnings.filterwarnings("ignore")

# ── paths ─────────────────────────────────────────────────────────────────────
HERE    = Path(__file__).parent
PLOTS   = HERE / "plots"
PLOTS.mkdir(exist_ok=True)
RESULTS = (HERE.parent / "scratch_overlay" / "platoon_baseline_eval"
           / "results_mcs_sweep")

# ── constants ─────────────────────────────────────────────────────────────────
MCS_VALUES  = [7, 10, 12, 14, 20]
N_SEEDS     = 10
RX_NODES    = list(range(1, 9))
BWP_TOTAL   = 100
SCH_SIZE    = 10
WINDOW_S    = 5.0
STEP_S      = 1.0

SCENARIOS = {
    "slinky":   dict(label="Scenario A — Slinky/Accordion",
                     key="ccam_platoon_slinky",
                     vlines=[(15,"peak",":"),(30,"min",":"),(45,"peak",":"),
                             (60,"min",":"),(75,"peak",":")]),
    "gap_open": dict(label="Scenario B — CACC Gap Opening",
                     key="ccam_platoon_gap_open",
                     vlines=[(22,"open","--"),(36,"open","--"),
                             (72,"close",":"),(86,"close",":")]),
    "merge":    dict(label="Scenario C — Platoon Merge",
                     key="ccam_platoon_merge",
                     vlines=[(70,"merge","--")]),
}

# ── MCS colour palette (blue→green→amber→orange→red) ─────────────────────────
_cmap   = cm.get_cmap("RdYlBu_r")          # low MCS=blue, high MCS=red
_norms  = np.linspace(0.10, 0.90, len(MCS_VALUES))
MCS_COLOR = {m: _cmap(n) for m, n in zip(MCS_VALUES, _norms)}


# ── sliding-window helpers ────────────────────────────────────────────────────
def _windows(t_min, t_max):
    return np.arange(t_min + WINDOW_S/2, t_max - WINDOW_S/2 + STEP_S, STEP_S)


def _prr_per_node_per_seed(pkt: pd.DataFrame, times: np.ndarray) -> np.ndarray:
    """
    Returns array (n_seeds, n_nodes, n_times) of PRR values.
    seeds and nodes indexed by position in RX_NODES list.
    """
    seeds = sorted(pkt.seed.unique())
    out   = np.full((len(seeds), len(RX_NODES), len(times)), np.nan)
    for si, s in enumerate(seeds):
        sg  = pkt[pkt.seed == s]
        tx  = sg[sg.txRx == "tx"].set_index("pktSeqNum")["timeSec"]
        rxg = sg[sg.txRx == "rx"].groupby("nodeId")
        for ni, nid in enumerate(RX_NODES):
            if nid not in rxg.groups:
                continue
            rx = rxg.get_group(nid).set_index("pktSeqNum")["timeSec"]
            for ti, t in enumerate(times):
                lo, hi   = t - WINDOW_S/2, t + WINDOW_S/2
                tx_w     = tx[(tx >= lo) & (tx < hi)]
                if len(tx_w) == 0:
                    continue
                rx_w     = rx[(rx >= lo) & (rx < hi + 0.05)]
                out[si, ni, ti] = rx_w.index.isin(tx_w.index).sum() / len(tx_w)
    return out   # (seeds, nodes, times)


def _pir_per_node_per_seed(pkt: pd.DataFrame, times: np.ndarray) -> np.ndarray:
    seeds = sorted(pkt.seed.unique())
    out   = np.full((len(seeds), len(RX_NODES), len(times)), np.nan)
    for si, s in enumerate(seeds):
        sg  = pkt[pkt.seed == s]
        rxg = sg[sg.txRx == "rx"].sort_values("timeSec").groupby("nodeId")
        for ni, nid in enumerate(RX_NODES):
            if nid not in rxg.groups:
                continue
            rx_t    = rxg.get_group(nid)["timeSec"].values
            if len(rx_t) < 2:
                continue
            mid_t   = 0.5*(rx_t[:-1] + rx_t[1:])
            gaps_ms = np.diff(rx_t) * 1000.0
            for ti, t in enumerate(times):
                lo, hi = t - WINDOW_S/2, t + WINDOW_S/2
                mask   = (mid_t >= lo) & (mid_t < hi)
                if mask.sum() > 0:
                    out[si, ni, ti] = np.nanmean(gaps_ms[mask])
    return out


def _tput_per_node_per_seed(pkt: pd.DataFrame, times: np.ndarray) -> np.ndarray:
    seeds = sorted(pkt.seed.unique())
    out   = np.full((len(seeds), len(RX_NODES), len(times)), np.nan)
    for si, s in enumerate(seeds):
        sg  = pkt[pkt.seed == s]
        rxg = sg[sg.txRx == "rx"].groupby("nodeId")
        for ni, nid in enumerate(RX_NODES):
            if nid not in rxg.groups:
                continue
            rx = rxg.get_group(nid)
            for ti, t in enumerate(times):
                lo, hi = t - WINDOW_S/2, t + WINDOW_S/2
                rx_w   = rx[(rx.timeSec >= lo) & (rx.timeSec < hi)]
                out[si, ni, ti] = rx_w.pktSizeBytes.sum() * 8 / (WINDOW_S * 1000.0)
    return out


def _chan_per_seed(tx_mac: pd.DataFrame, rx_phy: pd.DataFrame,
                   pkt: pd.DataFrame, times: np.ndarray) -> tuple:
    """
    Returns (occ_arr, waste_arr) each (n_seeds, n_times).
    """
    seeds    = sorted(tx_mac.seed.unique())
    occ_out  = np.full((len(seeds), len(times)), np.nan)
    wst_out  = np.full((len(seeds), len(times)), np.nan)
    pkt_sz_map = {s: int(g.pktSizeBytes.mode()[0])
                  for s, g in pkt[pkt.txRx == "tx"].groupby("seed")}
    for si, s in enumerate(seeds):
        tm  = tx_mac[tx_mac.seed == s]
        rp  = rx_phy[(rx_phy.seed == s) & (rx_phy.ndi == 1)]
        p_sz= pkt_sz_map.get(s, 800)
        tm_t  = tm.timeMs.values / 1000.0
        tm_rb = tm.rbLen.values
        rp_t  = rp.timeMs.values / 1000.0
        rp_tb = rp.tbSizeBytes.values
        rp_rb = rp.psschRbLen.values
        for ti, t in enumerate(times):
            lo, hi = t - WINDOW_S/2, t + WINDOW_S/2
            mk_tx  = (tm_t >= lo) & (tm_t < hi)
            mk_rx  = (rp_t >= lo) & (rp_t < hi)
            if mk_tx.sum() > 0:
                occ_out[si, ti] = np.mean(tm_rb[mk_tx]) / BWP_TOTAL
            if mk_rx.sum() > 0:
                fill = np.minimum(p_sz / rp_tb[mk_rx], 1.0)
                nsub = rp_rb[mk_rx] / SCH_SIZE
                wst_out[si, ti] = np.mean(nsub * (1.0 - fill))
    return occ_out, wst_out


# ── per-MCS aggregated time-series  ──────────────────────────────────────────
def _agg_rx(cube: np.ndarray, higher_is_worse: bool = False):
    """
    cube: (seeds, nodes, times)
    Returns worst_mean, p5_mean, mean_mean  each (times,)
    """
    # worst per seed = min (or max) across nodes
    if higher_is_worse:
        worst_per_seed = np.nanmax(cube, axis=1)     # (seeds, times)
    else:
        worst_per_seed = np.nanmin(cube, axis=1)
    worst_mean = np.nanmean(worst_per_seed, axis=0)

    # p5 across all seeds × nodes at each time step
    flat = cube.reshape(-1, cube.shape[2])           # (seeds*nodes, times)
    p5_mean = np.nanpercentile(flat, 5, axis=0) if not higher_is_worse \
              else np.nanpercentile(flat, 95, axis=0)

    mean_mean = np.nanmean(cube, axis=(0, 1))
    return worst_mean, p5_mean, mean_mean


def _agg_chan(arr: np.ndarray):
    """arr: (seeds, times) → mean, lo, hi"""
    return (np.nanmean(arr, axis=0),
            np.nanpercentile(arr, 5,  axis=0),
            np.nanpercentile(arr, 95, axis=0))


# ── load helpers ──────────────────────────────────────────────────────────────
def _load_pkt(scen_key, mcs):
    dfs = []
    for s in range(1, N_SEEDS+1):
        db = RESULTS / scen_key / f"{scen_key}_mcs{mcs}_seed{s}.db"
        if not db.exists():
            continue
        con = sqlite3.connect(db)
        df  = pd.read_sql_query(
            "SELECT timeSec, txRx, nodeId, pktSizeBytes, pktSeqNum FROM pktTxRx", con)
        con.close()
        df["seed"] = s
        dfs.append(df)
    return pd.concat(dfs, ignore_index=True)


def _load_mac(scen_key, mcs):
    tx_dfs, rx_dfs = [], []
    for s in range(1, N_SEEDS+1):
        db = RESULTS / scen_key / f"{scen_key}_mcs{mcs}_seed{s}.db"
        if not db.exists():
            continue
        con = sqlite3.connect(db)
        tx  = pd.read_sql_query(
            "SELECT timeMs, rbLen, ndi, rv FROM psschTxUeMac", con)
        rx  = pd.read_sql_query(
            "SELECT timeMs, psschRbLen, tbSizeBytes, ndi FROM psschRxUePhy "
            "WHERE txRnti=1 AND rnti=2", con)
        con.close()
        tx["seed"] = s; rx["seed"] = s
        tx_dfs.append(tx); rx_dfs.append(rx)
    return pd.concat(tx_dfs, ignore_index=True), pd.concat(rx_dfs, ignore_index=True)


# ── draw helpers ──────────────────────────────────────────────────────────────
def _draw(ax, times, worst, p5, mean, color, mcs_label):
    ax.plot(times, worst, color=color, lw=1.6, ls="-",  zorder=4,
            label=f"MCS {mcs_label}  worst")
    ax.plot(times, p5,    color=color, lw=1.2, ls="--", zorder=3, alpha=0.80,
            label=f"MCS {mcs_label}  p5")
    ax.fill_between(times, p5, worst, color=color, alpha=0.10, zorder=2)


def _draw_chan_line(ax, times, mean, lo, hi, color, mcs_label):
    ax.plot(times, mean, color=color, lw=1.6, ls="-", zorder=4,
            label=f"MCS {mcs_label}")
    ax.fill_between(times, lo, hi, color=color, alpha=0.12, zorder=2)


def _vlines(ax, vlines, y_top):
    for xv, lbl, vls in vlines:
        ax.axvline(xv, color="gray", lw=0.8, ls=vls, alpha=0.55, zorder=1)
        ax.text(xv + 0.3, y_top, lbl, fontsize=5.5, color="dimgray",
                va="top", rotation=0)


# ── main figure ───────────────────────────────────────────────────────────────
def plot_all():
    n_rows  = 5
    n_cols  = len(SCENARIOS)
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(15, 17))

    fig.suptitle(
        "MCS sweep — KPI time-series  ·  solid = worst RX  ·  dashed = p5 RX  "
        "·  band = p5–worst range\n"
        "Rows 3–4 (channel metrics) = TX-side: solid = mean, band = 5th–95th pct "
        "across seeds  ·  5-s sliding window  ·  10 seeds/MCS",
        fontsize=10, fontweight="bold", y=1.005,
    )

    ylabels = [
        "PRR",
        "Mean PIR  [ms]",
        "Throughput  [kbps]",
        "Channel occupancy  [%]",
        "Wasted subch. equiv.",
    ]
    row_ref_line = [1.0, 10.0, None, None, None]     # horizontal reference

    for col, (scen_name, scen_meta) in enumerate(SCENARIOS.items()):
        scen_key  = scen_meta["key"]
        vlines_cfg = scen_meta["vlines"]
        axes[0, col].set_title(scen_meta["label"], fontsize=10,
                               fontweight="bold", pad=4)

        # Determine time axis from MCS14 (reference)
        pkt_ref = _load_pkt(scen_key, 14)
        tx_ref  = pkt_ref[pkt_ref.txRx == "tx"]
        t_min   = tx_ref.timeSec.min()
        t_max   = tx_ref.timeSec.max()
        times   = _windows(t_min, t_max)

        for row in range(n_rows):
            ax = axes[row, col]
            ax.set_xlim(t_min, t_max)
            ax.grid(ls=":", alpha=0.4, zorder=0)
            ax.set_xlabel("Simulation time  [s]", fontsize=8)

        # ── per-MCS traces ────────────────────────────────────────────────────
        for mcs in MCS_VALUES:
            color = MCS_COLOR[mcs]
            print(f"    {scen_name}  MCS {mcs} …", end=" ", flush=True)

            # --- rows 0-2: per-RX metrics -----------------------------------
            pkt = _load_pkt(scen_key, mcs) if mcs != 14 else pkt_ref

            # Use local times for this MCS (sim times may differ slightly)
            tx_pkt = pkt[pkt.txRx == "tx"]
            t0 = tx_pkt.timeSec.min(); t1 = tx_pkt.timeSec.max()
            t_local = _windows(t0, t1)

            prr_cube  = _prr_per_node_per_seed(pkt, t_local)
            pir_cube  = _pir_per_node_per_seed(pkt, t_local)
            tput_cube = _tput_per_node_per_seed(pkt, t_local)

            w_prr,  p5_prr,  m_prr  = _agg_rx(prr_cube,  higher_is_worse=False)
            w_pir,  p5_pir,  m_pir  = _agg_rx(pir_cube,  higher_is_worse=True)
            w_tput, p5_tput, m_tput = _agg_rx(tput_cube, higher_is_worse=False)

            _draw(axes[0, col], t_local, w_prr,  p5_prr,  m_prr,  color, mcs)
            _draw(axes[1, col], t_local, w_pir,  p5_pir,  m_pir,  color, mcs)
            _draw(axes[2, col], t_local, w_tput, p5_tput, m_tput, color, mcs)

            # --- rows 3-4: channel metrics -----------------------------------
            tx_mac, rx_phy = _load_mac(scen_key, mcs)
            occ_arr, wst_arr = _chan_per_seed(tx_mac, rx_phy, pkt, t_local)
            occ_m, occ_lo, occ_hi = _agg_chan(occ_arr)
            wst_m, wst_lo, wst_hi = _agg_chan(wst_arr)

            _draw_chan_line(axes[3, col], t_local,
                           occ_m*100, occ_lo*100, occ_hi*100, color, mcs)
            _draw_chan_line(axes[4, col], t_local,
                           wst_m, wst_lo, wst_hi, color, mcs)
            print("✓")

        # ── axis limits, reference lines, event markers ───────────────────────
        axes[0, col].set_ylim(0, 1.05)
        axes[0, col].yaxis.set_major_formatter(
            plt.FuncFormatter(lambda v, _: f"{v:.0%}"))

        pir_max = max(
            np.nanmax(axes[1, col].get_lines()[i].get_ydata())
            for i in range(len(axes[1, col].get_lines()))
            if len(axes[1, col].get_lines()[i].get_ydata()) > 0
        ) if axes[1, col].get_lines() else 200
        pir_top = min(max(pir_max * 1.1, 30), 500)
        axes[1, col].set_ylim(0, pir_top)

        axes[2, col].set_ylim(0, None)
        axes[3, col].set_ylim(0, 105)
        axes[4, col].set_ylim(0, None)

        # Reference lines
        axes[0, col].axhline(1.0,  color="gray",      lw=0.8, ls="--", alpha=0.4)
        axes[1, col].axhline(10.0, color="firebrick",  lw=0.9, ls=":",  alpha=0.7)

        # Event markers (use axes[0] y limits for text placement)
        for row in range(n_rows):
            ax  = axes[row, col]
            yl  = ax.get_ylim()
            _vlines(ax, vlines_cfg, yl[1] * 0.97)

    # ── y-labels (left column only) ───────────────────────────────────────────
    for row, lbl in enumerate(ylabels):
        axes[row, 0].set_ylabel(lbl, fontsize=9)

    # ── shared legend (MCS colours + line styles) ─────────────────────────────
    mcs_handles = [
        Line2D([0],[0], color=MCS_COLOR[m], lw=2.0, ls="-", marker="none",
               label=f"MCS {m}")
        for m in MCS_VALUES
    ]
    style_handles = [
        Line2D([0],[0], color="dimgray", lw=1.6, ls="-",  label="Worst RX"),
        Line2D([0],[0], color="dimgray", lw=1.2, ls="--", label="p5 RX"),
        Line2D([0],[0], color="firebrick", lw=0.9, ls=":", label="Nominal (10 ms PIR)"),
    ]
    axes[0, 2].legend(handles=mcs_handles + style_handles,
                      fontsize=7.5, ncol=2, loc="lower right",
                      framealpha=0.90, title="Line colour / style", title_fontsize=7)

    fig.tight_layout(rect=[0, 0, 1, 1])
    out = PLOTS / "mcs_sweep_timeseries.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"\n  Saved {out}")


# ── main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("MCS sweep time-series KPI plots …\n")
    plot_all()
