# Genie campaign on Grid'5000 (Rennes, cluster paradoxe)

The SCHEMES_COMPARISON data with the genie helper (per-window best-helper schedule replay, `scripts/genie.py`):
5 stages, 4170 runs, about 1700 core-hours. Same stages as `launch_genie.sh` on ease-rtx, run as OAR jobs.

| step | where | what |
|---|---|---|
| 0 | once | build dependencies into `~/g5k_deps/root` (GSL, SQLite and Eigen headers, GSL runtime; `apt-get download` + `dpkg -x`, no root) and `pip install --user ninja` |
| 1 | frontend | `bootstrap.sh`: clone `coop-arms`, `setup.sh --minimal`, unpack `genie_inputs_g5k.tar.gz` (v3 templates, schedules, preflight references) |
| 2 | node (OAR job) | `submit.sh setup` -> `setup.sh`: configure + build (optimized, native CPU), unit tests, preflight (R6_genie, 3 routes, 0 cars, seed 1 must equal the laptop runs bit for bit); writes `out/g5k_preflight_ok` |
| 3 | nodes (OAR jobs) | `submit.sh campaign`: five jobs on five paradoxe nodes (load / timing / period+hmiss / psfch 1 of 2 / psfch 2 of 2), night jobs by default |
| - | frontend | progress `scripts/monitor_genie.sh`, stop `scripts/g5k/stop.sh` |

Usage policy (default queue): on weekdays no job may cross 09:00 or 19:00, so day jobs end by 19:00 and night jobs
(`-t night`) run 19:00-09:00 (at most 14 h); daytime use is capped at 2 h of the whole cluster per day. The frontend
is for file work and job submission only: no build, no simulation, no long-running process there. Every stage
resumes where it stopped, so a job cut by its walltime is simply submitted again.

Storage: everything lives in `~/` on the Rennes NFS home (25 GB soft quota, shared by frontend and nodes of the site,
no backup). Copy the results back with rsync from the laptop.
