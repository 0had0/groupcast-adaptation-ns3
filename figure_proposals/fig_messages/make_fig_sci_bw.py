#!/usr/bin/env python3
"""SCI format 1-A and the proposed SCI format 2-E as field strips, black outline / white fill (3GPP style).

  python3 make_fig_sci_bw.py  ->  fig_sci_bw.{pdf,png}

Field list and widths as given by the user (pasted layout, 2026-11-19); widths drawn to scale except 1-bit fields
(widened) and the CRC (shortened); bit counts under each field. A name that does not fit inside its field is placed
above it and tied to the field by a thin leader line (patent-drawing style), all names at one font size.
"""
import itertools
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

HERE = Path(__file__).resolve().parent
mpl.rcParams.update({
    "font.family": "serif", "font.serif": ["Times New Roman", "Times", "DejaVu Serif"], "mathtext.fontset": "stix",
    "font.size": 8, "pdf.fonttype": 42, "ps.fonttype": 42, "savefig.bbox": "tight", "savefig.pad_inches": 0.02})

SCI1A = [("Prio", 3), ("Frequency\nresource", 11), ("Time\nresource", 9), ("RRP", 4), ("DMRS", 2),
         ("Fmt", 3), ("β-off", 2), ("Port", 1), ("MCS", 5), ("PSFCH", 1), ("Rsv", 3), ("CRC", 24)]
# standard SCI 1-A with the same configuration-dependent widths, differing only where the modified one changes it:
# 2nd-stage SCI format 2 bits (TS 38.212 8.3.1.1), reserved = sl-NumReservedBits = 4
SCI1A_STD = [f if f[0] not in ("Fmt", "Rsv") else (f[0], {"Fmt": 2, "Rsv": 4}[f[0]]) for f in SCI1A]
CHANGED = {"Fmt", "Rsv"}
SCI2E = [("HPN", 4), ("NDI", 1), ("RV", 2), ("Source ID", 8), ("Destination ID", 16), ("FB", 1),
         ("Relay member ID", 16), ("Mode", 1), ("Cast", 2), ("CSI", 1), ("Rsv", 4), ("CRC", 24)]
FS = 7.0          # every field name
PAD = 0.6         # free space (bit units) a name needs inside its field
GAP = 0.5         # minimum space (bit units) between a name and the next leader line
LIFT = 0.3        # height of the lowest leader label above the field top
STEP = 0.42       # height between two stacked leader labels
XMIN, XMAX = -15, 87


def width(bits):
    return max(bits, 1.9) if bits < 24 else 20   # 1-bit fields widened, CRC shortened: not drawn to scale


def text_w(fig, ax, s):
    """Width of a label in data (bit) units."""
    t = ax.text(0, 0, s, fontsize=FS)
    bb = t.get_window_extent(renderer=fig.canvas.get_renderer())
    t.remove()
    inv = ax.transData.inverted()
    return inv.transform((bb.x1, 0))[0] - inv.transform((bb.x0, 0))[0]


def span(a, tw, sd):
    return (a - 0.25, a - 0.25 + tw) if sd > 0 else (a + 0.25 - tw, a + 0.25)


def ok(L, lv, sd):
    for i in range(len(L)):
        lo, hi = span(L[i][0], L[i][2], sd[i])
        for j in range(len(L)):
            if i == j:
                continue
            if lv[i] == lv[j]:
                lo2, hi2 = span(L[j][0], L[j][2], sd[j])
                if lo < hi2 + GAP and lo2 < hi + GAP:
                    return False
            elif lv[j] > lv[i] and lo - GAP / 2 <= L[j][0] <= hi + GAP / 2:
                return False
    return True


def place(L):
    """Fewest stacked rows, then lowest total height, then names to the right of their line."""
    n = len(L)
    for top in range(n):
        best = None
        for lv in itertools.product(range(top + 1), repeat=n):
            if max(lv, default=0) != top:
                continue
            for sd in itertools.product((1, -1), repeat=n):
                if ok(L, lv, sd):
                    key = (sum(lv), sum(1 for v in sd if v < 0))
                    if best is None or key < best[0]:
                        best = (key, list(lv), list(sd))
        if best:
            return best[1], best[2]
    return [0] * n, [1] * n


def strip(fig, ax, y, fields, label, mark=()):
    x, leaders = 0, []
    for name, bits in fields:
        w = width(bits)
        ax.add_patch(Rectangle((x, y), w, 1, fc="white", ec="k", lw=0.8))
        if name in mark:   # changed field: second, inner outline (prints in black and white)
            ax.add_patch(Rectangle((x + 0.3, y + 0.09), w - 0.6, 0.82, fc="none", ec="k", lw=0.5, ls=(0, (2, 1.5))))
        tw = max(text_w(fig, ax, line) for line in name.split("\n"))
        if tw + PAD <= w:
            ax.text(x + w / 2, y + 0.5, name, ha="center", va="center", fontsize=FS, linespacing=0.95)
        else:
            leaders.append([x + w / 2, x + w / 2, text_w(fig, ax, name.replace("\n", " ")), name.replace("\n", " ")])
        ax.text(x + w / 2, y - 0.1, str(bits), ha="center", va="top", fontsize=6.5)
        x += w
    # straight vertical leaders: each name sits at the top of its line, to the right or to the left of it; the
    # side and the height are searched so that no name overlaps another name or crosses a taller line
    lvl, side = place(leaders)
    for (anchor, _, tw, name), k, sd in zip(leaders, lvl, side):
        top = y + 1 + LIFT + k * STEP
        ax.plot([anchor, anchor], [y + 1, top + 0.03], color="k", lw=0.45)
        ax.text(anchor - 0.25 * sd, top, name, ha="left" if sd > 0 else "right", va="bottom", fontsize=FS)
    levels = max(lvl, default=0)
    ax.text(-1.0, y + 0.5, label, ha="right", va="center", fontsize=8)
    ax.text(x + 0.6, y + 0.5, f"{sum(b for _, b in fields)} bits", ha="left", va="center", fontsize=6.5)
    return levels


fig, ax = plt.subplots(figsize=(7.16, 4.2))
ax.set_xlim(XMIN, XMAX)
ax.set_ylim(-0.4, 8.0)
ax.axis("off")
strip(fig, ax, 5.8, SCI1A_STD, "SCI 1-A\n(TS 38.212)")
strip(fig, ax, 2.9, SCI1A, "SCI 1-A\n(modified)", mark=CHANGED)
strip(fig, ax, 0.0, SCI2E, "SCI 2-E")
fig.savefig(HERE / "fig_sci_bw.pdf")
fig.savefig(HERE / "fig_sci_bw.png", dpi=220)
