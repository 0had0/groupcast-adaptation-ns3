"""Per-run metrics of the helper-reception campaign, for notebook 10 of coop-harq-analytical.

Rows: every finished run of out/local_helperrx (phase fixed and controlled) plus the campaign-v3 baseline of the
same cells (Kuo's helper, no forced miss: R3, R4_bsc, R6, periodic, no background, seeds 1..10). Columns: group and
worst-member PRR (run.csv prr_mean / prr_min, the KPI window, as in notebook 04), the forced-miss counter, and the
link split of link_split.ns3_split (helper decodes copy 1, members' first copy, recovery with and without the
helper, the helper's own PRR, the counts behind them).

  python3 extract.py   ->  <coop-harq-analytical>/notebooks/results_10/ns3_helper_rx.csv
"""
import glob
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent
ANALYTICAL = Path(os.environ.get("COOPHARQ_ANALYTICAL", "/Users/hadih/Documents/coop-harq-analytical"))
sys.path.insert(0, str(ANALYTICAL / "notebooks"))
import link_split as L  # noqa: E402

OUT = Path(os.environ.get("HRX_OUT", STUDY / "out/local_helperrx"))  # override: tests
V3 = STUDY / "out/remote_v3"
SC = {"hwy": "Highway", "rbt": "Roundabout W", "rbtE": "Roundabout E"}
ARM = {"R3": "Opt 2", "R4_bsc": "Blind helper+C", "R6": "Cooperative"}
PDB = 0.100  # periodic traffic


def one(args):
    exp, arm, pre = args
    name = os.path.basename(pre)
    m = re.match(r"(hwy|rbtE|rbt)_ivd30_periodic1tb_none(?:_h(\d+))?(?:_p([\d.]+)_(data|sci))?_s(\d+)$", name)
    sc, k, p, mode, seed = m.groups()
    run = pd.read_csv(pre + "_run.csv.gz" if os.path.exists(pre + "_run.csv.gz") else pre + "_run.csv").iloc[0]
    row = dict(experiment=exp, scheme=ARM[arm], scenario=SC[sc], helper=int(k) if k else 0,
               p=float(p) if p else 0.0, mode=mode or "", seed=int(seed), group_prr=run.prr_mean,
               worst_prr=run.prr_min, forced_miss=run.get("helper_forced_miss", 0))
    if arm != "R3":
        row.update(L.ns3_split(pre, PDB))
    return row


def main():
    jobs = []
    for exp in ("fixed", "controlled"):
        for f in glob.glob(str(OUT / exp / "*" / "*_run.csv.gz")):
            jobs.append((exp, Path(f).parent.name, f[: -len("_run.csv.gz")]))
    for arm in ARM:
        for sc in SC:
            for f in glob.glob(str(V3 / arm / f"{sc}_ivd30_periodic1tb_none_s*_run.csv*")):
                jobs.append(("baseline", arm, re.sub(r"_run\.csv(\.gz)?$", "", f)))
    with ProcessPoolExecutor(4) as ex:
        df = pd.DataFrame(list(ex.map(one, jobs, chunksize=4)))
    dest = Path(os.environ.get("HRX_DEST", ANALYTICAL / "notebooks/results_10"))
    dest.mkdir(exist_ok=True)
    df.sort_values(["experiment", "scenario", "scheme", "helper", "mode", "p", "seed"]).to_csv(
        dest / "ns3_helper_rx.csv", index=False)
    print(f"{len(df)} runs -> {dest / 'ns3_helper_rx.csv'}")
    print(df.groupby(["experiment", "scheme"]).size().to_string())


if __name__ == "__main__":
    main()
