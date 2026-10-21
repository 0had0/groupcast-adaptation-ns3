#!/usr/bin/env python3
"""make_synth_traces.py -- static synthetic platoon traces in the LoadTrace format of
platoon_harq_robustness_eval.cc (lines 91-170): '# key=value' meta lines, header, rows 'time,node_id,x,y,speed'.

Node 0 = leader (front), nodes 1..8 = members behind it on the same highway lane (y = -4.8 m), all static.
Python twin of tests/common/synth_trace.cc (must write byte-identical files; A05 compares them).
"""

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import runsets as rs  # noqa: E402

LEADER_X = 2000.0
LANE_Y = -4.8
DT = 0.1


def render(geom: str, members, t_end=rs.SYN_TEND, kpi_start=rs.SYN_KPI_START):
    first = members[0]
    spacing = math.hypot(first[0], first[1])
    lines = []
    meta = [
        ("scenario", "highway"), ("ivd_m", f"{max(spacing - 5.0, 0.0):.1f}"), ("veh_len_m", "5.0"),
        ("platoon", "9"), ("nbg_target", "0"), ("seats", "0"), ("bg_in_area_mean", "0"), ("bg_in_area_min", "0"),
        ("bg_in_area_max", "0"), ("t_end", f"{t_end:.1f}"), ("kpi_start", f"{kpi_start:.1f}"), ("dt", f"{DT:.1f}"),
        ("seed", "1"), ("platoon_speed_mps", "0.0"), ("synthetic", geom),
    ]
    for k, v in meta:
        lines.append(f"# {k}={v}")
    lines.append("time,node_id,x,y,speed")
    n_steps = int(round(t_end / DT))
    pts = [(LEADER_X, LANE_Y)] + [(LEADER_X + dx, LANE_Y + dy) for dx, dy in members]
    xs = [p[0] for p in pts]
    for step in range(n_steps + 1):
        t = step * DT
        for nid, (x, y) in enumerate(pts):
            lines.append(f"{t:.3f},{nid},{x:.3f},{y:.3f},0.000")
    return "\n".join(lines) + "\n", meta, xs


def write_all(outdir: Path = rs.TRACES_SYN):
    outdir.mkdir(parents=True, exist_ok=True)
    done = []
    for geom, spec in rs.GEOMS.items():
        txt, meta, xs = render(geom, spec["members"])
        p = outdir / f"{geom}.csv"
        if not p.exists() or p.read_text() != txt:
            p.write_text(txt)
        (outdir / f"{geom}.json").write_text(json.dumps({"meta": dict(meta), "members_dxdy": spec["members"]}, indent=1))
        done.append(str(p))
    return done


if __name__ == "__main__":
    for p in write_all():
        print(p)
