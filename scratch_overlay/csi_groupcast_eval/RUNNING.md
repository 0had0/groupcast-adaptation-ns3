# Running csi_groupcast_eval

## Requirements

- Build with `libsumo` enabled (NS3_TRACI must be defined).
- A SUMO `.sumocfg` scenario (the simulation requires external vehicle mobility).
- `--blockerIds` lists the SUMO vehicle IDs that act as NLOSv obstacles.
  When empty the simulation runs with the geometric wrapper installed but
  transparent — equivalent to a plain V2V_Urban channel.

## Build

```bash
cd /path/to/groupcast-adaptation-ns3/ns-3-dev
./ns3 configure --enable-examples   # ensure NS3_TRACI is picked up
./ns3 build csi_groupcast_eval
# Binary: build/scratch/csi_groupcast_eval/ns3.42-csi_groupcast_eval-optimized
```

## Smoke test — no blockers (transparent wrapper)

```bash
./ns3 run "csi_groupcast_eval \
  --runId=smoke_noblk \
  --sumoCfg=/path/to/scenario.sumocfg \
  --numUEs=6 --mu=1 --bwPRBs=50 --mcs=14 \
  --baseScenario=V2V_Urban \
  --simTime=4 --warmup=1 \
  --outputDir=/tmp/cgce/ \
  --outRunCsv=/tmp/cgce/smoke_noblk_run.csv"
```

## With truck blockers

```bash
./ns3 run "csi_groupcast_eval \
  --runId=smoke_blk \
  --sumoCfg=/path/to/scenario.sumocfg \
  --numUEs=6 --mu=1 --bwPRBs=50 --mcs=14 \
  --baseScenario=V2V_Urban \
  --blockerIds=truck.0,truck.1 \
  --blockerL=8.0 --blockerW=2.6 \
  --simTime=4 --warmup=1 \
  --outputDir=/tmp/cgce/ \
  --outRunCsv=/tmp/cgce/smoke_blk_run.csv"
```

## V2V_Highway base model (regression vs. groupcast_eval)

```bash
./ns3 run "csi_groupcast_eval \
  --runId=regression \
  --sumoCfg=/path/to/scenario.sumocfg \
  --numUEs=8 --baseScenario=V2V_Highway \
  --blockerIds= \
  --simTime=10 --outputDir=/tmp/cgce/"
```
KPIs should match `groupcast_eval` for the same SUMO config.

## Verify NLOSv is firing

Add NS3_LOG to see per-call geometry decisions:

```bash
NS_LOG="ThreeGppGeometricNlosvModel=info" ./ns3 run "csi_groupcast_eval ..." 2>&1 | grep GeometricNLOSv
```

## Key CLI flags

| Flag             | Default      | Description                                    |
|------------------|--------------|------------------------------------------------|
| `--sumoCfg`      | (required)   | Path to SUMO `.sumocfg`                        |
| `--sumoBinary`   | `sumo`       | SUMO executable                                |
| `--sumoSync`     | `0.1`        | TraCI sync interval (s)                        |
| `--numUEs`       | `8`          | Number of UE nodes                             |
| `--blockerIds`   | `""`         | Comma-separated SUMO IDs → NLOSv obstacles     |
| `--blockerL`     | `5.0`        | Blocker length (m)                             |
| `--blockerW`     | `2.5`        | Blocker width (m)                              |
| `--baseScenario` | `V2V_Urban`  | `V2V_Highway` \| `V2V_Urban` \| `UMi_StreetCanyon` |
| `--shadowing`    | `true`       | Enable log-normal shadowing                    |
| `--mu`           | `1`          | Numerology (0=15 kHz, 1=30 kHz, 2=60 kHz)     |
| `--bwPRBs`       | `50`         | Subchannel PRB width                           |
| `--mcs`          | `14`         | Fixed MCS index                                |
| `--maxNumTx`     | `5`          | PSSCH max transmissions (1=no retx)            |
| `--psfchPeriod`  | `0`          | PSFCH period slots (0=no HARQ feedback)        |
| `--flows`        | (3 defaults) | Flow spec string (same format as groupcast_eval) |
| `--simTime`      | `10.0`       | Traffic duration per flow (s)                  |
| `--warmup`       | `1.0`        | Warm-up discarded by V2xKpi                    |
| `--outputDir`    | `./`         | SQLite DB directory                            |
| `--outRunCsv`    | `""`         | Per-run summary CSV path                       |
| `--outPacketsCsv`| `""`         | Per-packet CSV path                            |
