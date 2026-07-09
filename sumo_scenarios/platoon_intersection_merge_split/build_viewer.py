#!/usr/bin/env python3
"""Build the HTML viewer for platoon_intersection_merge_split.

Reads `platoon_intersection_merge_split.mob.csv` and emits a self-contained
HTML file at /tmp/platoon_viewer.html with a canvas-based animation of all
10 vehicles along the south → east → north path.
"""
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
CSV  = HERE / "platoon_intersection_merge_split.mob.csv"
OUT  = Path("/tmp/platoon_viewer.html")

# Vehicle color / name tables
COLORS = {
    0: "#e74c3c",  # v0 leader — red
    1: "#27ae60",  # v1 leaver — green
    2: "#2980b9",  # v2        — blue
    3: "#27ae60",  # v3 leaver — green
    4: "#8e44ad",  # v4        — purple
    5: "#16a085",  # v5        — teal
    6: "#27ae60",  # v6 leaver — green
    7: "#34495e",  # v7        — slate
    8: "#f39c12",  # j0 joiner — orange
    9: "#d35400",  # j1 joiner — dark orange
}
NAMES = {
    0: "v0 (leader)",
    1: "v1 (leaver)",
    2: "v2",
    3: "v3 (leaver)",
    4: "v4",
    5: "v5",
    6: "v6 (leaver)",
    7: "v7",
    8: "j0 (joiner)",
    9: "j1 (joiner)",
}

# Load CSV and build FRAMES list
df = pd.read_csv(CSV, sep=",")
df.columns = [c.strip().lstrip("#").strip() for c in df.columns]
df["node_id"] = df["node_id"].astype(int)

frames = []
for t, g in df.groupby("time"):
    t = round(float(t), 1)
    vehicles = [
        {
            "id": int(r["node_id"]),
            "x": round(float(r["x"]), 2),
            "y": round(float(r["y"]), 2),
            "v": round(float(r["speed"]), 2),
        }
        for _, r in g.iterrows()
    ]
    frames.append({"t": t, "vehicles": vehicles})

# Network geometry (matches nodes.nod.xml, post-extension to 790m north leg).
J1 = {"x": 200.0, "y": 200.0}
J2 = {"x": 300.0, "y": 200.0}
NORTH_END_Y = 1000.0   # north leg now goes up to y=1000
EAST_END_X  = 500.0    # east leg leaver route
WEST_END_X  = 0.0      # west approach joiner route
SOUTH_END_Y = 0.0      # south leg

# Compute world bounds (with 50m padding)
PADDING = 50.0
X_MIN, X_MAX = WEST_END_X - PADDING, EAST_END_X + PADDING
Y_MIN, Y_MAX = SOUTH_END_Y - PADDING, NORTH_END_Y + PADDING

CANVAS_W = 1500
CANVAS_H = 1200

# Pixels per metre
WORLD_W = X_MAX - X_MIN
WORLD_H = Y_MAX - Y_MIN
SCALE_X = (CANVAS_W - 100) / WORLD_W
SCALE_Y = (CANVAS_H - 100) / WORLD_H
SCALE   = min(SCALE_X, SCALE_Y)

# World→screen with manual offset so the world fits the canvas with margins
def w2sx(x):
    return 50 + (x - X_MIN) * SCALE
def w2sy(y):
    return CANVAS_H - 50 - (y - Y_MIN) * SCALE

# Build the HTML
COLORS_JSON = json.dumps({str(k): v for k, v in COLORS.items()})
NAMES_JSON  = json.dumps({str(k): v for k, v in NAMES.items()})
FRAMES_JSON = json.dumps(frames, separators=(",", ":"))

HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>platoon_intersection_merge_split viewer</title>
<style>
  body {{
    margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #1c1f24; color: #ecf0f1;
  }}
  #wrap {{ display: flex; flex-direction: column; align-items: center; padding: 12px; }}
  h1 {{ font-size: 18px; margin: 0 0 8px; }}
  #canvas {{ background: #2a2e34; border: 1px solid #3a3f48; display: block; }}
  #controls {{
    display: flex; gap: 10px; align-items: center; margin: 10px 0;
    flex-wrap: wrap; justify-content: center;
  }}
  button {{
    background: #3498db; color: white; border: none;
    padding: 8px 16px; border-radius: 4px; font-size: 13px;
    cursor: pointer; min-width: 80px;
  }}
  button:hover {{ background: #2980b9; }}
  button:disabled {{ background: #555; cursor: not-allowed; }}
  #reset {{ background: #e67e22; }}
  #reset:hover {{ background: #d35400; }}
  #info {{ font-size: 13px; color: #bdc3c7; display: flex; gap: 18px; }}
  #info span b {{ color: #ecf0f1; }}
  #slider {{ width: 1100px; }}
  #legend {{
    display: flex; flex-wrap: wrap; gap: 8px 18px; justify-content: center;
    margin: 6px 0; font-size: 12px; max-width: 1100px;
  }}
  #legend div {{ display: flex; align-items: center; gap: 5px; }}
  #legend span {{ display: inline-block; width: 12px; height: 12px; border-radius: 2px; }}
</style>
</head>
<body>
<div id="wrap">
  <h1>platoon_intersection_merge_split — trace viewer</h1>
  <canvas id="canvas" width="{CANVAS_W}" height="{CANVAS_H}"></canvas>
  <div id="controls">
    <button id="play">▶ Play</button>
    <button id="pause" disabled>⏸ Pause</button>
    <button id="step">⏭ Step</button>
    <button id="reset">⟲ Reset</button>
    Speed:
    <select id="interval">
      <option value="200">0.2 s / frame</option>
      <option value="100" selected>0.1 s / frame</option>
      <option value="50">0.05 s / frame</option>
    </select>
    <span>Frame <b id="frame_idx">0</b> / <b id="frame_total">{len(frames)-1}</b></span>
    <span>t = <b id="time_str">0.0</b> s</span>
    <span>Active vehicles <b id="active">0</b></span>
  </div>
  <input id="slider" type="range" min="0" max="{len(frames)-1}" value="0">
  <div id="legend">
    <div><span style="background:#e74c3c"></span>v0 (leader)</div>
    <div><span style="background:#27ae60"></span>v1, v3, v6 (leavers)</div>
    <div><span style="background:#2980b9"></span>v2</div>
    <div><span style="background:#8e44ad"></span>v4</div>
    <div><span style="background:#16a085"></span>v5</div>
    <div><span style="background:#34495e"></span>v7</div>
    <div><span style="background:#f39c12"></span>j0 (joiner)</div>
    <div><span style="background:#d35400"></span>j1 (joiner)</div>
  </div>
</div>
<script>
const FRAMES = {FRAMES_JSON};
const COLORS = {COLORS_JSON};
const NAMES  = {NAMES_JSON};

const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");

// Network geometry (matches nodes.nod.xml, post-extension to 790m north leg)
const J1 = {{x: {J1['x']}, y: {J1['y']}}};
const J2 = {{x: {J2['x']}, y: {J2['y']}}};
const SCALE = {SCALE:.4f};

function W2Sx(x) {{ return 50 + (x - {X_MIN}) * SCALE; }}
function W2Sy(y) {{ return {CANVAS_H} - 50 - (y - {Y_MIN}) * SCALE; }}

let currentFrame = 0;
let playing = false;
let timer = null;

const playBtn   = document.getElementById("play");
const pauseBtn  = document.getElementById("pause");
const stepBtn   = document.getElementById("step");
const resetBtn  = document.getElementById("reset");
const slider    = document.getElementById("slider");
const interval  = document.getElementById("interval");
const frameIdx  = document.getElementById("frame_idx");
const frameTotal = document.getElementById("frame_total");
const timeStr   = document.getElementById("time_str");
const activeEl  = document.getElementById("active");

function drawRoad(x0, y0, x1, y1, w) {{
  ctx.fillStyle = "#3a3f48";
  ctx.fillRect(W2Sx(Math.min(x0,x1)) - w/2, W2Sy(Math.max(y0,y1)) - w/2,
               Math.abs(x1-x0)*SCALE + w, Math.abs(y1-y0)*SCALE + w);
  ctx.strokeStyle = "#5a6068";
  ctx.setLineDash([6, 4]);
  ctx.lineWidth = 1;
  ctx.beginPath();
  if (x0 === x1) {{
    ctx.moveTo(W2Sx(x0), W2Sy(Math.max(y0,y1)));
    ctx.lineTo(W2Sx(x1), W2Sy(Math.min(y0,y1)));
  }} else {{
    ctx.moveTo(W2Sx(Math.min(x0,x1)), W2Sy(y0));
    ctx.lineTo(W2Sx(Math.max(x0,x1)), W2Sy(y1));
  }}
  ctx.stroke();
  ctx.setLineDash([]);
}}

function drawScene() {{
  ctx.fillStyle = "#2a2e34";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  const W = 12;  // half road width in metres
  // South leg (x=201.6, y=0..200)
  drawRoad(201.6, 0, 201.6, 200, W);
  // North leg (x=301.6, y=200..1000) — extended to 790m
  drawRoad(301.6, 200, 301.6, 1000, W);
  // East-west leg (y=198.4, x=0..500)
  drawRoad(0, 198.4, 500, 198.4, W);

  // Intersection boxes
  ctx.fillStyle = "#3a3f48";
  ctx.fillRect(W2Sx(200)-W/2, W2Sy(200)-W/2, W, W);  // J1
  ctx.fillRect(W2Sx(300)-W/2, W2Sy(200)-W/2, W, W);  // J2

  // Intersection labels
  ctx.fillStyle = "#ecf0f1";
  ctx.font = "bold 18px sans-serif";
  ctx.textAlign = "center";
  ctx.fillText("J1", W2Sx(200), W2Sy(200) - W/2 - 10);
  ctx.fillText("J2", W2Sx(300), W2Sy(200) - W/2 - 10);

  // Cardinal labels at the edges
  ctx.font = "11px sans-serif";
  ctx.textAlign = "left";
  ctx.fillStyle = "#7f8c8d";
  ctx.fillText("N", W2Sx(301.6) - 5, W2Sy(1000) + 15);
  ctx.fillText("S", W2Sx(201.6) - 5, W2Sy(0) - 5);
  ctx.fillText("E", W2Sx(500) + 5, W2Sy(198.4) + 4);
  ctx.fillText("W", W2Sx(0) - 15, W2Sy(198.4) + 4);
}}

function drawVehicle(v) {{
  const L = 4.5;   // length
  const Wd = 2.0;  // width
  const cx = W2Sx(v.x);
  const cy = W2Sy(v.y);

  // Infer heading from world position (vehicle is always aligned with the road)
  let angle = 0;
  if (Math.abs(v.x - 201.6) < 1.5)      angle = 90 * Math.PI/180;   // south leg, going up
  else if (Math.abs(v.x - 301.6) < 1.5) angle = 90 * Math.PI/180;   // north leg, going up
  else if (Math.abs(v.y - 198.4) < 5)   angle = 0;                  // east-west, going right

  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(-angle);  // canvas y is flipped
  ctx.fillStyle = COLORS[v.id] || "#999";
  ctx.strokeStyle = "#ecf0f1";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.rect(-L/2 * SCALE, -Wd/2 * SCALE, L * SCALE, Wd * SCALE);
  ctx.fill();
  ctx.stroke();
  // Direction triangle at the front
  ctx.fillStyle = "#ecf0f1";
  ctx.beginPath();
  ctx.moveTo(L/2 * SCALE * 0.7, 0);
  ctx.lineTo(L/2 * SCALE * 0.3, -Wd/2 * SCALE * 0.4);
  ctx.lineTo(L/2 * SCALE * 0.3, Wd/2 * SCALE * 0.4);
  ctx.closePath();
  ctx.fill();
  ctx.restore();

  // Label above the vehicle
  ctx.fillStyle = "#ecf0f1";
  ctx.font = "bold 10px sans-serif";
  ctx.textAlign = "center";
  ctx.fillText(NAMES[v.id] || ("n"+v.id), cx, cy - Wd/2 * SCALE - 4);
}}

function render() {{
  drawScene();
  const frame = FRAMES[currentFrame];
  if (!frame) return;
  for (const v of frame.vehicles) drawVehicle(v);
  frameIdx.textContent = currentFrame;
  timeStr.textContent  = frame.t.toFixed(1);
  activeEl.textContent = frame.vehicles.length;
  slider.value = currentFrame;
}}

function play() {{
  if (playing) return;
  playing = true;
  playBtn.disabled  = true;
  pauseBtn.disabled = false;
  stepBtn.disabled  = true;
  const tick = () => {{
    if (!playing) return;
    if (currentFrame >= FRAMES.length - 1) {{ pause(); return; }}
    currentFrame++;
    render();
    timer = setTimeout(tick, parseInt(interval.value, 10));
  }};
  tick();
}}

function pause() {{
  playing = false;
  if (timer) {{ clearTimeout(timer); timer = null; }}
  playBtn.disabled  = false;
  pauseBtn.disabled = true;
  stepBtn.disabled  = false;
}}

function step() {{
  if (currentFrame < FRAMES.length - 1) currentFrame++;
  render();
}}

function reset() {{
  pause();
  currentFrame = 0;
  render();
}}

playBtn.addEventListener("click", play);
pauseBtn.addEventListener("click", pause);
stepBtn.addEventListener("click", step);
resetBtn.addEventListener("click", reset);
slider.addEventListener("input", (e) => {{
  pause();
  currentFrame = parseInt(e.target.value, 10);
  render();
}});
interval.addEventListener("change", () => {{
  if (playing) {{ pause(); play(); }}
}});

document.addEventListener("keydown", (e) => {{
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  if (e.code === "Space") {{ e.preventDefault(); playing ? pause() : play(); }}
  else if (e.code === "ArrowRight") {{ pause(); step(); }}
  else if (e.code === "KeyR") {{ reset(); }}
}});

render();
</script>
</body>
</html>
"""

OUT.write_text(HTML)
print(f"Wrote {OUT} ({len(HTML):,} bytes, {len(frames)} frames, {len(frames[0]['vehicles'])} vehicles in first frame)")
