"""Where the helper-reception campaign is.

  python3 monitor.py            # once
  python3 monitor.py --watch 30 # refresh every 30 s (what the tmux "monitor" window runs)
"""
import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

OUT = Path(os.environ.get("HRX_OUT", Path(__file__).resolve().parent.parent / "out/local_helperrx"))
SESSION = os.environ.get("SESSION", "helperrx")


def bar(done, total, width=30):
    f = int(width * done / total) if total else 0
    return "#" * f + "." * (width - f)


def du(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6 if path.exists() else 0.0


def report() -> str:
    lines = [f"helper-reception campaign   {dt.datetime.now():%Y-%m-%d %H:%M:%S}"]
    st = OUT / "status.json"
    if not st.exists():
        return lines[0] + "\n  not started (no status.json)"
    s = json.loads(st.read_text())
    alive = subprocess.run(["tmux", "has-session", "-t", SESSION], capture_output=True).returncode == 0
    try:
        os.kill(s["pid"], 0)
        running = True
    except OSError:
        running = False
    lines.append(f"  state: {s['state']}   runner pid {s['pid']} {'alive' if running else 'not running'}   "
                 f"tmux session '{SESSION}' {'up' if alive else 'down'}   started {s['started']}")
    left = 0
    for ph, p in s["phases"].items():
        mark = "<-" if ph == s.get("current_phase") else "  "
        lines.append(f"  {ph:10s} [{bar(p['done'], p['total'])}] {p['done']:4d}/{p['total']:<4d} failed {p['failed']} {mark}")
        left += p["total"] - p["done"] - p["failed"]
    d = s.get("durations", [])
    if d:
        mean = sum(d) / len(d)
        eta = left * mean / max(1, s["jobs"])
        lines.append(f"  run time: mean {mean:.0f} s over the last {len(d)} runs, {s['jobs']} in parallel; "
                     f"{left} left, ETA {dt.timedelta(seconds=int(eta))} "
                     f"(~{(dt.datetime.now() + dt.timedelta(seconds=eta)):%H:%M})")
    now = time.time()
    if s["running"]:
        lines.append("  running now:")
        for k, t0 in sorted(s["running"].items(), key=lambda x: x[1]):
            lines.append(f"    {k:55s} {now - t0:5.0f} s")
    free = shutil.disk_usage(OUT).free / 1e9
    lines.append(f"  output {du(OUT):.0f} MB in {OUT}   disk free {free:.1f} GB")
    fl = OUT / "failures.csv"
    if fl.exists():
        tail = fl.read_text().strip().splitlines()[-3:]
        lines.append("  last failures: " + " | ".join(tail))
    lg = OUT / "campaign.log"
    if lg.exists():
        lines.append("  log:")
        lines += ["    " + x for x in lg.read_text().strip().splitlines()[-4:]]
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=float, default=0)
    a = ap.parse_args()
    if not a.watch:
        print(report())
    else:
        while True:
            print("\033[2J\033[H" + report(), flush=True)
            time.sleep(a.watch)
