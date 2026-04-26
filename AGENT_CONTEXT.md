# Agent Context — groupcast-adaptation-ns3

Compact orientation file for AI agents working in this repo. Read this before
starting any task. Prefer reading files directly over speculating.

## What this project is

NS-3 / NR sidelink groupcast PHY-adaptation experiment. Runs Mode-2
sensing-based resource selection on a SUMO-driven highway with multiple
concurrent V2X flows; measures collision rate, BLER, PRR, and now HARQ
feedback under different (μ, W, MCS) and traffic configurations.

The simulation binary is `groupcast_eval` (NS-3 scratch program). Sweeps
are driven by Python scripts in `scratch_overlay/adaptive-phy-eval/scripts/`.

## Repo layout (multi-repo, submodule-pinned)

```
groupcast-adaptation-ns3/
├── setup.sh, build.sh, env.sh         # one-shot bootstrap on a fresh clone
├── scratch_overlay/                   # user-authored code, tracked in parent
│   ├── groupcast_eval/                # main NS-3 scratch program
│   │   ├── groupcast_eval.cc          # CLI + orchestration
│   │   ├── groupcast_flows.{h,cc}     # FlowDef + InstallGroupcastFlows
│   │   ├── nr_sl_stack.{h,cc}         # NR + sidelink stack builder
│   │   ├── kpi_hooks.{h,cc}           # SQLite trace logging
│   │   ├── mobility.{h,cc}            # SUMO + fallback highway mobility
│   │   ├── ARCHITECTURE.md, DB_SCHEMA.md, RUNNING.md
│   └── adaptive-phy-eval/
│       ├── scripts/{run_sweep,analyze_sweep,plot_sweep}.py
│       └── sumo/                      # per-scenario SUMO configs
├── patches/                           # local mods to upstream submodules
│   ├── ns-3-dev/local.patch + extras/{traci,vehicle-visualizer}.tar.gz
│   ├── nr/local.patch                 # numerology + reverse-iter UB fix
│   └── netanim/local.patch
├── ns-3-dev/                          # submodule (branch v2x-lte-dev)
│   └── contrib/nr/                    # nested submodule
├── netanim/, sumo/                    # submodules
└── scripts/{regen-patches.sh, hooks/pre-commit}
```

`ns-3-dev/scratch/groupcast_eval` and `ns-3-dev/scratch/adaptive-phy-eval`
are **symlinks** into `scratch_overlay/` created by `setup.sh`. Edit the
files in `scratch_overlay/`, not under `ns-3-dev/scratch/`.

When editing inside a submodule (`ns-3-dev`, `nr`, `netanim`), commit in
the **parent** repo only. The pre-commit hook auto-regenerates patches
via `scripts/regen-patches.sh`. Never `git commit` inside a submodule.

## Build & run

```bash
./setup.sh && ./build.sh        # first time only
source env.sh                   # adds sumo/netanim to PATH, activates .venv

cd ns-3-dev && ./ns3 build groupcast_eval

# single run (writes <runId>.db + <runId>_run.csv to outputDir)
./build/scratch/groupcast_eval/ns3.42-groupcast_eval-default \
    --runId=test --simTime=10 --numUEs=8 --mu=1 --bwPRBs=50 --mcs=14 \
    --seed=1 --outputDir=/tmp/gce/

# sweep
python3 scratch/adaptive-phy-eval/scripts/run_sweep.py \
    --scenarios all --mu 1,2 --w 25,50,75,100 --mcs all \
    --seeds 1,2,3,4,5 --parallel $(nproc) \
    --outDir /path/to/large/storage/tmp --skip-existing
```

Completed runs are gzipped automatically. Analyse with
`analyze_sweep.py --tmp <outDir>` (read-only; safe while sweep runs).

## CLI flags on `groupcast_eval`

Stable knobs (set by sweep scripts):
`--runId --seed --numUEs --mu --bwPRBs --mcs --simTime --warmup
 --outPacketsCsv --outRunCsv --sumoCfg --sumoBinary --sumoSync
 --outputDir --simTag`

Recent additions (all backward-compatible — defaults reproduce prior runs):

| Flag | Default | Notes |
|---|---|---|
| `--harq` | `true` | Global HARQ enable (per-flow `harq=` overrides) |
| `--maxNumTx` | `5` | `slMaxTxTransNumPssch` (1..32). 1 disables retx. |
| `--psfchPeriod` | `0` | PSFCH period in slots (0=off, 1/2/4=on). **Required >0 for ACK/NACK feedback to actually flow.** With 0, HARQ retx happens blindly and `slHarqTimeout` rows accrue but `slHarqFeedback` stays empty. |
| `--flows` | _empty_ | Per-flow spec, comma-separated; each flow is `;`-separated `key=value` (`label,port,prio,rri_ms,pdb_ms,dynamic,rate_kbps,harq,enabled`). Empty => 3-flow default (cam/video/bg on 8001/8002/8003). |

`run_sweep.py` exposes `--maxNumTx`, `--psfchPeriod`, `--flows` as
optional pass-throughs. When unset, the emitted command is byte-identical
to the pre-feature version.

## SQLite output schema (per run)

Existing tables (see `scratch_overlay/groupcast_eval/DB_SCHEMA.md`):
`pscchTxUeMac`, `psschTxUeMac` (incl. `harqId`, `ndi`, `gapReTx{1,2}`),
`pscchRxUePhy`, `psschRxUePhy`, `rlcRx`, `pktTxRx`, `simulPsschTx` (+
V2xKpi-derived: `pir`, `prr`, `thput`, `avrgPir`, etc.).

New tables (added when adding HARQ feedback logging):
- `slHarqFeedback(timeSec, rxNodeId, rxRnti, txRnti, dstL2Id, harqId, status TEXT)`
  — one row per PSFCH ACK/NACK (only populates if `--psfchPeriod>0`).
- `slHarqTimeout(timeSec, txNodeId, harqId)` — TBs that exhausted retries
  with no ACK ever arriving. Always non-zero when HARQ is on; large counts
  signal feedback-channel starvation or extreme collision pressure.

## Standard scenarios (driven by SUMO subdirs)

- **s1** group size: `nrx02 / nrx04 / nrx08 / nrx12 / nrx16 / nrx20` (numUEs)
- **s2** inter-vehicle distance: `ivd010m / 020 / 050 / 100 / 150`
- **s3** speed: `spd030kmh / 060 / 100 / 140 / 200`
- **s4** propagation: `highway_los / urban_nlos / mixed`

PHY sweep space: `μ ∈ {0,1,2}`, `W ∈ {25,50,75,100} PRBs`,
`MCS ∈ {0,3,5,7,9,11,13,15,17,19,21,23,25,27}`. Seeds typically `1..5`.

## Known characteristics / gotchas

- **High SINR but high collision rate is expected**: simulations run at
  short range, so SINR is ~30 dB but Mode-2 contention drives ~75% collision
  rates at moderate group sizes. Fix is PHY tuning + traffic shaping, not
  link budget.
- **Default flow set** is 3 concurrent flows: 200 kb/s CAM (DG), 1 Mb/s
  video (SPS), 100 kb/s bg (SPS) — video is the dominant air-time consumer.
- **HARQ feedback was effectively a no-op before psfchPeriod was wired**:
  if you read older run DBs (psfchPeriod=0), the HARQ retransmissions
  happened but never carried meaningful feedback; ACK-rate analyses against
  pre-psfchPeriod data will be misleading.
- **sumo/netanim binaries** are built inside the submodules and put on
  PATH by `env.sh`. NetAnim's `main.cpp` has a local patch.
- **Reproducible tarballs on macOS**: `bsdtar` produces non-deterministic
  byte output even with `--mtime=@0`; the pre-commit hook re-stages them
  every commit. Not blocking, just noisy.

## File-pointer cheatsheet

- Resource pool config (where psfchPeriod, slMaxTxTransNumPssch, RRI list,
  subchannel size all live): `scratch_overlay/groupcast_eval/nr_sl_stack.cc`
  around the `NrSlCommResourcePoolFactory` block.
- Trace connection (where new SQL tables are wired to NR traces):
  `scratch_overlay/groupcast_eval/kpi_hooks.cc :: ConnectPhyMacTraces`.
- Flow installation (per-flow `SidelinkInfo`, OnOff/PacketSink apps):
  `scratch_overlay/groupcast_eval/groupcast_flows.cc :: InstallGroupcastFlows`.
- Sweep command construction:
  `scratch_overlay/adaptive-phy-eval/scripts/run_sweep.py :: build_ns3_command`.

## Working principles for agents in this repo

1. **Default behaviour must stay byte-identical** when adding optional
   flags — existing sweeps must be repeatable. Test with
   `run_sweep.py --dry-run` and diff command output before/after.
2. **Don't add per-feature config files** — wire knobs through
   `SlStackParams` / `FlowDef` and the existing `CommandLine`.
3. **Pre-commit hook regenerates patches** if you edit submodule files.
   If patch regen fails, run `scripts/regen-patches.sh` and inspect.
4. **The big test runs happen on a Linux remote** (more disk). Mac is for
   development — keep paths derived from `__file__`, never hardcoded
   `/Users/hadih/...` paths in any committed Python.
5. **Read `scratch_overlay/groupcast_eval/{ARCHITECTURE,DB_SCHEMA,RUNNING}.md`**
   for deeper detail on each subsystem before redesigning.
