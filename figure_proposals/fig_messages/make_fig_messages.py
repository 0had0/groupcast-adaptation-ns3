#!/usr/bin/env python3
"""Three candidate figures for the cooperative HARQ messages (paper text, Sec. II-III, main.tex lines 53-64).

  python3 make_fig_messages.py   ->  opt_A_sci_fields.{pdf,png}, opt_B_message_matrix.{pdf,png}, opt_C_feedback_rule.{pdf,png}

SCI-2A field widths: TS 38.212 Sec. 8.4.1.1 (Rel-16/17), to be checked against the spec before use. The helper index
(3 bits for N = 8 members) is the paper's PROPOSED encoding; the evaluated simulator passes it in an auxiliary
descriptor that is not charged to the air interface.
"""
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle

HERE = Path(__file__).resolve().parent
mpl.rcParams.update({
    "font.family": "serif", "font.serif": ["Times New Roman", "Times", "DejaVu Serif"], "mathtext.fontset": "stix",
    "font.size": 8, "pdf.fonttype": 42, "ps.fonttype": 42, "savefig.bbox": "tight", "savefig.pad_inches": 0.02})
SRC, HLP, PROP, GREY = "#DCEAF6", "#FBE9D0", "#0072B2", "0.92"


def save(fig, name):
    fig.savefig(HERE / f"{name}.pdf")
    fig.savefig(HERE / f"{name}.png", dpi=220)
    plt.close(fig)


# ------------------------------------------------------------------ A: SCI-2A field strip + proposed helper index
def opt_a():
    fields = [("HARQ\nproc.", 4, "kept"), ("NDI", 1, "kept"), ("RV", 2, "set"), ("Source ID", 8, "kept"),
              ("Destination ID", 16, "kept"), ("HARQ fb.", 1, "kept"), ("Cast type", 2, "kept"),
              ("CSI", 1, "kept"), ("Helper\nindex", 3, "new")]
    fig, ax = plt.subplots(figsize=(7.16, 1.75))
    x, scale = 0.0, 1.0
    for name, w, kind in fields:
        dashed = kind == "new"
        ax.add_patch(Rectangle((x, 0), w * scale, 1.0, fc="white" if dashed else (HLP if kind == "set" else SRC),
                               ec=PROP if dashed else "k", lw=1.0, ls="--" if dashed else "-"))
        narrow = w <= 2
        ax.text(x + w * scale / 2, 0.5, name.replace("\n", " ") if narrow else name, ha="center", va="center",
                fontsize=6.6 if narrow else 7, rotation=90 if narrow else 0, color=PROP if dashed else "k")
        ax.text(x + w * scale / 2, -0.17, f"{w}", ha="center", va="top", fontsize=6.5, color="0.35")
        x += w * scale
    ax.text(-0.5, 0.5, "SCI 2-A", ha="right", va="center", fontsize=8, weight="bold")
    ax.text(-0.5, -0.17, "bits", ha="right", va="top", fontsize=6.5, color="0.35")
    ax.annotate("", xy=(0, 1.32), xytext=(35, 1.32), arrowprops=dict(arrowstyle="<->", lw=0.6))
    ax.text(17.5, 1.38, "standard fields, 35 bits [TS 38.212]", ha="center", va="bottom", fontsize=7)
    ax.text(36.5, 1.38, "proposed", ha="center", va="bottom", fontsize=7, color=PROP)
    hs = [Rectangle((0, 0), 1, 1, fc=SRC, ec="k", lw=0.6, label="helper copies the source's value"),
          Rectangle((0, 0), 1, 1, fc=HLP, ec="k", lw=0.6, label="helper sends RV 2"),
          Rectangle((0, 0), 1, 1, fc="white", ec=PROP, ls="--", lw=0.8,
                    label=r"helper index ($\lceil\log_2 N\rceil$ = 3 bits); not charged in the evaluation")]
    ax.legend(handles=hs, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3, frameon=False, fontsize=6.8,
              handlelength=1.2)
    ax.set_xlim(-6, 38.5)
    ax.set_ylim(-0.5, 1.7)
    ax.axis("off")
    save(fig, "opt_A_sci_fields")


# ------------------------------------------------------------------ B: what each message carries
def opt_b():
    cols = ["Message", "Sent by", "Opportunity", "Source ID,\nHARQ ID, NDI", "RV", "Helper\nindex", "Purpose"]
    rows = [
        ("Copy 1 (PSSCH)", "Source", "1", "source", "0", "yes", "first copy; names the helper"),
        ("PSFCH, member", "Each member", "after 1", "—", "—", "—", "ACK / NACK (Option 2)"),
        ("PSFCH, helper", "Helper", "after 1", "—", "—", "—", "NACK if TB missed; no ACK"),
        ("Helper copy", "Helper", "2, 3", "source's", "2", "—", "spatial retransmission"),
        ("Takeover copy", "Source", "2 or 3", "source", "2 / 3", "—", "helper NACK, member NACK"),
        ("Fallback copy", "Source", "3", "source", "3", "—", "no helper SCI, member NACK"),
        ("ACK from all", "All members", "any", "—", "—", "—", "release remaining slots"),
    ]
    shade = {"Source": SRC, "Helper": HLP}
    W = [1.25, 0.85, 0.8, 0.95, 0.45, 0.55, 1.75]
    fig, ax = plt.subplots(figsize=(7.16, 2.05))
    xs = [sum(W[:i]) for i in range(len(W) + 1)]
    H = 1.0
    for j, c in enumerate(cols):
        ax.text(xs[j] + 0.05, 0.5 * H, c, ha="left", va="center", fontsize=7.4, weight="bold")
    ax.plot([0, xs[-1]], [0, 0], color="k", lw=0.8)
    ax.plot([0, xs[-1]], [H, H], color="k", lw=0.8)
    for i, r in enumerate(rows):
        y0 = -(i + 1) * 0.62
        fc = shade.get(r[1])
        if fc:
            ax.add_patch(Rectangle((0, y0), xs[-1], 0.62, fc=fc, ec="none", zorder=0))
        for j, v in enumerate(r):
            ax.text(xs[j] + 0.05, y0 + 0.31, v, ha="left", va="center", fontsize=7.2,
                    color=PROP if (j == 5 and v == "yes") else "k")
    ax.plot([0, xs[-1]], [-len(rows) * 0.62] * 2, color="k", lw=0.8)
    ax.set_xlim(0, xs[-1])
    ax.set_ylim(-len(rows) * 0.62 - 0.05, H + 0.05)
    ax.axis("off")
    save(fig, "opt_B_message_matrix")


# ------------------------------------------------------------------ C: PSFCH response rule after copy 1
def opt_c():
    fig, ax = plt.subplots(figsize=(3.5, 1.75))
    heads = ["State after copy 1", "Member", "Helper"]
    rows = [("TB decoded", "ACK", "nothing"), ("SCI decoded, TB missed", "NACK", "NACK"),
            ("SCI missed", "nothing", "nothing")]
    action = ["helper relays", "source takes over", "source falls back\n(no helper SCI seen)"]
    X = [0, 1.75, 2.45, 3.15]
    ax.text(X[0] + 0.03, 0.35, heads[0], fontsize=7.2, weight="bold", va="center")
    ax.text(X[1] + 0.32, 0.35, heads[1], fontsize=7.2, weight="bold", ha="center", va="center")
    ax.text(X[2] + 0.32, 0.35, heads[2], fontsize=7.2, weight="bold", ha="center", va="center", color="#8a5a00")
    ax.text(X[3] + 0.05, 0.35, "If it is\nthe helper", fontsize=7.0, weight="bold", va="center")
    for i, (st, m, h) in enumerate(rows):
        y = -0.5 - i * 0.62
        ax.text(X[0] + 0.03, y, st, fontsize=7.2, va="center")
        for k, v in ((1, m), (2, h)):
            col = {"ACK": "#2e7d32", "NACK": "#c62828"}.get(v, "0.45")
            ax.add_patch(FancyBboxPatch((X[k] + 0.06, y - 0.17), 0.52, 0.34, boxstyle="round,pad=0.01,rounding_size=0.06",
                                        fc="white", ec=col, lw=0.9, ls="-" if v != "nothing" else ":"))
            ax.text(X[k] + 0.32, y, v, ha="center", va="center", fontsize=7, color=col)
        ax.text(X[3] + 0.05, y, action[i], fontsize=6.8, va="center", color="0.25")
    ax.plot([0, 4.45], [0.0, 0.0], color="k", lw=0.7)
    ax.add_patch(Rectangle((X[2], -2.05), 0.65, 2.0, fc=HLP, ec="none", zorder=0))
    ax.set_xlim(0, 4.45)
    ax.set_ylim(-2.05, 0.65)
    ax.axis("off")
    save(fig, "opt_C_feedback_rule")


opt_a()
opt_b()
opt_c()
