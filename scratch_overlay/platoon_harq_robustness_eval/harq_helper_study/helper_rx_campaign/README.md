# Helper-reception campaign (ns-3 backing of the helper reception results)

Two ns-3 experiments on the helper reception ratio, run one after the other on the laptop (4 runs in parallel),
then compared with the analytical model in notebook 10 of coop-harq-analytical.

| phase | what changes | runs |
|---|---|---|
| fixed | helper fixed to member k = 1..8 (`--helperSelect=static --helper=k`): natural, channel-driven misses | 3 routes x 8 helpers x 2 arms x 10 seeds = 480 |
| controlled | Kuo's helper forced to miss copy 1 with p = 0.1..0.7 (`--helperMissProb`, `--helperMissMode=data|sci`) | 3 routes x 5 p x 3 (R4_bsc data, R6 data, R6 sci) x 10 seeds = 450 |

Arms R4_bsc (helper blind + C) and R6 (cooperative), periodic traffic, no background. Every command is the
campaign-v3 command of the same cell and seed with only these flags changed; p = 0 / Kuo is campaign v3 itself
(the local build reproduces it bit for bit). `--helperMissProb` is new in the NR module
(`NrSpectrumPhy::SlHelperMissProb`, `SlHelperMissMode`, trace `SlHelperForcedMiss`; run.csv column
`helper_forced_miss`); off by default, it draws nothing.

- `./launch.sh`: starts tmux session `helperrx` (window `run`: the runner under caffeinate; window `monitor`).
- `python3 monitor.py` (once) or `tmux attach -t helperrx`: progress per phase, running runs, ETA, disk, failures.
- `./stop.sh`: stops; `./launch.sh` again resumes (finished runs are kept).
- At the end the runner calls `extract.py` (-> coop-harq-analytical/notebooks/results_10/ns3_helper_rx.csv) and
  builds notebook `10_helper_rx_ns3`.

Outputs: `../out/local_helperrx/{fixed,controlled}/<arm>/` (gzipped CSV, about 0.15 MB per run), `status.json`,
`campaign.log`, `failures.csv`. Run time on the laptop (debug build): about 30 s per run, about 2 h in all.
