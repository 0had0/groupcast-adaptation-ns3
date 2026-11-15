#!/usr/bin/env python3
"""Build MAP_HELPER_RESULTS_v3.ipynb: the map-vs-Kuo helper-selection analysis of MAP_HELPER_RESULTS.ipynb
(campaign v2) re-pointed at campaign v3. Data: out/remote_v3 (leader arms and Kuo helper arms, 0-60 vehicles)
and out/remote_v3_map (map helper arms, 1500 runs), both commit 32e29dd. The analysis cells are copied unchanged
from the v2 notebook; only the data sources, the pairing folder and the text change.

  python3 build_map_v3_notebook.py
  python3 -m jupyter nbconvert --to notebook --execute --inplace MAP_HELPER_RESULTS_v3.ipynb
"""
import json
import os

import nbformat as nbf

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'MAP_HELPER_RESULTS.ipynb')
DST = os.path.join(HERE, 'MAP_HELPER_RESULTS_v3.ipynb')
FINDINGS = os.path.join(HERE, 'map_v3_findings.md')


def rep(cell, old, new):
    s = ''.join(cell['source'])
    assert old in s, old[:70]
    cell['source'] = s.replace(old, new)


def main():
    nb = nbf.read(SRC, as_version=4)
    C = nb.cells
    for c in C:
        if c.cell_type == 'code':
            c.outputs, c.execution_count = [], None
    rep(C[0], '# Map helper selection over the load sweep: R4, R4.1, R4.2, R5, R6',
        '# Map helper selection over the load sweep, campaign v3: R4, R4.1, R4.2, R5, R6')
    rep(C[0], 'This notebook re-runs the five helper arms with `map`',
        'This notebook is the v3 version of `MAP_HELPER_RESULTS.ipynb` (v2 data, 2026-11-01): the same analysis '
        'on campaign v3, which sends SCI 1-A on every PSSCH (`--sciOnEveryTx=true`) and puts the leader\'s '
        'aperiodic traffic on dynamic grants. It re-runs the five helper arms with `map`')
    rep(C[0], '| none (baselines, from v2) |', '| none (baselines, from v3) |')
    rep(C[0], '| `kuo` (v2) or `map` (this campaign) |', '| `kuo` (v3) or `map` (v3_map) |')
    s = ''.join(C[0].source)
    i, j = s.index('**Data** (all after'), s.index('**Pairing.**')
    C[0].source = s[:i] + """**Data** (campaign v3, commit `32e29dd`, ease-rtx; after the stale-NDI fix):

| folder in `out/` | arms | loads | origin |
|---|---|---|---|
| `remote_v3` | R1-R3, Kuo R4, R4.1, R4.2, R5, R6 | 0, 15, 30, 45, 60 | campaign v3 (3000 runs) |
| `remote_v3_map` | map R4, R4.1, R4.2, R5, R6 | 0, 15, 30, 45, 60 | campaign v3, map arms (1500 runs) |

Every arm has every load, so no fill campaign is needed.

""" + s[j:]
    C[1].source = """## 0. Producing the data

Both folders were produced on ease-rtx at commit `32e29dd` (campaign v3, then its map arms as `out/v3_map`,
finished 2026-11-13, 1500 runs, 0 failed) and copied to the laptop as `out/remote_v3` and `out/remote_v3_map`.
Nothing in this notebook connects to the remote. Copy-back command for the map arms (from `harq_helper_study/`):
```
rsync -av -e "ssh -F $HOME/.ssh/ease-config" --prune-empty-dirs --include='*/' --include='COMMIT' \\
  --include='*.cmd.json' --include='*_run.csv*' --include='*_member.csv*' --include='*_pkt.csv*' \\
  --include='*_ltx.csv*' --include='*_htx.csv*' --include='*_hsel.csv*' --include='*_nack.csv*' \\
  --include='*_grants.csv*' --exclude='*' \\
  "ease-rtx:Documents/groupcast-adaptation-ns3/scratch_overlay/platoon_harq_robustness_eval/harq_helper_study/out/v3_map/" \\
  "out/remote_v3_map/"
```
The per-run metrics are cached in `out/map_analysis_cache/remote_v3_map/` (recomputed when a folder's run count
changes)."""
    rep(C[2], """MAP_DIR = os.environ.get('MAP_DIR', 'remote_map_v2')            # folders relative to OUT
FILL_DIR = os.environ.get('MAP_FILL_DIR', 'remote_map_fill_v2')""",
        """MAP_DIR = os.environ.get('MAP_DIR', 'remote_v3_map')            # folders relative to OUT
BASE_DIR = os.environ.get('MAP_BASE_DIR', 'remote_v3')         # leader arms and Kuo helper arms, all loads""")
    s = ''.join(C[2].source)
    i, j = s.index('SOURCES = ('), s.index('for d in sorted(')
    C[2].source = s[:i] + """SOURCES = ([(BASE_DIR, a, a, 'leader') for a in LEADER_ARMS]
           + [(BASE_DIR, a, a, 'kuo') for a in HELPER_ARMS]
           + [(MAP_DIR, a + '_map', a, 'map') for a in HELPER_ARMS])
""" + s[j:]
    rep(C[4], """        kfold = 'remote_density_v2' if bg.startswith('n') and bg != 'none' else 'remote_full_v2'
        kp = os.path.join(OUT, kfold, arm, tag + '.cmd.json')
        if not os.path.exists(kp):
            kp = os.path.join(OUT, FILL_DIR, arm, tag + '.cmd.json')""",
        """        kp = os.path.join(OUT, BASE_DIR, arm, tag + '.cmd.json')""")
    rep(C[13], '* Opt 2 (R3) for every arm, the strongest leader scheme in the v2 data.\n\nR2 exists at 15-45 vehicles only with the fill campaign.',
        '* Opt 2 (R3) for every arm, the strongest leader scheme in the v3 data (as in v2).')
    rep(C[3], '(never mix pre-fix and v2 data)', '(never mix pre-fix and post-fix data)')
    C[33].source = open(FINDINGS).read() if os.path.exists(FINDINGS) else '## 10. Findings\n\n(to be written)'
    nbf.write(nb, DST)
    print('wrote', os.path.basename(DST))


if __name__ == '__main__':
    main()
