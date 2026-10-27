#!/usr/bin/env python3
"""runsets.py -- single source of truth for the run-sets of the platoon_harq_robustness_eval test suite.

See tests/TEST_PLAN.md section 7.4.  Synthetic run-sets run the production binary on synthetic static traces
(tests/out/traces/); CORE / SPSDYN use the real traces of scratch_overlay/platoon_harq_robustness_eval/traces/.

BASE_FLAGS, the background levels and the arm flags are copied from run_retx.py; assert_base_flags() imports
run_retx and compares (deviation would abort the launch script).
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent
EVAL = TESTS.parent
REPO = EVAL.parents[1]
sys.path.insert(0, str(EVAL))
sys.path.insert(0, str(HERE))

# PTEST_BIN overrides the simulator (e.g. a saved build of an earlier commit for a before / after comparison)
BIN = Path(os.environ.get("PTEST_BIN") or
           REPO / "ns-3-dev/build/scratch/platoon_harq_robustness_eval/ns3.42-platoon_harq_robustness_eval-default")
OUT = TESTS / "out"
TRACES_REAL = EVAL / "traces"
TRACES_SYN = OUT / "traces"
SUMO_NET = EVAL / "sumo/roundabout/roundabout.net.xml"
SHORT = {"highway": "hwy", "roundabout": "rbt"}

# --- BASE_FLAGS (TEST_PLAN section 3), identical to run_retx.py (+ --maxNumTx=3 which run_retx puts in the arm flags)
RRI_LIST = "0,10,20,50,100,200,300,400,500,600,700,800,900,1000"
BASE_FLAGS = [
    "--dedicatedCarrier=true",
    "--leaderSps=true",
    "--spsReselectOnMisfit=true",
    "--slDynExclusion=true",
    "--slProjectAnnounced=true",
    "--slProbResourceKeep=0.8",
    f"--rriList={RRI_LIST}",
    "--ns3::LteRlcUm::ReorderingTimer=0ms",
    "--maxNumTx=3",
]
DIAG_FLAGS = ["--writeAtt=true", "--writeAllTx=true", "--writeGrants=true"]

# background levels: name -> (trace NBG, flows per car)   (run_retx.get_bg_levels)
BG_LEVELS = {"none": (0, 1.0), "moderate": (60, 1.67), "high": (60, 3.33),
             # QUICK-PROFILE SURROGATES (deviation, see report): a true n60 cell costs 6-80 min per run (measured wall_s of
             # hwy n60 k=1.67: 456 s for a 20 s run, k=3.33: 1216 s; roundabout k=3.33: 4952 s), so the quick profile uses
             # 15 background cars: bgq = n15, 1.67 flows/car (85 s for 20 s), bgq2 = n15, 3.33 flows/car (263 s).
             "bgq": (15, 1.67), "bgq2": (15, 3.33)}

# --- the 6 arms of the campaign (TEST_PLAN section 0.1 D-1: Kuo only, H1 and H5 dropped)
ARMS = {
    "R1": ["--retx=R1"],
    "R2": ["--retx=R2"],
    "R3": ["--retx=R3"],
}
for _r in ("R4", "R5", "R6"):
    ARMS[f"{_r}_S1_H2"] = [f"--retx={_r}", "--helperSched=S1", "--helperSelect=kuo", "--helper=4"]
ARM_ORDER = ["R1", "R2", "R3", "R4_S1_H2", "R5_S1_H2", "R6_S1_H2"]
assert set(ARM_ORDER) == set(ARMS)

# extra arms used only by single tests (not part of the 6-arm study)
EXTRA_ARMS = {
    # A04: leader scheduler class NrSlCoopScheduler with cooperation off (helperMode=none) vs the stock one (helperMode=off)
    "R2_hmnone": ["--harqMode=opt1", "--helperMode=none"],
}

PDB_MS = {"periodic1tb": 100.0, "periodic2tb": 10.0, "aperiodic1tb": 50.0, "periodic1fx": 100.0, "periodic2fx": 10.0, "aperiodic1fx": 50.0}


def assert_base_flags():
    """Compare with run_retx.py (import)."""
    import run_retx  # noqa: E402
    rr = list(run_retx.BASE_FLAGS)
    mine = [f for f in BASE_FLAGS if f != "--maxNumTx=3"]
    if rr != mine:
        raise SystemExit(f"BASE_FLAGS differ from run_retx.py:\n  run_retx: {rr}\n  tests:    {mine}")
    study = {k: v for k, v in BG_LEVELS.items() if k in ("none", "moderate", "high")}
    if run_retx.get_bg_levels() != study:
        raise SystemExit(f"BG levels differ from run_retx.py: {run_retx.get_bg_levels()} vs {study}")
    # the arm flags of Stage B in run_retx must contain the ones used here
    b = run_retx.generate_stageB_arms()
    for arm, flags in ARMS.items():
        name = arm
        if arm in ("R1", "R2", "R3"):
            continue
        want = sorted(set(flags) | {"--maxNumTx=3"})
        if name in b and sorted(set(b[name])) != want:
            raise SystemExit(f"arm {arm} flags differ from run_retx Stage B: {b[name]} vs {want}")
    return True


# --- synthetic geometries (TEST_PLAN section 7.4).  Offsets (dx, dy) of members 1..8 from the leader (m).
# DEVIATION from the plan (see report): SYN_NEAR / the first 7 members of SYN_DTX / SYN_MCR sit on a ring of radius
# RING_R around the leader instead of in a line.  In a line, vehicle blockage (NLOSv) makes the tail members fail
# 2-10% of the attempts even at 6-10 m spacing (probe: member 8, 10 m spacing, PRR 0.958, 24 corrupt attempts / 186),
# so "everything decodes" is unreachable in a line.  On the ring no vehicle lies on a leader-member segment: LOS.
import math

RING_R = 12.0


def _line(spacing, n=8):
    return [(-spacing * k, 0.0) for k in range(1, n + 1)]  # members behind the leader on the same lane


def _ring(n, r=RING_R, offset_deg=10.0):
    return [(round(r * math.cos(math.radians(offset_deg + 360.0 * k / n)), 3),
             round(r * math.sin(math.radians(offset_deg + 360.0 * k / n)), 3)) for k in range(n)]


GEOMS = {
    "SYN_NEAR": {"members": _ring(8)},
    "SYN_MARGINAL": {"members": _line(60)},
    "SYN_DTX": {"members": _ring(7) + [(-3000.0, 0.0)]},
    "SYN_MCR": {"members": _ring(7) + [(-650.0, 0.0)]},
}
for _s in (10, 20, 30, 45, 60, 80):
    GEOMS[f"SYN_DIST_{_s}"] = {"members": _line(_s)}
# SYN_SPS uses the SYN_MARGINAL geometry
GEOMS["SYN_SPS"] = GEOMS["SYN_MARGINAL"]
# C06 (cross-source combining): leader L, helper H 10 m behind, receiver J beside H on the adjacent lane (LOS from L and
# H, no vehicle on either segment); only 3 platoon nodes (--numPlatoon=3); the PSSCH SINR of every link is fixed by
# --testFixedSinr, so the geometry only matters for PSCCH / PSFCH / sensing (strong at 10 m)
GEOMS["SYN_COMB"] = {"members": [(-10.0, 0.0), (-10.0, 3.7)]}

SYN_TEND = 20.0
SYN_KPI_START = 3.0
SYN_SEEDS = [1, 2, 3]


@dataclass
class Run:
    runset: str
    arm: str                   # arm name (or a DET variant name)
    scenario: str
    ivd: str                   # ivd (real traces) or centre spacing (synthetic)
    bg: str
    traffic: str
    seed: int
    leader_sps: bool
    trace: Path
    flags: list = field(default_factory=list)        # arm flags (retx etc.)
    extra: list = field(default_factory=list)        # run-set specific flags
    bg_flows: float = 1.0
    max_time: float = 0.0
    env_set: dict = field(default_factory=dict)      # env variables set explicitly (A05 probe only)
    diag: bool = True

    @property
    def tag(self):
        t = f"{SHORT[self.scenario]}_ivd{self.ivd}_{self.traffic}_{self.bg}_s{self.seed}"
        if not self.leader_sps:
            t += "_sps0"
        return t

    @property
    def outdir(self):
        return OUT / self.runset / self.arm

    @property
    def prefix(self):
        return self.outdir / self.tag

    def args(self):
        a = [f"--scenario={self.scenario}", f"--mobility={self.trace}", f"--traffic={self.traffic}",
             f"--seed={self.seed}", f"--run={self.seed}", f"--bgFlowsPerCar={self.bg_flows:g}",
             f"--out={self.prefix}"]
        base = [f for f in BASE_FLAGS]
        if not self.leader_sps:
            base = [("--leaderSps=false" if f == "--leaderSps=true" else f) for f in base]
        a += base + self.flags
        if self.traffic == "aperiodic1tb" and not any(f.startswith("--aperiodicRri=") for f in self.flags):
            a.append("--aperiodicRri=20")
        if self.scenario == "roundabout":
            a.append(f"--sumoNet={SUMO_NET}")
        if self.max_time > 0:
            a.append(f"--maxTime={self.max_time:g}")
        if self.diag:
            a += DIAG_FLAGS
        a += self.extra
        return a


def _real_trace(sc, ivd, bg, seed):
    nbg = BG_LEVELS[bg][0]
    return TRACES_REAL / f"{SHORT[sc]}_ivd{ivd}_n{nbg}_s{seed}.csv.gz"


def syn_trace_path(geom):
    return TRACES_SYN / f"{geom}.csv"


def _syn_run(runset, geom, arm, traffic, seed, spacing, leader_sps=True, extra=None, env_set=None, diag=True,
             arm_name=None):
    return Run(runset=runset, arm=arm_name or arm, scenario="highway", ivd=str(spacing), bg="none", traffic=traffic,
               seed=seed, leader_sps=leader_sps, trace=syn_trace_path(geom), flags=list(ARMS.get(arm) or EXTRA_ARMS[arm]),
               extra=list(extra or []), bg_flows=1.0, max_time=SYN_TEND, env_set=dict(env_set or {}), diag=diag)


def build_synth():
    runs = {}
    trafs3 = ["periodic1tb", "aperiodic1tb", "periodic2tb"]
    trafs2 = ["periodic1tb", "aperiodic1tb"]
    near = ["R1", "R2", "R3", "R4_S1_H2", "R5_S1_H2", "R6_S1_H2"]
    runs["SYN_NEAR"] = [_syn_run("SYN_NEAR", "SYN_NEAR", a, t, s, 10)
                        for a in near for t in trafs3 for s in SYN_SEEDS]
    runs["SYN_MARGINAL"] = [_syn_run("SYN_MARGINAL", "SYN_MARGINAL", a, t, s, 60)
                            for a in ARM_ORDER for t in trafs3 for s in SYN_SEEDS]
    runs["SYN_DTX"] = [_syn_run("SYN_DTX", "SYN_DTX", a, t, s, 10, extra=["--maxLossDb=110"])
                       for a in ["R1", "R2", "R3", "R5_S1_H2", "R6_S1_H2"] for t in trafs2 for s in SYN_SEEDS]
    runs["SYN_MCR"] = [_syn_run("SYN_MCR", "SYN_MCR", a, t, s, 10)
                       for a in ["R2", "R3"] for t in trafs2 for s in SYN_SEEDS]
    dist = []
    for sp in (10, 20, 30, 45, 60, 80):
        dist += [_syn_run("SYN_DIST", f"SYN_DIST_{sp}", a, t, s, sp)
                 for a in ["R1", "R2", "R3", "R4_S1_H2", "R5_S1_H2", "R6_S1_H2"] for t in trafs2 for s in SYN_SEEDS]
    runs["SYN_DIST"] = dist
    runs["SYN_SPS"] = [_syn_run("SYN_SPS", "SYN_SPS", a, t, s, 60, leader_sps=sps)
                       for a in ["R2", "R6_S1_H2"] for t in trafs2 for sps in (True, False) for s in SYN_SEEDS]
    # periodic2tb (RRI = PDB = 10 ms) with an SPS and with a dynamic leader: PDB / HARQ-id sharing mechanism (F04, B01, F30)
    runs["SYN_P2"] = [_syn_run("SYN_P2", "SYN_MARGINAL", a, "periodic2tb", s, 60, leader_sps=sps)
                      for a in ["R1", "R2", "R6_S1_H2"] for sps in (True, False) for s in SYN_SEEDS]
    runs["SYN_HM"] = [_syn_run("SYN_HM", "SYN_MARGINAL", a, t, s, 60)
                      for a in ["R2_hmnone"] for t in trafs2 for s in SYN_SEEDS]
    # A05: one deliberate run with the MAGIC_HELPER_ID hook live.  DEVIATION: SYN_MARGINAL instead of SYN_NEAR (the hook
    # forces decode success of node id 3 = member 3; on the lossless SYN_NEAR ring nothing could differ).
    runs["MAGIC"] = [_syn_run("MAGIC", "SYN_MARGINAL", "R6_S1_H2", "periodic1tb", 1, 60, env_set={"MAGIC_HELPER_ID": "4"})]
    runs["SYN_COMB"] = build_comb()
    runs["SYN_STALE"] = build_stale()
    return runs


# --- C06 (cross-source combining): 3 platoon nodes L = 0, H = 1, J = 2; the PSSCH SINR of every link is fixed
# (--testFixedSinr "rx:tx=dB", node ids), so decoding is deterministic up to the TBLER of the chosen points.
# NrEesmIrT1, MCS 14, periodic1fx TB = 335 B on 20 RB (C06 --calibrate 335 20): one copy alone TBLER 1.0 up to
# 8.5 dB, 0 from 10 dB; two copies combined TBLER 0.001 at 7.0 dB, 0 from 7.5 dB.
COMB_SEEDS = [1, 2]
COMB_TEND = 12.0
COMB_HELPER = ["--helperSched=S1", "--helperSelect=static", "--helper=1"]
COMB_SINR = {
    # J <- L 7.8 dB, J <- H 8.2 dB: each copy alone fails, leader + helper combined decodes
    "main": "1:0=30;2:0=7.8;2:1=8.2;0:1=30;0:2=30;1:2=30",
    # J <- L 0 dB (SCI-2A still decoded), J <- H 10 dB: the helper copy alone decodes, combined with the weak
    # leader copy it does not (EESM-IR averages the copies)
    "weak": "1:0=30;2:0=0;2:1=10;0:1=30;0:2=30;1:2=30",
    # HARQ-id reuse (periodic2fx: the SPS leader keeps one HARQ id, RRI 10 ms): J decodes no copy, so the helper
    # relays in every handed slot (the latest copies of a TB); does a copy of TB k reach J after TB k+1's NDI = 1?
    "reuse": "1:0=30;2:0=0;2:1=0;0:1=30;0:2=30;1:2=30",
}
COMB_CASES = {
    # name: (retx flags, SINR key, extra flags, traffic)
    "R6_sc1": (["--retx=R6"] + COMB_HELPER, "main", [], "periodic1fx"),
    "R6_sc0": (["--retx=R6"] + COMB_HELPER, "main", ["--softCombining=false"], "periodic1fx"),
    "R4.2_sc1": (["--retx=R4.2"] + COMB_HELPER, "main", [], "periodic1fx"),
    "R4.2_sc0": (["--retx=R4.2"] + COMB_HELPER, "main", ["--softCombining=false"], "periodic1fx"),
    "R4_bsc0": (["--retx=R4"] + COMB_HELPER, "main", ["--blindSoftCombining=false"], "periodic1fx"),
    "R4_bsc1": (["--retx=R4"] + COMB_HELPER, "main", ["--blindSoftCombining=true"], "periodic1fx"),
    "R3_sc1": (["--retx=R3"], "main", [], "periodic1fx"),
    "R3_sc0": (["--retx=R3"], "main", ["--softCombining=false"], "periodic1fx"),
    "R6_weak_sc1": (["--retx=R6"] + COMB_HELPER, "weak", [], "periodic1fx"),
    "R6_weak_sc0": (["--retx=R6"] + COMB_HELPER, "weak", ["--softCombining=false"], "periodic1fx"),
    "R6_reuse": (["--retx=R6"] + COMB_HELPER, "reuse", [], "periodic2fx"),
}


def build_comb():
    runs = []
    for name, (flags, key, extra, traffic) in COMB_CASES.items():
        for seed in COMB_SEEDS:
            runs.append(Run(runset="SYN_COMB", arm=name, scenario="highway", ivd="10", bg="none", traffic=traffic,
                            seed=seed, leader_sps=True, trace=syn_trace_path("SYN_COMB"), flags=list(flags),
                            extra=["--numPlatoon=3", "--mcs=14", "--writeHarqTrace=true",
                                   f"--testFixedSinr={COMB_SINR[key]}"] + list(extra),
                            bg_flows=1.0, max_time=COMB_TEND))
    return runs


# --- C07 (stale HARQ state after a missed new-data SCI): the SYN_COMB geometry and run setup; --testDropSci
# "rx:tx:k,..." makes J (node 2) ignore its k-th expected PSSCH reception from tx (both SCI stages unread; the
# SCI-1A reservation of the later copies is kept, as in 3GPP where every copy carries its own SCI-1A). The leader
# (SPS, one HARQ id) sends one TB per 100 ms; the k values below are the receptions of the unfixed binary at
# seed 1 (C07 checks that each drop hits the intended copy).
STALE_SEEDS = [1, 2]
STALE_TEND = 4.0
STALE_SINR = {
    # J needs 2 copies of a TB (7.8 dB: alone TBLER 1, two combined 0); H decodes copy 1 (Option 2 arms)
    "j2": "1:0=30;2:0=7.8;2:1=8.2;0:1=30;0:2=30;1:2=30",
    # J and H need 2 copies (Option 1: copy 2 is sent only on H's NACK when J's copy 1 is ignored)
    "jh2": "1:0=7.8;2:0=7.8;2:1=8.2;0:1=30;0:2=30;1:2=30",
    # J decodes copy 1; H needs copy 2 (J skips and, under Option 2, ACKs copy 2)
    "jok": "1:0=7.8;2:0=30;2:1=30;0:1=30;0:2=30;1:2=30",
    # J never decodes (5 dB: TBLER 1 alone and combined): buffer of 1, 2, 3 copies
    "jfail": "1:0=30;2:0=5;2:1=5;0:1=30;0:2=30;1:2=30",
}
STALE_CASES = {
    # name: (retx flags, SINR key, --testDropSci, extra flags)
    # (a) TB n decoded on HARQ id h, copy 1 of TB n+1 ignored (Option 1: no ACK of a decoded TB)
    "a_R2": (["--retx=R2"], "jh2", "2:0:3", []),
    # (b) as (a) with Option 2 (SlAckPrevDecodedTb)
    "b_R3": (["--retx=R3"], "j2", "2:0:3", []),
    # (c) TB n not decoded (its later copies ignored, copy 1 stays in the buffer), copy 1 of TB n+1 ignored
    "c_R2": (["--retx=R2"], "jh2", "2:0:4,5", []),
    "c_R3": (["--retx=R3"], "j2", "2:0:4,5,6", []),
    # (d) cross-source: copy 2 of TB n+1 is the helper's (relayedFromRnti = leader, leader's HARQ id)
    "d_R6": (["--retx=R6"] + COMB_HELPER, "j2", "2:0:2", []),
    "d_R6_stale": (["--retx=R6"] + COMB_HELPER, "j2", "2:1:2,3;2:0:3", []),
    # (e) regression, nothing ignored
    "e_R3": (["--retx=R3"], "j2", "", []),
    "e_R3_3copies": (["--retx=R3"], "jfail", "", []),
    "e_R3_decoded": (["--retx=R3"], "jok", "", []),
    "e_R1": (["--retx=R1"], "j2", "", []),
    "e_R1_bsc": (["--retx=R1"], "j2", "", ["--blindSoftCombining=true"]),
    "e_R6": (["--retx=R6"] + COMB_HELPER, "j2", "", []),
}


def build_stale():
    runs = []
    for name, (flags, key, drop, extra) in STALE_CASES.items():
        for seed in STALE_SEEDS:
            ex = ["--numPlatoon=3", "--mcs=14", "--writeHarqTrace=true", f"--testFixedSinr={STALE_SINR[key]}"]
            if drop:
                ex.append(f"--testDropSci={drop}")
            runs.append(Run(runset="SYN_STALE", arm=name, scenario="highway", ivd="10", bg="none",
                            traffic="periodic1fx", seed=seed, leader_sps=True, trace=syn_trace_path("SYN_COMB"),
                            flags=list(flags), extra=ex + list(extra), bg_flows=1.0, max_time=STALE_TEND))
    return runs


DET_TEND = 8.0  # DEVIATION: 8 s instead of 53 s (n60 moderate costs ~6 min per 10 s of simulated time)


def build_det():
    """DET: hwy_ivd30_n60_s1 moderate periodic1tb; arms R2 and R6_S1_H2; variants: a/b (identical), off (diag off),
    coll (--collCond=true)."""
    tr = _real_trace("highway", 30, "moderate", 1)
    runs = []
    for arm in ("R2", "R6_S1_H2"):
        for var, diag, extra in (("on_a", True, []), ("on_b", True, []), ("off", False, []),
                                 ("coll", True, ["--collCond=true"])):
            runs.append(Run(runset="DET", arm=f"{arm}__{var}", scenario="highway", ivd="30", bg="moderate",
                            traffic="periodic1tb", seed=1, leader_sps=True, trace=tr, flags=list(ARMS[arm]),
                            extra=extra, bg_flows=BG_LEVELS["moderate"][1], max_time=DET_TEND, diag=diag))
    return runs


def _max_time(profile, sc):
    # profiles: quick (20 s everywhere), reduced / full (hwy 53 s, roundabout whole trace)
    """Full profile = the campaign (hwy 53 s, roundabout whole trace); quick = 20 s everywhere (KPI window 3..19.7 s)."""
    if profile in ("quick", "pilot"):
        return 20.0
    return 53.0 if sc == "highway" else 0.0


def build_core(profile):
    if profile == "quick":
        cells = [("highway", 30), ("roundabout", 30)]
        bgs = ["none", "bgq"]
        trafs = {"periodic1tb": [1, 2], "aperiodic1tb": [1, 2]}
    elif profile == "pilot":
        # TEST_PLAN 0.3: pilot of the second fixed-simulator round: 20 s runs, hwy ivd30 + rbt ivd30, bg none / moderate (n60), seeds 1-2
        cells = [("highway", 30), ("roundabout", 30)]
        bgs = ["none", "moderate"]
        trafs = {"periodic1tb": [1, 2], "aperiodic1tb": [1, 2], "periodic2fx": [1, 2]}
    elif profile == "reduced":
        # TEST_PLAN 0.2: reduced profile of the fixed-simulator round (324 runs): hwy ivd15 / ivd30 and rbt ivd30, bg none and moderate
        # (trace n60, 1.67 flows/car), 3 traffic classes, 6 arms, seeds 1-3; hwy --maxTime=53, roundabout the whole trace
        cells = [("highway", 15), ("highway", 30), ("roundabout", 30)]
        bgs = ["none", "moderate"]
        trafs = {"periodic1tb": [1, 2, 3], "aperiodic1tb": [1, 2, 3], "periodic2fx": [1, 2, 3]}
    else:
        cells = [("highway", 5), ("highway", 15), ("highway", 30), ("roundabout", 30)]
        bgs = ["none", "moderate", "high"]
        trafs = {"periodic1tb": [1, 2, 3, 4, 5], "aperiodic1tb": [1, 2, 3, 4, 5], "periodic2tb": [1, 2, 3]}
    runs = []
    for arm in ARM_ORDER:
        for sc, ivd in cells:
            for bg in bgs:
                if profile == "pilot" and sc == "roundabout" and bg != "none":
                    continue  # user 2026-10-19: rbt + moderate is the costliest cell; it runs in the evening job
                for tr, seeds in trafs.items():
                    for seed in seeds:
                        # periodic2fx: T2 = 18 slots (< remaining PDB): TB k's transmissions end before
                        # packet k+1 arrives on the same SPS HARQ process (TEST_PLAN 0.4)
                        p2 = ["--t2Ms=9"] if (tr == "periodic2fx" and profile == "reduced") else []
                        runs.append(Run(runset="CORE", arm=arm, scenario=sc, ivd=str(ivd), bg=bg, traffic=tr,
                                        seed=seed, leader_sps=True, trace=_real_trace(sc, ivd, bg, seed),
                                        flags=list(ARMS[arm]) + p2, bg_flows=BG_LEVELS[bg][1],
                                        max_time=_max_time(profile, sc)))
    if profile == "quick":
        # one 3-level cell (none < bgq < bgq2) so that the background monotonicity checks have three points
        for arm in ARM_ORDER:
            for seed in (1, 2):
                runs.append(Run(runset="CORE", arm=arm, scenario="highway", ivd="30", bg="bgq2", traffic="periodic1tb",
                                seed=seed, leader_sps=True, trace=_real_trace("highway", 30, "bgq2", seed),
                                flags=list(ARMS[arm]), bg_flows=BG_LEVELS["bgq2"][1], max_time=_max_time(profile, "highway")))
    return runs


def build_spsdyn(profile):
    if profile in ("reduced", "pilot"):
        profile = "quick"  # TEST_PLAN 0.2: SPSDYN = the quick spec, re-run with the fixed binary
    arms = ["R1", "R2", "R3", "R4_S1_H2", "R5_S1_H2", "R6_S1_H2"]
    if profile == "quick":
        cells = [("highway", 30)]
        bgs = ["none", "bgq"]
        trafs = {"periodic1tb": [1, 2]}
    else:
        cells = [("highway", 15), ("highway", 30)]
        bgs = ["none", "moderate", "high"]
        trafs = {"periodic1tb": [1, 2, 3], "aperiodic1tb": [1, 2, 3], "periodic2tb": [1, 2, 3]}
    runs = []
    for arm in arms:
        for sc, ivd in cells:
            for bg in bgs:
                for tr, seeds in trafs.items():
                    for seed in seeds:
                        runs.append(Run(runset="SPSDYN", arm=arm, scenario=sc, ivd=str(ivd), bg=bg, traffic=tr,
                                        seed=seed, leader_sps=False, trace=_real_trace(sc, ivd, bg, seed),
                                        flags=list(ARMS[arm]), bg_flows=BG_LEVELS[bg][1],
                                        max_time=_max_time(profile, sc)))
    return runs


def build_rbt_ring():
    """Roundabout cells over the WHOLE trace (54 s): the platoon reaches the ring (buildings, NLOS) only after ~22 s, so the 20 s
    quick CORE roundabout cells never see a building.  No background traffic -> cheap.  Used by E03 (NLOS / --sumoNet checks)."""
    return [Run(runset="RBT_RING", arm=arm, scenario="roundabout", ivd="30", bg="none", traffic="periodic1tb", seed=1,
                leader_sps=True, trace=_real_trace("roundabout", 30, "none", 1), flags=list(ARMS[arm]), bg_flows=BG_LEVELS["none"][1],
                max_time=0.0)
            for arm in ARM_ORDER]


SYNTH_NAMES = ["SYN_NEAR", "SYN_MARGINAL", "SYN_DTX", "SYN_MCR", "SYN_DIST", "SYN_SPS", "SYN_P2", "SYN_HM", "DET", "MAGIC",
               "SYN_COMB", "SYN_STALE"]
CAMPAIGN_NAMES = ["CORE", "SPSDYN", "RBT_RING"]


def build(runset, profile="quick"):
    """Planned runs of a run-set."""
    if runset in SYNTH_NAMES and runset not in ("DET",):
        return build_synth()[runset]
    if runset == "DET":
        return build_det()
    if runset == "CORE":
        return build_core(profile)
    if runset == "SPSDYN":
        return build_spsdyn(profile)
    if runset == "RBT_RING":
        return build_rbt_ring()
    raise SystemExit(f"unknown run-set {runset}")


def traces_needed(runs):
    return sorted({r.trace for r in runs})


if __name__ == "__main__":
    prof = sys.argv[1] if len(sys.argv) > 1 else "quick"
    assert_base_flags()
    print("BASE_FLAGS == run_retx.py: OK")
    tot = 0
    for rs in SYNTH_NAMES + CAMPAIGN_NAMES:
        n = len(build(rs, prof))
        tot += n
        print(f"{rs:14s} {n:5d} runs")
    print(f"total {tot} runs (profile {prof})")
