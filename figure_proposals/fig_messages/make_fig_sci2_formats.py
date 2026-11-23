#!/usr/bin/env python3
"""All 2nd-stage SCI formats side by side (payload bits, CRC not drawn), black outline / white fill, names that do
not fit placed above with straight leader lines (same style as fig_sci_bw).

  python3 make_fig_sci2_formats.py  ->  fig_sci2_formats.drawio (editable) + fig_sci2_formats_preview.png

Sources: TS 38.212 V17.13.0 cl. 8.4.1.1-8.4.1.3 (2-A, 2-B, 2-C) and V18.8.0 cl. 8.4.1.4 (2-D, Table 8.4.1.4-1).
Configuration-dependent widths use the evaluated pool: N_subch = 5, mu = 1; assumed sl-ResourceReservePeriodList of
16 entries (Y = 4), N_SL-PRS = 4 PRS resources per slot (2 bits), SL PRS request present (1 bit).
SCI 2-E is the proposal (not 3GPP): draft = the pasted 56-bit layout, compact = SCI 2-A + 3-bit member index.
Fields that differ from SCI 2-A in the proposals are marked with a dashed inner box.
"""
import itertools
from pathlib import Path
from xml.sax.saxutils import escape

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

HERE = Path(__file__).resolve().parent
COMMON = [("HPN", 4), ("NDI", 1), ("RV", 2), ("Source ID", 8), ("Destination ID", 16), ("FB", 1)]
ROWS = [
    ("SCI 2-A", "Rel-16", COMMON + [("Cast", 2), ("CSI", 1)], ()),
    ("SCI 2-B", "Rel-16", COMMON + [("Zone ID", 12), ("Range", 4)], ()),
    ("SCI 2-C", "Rel-17", COMMON + [("CSI", 1), ("P/R", 1), ("Resource combinations", 38), ("First res.", 8),
                                   ("Ref. slot", 15), ("Set", 1), ("Low. subch.", 6)], ()),
    ("SCI 2-D", "Rel-18", [("PRS ID", 2), ("PRS req.", 1), ("Emb. fmt", 2),
                           ("Embedded SCI 2-A / 2-B payload", 48)], ()),
    ("SCI 2-E", "proposed, draft", COMMON + [("Relay member ID", 16), ("Mode", 1), ("Cast", 2), ("CSI", 1),
                                            ("Rsv", 4)], {"Relay member ID", "Mode", "Rsv"}),
    ("SCI 2-E", "proposed, compact", COMMON + [("Cast", 2), ("CSI", 1), ("Idx", 3)], {"Idx"}),
]
UX, UY, X0, ROWH = 6.4, 34, 150, 92     # px per bit, box height, strip start, row pitch (px)
FS = 10                                 # field names (px)
PAD, GAP, LIFT, STEP = 12, 4, 6, 14     # px
FONT = "Helvetica"

_fig = plt.figure()
_r = _fig.canvas.get_renderer()


def tw(s):
    t = _fig.text(0, 0, s, fontsize=FS * 0.75, family="sans-serif")
    w = t.get_window_extent(renderer=_r).width * 72 / _fig.dpi / 0.75
    t.remove()
    return w


def width(bits):
    return max(bits, 2.2) * UX


def span(a, w, sd):
    return (a - 2, a - 2 + w) if sd > 0 else (a + 2 - w, a + 2)


def ok(L, lv, sd):
    for i in range(len(L)):
        lo, hi = span(L[i][0], L[i][1], sd[i])
        for j in range(len(L)):
            if i == j:
                continue
            if lv[i] == lv[j]:
                lo2, hi2 = span(L[j][0], L[j][1], sd[j])
                if lo < hi2 + GAP and lo2 < hi + GAP:
                    return False
            elif lv[j] > lv[i] and lo - GAP / 2 <= L[j][0] <= hi + GAP / 2:
                return False
    return True


def place(L):
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


shapes = []


def rect(x, y, w, h, text="", dashed=False, fill="#FFFFFF", lw=1.0):
    shapes.append(("rect", dict(x=x, y=y, w=w, h=h, text=text, dashed=dashed, fill=fill, lw=lw)))


def label(x, y, w, h, text, size=FS, align="center", bold=False, color="#000000"):
    shapes.append(("text", dict(x=x, y=y, w=w, h=h, text=text, size=size, align=align, bold=bold, color=color)))


def line(x, y1, y2):
    shapes.append(("line", dict(x=x, y1=y1, y2=y2)))


for k, (name, rel, fields, mark) in enumerate(ROWS):
    top = 40 + k * ROWH
    x, leaders = X0, []
    for fname, bits in fields:
        w = width(bits)
        inside = tw(fname) + PAD <= w
        rect(x, top, w, UY, fname if inside else "")
        if fname in mark:
            rect(x + 2.5, top + 3, w - 5, UY - 6, dashed=True, fill="none", lw=0.75)
        if not inside:
            leaders.append([x + w / 2, tw(fname) + 2, fname])
        label(x, top + UY + 1, w, 13, str(bits), size=8.5)
        x += w
    lvl, side = place(leaders)
    for (a, wl, fname), lv, sd in zip(leaders, lvl, side):
        ytop = top - LIFT - lv * STEP
        line(a, top, ytop)
        label(a - 2 if sd > 0 else a + 2 - wl - 6, ytop - 13, wl + 6, 13, fname, align="left" if sd > 0 else "right")
    total = sum(b for _, b in fields)
    label(8, top + 2, X0 - 16, 16, name, size=11, align="right", bold=True)
    label(8, top + 17, X0 - 16, 14, rel, size=8.5, align="right", color="#555555")
    label(x + 6, top + 9, 120, 16, f"{total} bits + 24 CRC", size=9, align="left")
W = X0 + max(sum(width(b) for _, b in f) for _, _, f, _ in ROWS) + 140
H = 40 + len(ROWS) * ROWH
label(X0, H - 30, 700, 14, "N_subch = 5, μ = 1, 16-entry reservation period list, 4 SL PRS resources; "
      "dashed: fields new in the proposal", size=8.5, align="left", color="#555555")


def drawio():
    out, n = [], [1]

    def nid():
        n[0] += 1
        return f"c{n[0]}"
    for kind, d in shapes:
        if kind == "rect":
            st = (f"rounded=0;whiteSpace=wrap;html=1;fillColor={d['fill']};strokeColor=#000000;strokeWidth={d['lw']};"
                  f"fontFamily={FONT};fontSize={FS};" + ("dashed=1;dashPattern=3 2;" if d["dashed"] else ""))
            out.append(f'<mxCell id="{nid()}" value="{escape(d["text"])}" style="{st}" vertex="1" parent="1">'
                       f'<mxGeometry x="{d["x"]:.1f}" y="{d["y"]:.1f}" width="{d["w"]:.1f}" height="{d["h"]:.1f}" '
                       f'as="geometry"/></mxCell>')
        elif kind == "text":
            st = (f"text;html=1;strokeColor=none;fillColor=none;align={d['align']};verticalAlign=middle;"
                  f"whiteSpace=nowrap;fontFamily={FONT};fontSize={d['size']};fontColor={d['color']};"
                  + ("fontStyle=1;" if d["bold"] else "") + "spacing=0;")
            out.append(f'<mxCell id="{nid()}" value="{escape(d["text"])}" style="{st}" vertex="1" parent="1">'
                       f'<mxGeometry x="{d["x"]:.1f}" y="{d["y"]:.1f}" width="{d["w"]:.1f}" height="{d["h"]:.1f}" '
                       f'as="geometry"/></mxCell>')
        else:
            out.append(f'<mxCell id="{nid()}" value="" style="endArrow=none;html=1;strokeColor=#000000;'
                       f'strokeWidth=0.75;" edge="1" parent="1"><mxGeometry relative="1" as="geometry">'
                       f'<mxPoint x="{d["x"]:.1f}" y="{d["y1"]:.1f}" as="sourcePoint"/>'
                       f'<mxPoint x="{d["x"]:.1f}" y="{d["y2"]:.1f}" as="targetPoint"/></mxGeometry></mxCell>')
    xml = (f'<mxfile host="app.diagrams.net"><diagram name="2nd-stage SCI formats" id="sci2"><mxGraphModel grid="1" '
           f'gridSize="10" guides="1" connect="0" arrows="0" page="1" pageWidth="{W:.0f}" pageHeight="{H:.0f}" '
           f'math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>' + "".join(out)
           + "</root></mxGraphModel></diagram></mxfile>\n")
    (HERE / "fig_sci2_formats.drawio").write_text(xml)


def preview():
    fig, ax = plt.subplots(figsize=(W / 100, H / 100))
    for kind, d in shapes:
        if kind == "rect":
            ax.add_patch(Rectangle((d["x"], d["y"]), d["w"], d["h"], fc="none" if d["fill"] == "none" else d["fill"],
                                   ec="k", lw=d["lw"] * 0.8, ls="--" if d["dashed"] else "-"))
            if d["text"]:
                ax.text(d["x"] + d["w"] / 2, d["y"] + d["h"] / 2, d["text"], ha="center", va="center",
                        fontsize=FS * 0.72, family="sans-serif")
        elif kind == "text":
            xx = {"left": d["x"], "center": d["x"] + d["w"] / 2, "right": d["x"] + d["w"]}[d["align"]]
            ax.text(xx, d["y"] + d["h"] / 2, d["text"], ha=d["align"], va="center", fontsize=d["size"] * 0.72,
                    weight="bold" if d["bold"] else "normal", color=d["color"], family="sans-serif")
        else:
            ax.plot([d["x"], d["x"]], [d["y1"], d["y2"]], color="k", lw=0.6)
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")
    fig.savefig(HERE / "fig_sci2_formats_preview.png", dpi=150, bbox_inches="tight")


drawio()
preview()
print({r[0] + " " + r[1]: sum(b for _, b in r[2]) for r in ROWS}, f"W={W:.0f} H={H:.0f}")
