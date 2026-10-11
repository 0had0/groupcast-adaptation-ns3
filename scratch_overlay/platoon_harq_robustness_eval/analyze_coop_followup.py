#!/usr/bin/env python3
"""
analyze_coop_followup.py -- analysis of the cooperative-HARQ follow-up (README
"Cooperative HARQ: follow-up"): step 2, the roundabout footprint test, and
step 3, the helper-position sweep. Step 1 (split / additive arms) is analysed
by run_coop.py --analyze-only (coop_verdict.csv, figc7).

  python3 analyze_coop_followup.py footprint \\
      --rbt results/coop_rbt_rlc0 --hwy results/coop_rlc0
  python3 analyze_coop_followup.py sweep \\
      --sweep results/coop_sweep_rlc0 --base results/coop_rlc0

footprint (pre-registered decision, written in the README before the results):
  SUPPORTED if, during the turn phase (leader and tail on different road
  segments), leader_only + helper_only >= 15 % of the collided failed leader
  attempts, OR the median sensing-map divergence of the leader's selections in
  the turn phase >= 15 %; REJECTED otherwise.
  - shares of collided failed attempts by who had sensed an interferer
    (neither / helper_only / leader_only / both), overall, by turn phase and by
    link condition leader -> interferer / helper -> interferer (--collCond runs)
  - P(sensed by the leader) vs leader-interferer distance, roundabout vs highway
  - divergence per selection = |A xor B| / |A or B| (Jaccard distance) of the
    (slot, subchannel) units the two UEs' sensing data exclude in the same
    window at the same RSRP threshold (<tag>_div.csv); CDF by turn phase
  Outputs <rbt>/analysis_footprint/: footprint_shares.csv, footprint_psensed.csv,
  footprint_divergence.csv, footprint_verdict.txt, figf1..figf3.

sweep (helpers 2, 4, 6, 7; member 8 = tail):
  - static oracle: best helper per traffic x arm on seeds 1-5 (tail prr_pdb),
    reported on seeds 6-10 next to helper 4 and leader-only (none, from --base)
  - dynamic oracle: tail prr_pdb in 1 s windows (by packet generation time) per
    helper and seed (same seed = same mobility trace); per-window maximum over
    helpers = upper bound of per-packet dynamic helper selection; how often the
    best helper changes during the turn
  - one implementable rule, fixed before looking at seeds 6-10: the rearmost of
    the swept helpers within X m (straight line, CAM positions) of the leader,
    X tuned on seeds 1-5 from {50, 100, 150, 200, 250, 300}; evaluated on 6-10
    by composing the static runs window by window (an approximation: switching
    the helper in a run would not change the other windows)
  Outputs <sweep>/analysis_sweep/: sweep_runs.csv, sweep_static.csv,
  sweep_dynamic.csv, sweep_rule.csv, figs1..figs2.
"""

import argparse
import gzip
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analyze as an  # noqa: E402  (palette, ci95)
import run_campaign as rc  # noqa: E402  (trace paths)

PDB_MS = {"periodic1": 100.0, "periodic2": 10.0, "aperiodic1": 50.0}
CATS = ["neither", "helper_only", "leader_only", "both"]
CAT_COL = {"neither": "#e34948", "helper_only": "#eda100", "leader_only": "#4a3aa7",
           "both": "#1baf7a"}
COND = {0: "NLOS", 1: "NLOSv", 2: "LOS"}
DIST_BINS = [0, 100, 300, 500, 750, 1000, 1500]
THRESH = 0.15  # pre-registered


def segment(x, y):
    """Road segment on the roundabout (centre (0, 0)), as in the program:
    0 ring (<= 35 m), 1 north, 2 east, 3 south, 4 west."""
    r = np.hypot(x, y)
    seg = np.where(np.abs(y) >= np.abs(x), np.where(y > 0, 1, 3), np.where(x > 0, 2, 4))
    return np.where(r <= 35.0, 0, seg)


def read_gz(path, **kw):
    try:
        return pd.read_csv(path, **kw)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return None


def runs_of(root):
    rows = []
    for f in sorted(Path(root).glob("*/*_run.csv")):
        r = pd.read_csv(f)
        r["arm"] = f.parent.name
        r["tag"] = f.name[: -len("_run.csv")]
        r["dir"] = str(f.parent)
        rows.append(r)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


# ---------------------------------------------------------------------------
# step 2: footprint
# ---------------------------------------------------------------------------
def coll_rows(runs, role=None):
    """collided failed attempts of the runs: one row per (attempt, member,
    interferer) as written by the program"""
    out = []
    for _, r in runs.iterrows():
        c = read_gz(Path(r.dir) / f"{r.tag}_coll.csv.gz")
        if c is None or c.empty:
            continue
        for k in ("arm", "scenario", "traffic", "nbg", "seed"):
            c[k] = r[k]
        out.append(c)
    if not out:
        return pd.DataFrame()
    c = pd.concat(out, ignore_index=True)
    return c if role is None else c[c.role == role]


def attempt_categories(c):
    """per (attempt, member): who had sensed an interferer (any of them), as the
    program's lcoll_* / hcoll_*; turn phase and conditions of the attempt"""
    keys = ["arm", "traffic", "nbg", "seed", "t", "role", "member"]
    g = c.groupby(keys)
    a = g.agg(sl=("sensed_by_leader", "max"), sh=("sensed_by_helper", "max"),
              turn=("turn", "max") if "turn" in c else ("t", "size")).reset_index()
    if "turn" not in c:
        a["turn"] = -1
    a["cat"] = np.select([(a.sl == 1) & (a.sh == 1), a.sl == 1, a.sh == 1],
                         ["both", "leader_only", "helper_only"], "neither")
    return a


def shares(a, by):
    t = a.groupby(by + ["cat"]).size().unstack("cat", fill_value=0)
    for k in CATS:
        if k not in t:
            t[k] = 0
    n = t[CATS].sum(axis=1)
    s = t[CATS].div(n, axis=0)
    s["n"] = n
    s["leader_or_helper_only"] = s.leader_only + s.helper_only
    return s.reset_index()


def footprint(a):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    R = runs_of(a.rbt)
    R = R[R.scenario == "roundabout"]
    if R.empty:
        sys.exit(f"no roundabout runs in {a.rbt}")
    out = Path(a.rbt) / "analysis_footprint"
    out.mkdir(parents=True, exist_ok=True)
    C = coll_rows(R)
    A = attempt_categories(C)
    AL = A[A.role == 0]  # leader attempts: the pre-registered quantity
    S_all = shares(AL.assign(phase="all"), ["phase"])
    S_turn = shares(AL.assign(phase=np.where(AL.turn == 1, "turn", "no turn")), ["phase"])
    S_cell = shares(AL.assign(phase=np.where(AL.turn == 1, "turn", "no turn")),
                    ["nbg", "traffic", "phase"])
    S_arm = shares(A.assign(phase=np.where(A.turn == 1, "turn", "no turn"),
                            who=np.where(A.role == 0, "leader", "helper")),
                   ["arm", "who", "phase"])
    pd.concat([S_all.assign(level="all"), S_turn.assign(level="turn phase"),
               S_cell.assign(level="cell x turn phase"), S_arm.assign(level="arm x role x turn")],
              ignore_index=True).to_csv(out / "footprint_shares.csv", index=False)

    # link condition (collCond runs): per (attempt, interferer)
    P = C.drop_duplicates(["arm", "traffic", "nbg", "seed", "t", "role", "interferer"])
    cond_rows = []
    if "cond_leader_interf" in P and (P.cond_leader_interf >= 0).any():
        for (cl, ch), g in P[P.role == 0].groupby(["cond_leader_interf", "cond_helper_interf"]):
            cond_rows.append({"cond_leader": COND.get(cl, cl), "cond_helper": COND.get(ch, ch),
                              "pairs": len(g), "p_sensed_leader": g.sensed_by_leader.mean(),
                              "p_sensed_helper": g.sensed_by_helper.mean()})
    pd.DataFrame(cond_rows).to_csv(out / "footprint_condition.csv", index=False)

    # P(sensed by the leader) vs leader-interferer distance: roundabout vs highway
    def psensed(P, scen):
        L = P[P.role == 0].copy()
        L["bin"] = pd.cut(L.d_tx_interferer_m, DIST_BINS, right=False)
        g = L.groupby("bin", observed=False).sensed_by_leader
        return pd.DataFrame({"scenario": scen, "bin": g.mean().index.astype(str),
                             "p_sensed": g.mean().values, "pairs": g.size().values})
    ps = [psensed(P, "roundabout")]
    if a.hwy:
        H = runs_of(a.hwy)
        H = H[(H.scenario == "highway") & (H.arm == "none")]
        if not H.empty:
            CH = coll_rows(H).drop_duplicates(["traffic", "nbg", "seed", "t", "role",
                                               "interferer"])
            ps.append(psensed(CH, "highway"))
    PS = pd.concat(ps, ignore_index=True)
    PS.to_csv(out / "footprint_psensed.csv", index=False)

    # sensing-map divergence per selection
    D = []
    for _, r in R.iterrows():
        d = read_gz(Path(r.dir) / f"{r.tag}_div.csv.gz")
        if d is None or d.empty:
            continue
        for k in ("arm", "traffic", "nbg", "seed"):
            d[k] = r[k]
        D.append(d)
    D = pd.concat(D, ignore_index=True) if D else pd.DataFrame()
    med_turn = np.nan
    if not D.empty:
        union = D.n_sel + D.n_other - D.n_both
        D["divergence"] = ((D.n_sel + D.n_other - 2 * D.n_both) / union).where(union > 0)
        D["div_window"] = (D.n_sel + D.n_other - 2 * D.n_both) / D.n_window.where(D.n_window > 0)
        D["turn"] = ((D.leader_seg >= 0) & (D.tail_seg >= 0) & (D.leader_seg != D.tail_seg))
        DS = (D.groupby(["selector", "turn"])
              .agg(selections=("divergence", "size"), with_union=("divergence", "count"),
                   median=("divergence", "median"), mean=("divergence", "mean"),
                   p90=("divergence", lambda x: x.quantile(0.9)),
                   median_window_share=("div_window", "median")).reset_index())
        DS.to_csv(out / "footprint_divergence.csv", index=False)
        lt = D[(D.selector == 0) & D.turn].divergence.dropna()
        med_turn = lt.median() if len(lt) else np.nan

    # pre-registered decision
    tshare = S_turn[S_turn.phase == "turn"]
    only_turn = tshare.leader_or_helper_only.iloc[0] if len(tshare) else np.nan
    n_turn = int(tshare.n.iloc[0]) if len(tshare) else 0
    supported = (only_turn >= THRESH) or (med_turn >= THRESH)
    if np.isnan(only_turn) and np.isnan(med_turn):
        decision = "INSUFFICIENT DATA (no turn-phase collisions nor selections)"
    else:
        decision = "SUPPORTED" if supported else "REJECTED"
    verdict = (f"Footprint hypothesis (pre-registered, threshold {THRESH:.0%}):\n"
               f"  turn phase, collided failed leader attempts: leader_only + helper_only = "
               f"{only_turn:.3f} (n = {n_turn})\n"
               f"  turn phase, leader selections: median divergence = {med_turn:.3f}\n"
               f"  -> {decision}\n")
    (out / "footprint_verdict.txt").write_text(verdict)
    print(verdict)

    # figures
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8), squeeze=False)
    ax = axes[0, 0]
    cs = S_cell.copy()
    cs["lab"] = (cs.traffic + "\nN=" + cs.nbg.astype(str) + "\n" + cs.phase)
    bottom = np.zeros(len(cs))
    for k in CATS:
        ax.bar(range(len(cs)), cs[k], 0.7, bottom=bottom, color=CAT_COL[k], label=k,
               edgecolor="white", linewidth=0.8)
        bottom += cs[k].values
    for q, n in enumerate(cs.n):
        ax.text(q, 1.01, str(int(n)), ha="center", fontsize=6, color=an.INK2)
    ax.set_xticks(range(len(cs)))
    ax.set_xticklabels(cs.lab, fontsize=6)
    ax.set_ylim(0, 1.08)
    ax.axhline(1 - THRESH, color=an.INK2, ls=":", lw=0.8)
    ax.set_ylabel("share of collided failed leader attempts")
    ax.set_title("who had sensed an interferer (roundabout)", fontsize=10, color=an.INK)
    ax.legend(fontsize=7, loc="lower right")
    ax = axes[0, 1]
    for scen, col in (("roundabout", "#eb6834"), ("highway", "#2a78d6")):
        d = PS[PS.scenario == scen]
        if len(d):
            ax.plot(range(len(d)), d.p_sensed, "o-", color=col, label=scen)
    ax.set_xticks(range(len(DIST_BINS) - 1))
    ax.set_xticklabels([f"{DIST_BINS[i]}-{DIST_BINS[i + 1]}" for i in range(len(DIST_BINS) - 1)],
                       fontsize=7)
    ax.set_xlabel("leader - interferer distance (m)")
    ax.set_ylabel("P(interferer sensed by the leader)")
    ax.set_ylim(0, 1)
    ax.grid(color=an.GRID, lw=0.5)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out / "figf1_sensing_shares_psensed.png")
    plt.close(fig)
    if not D.empty:
        fig, ax = plt.subplots(figsize=(6, 3.8))
        for (sel, turn), col, ls in (((0, True), "#e34948", "-"), ((0, False), "#2a78d6", "-"),
                                     ((1, True), "#e34948", "--"), ((1, False), "#2a78d6", "--")):
            x = np.sort(D[(D.selector == sel) & (D.turn == turn)].divergence.dropna())
            if len(x):
                ax.plot(x, np.arange(1, len(x) + 1) / len(x), ls=ls, color=col,
                        label=f"{'leader' if sel == 0 else 'helper'} selections, "
                              f"{'turn' if turn else 'no turn'} (n={len(x)})")
        ax.axvline(THRESH, color=an.INK2, ls=":", lw=0.8)
        ax.set_xlabel("sensing-map divergence |A xor B| / |A or B|")
        ax.set_ylabel("CDF")
        ax.grid(color=an.GRID, lw=0.5)
        ax.legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(out / "figf2_divergence_cdf.png")
        plt.close(fig)
    print(f"written to {out}")


# ---------------------------------------------------------------------------
# step 3: helper sweep
# ---------------------------------------------------------------------------
def tail_windows(pkt_gz, tail, pdb_ms, win_s=1.0):
    p = pd.read_csv(pkt_gz, usecols=["seq", "t_tx", "member", "rx", "delay_ms"])
    p = p[p.member == tail]
    p["ok"] = (p.rx == 1) & (p.delay_ms <= pdb_ms + 1e-6)
    p["w"] = np.floor(p.t_tx / win_s).astype(int)
    return p.groupby("w").agg(n=("ok", "size"), ok=("ok", "sum"))


_POS = {}


def positions(tdir, nbg, seed):
    """mobility trace of the roundabout cell (same seed = same trace for every helper)"""
    key = (nbg, seed)
    if key not in _POS:
        _POS[key] = pd.read_csv(rc.trace_path(Path(tdir), "roundabout", 30, nbg, seed),
                                comment="#")
    return _POS[key]


def sweep(a):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    root = Path(a.sweep)
    rows = []
    for hdir in sorted(root.glob("h*")):
        if not hdir.name[1:].isdigit():
            continue
        R = runs_of(hdir)
        if R.empty:
            continue
        R["helper_member"] = int(hdir.name[1:])
        rows.append(R)
    if not rows:
        sys.exit(f"no runs under {root}/h*")
    R = pd.concat(rows, ignore_index=True)
    R = R[(R.scenario == "roundabout")]
    tail = 8  # 9-vehicle platoon: member 8 is the tail
    out = root / "analysis_sweep"
    out.mkdir(parents=True, exist_ok=True)

    # tail prr_pdb per run, from the member CSV (exact)
    def tail_pdb(r):
        m = pd.read_csv(Path(r.dir) / f"{r.tag}_member.csv")
        m = m[m.member.astype(str) == str(tail)]
        return float(m.prr_pdb.iloc[0]) if len(m) else np.nan
    R["tail_prr_pdb"] = [tail_pdb(r) for _, r in R.iterrows()]
    base = runs_of(a.base) if a.base else pd.DataFrame()
    if not base.empty:
        base = base[(base.scenario == "roundabout") & (base.nbg.isin(set(R.nbg)))
                    & (base.arm == "none")].copy()
        base["tail_prr_pdb"] = [tail_pdb(r) for _, r in base.iterrows()]
    R.to_csv(out / "sweep_runs.csv", index=False)

    train, test = set(range(1, 6)), set(range(6, 11))
    st = []
    for (nbg, tr, arm), g in R.groupby(["nbg", "traffic", "arm"]):
        tr_mean = g[g.seed.isin(train)].groupby("helper_member").tail_prr_pdb.mean()
        best = int(tr_mean.idxmax()) if len(tr_mean) else None
        for h, lab in ((best, "oracle (picked on seeds 1-5)"), (4, "helper 4")):
            x = g[(g.helper_member == h) & g.seed.isin(test)].tail_prr_pdb
            st.append({"nbg": nbg, "traffic": tr, "arm": arm, "what": lab, "helper": h,
                       "seeds": len(x), "tail_prr_pdb": x.mean(), "ci": an.ci95(x)})
        if not base.empty:
            x = base[(base.nbg == nbg) & (base.traffic == tr) & base.seed.isin(test)].tail_prr_pdb
            st.append({"nbg": nbg, "traffic": tr, "arm": arm, "what": "leader only (none)",
                       "helper": 0, "seeds": len(x), "tail_prr_pdb": x.mean(),
                       "ci": an.ci95(x)})
    ST = pd.DataFrame(st)
    ST.to_csv(out / "sweep_static.csv", index=False)

    # dynamic oracle and the rule, window by window
    dyn, rule_rows, timeline = [], [], []
    xs = [50, 100, 150, 200, 250, 300]
    for (nbg, tr, arm), g in R.groupby(["nbg", "traffic", "arm"]):
        helpers = sorted(g.helper_member.unique())
        per_seed = {}
        for seed, gs in g.groupby("seed"):
            W = {}
            for _, r in gs.iterrows():
                f = Path(r.dir) / f"{r.tag}_pkt.csv.gz"
                if f.exists():
                    W[int(r.helper_member)] = tail_windows(f, tail, PDB_MS[tr])
            if len(W) < 2:
                continue
            J = pd.concat(W, axis=1, names=["helper", "k"])
            n = J.xs("n", axis=1, level="k").max(axis=1)
            ok = J.xs("ok", axis=1, level="k").fillna(0)
            frac = ok.div(n, axis=0)
            per_seed[seed] = (n, ok, frac)
        tl_seed = min(test & set(per_seed)) if test & set(per_seed) else None
        for seed, (n, ok, frac) in per_seed.items():
            tr_pos = positions(a.traces, nbg, seed)
            tw = (frac.index.values + 0.5)
            # segments of leader / tail and distances leader -> members at window centres
            snap = tr_pos[tr_pos.time.isin(np.round(tw, 1))]
            pos = {k: v.set_index("time") for k, v in snap.groupby("node_id")}
            turn, dist = [], {h: [] for h in helpers}
            for t in np.round(tw, 1):
                try:
                    L, T = pos[0].loc[t], pos[tail].loc[t]
                    turn.append(int(segment(L.x, L.y)) != int(segment(T.x, T.y)))
                    for h in helpers:
                        M = pos[h].loc[t]
                        dist[h].append(float(np.hypot(M.x - L.x, M.y - L.y)))
                except KeyError:
                    turn.append(False)
                    for h in helpers:
                        dist[h].append(np.nan)
            turn = np.array(turn)
            best = frac.max(axis=1)
            argbest = frac.apply(lambda r: set(r.index[r == r.max()]), axis=1)
            changes = sum(1 for i in range(1, len(argbest))
                          if turn[i] and not (argbest.iloc[i] & argbest.iloc[i - 1]))
            tot = n.sum()
            dyn.append({"nbg": nbg, "traffic": tr, "arm": arm, "seed": seed,
                        "dynamic_oracle": (best * n).sum() / tot,
                        **{f"h{h}": ok[h].sum() / tot for h in helpers if h in ok},
                        "turn_windows": int(turn.sum()),
                        "best_changes_in_turn": changes})
            for X in xs:
                pick = []
                for i in range(len(frac)):
                    cand = [h for h in helpers if dist[h][i] <= X]
                    pick.append(max(cand) if cand else min(helpers))
                val = sum(ok[p].iloc[i] for i, p in enumerate(pick)) / tot
                rule_rows.append({"nbg": nbg, "traffic": tr, "arm": arm, "seed": seed, "X": X,
                                  "rule": val})
            if seed == tl_seed:
                for i, w in enumerate(frac.index):
                    timeline.append({"nbg": nbg, "traffic": tr, "arm": arm, "seed": seed,
                                     "t": w + 0.5, "turn": bool(turn[i]),
                                     **{f"h{h}": frac[h].iloc[i] for h in helpers if h in frac}})
    DY = pd.DataFrame(dyn)
    DY.to_csv(out / "sweep_dynamic.csv", index=False)
    RU = pd.DataFrame(rule_rows)
    summ = []
    if not RU.empty:
        for (nbg, tr, arm), g in RU.groupby(["nbg", "traffic", "arm"]):
            trn = g[g.seed.isin(train)].groupby("X").rule.mean()
            X = int(trn.idxmax())
            x = g[(g.X == X) & g.seed.isin(test)].rule
            summ.append({"nbg": nbg, "traffic": tr, "arm": arm, "X_m": X,
                         "rule_tail_prr_pdb_test": x.mean(), "ci": an.ci95(x)})
    pd.DataFrame(summ).to_csv(out / "sweep_rule.csv", index=False)
    TL = pd.DataFrame(timeline)
    TL.to_csv(out / "sweep_timeline.csv", index=False)

    # gaps on the test seeds: dynamic oracle, static oracle, helper 4
    gap = []
    for (nbg, tr, arm), g in DY[DY.seed.isin(test)].groupby(["nbg", "traffic", "arm"]):
        s = ST[(ST.nbg == nbg) & (ST.traffic == tr) & (ST.arm == arm)]
        gap.append({"nbg": nbg, "traffic": tr, "arm": arm,
                    "dynamic_oracle": g.dynamic_oracle.mean(),
                    "static_oracle": s[s.what.str.startswith("oracle")].tail_prr_pdb.mean(),
                    "helper4": s[s.what == "helper 4"].tail_prr_pdb.mean(),
                    "none": s[s.what.str.startswith("leader")].tail_prr_pdb.mean(),
                    "best_changes_in_turn_per_seed": g.best_changes_in_turn.mean(),
                    "turn_windows_per_seed": g.turn_windows.mean()})
    GP = pd.DataFrame(gap)
    GP.to_csv(out / "sweep_gaps.csv", index=False)
    with pd.option_context("display.width", 200, "display.float_format", "{:.3f}".format):
        print("static oracle (seeds 6-10):\n", ST.to_string(index=False))
        print("\ngaps (seeds 6-10):\n", GP.to_string(index=False))
        print("\nrule (X tuned on seeds 1-5, evaluated on 6-10):\n",
              pd.DataFrame(summ).to_string(index=False))

    # figures: tail prr_pdb vs helper index (all seeds), dynamic-oracle timeline
    trs = sorted(R.traffic.unique())
    fig, axes = plt.subplots(1, len(trs), figsize=(5.2 * len(trs), 3.6), squeeze=False)
    cols = {"leaderRes": "#eb6834", "ownRes": "#1baf7a", "split": "#4a3aa7",
            "additive": "#eda100"}
    for j, tr in enumerate(trs):
        ax = axes[0, j]
        for arm, g in R[R.traffic == tr].groupby("arm"):
            m = g.groupby("helper_member").tail_prr_pdb
            ax.errorbar(m.mean().index, m.mean().values, yerr=m.apply(an.ci95).values,
                        fmt="o-", capsize=2, color=cols.get(arm, "#52514e"), label=arm)
        if not base.empty:
            b = base[base.traffic == tr].tail_prr_pdb
            ax.axhline(b.mean(), color="#2a78d6", ls="--", lw=1, label="leader only")
        ax.set_xlabel("helper (member index; 8 = tail)")
        ax.set_ylabel("tail PRR within PDB")
        ax.set_title(f"roundabout, {tr}", fontsize=10, color=an.INK)
        ax.grid(color=an.GRID, lw=0.5)
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(out / "figs1_tail_vs_helper.png")
    plt.close(fig)
    if not TL.empty:
        keys = TL[["traffic", "arm"]].drop_duplicates().values.tolist()
        fig, axes = plt.subplots(len(keys), 1, figsize=(9, 2.2 * len(keys)), squeeze=False,
                                 sharex=True)
        for i, (tr, arm) in enumerate(keys):
            ax = axes[i, 0]
            d = TL[(TL.traffic == tr) & (TL.arm == arm)]
            for c in [c for c in d.columns if c.startswith("h")]:
                ax.plot(d.t, d[c], lw=1, label=c)
            ax.plot(d.t, d[[c for c in d.columns if c.startswith("h")]].max(axis=1), "k-",
                    lw=2, alpha=0.4, label="dynamic oracle")
            for t, tu in zip(d.t, d.turn):
                if tu:
                    ax.axvspan(t - 0.5, t + 0.5, color="#eda100", alpha=0.12, lw=0)
            ax.set_ylabel(f"{tr}\n{arm}", fontsize=8)
            ax.set_ylim(-0.02, 1.02)
            if i == 0:
                ax.legend(fontsize=6, ncol=6, loc="lower left")
        axes[-1, 0].set_xlabel("time (s); shaded: leader and tail on different segments")
        fig.suptitle(f"Tail PRR within PDB per 1 s window and helper (seed {int(TL.seed.iloc[0])})",
                     fontsize=10, x=0.01, ha="left")
        fig.tight_layout()
        fig.savefig(out / "figs2_oracle_timeline.png")
        plt.close(fig)
    print(f"written to {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("footprint")
    f.add_argument("--rbt", default=str(HERE / "results/coop_rbt_rlc0"))
    f.add_argument("--hwy", default=str(HERE / "results/coop_rlc0"))
    s = sub.add_parser("sweep")
    s.add_argument("--sweep", default=str(HERE / "results/coop_sweep_rlc0"))
    s.add_argument("--base", default=str(HERE / "results/coop_rlc0"))
    s.add_argument("--traces", default=str(HERE / "traces"))
    a = ap.parse_args()
    footprint(a) if a.cmd == "footprint" else sweep(a)


if __name__ == "__main__":
    main()
