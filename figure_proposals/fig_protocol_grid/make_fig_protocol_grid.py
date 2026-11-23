#!/usr/bin/env python3
"""Cooperative-HARQ protocol figure drawn on a sidelink resource grid (slots x subchannels), four cases:
(a) helper relays, (b) takeover after a helper NACK, (c) fallback after a silent helper, (d) all ACK, slots released.

  python3 make_fig_protocol_grid.py  ->  fig_protocol_grid.drawio (editable) + fig_protocol_grid_preview.png

Timing: PSFCH every 2 slots (sl-PSFCH-Period = 2, PSFCH in the even slots), feedback for slot n in the first PSFCH
slot >= n + 3 (MinTimeGapPSFCH = 3), the next opportunity at least 2 slots later (MinTimeGapProcessing = 2).
Opportunities at slots 1, 7, 13 in the top two subchannels (positions from the user's edit, 2026-11-19); the helper
reuses the source's reserved resources. Sized for full page width (figure*): about 9 pt text in print.
"""
from pathlib import Path
from xml.sax.saxutils import escape


def esc(t):
    return escape(t, {'"': "&quot;"})

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Rectangle

HERE = Path(__file__).resolve().parent
FONT = "Helvetica"
# palette of the original figure
LEAD, HELP, TAKE, PSFCH = "#E3F1F8", "#FDF1E3", "#E1ECE7", "#ECE8F4"   # PSFCH neutral: green/red mean ACK/NACK
GRID, EDGE, OK, BAD, GREY = "#D9D9D9", "#000000", "#2E7D32", "#C62828", "#666666"

NS, NB = 14, 4            # slots shown, subchannels shown
SW, BH = 50, 22           # slot width, subchannel height (px)
PERIOD = 2                # PSFCH period (slots); PSFCH in slots with index % PERIOD == 0
MIN_GAP_PSFCH, MIN_GAP_PROC = 3, 2
PW = round(SW * 3 / 14)   # PSFCH: last 3 of 14 symbols
CW, CH = round(SW * 3 / 14), 12  # PSCCH: first 3 symbols, part of the lowest subchannel
X0 = 62
PANEL_H = NB * BH + 118
OPP = [(1, 2), (7, 2), (13, 2)]   # (slot, lowest subchannel) of the three selected opportunities, 2 subchannels each
HAS_PSFCH = lambda s: s % PERIOD == 0
FB = {s: next(t for t in range(s + MIN_GAP_PSFCH, NS) if HAS_PSFCH(t)) for s, _ in OPP[:2]}  # feedback slot
for (s, _), (nxt, _) in zip(OPP, OPP[1:]):   # the next opportunity must come after feedback + processing
    assert nxt >= FB[s] + MIN_GAP_PROC, (s, FB[s], nxt)

shapes = []   # (kind, dict)


def rect(x, y, w, h, fill="#FFFFFF", stroke=EDGE, dashed=False, text="", size=9, color="#000000", sw=1.0,
         vertical=False):
    shapes.append(("rect", dict(x=x, y=y, w=w, h=h, fill=fill, stroke=stroke, dashed=dashed, text=text, size=size,
                                color=color, sw=sw, vertical=vertical)))


def label(x, y, w, h, text, size=9, color="#000000", bold=False, align="center"):
    shapes.append(("text", dict(x=x, y=y, w=w, h=h, text=text, size=size, color=color, bold=bold, align=align)))


def line(x1, y1, x2, y2, color=EDGE, sw=1.0, arrow=False, curved=False, mid=None):
    shapes.append(("line", dict(x1=x1, y1=y1, x2=x2, y2=y2, color=color, sw=sw, arrow=arrow, curved=curved, mid=mid)))


def panel(k, tag, opp_kind, fb, notes):
    top = 50 + k * PANEL_H
    gy = lambda b: top + (NB - b) * BH          # top edge y of subchannel b (b counted from the bottom)
    label(4, top + NB * BH / 2 - 11, 50, 22, f"({tag})", size=15, bold=True)
    for s in range(NS):                           # empty grid; PSFCH region at the end of every PSFCH slot
        pw = PW if HAS_PSFCH(s) else 0
        for b in range(NB):
            rect(X0 + s * SW, gy(b + 1), SW - pw, BH, stroke=GRID, sw=0.5)
        if pw:
            rect(X0 + s * SW + SW - PW, gy(NB), PW, NB * BH, fill="#F6F4FA", stroke=GRID, sw=0.5)
    for (s, b0), kind in zip(OPP, opp_kind):    # the three opportunities
        x, y, w, h = X0 + s * SW, gy(b0 + 2), SW - (PW if HAS_PSFCH(s) else 0), 2 * BH
        if kind in ("released", "sensed"):
            rect(x, y, w, h, fill="#FFFFFF", stroke=GREY if kind == "released" else EDGE, dashed=True,
                 text="released" if kind == "released" else "no SCI", size=11, color=GREY)
            continue
        rect(x, y, w, h, fill={"lead": LEAD, "help": HELP, "take": TAKE}[kind], text="PSSCH", size=12)
        rect(x, y + h - CH, CW, CH, fill="#FFFFFF", sw=0.8)          # PSCCH in the first symbols
    for (src, occ), state in zip(FB.items(), fb):  # PSFCH occasions used for the feedback of opportunities 1 and 2
        if state is None:
            continue
        x = X0 + occ * SW + SW - PW
        rect(x, gy(NB), PW, NB * BH, fill=PSFCH, sw=1.0)
        xs = X0 + src * SW + (SW - (PW if HAS_PSFCH(src) else 0)) / 2
        line(xs, gy(NB) - 1, x + PW / 2, gy(NB) - 1, arrow=True, curved=True)
    for occ, txt, col in notes:                   # short notes under the grid: under the PSFCH column for feedback
        cx = X0 + occ * SW + (SW - PW / 2 if HAS_PSFCH(occ) and occ in FB.values() else SW / 2)
        label(cx - 95, gy(0) + 6, 190, 40, txt, size=14, color=col)
    line(X0 - 6, gy(0), X0 + NS * SW + 18, gy(0), sw=1.5, arrow=True)   # time axis


# feedback words coloured by value only: NACK red, ACK green, silent grey; everything else black
NACK, ACK = f'<font color="{BAD}"><b>NACK</b></font>', f'<font color="{OK}"><b>ACK</b></font>'
SIL = lambda why: f'<font color="{GREY}"><i>silent</i></font> ({why})'
panel(0, "a", ["lead", "help", "help"], ["fb", "fb"],
      [(4, f"Members: {NACK}<br>Helper: {SIL('decoded')}", "#000000"), (10, f"Members: {NACK}", "#000000")])
panel(1, "b", ["lead", "take", "help"], ["fb", "fb"],
      [(4, f"Helper: {NACK}<br>(TB missed)", "#000000"), (7, "<b>Takeover</b>", "#000000"),
       (10, f"Members: {NACK}<br>Helper: {SIL('decoded')}", "#000000")])
panel(2, "c", ["lead", "sensed", "take"], ["fb", None],
      [(4, f"Members: {NACK}<br>Helper: {SIL('SCI missed')}", "#000000"), (13, "<b>Fallback</b>", "#000000")])
panel(3, "d", ["lead", "released", "released"], ["fb", None], [(4, f"All members: {ACK}", "#000000")])

# axis captions and legend
top_last = 50 + 3 * PANEL_H
label(X0 + NS * SW - 110, top_last + NB * BH + 6, 110, 18, "time (slots)", size=12, color=GREY, align="right")
ly = 50 + 4 * PANEL_H - 30
items = [(LEAD, "Source copy"), (HELP, "Helper relay"), (TAKE, "Takeover / fallback"), (PSFCH, "PSFCH"),
         ("#FFFFFF", "PSCCH")]
x = X0
for fill, txt in items:
    rect(x, ly, 26, 17, fill=fill, sw=0.8)
    label(x + 32, ly - 1, 140, 20, txt, size=13, align="left")
    x += 32 + 7.2 * len(txt) + 26
W, H = X0 + NS * SW + 30, ly + 34


# ------------------------------------------------------------------------------------------------ draw.io output
def drawio():
    out, n = [], [1]

    def nid():
        n[0] += 1
        return f"c{n[0]}"
    for kind, d in shapes:
        if kind == "rect":
            st = (f"rounded=0;whiteSpace=wrap;html=1;fillColor={d['fill']};strokeColor={d['stroke']};"
                  f"strokeWidth={d['sw']};fontFamily={FONT};fontSize={d['size']};fontColor={d['color']};"
                  + ("dashed=1;dashPattern=3 2;" if d["dashed"] else ""))
            out.append(f'<mxCell id="{nid()}" value="{esc(d["text"])}" style="{st}" vertex="1" parent="1">'
                       f'<mxGeometry x="{d["x"]:.1f}" y="{d["y"]:.1f}" width="{d["w"]:.1f}" height="{d["h"]:.1f}" '
                       f'as="geometry"/></mxCell>')
        elif kind == "text":
            st = (f"text;html=1;strokeColor=none;fillColor=none;align={d['align']};verticalAlign=top;"
                  f"whiteSpace=wrap;fontFamily={FONT};fontSize={d['size']};fontColor={d['color']};"
                  + ("fontStyle=1;" if d["bold"] else ""))
            out.append(f'<mxCell id="{nid()}" value="{esc(d["text"])}" style="{st}" vertex="1" parent="1">'
                       f'<mxGeometry x="{d["x"]:.1f}" y="{d["y"]:.1f}" width="{d["w"]:.1f}" height="{d["h"]:.1f}" '
                       f'as="geometry"/></mxCell>')
        else:
            st = (f"html=1;strokeColor={d['color']};strokeWidth={d['sw']};"
                  + ("endArrow=classic;endSize=7;" if d["arrow"] else "endArrow=none;")
                  + ("curved=1;" if d["curved"] else ""))
            pts = ""
            if d["curved"]:
                mx, my = (d["x1"] + d["x2"]) / 2, d["y1"] - 32
                pts = (f'<Array as="points"><mxPoint x="{d["x1"]:.1f}" y="{my:.1f}"/>'
                       f'<mxPoint x="{d["x2"]:.1f}" y="{my:.1f}"/></Array>')
            out.append(f'<mxCell id="{nid()}" value="" style="{st}" edge="1" parent="1"><mxGeometry relative="1" '
                       f'as="geometry"><mxPoint x="{d["x1"]:.1f}" y="{d["y1"]:.1f}" as="sourcePoint"/>'
                       f'<mxPoint x="{d["x2"]:.1f}" y="{d["y2"]:.1f}" as="targetPoint"/>{pts}</mxGeometry></mxCell>')
    xml = (f'<mxfile host="app.diagrams.net"><diagram name="Protocol grid" id="pg"><mxGraphModel grid="1" gridSize="10" '
           f'guides="1" connect="0" arrows="0" page="1" pageWidth="{W:.0f}" pageHeight="{H:.0f}" math="0" shadow="0">'
           f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>' + "".join(out) + "</root></mxGraphModel></diagram></mxfile>\n")
    (HERE / "fig_protocol_grid.drawio").write_text(xml)


# ------------------------------------------------------------------------------------------------ preview (PNG)
def preview():
    import re
    fig, ax = plt.subplots(figsize=(W / 100, H / 100))
    for kind, d in shapes:
        if kind == "rect":
            ax.add_patch(Rectangle((d["x"], d["y"]), d["w"], d["h"], fc=d["fill"], ec=d["stroke"], lw=d["sw"] * 0.7,
                                   ls="--" if d["dashed"] else "-"))
            if d["text"]:
                ax.text(d["x"] + d["w"] / 2, d["y"] + d["h"] / 2, d["text"], ha="center", va="center",
                        fontsize=d["size"] * 0.72, color=d["color"], family="sans-serif")
        elif kind == "text":
            t = re.sub("<br>", "\n", d["text"])
            bold = "<b>" in t or d["bold"]
            t = re.sub("<[^>]+>", "", t)
            xx = {"left": d["x"], "center": d["x"] + d["w"] / 2, "right": d["x"] + d["w"]}[d["align"]]
            ax.text(xx, d["y"], t, ha=d["align"], va="top", fontsize=d["size"] * 0.72, color=d["color"],
                    weight="bold" if bold else "normal", family="sans-serif")
        else:
            if d["curved"]:
                ax.add_patch(FancyArrowPatch((d["x1"], d["y1"]), (d["x2"], d["y2"]),
                                             connectionstyle="arc3,rad=-0.45", arrowstyle="-|>", mutation_scale=7,
                                             lw=0.8, color=d["color"]))
            else:
                ax.add_patch(FancyArrowPatch((d["x1"], d["y1"]), (d["x2"], d["y2"]),
                                             arrowstyle="-|>" if d["arrow"] else "-", mutation_scale=9,
                                             lw=d["sw"] * 0.8, color=d["color"]))
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")
    fig.savefig(HERE / "fig_protocol_grid_preview.png", dpi=150, bbox_inches="tight")


drawio()
preview()
