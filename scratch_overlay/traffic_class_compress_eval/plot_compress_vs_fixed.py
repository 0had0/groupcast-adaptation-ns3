#!/usr/bin/env python3
"""
plot_compress_vs_fixed.py

Comparison plots: fixed-MCS=14 baseline vs adaptive-compression scheme.
Reads from:
  ../../traffic_class_eval/results_traffic_class_eval/tidy/   (fixed baseline)
  results_traffic_class_compress_eval/tidy/                   (compression results)

Outputs PNGs to results_traffic_class_compress_eval/plots/comparison/.
Run after extract_kpis.py (compress) and the baseline extract_kpis.py.
"""

from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import warnings
warnings.filterwarnings("ignore")

SCRIPT_DIR  = Path(__file__).resolve().parent
FIXED_TIDY  = SCRIPT_DIR.parent / "traffic_class_eval" / "results_traffic_class_eval" / "tidy"
COMP_TIDY   = SCRIPT_DIR / "results_traffic_class_compress_eval" / "tidy"
PLOT_DIR    = SCRIPT_DIR / "results_traffic_class_compress_eval" / "plots" / "comparison"

CLASS_ORDER  = ["periodic1", "periodic2", "periodic3", "aperiodic1", "aperiodic2"]
CLASS_LABELS = {
    "periodic1":  "P1\n200B·10Hz",
    "periodic2":  "P2\n1kB·10Hz",
    "periodic3":  "P3\n45kB·1Hz",
    "aperiodic1": "A1\n1kB·aperiodic",
    "aperiodic2": "A2\n20kB·aperiodic",
}
SCHEME_COLOR = {"fixed_mcs14": "#1f77b4", "compress_adaptive": "#d62728"}
SCHEME_LABEL = {"fixed_mcs14": "Fixed MCS=14", "compress_adaptive": "Adaptive compress."}


def load_fixed():
    p = FIXED_TIDY / "per_rx_kpis.csv"
    if not p.exists():
        print(f"  [missing fixed baseline] {p}")
        return pd.DataFrame()
    df = pd.read_csv(p)
    df["scheme"] = "fixed_mcs14"
    return df


def load_compress(mode="adaptive"):
    p = COMP_TIDY / "per_rx_kpis.csv"
    if not p.exists():
        print(f"  [missing compress tidy] {p}")
        return pd.DataFrame()
    df = pd.read_csv(p)
    # filter to the requested mode (scheme column in compress tidy is the comp mode dir)
    df = df[df.scheme == mode].copy() if "scheme" in df.columns else df.copy()
    df["scheme"] = "compress_adaptive"
    return df


def save(fig, name):
    PLOT_DIR.mkdir(parents=True, exist_ok=True)
    path = PLOT_DIR / name
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  → {path}")


def ci95(series):
    n = len(series)
    if n < 2:
        return 0.0
    return 1.96 * series.std() / np.sqrt(n)


# ──────────────────────────────────────────────────────────────────────────
# §A. Headline PRR table (also saved as PNG)
# ──────────────────────────────────────────────────────────────────────────

def plot_headline_prr(rx):
    classes  = [c for c in CLASS_ORDER if c in rx.trafficClass.unique()]
    schemes  = ["fixed_mcs14", "compress_adaptive"]
    x        = np.arange(len(classes))
    width    = 0.35
    fig, ax  = plt.subplots(figsize=(10, 5))

    for si, scheme in enumerate(schemes):
        sub = rx[rx.scheme == scheme]
        means = []
        errs  = []
        for cls in classes:
            s2 = sub[sub.trafficClass == cls]
            per_seed = s2.groupby("seed")["prr"].mean()
            means.append(per_seed.mean())
            errs.append(ci95(per_seed))
        offset = (si - 0.5) * width
        ax.bar(x + offset, means, width, yerr=errs, capsize=3,
               label=SCHEME_LABEL[scheme], color=SCHEME_COLOR[scheme], alpha=0.85)

    ax.set_xticks(x)
    ax.set_xticklabels([CLASS_LABELS.get(c, c) for c in classes])
    ax.set_ylabel("Mean PRR (mean ± 95 % CI over seeds)")
    ax.set_ylim(0, 1.08)
    ax.axhline(1.0, ls="--", color="gray", lw=0.8)
    ax.legend()
    ax.set_title("Headline PRR: Fixed MCS=14 vs Adaptive Compression", fontsize=11)
    fig.tight_layout()
    save(fig, "A_headline_prr.png")


# ──────────────────────────────────────────────────────────────────────────
# §B. Per-RX reliability: worst-RX, mean, p5 PRR
# ──────────────────────────────────────────────────────────────────────────

def plot_per_rx_reliability(rx):
    classes = [c for c in CLASS_ORDER if c in rx.trafficClass.unique()]
    schemes = ["fixed_mcs14", "compress_adaptive"]
    x       = np.arange(len(classes))
    width   = 0.35

    stats = {
        "Worst-RX PRR": lambda g: g.groupby("rx_node_id").mean().min(),
        "Mean PRR":      lambda g: g.mean(),
        "p5 PRR":        lambda g: g.quantile(0.05),
    }

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    for ax, (stat_name, stat_fn) in zip(axes, stats.items()):
        for si, scheme in enumerate(schemes):
            sub = rx[rx.scheme == scheme]
            vals = []
            for cls in classes:
                per = sub[sub.trafficClass == cls].groupby(["seed", "rx_node_id"])["prr"].mean()
                vals.append(stat_fn(per))
            offset = (si - 0.5) * width
            ax.bar(x + offset, vals, width,
                   label=SCHEME_LABEL[scheme], color=SCHEME_COLOR[scheme], alpha=0.85)
        ax.set_xticks(x)
        ax.set_xticklabels([CLASS_LABELS.get(c, c) for c in classes], fontsize=8)
        ax.set_ylim(0, 1.08)
        ax.set_title(stat_name)
        ax.legend(fontsize=8)
    axes[0].set_ylabel("PRR")
    fig.suptitle("Per-RX PRR reliability comparison", fontsize=11)
    fig.tight_layout()
    save(fig, "B_per_rx_reliability.png")


# ──────────────────────────────────────────────────────────────────────────
# §B2. Δ-PRR heatmap (compress − fixed) per (class × rx_node_id)
# ──────────────────────────────────────────────────────────────────────────

def plot_delta_prr_heatmap(rx):
    classes = [c for c in CLASS_ORDER if c in rx.trafficClass.unique()]
    # mean PRR per (scheme, class, rx_node_id) averaged over seeds
    grp = rx.groupby(["scheme", "trafficClass", "rx_node_id"])["prr"].mean().reset_index()
    fixed = grp[grp.scheme == "fixed_mcs14"].set_index(["trafficClass", "rx_node_id"])["prr"]
    comp  = grp[grp.scheme == "compress_adaptive"].set_index(["trafficClass", "rx_node_id"])["prr"]
    delta = (comp - fixed).unstack(level=0).reindex(columns=classes)

    if delta.empty:
        return
    fig, ax = plt.subplots(figsize=(8, 4))
    im = ax.imshow(delta.values, aspect="auto", vmin=-0.3, vmax=0.3, cmap="RdYlGn")
    ax.set_xticks(range(len(classes)))
    ax.set_xticklabels([CLASS_LABELS.get(c, c) for c in classes], fontsize=9)
    ax.set_yticks(range(len(delta.index)))
    ax.set_yticklabels([f"RX {i}" for i in delta.index], fontsize=9)
    ax.set_xlabel("Traffic class")
    ax.set_ylabel("RX node")
    plt.colorbar(im, ax=ax, label="Δ PRR (compress − fixed)")
    ax.set_title("Δ PRR heatmap: adaptive compression vs fixed MCS=14", fontsize=10)
    fig.tight_layout()
    save(fig, "B2_delta_prr_heatmap.png")


# ──────────────────────────────────────────────────────────────────────────
# §C. PIR comparison
# ──────────────────────────────────────────────────────────────────────────

def plot_pir_comparison(rx):
    classes = [c for c in CLASS_ORDER if c in rx.trafficClass.unique()]
    schemes = ["fixed_mcs14", "compress_adaptive"]
    x       = np.arange(len(classes))
    width   = 0.35
    fig, ax = plt.subplots(figsize=(10, 5))

    for si, scheme in enumerate(schemes):
        sub  = rx[rx.scheme == scheme]
        vals = []
        for cls in classes:
            s2 = sub[sub.trafficClass == cls]
            per_seed = s2.groupby("seed")["pir_ms"].mean()
            vals.append(per_seed.mean())
        offset = (si - 0.5) * width
        ax.bar(x + offset, vals, width,
               label=SCHEME_LABEL[scheme], color=SCHEME_COLOR[scheme], alpha=0.85)

    ax.set_xticks(x)
    ax.set_xticklabels([CLASS_LABELS.get(c, c) for c in classes])
    ax.set_ylabel("Mean PIR (ms)")
    ax.legend()
    ax.set_title("Mean PIR: Fixed MCS=14 vs Adaptive Compression", fontsize=11)
    fig.tight_layout()
    save(fig, "C_pir_comparison.png")


# ──────────────────────────────────────────────────────────────────────────
# §E. PDB violation rate
# ──────────────────────────────────────────────────────────────────────────

def plot_pdb_comparison():
    p_fixed = FIXED_TIDY / "pdb_violations.csv"
    p_comp  = COMP_TIDY  / "pdb_violations.csv"
    dfs = []
    if p_fixed.exists():
        df = pd.read_csv(p_fixed); df["scheme"] = "fixed_mcs14"; dfs.append(df)
    if p_comp.exists():
        df = pd.read_csv(p_comp)
        df["scheme"] = "compress_adaptive"
        dfs.append(df)
    if not dfs:
        return
    pdb = pd.concat(dfs, ignore_index=True)
    classes = [c for c in CLASS_ORDER if c in pdb.trafficClass.unique()]
    schemes = ["fixed_mcs14", "compress_adaptive"]
    x       = np.arange(len(classes))
    width   = 0.35
    fig, ax = plt.subplots(figsize=(10, 5))
    for si, scheme in enumerate(schemes):
        sub  = pdb[pdb.scheme == scheme]
        vals = []
        for cls in classes:
            s2 = sub[sub.trafficClass == cls]
            vals.append(s2.viol_rate.mean() * 100 if not s2.empty else 0)
        offset = (si - 0.5) * width
        ax.bar(x + offset, vals, width,
               label=SCHEME_LABEL.get(scheme, scheme),
               color=SCHEME_COLOR.get(scheme, "#888888"), alpha=0.85)
    ax.set_xticks(x)
    ax.set_xticklabels([CLASS_LABELS.get(c, c) for c in classes])
    ax.set_ylabel("PDB violation rate (%)")
    ax.legend()
    ax.set_title("PDB violation rate: Fixed MCS=14 vs Adaptive Compression", fontsize=11)
    fig.tight_layout()
    save(fig, "E_pdb_violation_rate.png")


# ──────────────────────────────────────────────────────────────────────────
# §F. Fragmentation count comparison
# ──────────────────────────────────────────────────────────────────────────

def plot_fragmentation_comparison():
    p = COMP_TIDY / "rlc_frag.csv"
    if not p.exists():
        return
    frag = pd.read_csv(p)
    if "nSegmentsPre" not in frag.columns:
        return
    classes = [c for c in CLASS_ORDER if c in frag.trafficClass.unique()]
    x = np.arange(len(classes))
    width = 0.35
    pre  = frag.groupby("trafficClass")["nSegmentsPre"].mean().reindex(classes)
    post = frag.groupby("trafficClass")["nSegmentsPost"].mean().reindex(classes)

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(x - 0.2, pre,  0.35, label="Before compression", color=SCHEME_COLOR["fixed_mcs14"], alpha=0.85)
    ax.bar(x + 0.2, post, 0.35, label="After compression",  color=SCHEME_COLOR["compress_adaptive"], alpha=0.85)
    ax.set_xticks(x)
    ax.set_xticklabels([CLASS_LABELS.get(c, c) for c in classes])
    ax.set_ylabel("Mean RLC segments per SDU")
    ax.axhline(1.0, ls="--", color="gray", lw=0.8)
    ax.legend()
    ax.set_title("RLC fragmentation: before vs after adaptive compression", fontsize=10)
    fig.tight_layout()
    save(fig, "F_fragmentation_comparison.png")


# ──────────────────────────────────────────────────────────────────────────
# §J. Summary table
# ──────────────────────────────────────────────────────────────────────────

def plot_summary_table(rx):
    classes = [c for c in CLASS_ORDER if c in rx.trafficClass.unique()]
    schemes = ["fixed_mcs14", "compress_adaptive"]
    rows = []
    for cls in classes:
        for scheme in schemes:
            sub = rx[(rx.trafficClass == cls) & (rx.scheme == scheme)]
            if sub.empty:
                continue
            per_seed = sub.groupby(["seed", "rx_node_id"])["prr"].mean()
            prr_mean  = sub.groupby("seed")["prr"].mean().mean()
            prr_worst = per_seed.groupby("rx_node_id").mean().min()
            prr_p5    = per_seed.quantile(0.05)
            pir_p95   = sub.pir_ms.quantile(0.95)
            tput      = sub.tput_kbps.mean()
            rows.append({
                "Class":         cls,
                "Scheme":        SCHEME_LABEL.get(scheme, scheme),
                "PRR mean":      f"{prr_mean:.3f}",
                "PRR worst-RX":  f"{prr_worst:.3f}",
                "PRR p5 RX":     f"{prr_p5:.3f}",
                "PIR p95 (ms)":  f"{pir_p95:.1f}" if not np.isnan(pir_p95) else "—",
                "Tput (kbps)":   f"{tput:.1f}",
            })

    df = pd.DataFrame(rows)
    fig, ax = plt.subplots(figsize=(14, 1 + 0.45 * len(rows)))
    ax.axis("off")
    t = ax.table(cellText=df.values, colLabels=df.columns, loc="center", cellLoc="center")
    t.auto_set_font_size(False)
    t.set_fontsize(8)
    t.scale(1.2, 1.4)
    # highlight large-payload classes
    for row_idx in range(1, len(rows) + 1):
        cls = df.iloc[row_idx - 1]["Class"]
        if cls in ("aperiodic2", "periodic3"):
            for col_idx in range(len(df.columns)):
                t[row_idx, col_idx].set_facecolor("#fff3cd")
    fig.suptitle("Summary table: Fixed MCS=14 vs Adaptive Compression", fontsize=10)
    fig.tight_layout()
    save(fig, "J_summary_table.png")


# ──────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────

def main():
    print("Loading per_rx_kpis from both campaigns ...")
    fixed = load_fixed()
    comp  = load_compress(mode="adaptive")

    if fixed.empty and comp.empty:
        print("No data found — run extract_kpis.py for both campaigns first.")
        return

    frames = [f for f in [fixed, comp] if not f.empty]
    rx = pd.concat(frames, ignore_index=True)

    print(f"Generating comparison plots → {PLOT_DIR}")
    plot_headline_prr(rx)
    plot_per_rx_reliability(rx)
    plot_delta_prr_heatmap(rx)
    plot_pir_comparison(rx)
    plot_pdb_comparison()
    plot_fragmentation_comparison()
    plot_summary_table(rx)
    print("Done.")


if __name__ == "__main__":
    main()
