#!/usr/bin/env python3
"""Editable draw.io version of fig_sci_bw: same fields, widths, leader-label placement as make_fig_sci_bw.py.

  python3 make_fig_sci_drawio.py  ->  fig_sci_bw.drawio   (open in draw.io / diagrams.net)
"""
from pathlib import Path
from xml.sax.saxutils import escape

import make_fig_sci_bw as m   # reuses the field lists, widths, text measuring and label placement

HERE = Path(__file__).resolve().parent
UX, UY = 9.0, 40.0        # px per bit, px per box height
X0 = 120                  # left edge of the strips
ROW_TOP = {"std": 80, "mod": 260, "2e": 440}
FONT = "Times New Roman"
cells, n = [], [2]


def cid():
    n[0] += 1
    return f"c{n[0]}"


def box(x, y, w, h, text="", style=""):
    cells.append(f'<mxCell id="{cid()}" value="{escape(text)}" style="rounded=0;whiteSpace=wrap;html=1;fillColor=#FFFFFF;'
                 f'strokeColor=#000000;fontFamily={FONT};fontSize=10;{style}" vertex="1" parent="1">'
                 f'<mxGeometry x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" as="geometry"/></mxCell>')


def text(x, y, w, h, s, align="center", size=10):
    cells.append(f'<mxCell id="{cid()}" value="{escape(s)}" style="text;html=1;strokeColor=none;fillColor=none;'
                 f'align={align};verticalAlign=middle;fontFamily={FONT};fontSize={size};whiteSpace=wrap;" vertex="1" '
                 f'parent="1"><mxGeometry x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" as="geometry"/></mxCell>')


def line(x, y1, y2):
    cells.append(f'<mxCell id="{cid()}" value="" style="endArrow=none;html=1;strokeColor=#000000;strokeWidth=0.75;" '
                 f'edge="1" parent="1"><mxGeometry relative="1" as="geometry"><mxPoint x="{x:.1f}" y="{y1:.1f}" '
                 f'as="sourcePoint"/><mxPoint x="{x:.1f}" y="{y2:.1f}" as="targetPoint"/></mxGeometry></mxCell>')


def strip(top, fields, label, mark=()):
    x, leaders = 0.0, []
    for name, bits in fields:
        w = m.width(bits)
        px, pw = X0 + x * UX, w * UX
        tw = max(m.text_w(m.fig, m.ax, ln) for ln in name.split("\n"))
        inside = tw + m.PAD <= w
        box(px, top, pw, UY, name.replace("\n", "<br>") if inside else "")
        if name in mark:
            box(px + 0.3 * UX, top + 0.09 * UY, pw - 0.6 * UX, 0.82 * UY, "", "fillColor=none;dashed=1;dashPattern=3 2;")
        if not inside:
            leaders.append([x + w / 2, None, m.text_w(m.fig, m.ax, name.replace("\n", " ")), name.replace("\n", " ")])
        text(px, top + UY + 2, pw, 14, str(bits), size=9)
        x += w
    lvl, side = m.place(leaders)
    for (a, _, tw, name), k, sd in zip(leaders, lvl, side):
        ax_px = X0 + a * UX
        ytop = top - (m.LIFT + k * m.STEP) * UY
        line(ax_px, top, ytop)
        tw_px = tw * UX + 8
        tx = ax_px - 0.25 * UX if sd > 0 else ax_px + 0.25 * UX - tw_px
        text(tx, ytop - 14, tw_px, 14, name, align="left" if sd > 0 else "right")
    text(X0 - 110, top, 100, UY, label, align="right", size=11)
    text(X0 + x * UX + 6, top, 60, UY, f"{sum(b for _, b in fields)} bits", align="left", size=9)


strip(ROW_TOP["std"], m.SCI1A_STD, "SCI 1-A<br>(TS 38.212)")
strip(ROW_TOP["mod"], m.SCI1A, "SCI 1-A<br>(modified)", mark=m.CHANGED)
strip(ROW_TOP["2e"], m.SCI2E, "SCI 2-E")
xml = ('<mxfile host="app.diagrams.net"><diagram name="SCI formats" id="sci"><mxGraphModel dx="1000" dy="600" grid="1" '
       'gridSize="10" guides="1" tooltips="1" connect="0" arrows="0" fold="1" page="1" pageScale="1" pageWidth="1100" '
       'pageHeight="560" math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
       + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>\n")
(HERE / "fig_sci_bw.drawio").write_text(xml)
print(len(cells), "cells")
