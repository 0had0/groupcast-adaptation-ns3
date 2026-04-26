# groupcast-adaptation-ns3

NS-3 / NR sidelink groupcast PHY adaptation experiment.

## Quick start (fresh clone)

```bash
git clone <this-repo-url> groupcast-adaptation-ns3
cd groupcast-adaptation-ns3
./setup.sh          # clone submodules, apply patches, create symlinks
./build.sh          # configure + build NS-3
source env.sh       # activate SUMO/NetAnim PATH + Python venv (do this in every shell session)
```

## Repository layout

```
.
├── setup.sh                   # assembles full source tree from submodules + patches
├── build.sh                   # ns3 configure + build
├── scratch_overlay/           # user-authored NS-3 scratch programs (tracked here)
│   ├── groupcast_eval/        # main simulation binary
│   └── adaptive-phy-eval/     # sweep scripts, SUMO configs, analysis scripts
├── scripts/
│   ├── regen-patches.sh       # regenerate patches from submodule working trees
│   └── hooks/pre-commit       # git hook: auto-regens patches on parent commit
├── patches/                   # local modifications to upstream submodules
│   ├── netanim/local.patch
│   ├── ns-3-dev/local.patch
│   ├── ns-3-dev/extras/       # tarballs for whole untracked dirs (src/traci, src/vehicle-visualizer)
│   └── nr/local.patch
├── ns-3-dev/                  # submodule: gitlab.com/cttc-lena/ns-3-dev  (branch v2x-lte-dev)
│   └── contrib/nr/            # submodule: gitlab.com/cttc-lena/nr        (branch nr-v2x-dev)
├── netanim/                   # submodule: gitlab.com/nsnam/netanim
└── sumo/                      # submodule: github.com/eclipse-sumo/sumo
```

`ns-3-dev/scratch/groupcast_eval` and `ns-3-dev/scratch/adaptive-phy-eval` are **symlinks** into
`scratch_overlay/` created by `setup.sh`. Edit source files in `scratch_overlay/`; they appear
inside NS-3's build tree automatically via the symlinks.

## Modifying upstream submodule code

1. Edit the files inside the submodule directory (e.g. `ns-3-dev/contrib/nr/model/...`).
2. Commit in the **parent** repo: `git add -p && git commit -m "..."`.
   The pre-commit hook runs `regen-patches.sh` automatically and stages the updated patch.
3. Do **not** commit inside the submodule itself.

To manually regenerate patches without committing:
```bash
scripts/regen-patches.sh
```

To check whether patches are stale (CI / pre-push guard):
```bash
scripts/regen-patches.sh --check
```

## Running the sweep

```bash
cd ns-3-dev
python3 scratch/adaptive-phy-eval/scripts/run_sweep.py \
    --scenarios all --mu 1,2 --w 25,50,75,100 --mcs all \
    --seeds 1,2,3,4,5 --parallel 4 \
    --outDir /path/to/large/storage/tmp \
    --skip-existing
```

Completed runs are gzip-compressed automatically. Analyse partial results without
stopping the sweep:
```bash
python3 scratch/adaptive-phy-eval/scripts/analyze_sweep.py \
    --tmp /path/to/large/storage/tmp
```
