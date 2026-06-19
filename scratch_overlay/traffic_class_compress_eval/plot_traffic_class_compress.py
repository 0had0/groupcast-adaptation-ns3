#!/usr/bin/env python3
"""
plot_traffic_class_compress.py

Standalone diagnostic plots for the adaptive-compression campaign.
Reads tidy CSVs from results_traffic_class_compress_eval/tidy/ and
writes PNGs into results_traffic_class_compress_eval/plots/.

Run after extract_kpis.py.
"""

from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import warnings
warnings.filterwarnings("ignore")

SCRIPT_DIR = Path(__file__).resolve().parent
TIDY_DIR   = SCRIPT_DIR / "results_traffic_class_compress_eval" / "tidy"
PLOT_DIR   = SCRIPT_DIR / "results_traffic_class_compress_eval" / "plots"

CLASS_ORDER  = ["periodic1", "periodic2", "periodic3", "aperiodic1", "aperiodic2"]
CLASS_LABELS = {
    "periodic1":  "P1\n200B·10Hz",
    "periodic2":  "P2\n1kB·10Hz",
    "periodic3":  "P3\n45kB·1Hz",
    "aperiodic1": "A1\n1kB·aperiodic",
    "aperiodic2": "A2\n20kB·aperiodic",
}
CODEC_NAMES  = {0: "NONE", 1: "LZ4", 2: "ZSTD_L1", 3: "ZSTD_L3"}
CODEC_COLORS = {0: "#aaaaaa", 1: "#4daf4a", 2: "#377eb8", 3: "#e41a1c"}

PALETTE = plt.cm.tab10.colors


def load(name):
    p = TIDY_DIR / name
    if not p.exists():
        print(f"  [missing] {p}")
        return pd.DataFrame()
    return pd.read_csv(p)


def save(fig, name):
    PLOT_DIR.mkdir(parents=True, exist_ok=True)
    path = PLOT_DIR / name
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → {path}")


# ──────────────────────────────────────────────────────────────────────────
# §1. PRR / PIR / throughput distributions per class
# ──────────────────────────────────────────────────────────────────────────

def plot_kpi_distributions(rx):
    classes = [c for c in CLASS_ORDER if c in rx.trafficClass.unique()]
    metrics = [
        ("prr",      "PRR",           (0, 1.05), None),
        ("pir_ms",   "PIR (ms)",      (0, None),  None),
        ("tput_kbps","Tput (kbps)",   (0, None),  None),
    ]
    fig, axes = plt.subplots(len(metrics), len(classes),
                             figsize=(3.5*len(classes), 3*len(metrics)))
    if len(classes) == 1:
        axes = axes.reshape(-1, 1)

    for ci, cls in enumerate(classes):
        sub = rx[rx.trafficClass == cls]
        for mi, (col, label, ylim, _) in enumerate(metrics):
            ax = axes[mi, ci]
            vals = sub.groupby("seed")[col].mean().dropna()
            if vals.empty:
                ax.set_visible(False)
                continue
            ax.violinplot([vals], positions=[0], showmedians=True)
            ax.set_xticks([])
            ax.set_ylabel(label if ci == 0 else "")
            ax.set_ylim(*ylim)
            if mi == 0:
                ax.set_title(CLASS_LABELS.get(cls, cls), fontsize=9)
    fig.suptitle("Per-seed KPI distributions (mean over RX nodes) — adaptive compression",
                 fontsize=10)
    fig.tight_layout()
    save(fig, "01_kpi_distributions.png")


# ──────────────────────────────────────────────────────────────────────────
# §2. PRR vs distance
# ──────────────────────────────────────────────────────────────────────────

def plot_prr_vs_distance(rx):
    classes = [c for c in CLASS_ORDER if c in rx.trafficClass.unique()]
    fig, axes = plt.subplots(1, len(classes), figsize=(3.5*len(classes), 3.5),
                             sharey=True)
    if len(classes) == 1:
        axes = [axes]
    for ax, cls in zip(axes, classes):
        sub = rx[rx.trafficClass == cls]
        by_rx = sub.groupby(["seed", "rx_node_id"])[["mean_dist_m", "prr"]].mean().reset_index()
        ax.scatter(by_rx.mean_dist_m, by_rx.prr, alpha=0.4, s=18, color=PALETTE[0])
        ax.set_xlabel("Mean dist (m)")
        ax.set_title(CLASS_LABELS.get(cls, cls), fontsize=9)
        ax.set_ylim(-0.05, 1.05)
    axes[0].set_ylabel("PRR")
    fig.suptitle("PRR vs. TX–RX distance — adaptive compression", fontsize=10)
    fig.tight_layout()
    save(fig, "02_prr_vs_distance.png")


# ──────────────────────────────────────────────────────────────────────────
# §3. Codec selection distribution per class (stacked bar)
# ──────────────────────────────────────────────────────────────────────────

def plot_codec_mix(comp):
    if comp.empty:
        return
    tx = comp[comp.txRx == "tx"]
    classes = [c for c in CLASS_ORDER if c in tx.trafficClass.unique()]
    codecs  = sorted(tx.codec.unique())

    counts = (tx.groupby(["trafficClass", "codec"])
                .size().unstack(fill_value=0)
                .reindex(classes, fill_value=0))
    totals = counts.sum(axis=1).clip(lower=1)
    pct    = counts.divide(totals, axis=0) * 100

    fig, ax = plt.subplots(figsize=(8, 4))
    bottom = np.zeros(len(classes))
    for codec in sorted(pct.columns):
        vals = pct[codec].values if codec in pct.columns else np.zeros(len(classes))
        ax.bar(range(len(classes)), vals, bottom=bottom,
               label=CODEC_NAMES.get(int(codec), str(codec)),
               color=CODEC_COLORS.get(int(codec), "#888888"))
        bottom += vals

    ax.set_xticks(range(len(classes)))
    ax.set_xticklabels([CLASS_LABELS.get(c, c) for c in classes], fontsize=9)
    ax.set_ylabel("% of TX packets")
    ax.set_ylim(0, 105)
    ax.legend(loc="upper right", fontsize=8)
    ax.set_title("Codec selection per traffic class (adaptive mode)", fontsize=10)
    fig.tight_layout()
    save(fig, "03_codec_mix.png")


# ──────────────────────────────────────────────────────────────────────────
# §4. Compression ratio distribution per class
# ──────────────────────────────────────────────────────────────────────────

def plot_compression_ratio(comp):
    if comp.empty:
        return
    tx_comp = comp[(comp.txRx == "tx") & (comp.codec > 0)].copy()
    if tx_comp.empty:
        return
    tx_comp["ratio"] = tx_comp["compSize"].astype(float) / tx_comp["origSize"].clip(lower=1)
    classes = [c for c in CLASS_ORDER if c in tx_comp.trafficClass.unique()]

    fig, ax = plt.subplots(figsize=(8, 4))
    data = [tx_comp[tx_comp.trafficClass == c]["ratio"].dropna().values for c in classes]
    parts = ax.violinplot(data, positions=range(len(classes)), showmedians=True)
    for pc in parts["bodies"]:
        pc.set_alpha(0.7)
    ax.set_xticks(range(len(classes)))
    ax.set_xticklabels([CLASS_LABELS.get(c, c) for c in classes], fontsize=9)
    ax.set_ylabel("Compressed / original size")
    ax.set_title("Compression ratio (codec > NONE, TX side)", fontsize=10)
    ax.set_ylim(0, 1.1)
    ax.axhline(1.0, ls="--", color="gray", lw=0.8)
    fig.tight_layout()
    save(fig, "04_compression_ratio.png")


# ──────────────────────────────────────────────────────────────────────────
# §5. Encode + decode latency per class
# ──────────────────────────────────────────────────────────────────────────

def plot_codec_latency(comp):
    if comp.empty:
        return
    tx = comp[(comp.txRx == "tx") & (comp.codec > 0)][["trafficClass", "encodeUs"]].copy()
    rx = comp[(comp.txRx == "rx") & (comp.codec > 0)][["trafficClass", "decodeUs"]].copy()
    classes = [c for c in CLASS_ORDER if c in comp.trafficClass.unique()]

    enc_mean = tx.groupby("trafficClass")["encodeUs"].mean().reindex(classes)
    dec_mean = rx.groupby("trafficClass")["decodeUs"].mean().reindex(classes)

    x = np.arange(len(classes))
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(x - 0.2, enc_mean, 0.35, label="Encode (TX) µs", color=PALETTE[1])
    ax.bar(x + 0.2, dec_mean, 0.35, label="Decode (RX) µs", color=PALETTE[2])
    ax.set_xticks(x)
    ax.set_xticklabels([CLASS_LABELS.get(c, c) for c in classes], fontsize=9)
    ax.set_ylabel("Mean latency (µs)")
    ax.set_title("Codec latency (encode + decode) per class", fontsize=10)
    ax.legend(fontsize=8)
    fig.tight_layout()
    save(fig, "05_codec_latency.png")


# ──────────────────────────────────────────────────────────────────────────
# §6. Fragmentation: N-segments before and after compression
# ──────────────────────────────────────────────────────────────────────────

def plot_fragmentation(frag):
    if frag.empty or "nSegmentsPre" not in frag.columns:
        return
    classes = [c for c in CLASS_ORDER if c in frag.trafficClass.unique()]
    pre  = frag.groupby("trafficClass")["nSegmentsPre"].mean().reindex(classes)
    post = frag.groupby("trafficClass")["nSegmentsPost"].mean().reindex(classes)

    x = np.arange(len(classes))
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(x - 0.2, pre,  0.35, label="N-seg (pre-compression)",  color=PALETTE[3])
    ax.bar(x + 0.2, post, 0.35, label="N-seg (post-compression)", color=PALETTE[0])
    ax.set_xticks(x)
    ax.set_xticklabels([CLASS_LABELS.get(c, c) for c in classes], fontsize=9)
    ax.set_ylabel("Mean RLC segments per SDU")
    ax.set_title("Fragmentation reduction: RLC segments before vs after compression", fontsize=10)
    ax.axhline(1.0, ls="--", color="gray", lw=0.8, label="N=1 (no fragmentation)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    save(fig, "06_fragmentation.png")


# ──────────────────────────────────────────────────────────────────────────
# §7. PDB violation rates
# ──────────────────────────────────────────────────────────────────────────

def plot_pdb_violations(pdb):
    if pdb.empty:
        return
    classes = [c for c in CLASS_ORDER if c in pdb.trafficClass.unique()]
    mean_viol = pdb.groupby("trafficClass")["viol_rate"].mean().reindex(classes) * 100

    fig, ax = plt.subplots(figsize=(8, 4))
    bars = ax.bar(range(len(classes)), mean_viol, color=PALETTE[4])
    ax.set_xticks(range(len(classes)))
    ax.set_xticklabels([CLASS_LABELS.get(c, c) for c in classes], fontsize=9)
    ax.set_ylabel("Mean PDB violation rate (%)")
    ax.set_title("PDB violation rate per traffic class (adaptive compression)", fontsize=10)
    for bar in bars:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2, h + 0.3, f"{h:.1f}%",
                ha="center", va="bottom", fontsize=8)
    fig.tight_layout()
    save(fig, "07_pdb_violations.png")


# ──────────────────────────────────────────────────────────────────────────
# §8. Decision-reason distribution
# ──────────────────────────────────────────────────────────────────────────

def plot_decision_reasons(comp):
    if comp.empty or "reason" not in comp.columns:
        return
    tx = comp[(comp.txRx == "tx") & comp.reason.notna()]
    if tx.empty:
        return
    classes = [c for c in CLASS_ORDER if c in tx.trafficClass.unique()]
    reasons = sorted(tx.reason.unique())

    fig, axes = plt.subplots(1, len(classes), figsize=(3*len(classes), 4))
    if len(classes) == 1:
        axes = [axes]
    for ax, cls in zip(axes, classes):
        sub = tx[tx.trafficClass == cls]
        counts = sub.reason.value_counts()
        ax.barh(counts.index, counts.values)
        ax.set_title(CLASS_LABELS.get(cls, cls), fontsize=9)
        ax.tick_params(axis="y", labelsize=7)
    fig.suptitle("Controller decision reasons per class (TX packets)", fontsize=10)
    fig.tight_layout()
    save(fig, "08_decision_reasons.png")


# ──────────────────────────────────────────────────────────────────────────
# §9. HARQ statistics
# ──────────────────────────────────────────────────────────────────────────

def plot_harq(harq):
    if harq.empty:
        return
    classes = [c for c in CLASS_ORDER if c in harq.trafficClass.unique()]
    metrics = [
        ("nack_rate",   "PSFCH NACK rate"),
        ("retx_ratio",  "Retx ratio"),
        ("phy_err_rate","PHY error rate"),
    ]
    x = np.arange(len(classes))
    fig, axes = plt.subplots(1, len(metrics), figsize=(5*len(metrics), 4))
    for ax, (col, label) in zip(axes, metrics):
        vals = harq.groupby("trafficClass")[col].mean().reindex(classes)
        ax.bar(x, vals, color=PALETTE[5])
        ax.set_xticks(x)
        ax.set_xticklabels([CLASS_LABELS.get(c, c) for c in classes], fontsize=8)
        ax.set_ylabel(label)
        ax.set_ylim(0, 1.05)
    fig.suptitle("HARQ statistics per class (adaptive compression)", fontsize=10)
    fig.tight_layout()
    save(fig, "09_harq_stats.png")


# ──────────────────────────────────────────────────────────────────────────
# §10. Summary table PNG
# ──────────────────────────────────────────────────────────────────────────

def plot_summary_table(rx, comp, frag):
    classes = [c for c in CLASS_ORDER if c in rx.trafficClass.unique()]
    rows = []
    for cls in classes:
        sub = rx[rx.trafficClass == cls]
        prr_mean = sub.prr.mean()
        prr_worst = sub.groupby(["seed", "rx_node_id"]).prr.mean().groupby("rx_node_id").mean().min()
        pir_p95   = sub.pir_ms.quantile(0.95) if not sub.pir_ms.dropna().empty else np.nan
        tput_mean = sub.tput_kbps.mean()

        comp_sub  = comp[(comp.trafficClass == cls) & (comp.txRx == "tx") & (comp.codec > 0)]
        ratio_med = comp_sub.ratio.median() if ("ratio" in comp_sub.columns and not comp_sub.empty) else np.nan
        enc_mean  = comp_sub.encodeUs.mean() if not comp_sub.empty else np.nan

        frag_sub  = frag[frag.trafficClass == cls] if not frag.empty else pd.DataFrame()
        n_pre  = frag_sub.nSegmentsPre.mean()  if ("nSegmentsPre"  in frag_sub.columns and not frag_sub.empty) else np.nan
        n_post = frag_sub.nSegmentsPost.mean() if ("nSegmentsPost" in frag_sub.columns and not frag_sub.empty) else np.nan

        rows.append({
            "Class":        cls,
            "PRR mean":     f"{prr_mean:.3f}",
            "PRR worst-RX": f"{prr_worst:.3f}",
            "PIR p95 (ms)": f"{pir_p95:.1f}" if not np.isnan(pir_p95) else "—",
            "Tput (kbps)":  f"{tput_mean:.1f}",
            "Ratio (med)":  f"{ratio_med:.2f}" if not np.isnan(ratio_med) else "—",
            "Enc (µs)":     f"{enc_mean:.1f}"  if not np.isnan(enc_mean)  else "—",
            "N-seg pre":    f"{n_pre:.2f}"     if not np.isnan(n_pre)    else "—",
            "N-seg post":   f"{n_post:.2f}"    if not np.isnan(n_post)   else "—",
        })

    df = pd.DataFrame(rows)
    fig, ax = plt.subplots(figsize=(12, 1 + 0.5 * len(rows)))
    ax.axis("off")
    t = ax.table(cellText=df.values, colLabels=df.columns, loc="center", cellLoc="center")
    t.auto_set_font_size(False)
    t.set_fontsize(9)
    t.scale(1.2, 1.5)
    fig.suptitle("Summary: adaptive compression KPIs per traffic class", fontsize=10)
    fig.tight_layout()
    save(fig, "10_summary_table.png")


# ──────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────

def main():
    print(f"Loading tidy CSVs from {TIDY_DIR} ...")
    rx   = load("per_rx_kpis.csv")
    comp = load("compression_log.csv")
    frag = load("rlc_frag.csv")
    pdb  = load("pdb_violations.csv")
    harq = load("harq_stats.csv")

    if rx.empty:
        print("No per_rx_kpis.csv — run extract_kpis.py first.")
        return

    print(f"Generating plots → {PLOT_DIR}")
    plot_kpi_distributions(rx)
    plot_prr_vs_distance(rx)
    plot_codec_mix(comp)
    plot_compression_ratio(comp)
    plot_codec_latency(comp)
    plot_fragmentation(frag)
    plot_pdb_violations(pdb)
    plot_decision_reasons(comp)
    plot_harq(harq)
    plot_summary_table(rx, comp, frag)
    print("Done.")


if __name__ == "__main__":
    main()
