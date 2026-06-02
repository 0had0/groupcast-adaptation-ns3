#!/usr/bin/env python3
"""analyse_nlosv_model_eval.py

Quantifies how far the probabilistic (density-based) NLOSv model diverges from
the geometric (position-exact) model, treating the geometric verdict as ground
truth.

Reads results_nlosv_model_eval/all_runs.csv (the concatenated campaign output)
and reports:
  * confusion matrix  geometric (truth) vs probabilistic-mode (prediction)
  * NLOSv detection metrics: recall, false-alarm rate, miss rate, precision, F1
  * expected per-link misclassification rate (hard and soft)
  * Brier score and calibration (ECE) of the probabilistic P(NLOSv)
  * mean pathloss-dB error introduced by the probabilistic model
  * breakdowns by distance bin, density tier and scenario
It prints a conclusion and writes analysis_summary.json next to the input.
"""

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

CLASSES = ["LOS", "NLOSv", "NLOS"]
DIST_BINS = [0, 50, 100, 150, 200, 300, 400, 1e9]
DIST_LABELS = ["0-50", "50-100", "100-150", "150-200", "200-300", "300-400", "400+"]


def load(path):
    df = pd.read_csv(path)
    df["dist_bin"] = pd.cut(df["dist2d"], bins=DIST_BINS, labels=DIST_LABELS,
                            right=False)
    return df


def confusion(df):
    """Soft confusion matrix: geometric truth (rows) vs probabilistic draw
    outcome (cols), weighted by the empirical draw probabilities."""
    emp = {"LOS": "p_los_emp", "NLOSv": "p_nlosv_emp", "NLOS": "p_nlos_emp"}
    mat = pd.DataFrame(0.0, index=CLASSES, columns=CLASSES)
    for truth in CLASSES:
        sub = df[df["geo_class"] == truth]
        if len(sub) == 0:
            continue
        for pred in CLASSES:
            mat.loc[truth, pred] = sub[emp[pred]].sum()
    return mat


def confusion_hard(df):
    """Hard confusion matrix: geometric truth vs probabilistic argmax class."""
    mat = pd.DataFrame(0, index=CLASSES, columns=CLASSES)
    for truth in CLASSES:
        sub = df[df["geo_class"] == truth]
        for pred in CLASSES:
            mat.loc[truth, pred] = int((sub["prob_mode_class"] == pred).sum())
    return mat


def detection_metrics(df):
    """Binary NLOSv detection. Truth = geo_nlosv. Hard prediction = the
    probabilistic argmax class == NLOSv."""
    truth = df["geo_nlosv"].astype(bool)
    pred = (df["prob_mode_class"] == "NLOSv")
    tp = int((truth & pred).sum())
    fp = int((~truth & pred).sum())
    fn = int((truth & ~pred).sum())
    tn = int((~truth & ~pred).sum())
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    false_alarm = fp / (fp + tn) if (fp + tn) else float("nan")
    miss = fn / (tp + fn) if (tp + fn) else float("nan")
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    f1 = (2 * precision * recall / (precision + recall)
          if precision and recall and not np.isnan(precision) and not np.isnan(recall)
          else float("nan"))
    # soft expected detection rate: average P(NLOSv) the model assigns where
    # the geometric truth is NLOSv.
    soft_recall = df.loc[truth, "p_nlosv_emp"].mean() if truth.any() else float("nan")
    soft_fa = df.loc[~truth, "p_nlosv_emp"].mean() if (~truth).any() else float("nan")
    return dict(tp=tp, fp=fp, fn=fn, tn=tn, recall=recall, false_alarm=false_alarm,
                miss=miss, precision=precision, f1=f1,
                soft_recall=soft_recall, soft_false_alarm=soft_fa)


def misclassification(df):
    """Hard: fraction of links where the argmax class != geometric class.
    Soft: expected fraction of draws that disagree with the geometric class."""
    hard = float((df["prob_mode_class"] != df["geo_class"]).mean())
    emp = {"LOS": "p_los_emp", "NLOSv": "p_nlosv_emp", "NLOS": "p_nlos_emp"}
    p_match = df.apply(lambda r: r[emp[r["geo_class"]]], axis=1)
    soft = float((1.0 - p_match).mean())
    return hard, soft


def brier(df):
    """Brier score of P(NLOSv) against the geometric NLOSv indicator, plus the
    multiclass Brier against the geometric one-hot vector."""
    binary = float(((df["p_nlosv_emp"] - df["geo_nlosv"]) ** 2).mean())
    onehot = pd.DataFrame({c: (df["geo_class"] == c).astype(float) for c in CLASSES})
    emp = df[["p_los_emp", "p_nlosv_emp", "p_nlos_emp"]].to_numpy()
    multi = float(((emp - onehot[CLASSES].to_numpy()) ** 2).sum(axis=1).mean())
    return binary, multi


def calibration(df, n_bins=10):
    """Reliability table of predicted P(NLOSv) vs observed geometric NLOSv
    frequency, plus the Expected Calibration Error."""
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    rows = []
    ece = 0.0
    n = len(df)
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        if i == n_bins - 1:
            m = (df["p_nlosv_emp"] >= lo) & (df["p_nlosv_emp"] <= hi)
        else:
            m = (df["p_nlosv_emp"] >= lo) & (df["p_nlosv_emp"] < hi)
        sub = df[m]
        if len(sub) == 0:
            rows.append(dict(bin=f"{lo:.1f}-{hi:.1f}", count=0,
                             mean_pred=float("nan"), obs_freq=float("nan")))
            continue
        mean_pred = float(sub["p_nlosv_emp"].mean())
        obs_freq = float(sub["geo_nlosv"].mean())
        ece += (len(sub) / n) * abs(mean_pred - obs_freq)
        rows.append(dict(bin=f"{lo:.1f}-{hi:.1f}", count=int(len(sub)),
                         mean_pred=mean_pred, obs_freq=obs_freq))
    return rows, float(ece)


def pathloss_error(df):
    err = df["loss_err_db"]
    return dict(mae=float(err.abs().mean()),
                rmse=float(np.sqrt((err ** 2).mean())),
                bias=float(err.mean()),
                p95_abs=float(err.abs().quantile(0.95)))


def fmt(x, nd=3):
    return "nan" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}"


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=os.path.join(
        here, "results_nlosv_model_eval", "all_runs.csv"))
    ap.add_argument("--output", default=None,
                    help="analysis_summary.json path (default: next to input)")
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        sys.exit(f"input not found: {args.input}\nrun run_nlosv_model_eval.sh first.")
    df = load(args.input)
    out_json = args.output or os.path.join(os.path.dirname(args.input),
                                           "analysis_summary.json")

    print("=" * 64)
    print("  NLOSv determination — probabilistic vs geometric")
    print(f"  rows: {len(df)}   scenarios: {sorted(df['base_scenario'].unique())}")
    print(f"  density tiers: {sorted(df['density'].unique())}")
    print("=" * 64)

    summary = {"n_rows": int(len(df))}

    # ---- overall confusion ----
    cm = confusion(df)
    cm_norm = cm.div(cm.sum(axis=1).replace(0, np.nan), axis=0)
    print("\nConfusion matrix (geometric truth = rows, probabilistic draw = cols)")
    print("  row-normalised:")
    print(cm_norm.to_string(float_format=lambda v: f"{v:6.3f}"))
    summary["confusion_soft_rownorm"] = cm_norm.fillna(0).round(4).to_dict()
    summary["confusion_hard_counts"] = confusion_hard(df).to_dict()

    # ---- detection ----
    det = detection_metrics(df)
    print("\nNLOSv detection (geometric truth vs probabilistic argmax)")
    print(f"  recall / detection rate : {fmt(det['recall'])}")
    print(f"  false-alarm rate        : {fmt(det['false_alarm'])}")
    print(f"  miss rate               : {fmt(det['miss'])}")
    print(f"  precision               : {fmt(det['precision'])}")
    print(f"  F1                      : {fmt(det['f1'])}")
    print(f"  soft recall  E[P(NLOSv)|truth NLOSv] : {fmt(det['soft_recall'])}")
    print(f"  soft false-alarm E[P(NLOSv)|truth ~] : {fmt(det['soft_false_alarm'])}")
    summary["detection"] = {k: (None if isinstance(v, float) and np.isnan(v) else v)
                            for k, v in det.items()}

    # ---- misclassification ----
    hard_mis, soft_mis = misclassification(df)
    print(f"\nExpected per-link misclassification rate")
    print(f"  hard (argmax != geometric)  : {fmt(hard_mis)}")
    print(f"  soft (E[draw != geometric]) : {fmt(soft_mis)}")
    summary["misclassification_hard"] = hard_mis
    summary["misclassification_soft"] = soft_mis

    # ---- brier + calibration ----
    b_bin, b_multi = brier(df)
    cal_rows, ece = calibration(df)
    print(f"\nBrier score  P(NLOSv) vs geometric indicator : {fmt(b_bin)}")
    print(f"Brier score  multiclass vs geometric one-hot : {fmt(b_multi)}")
    print(f"Expected Calibration Error (ECE)             : {fmt(ece)}")
    print("\nCalibration (predicted P(NLOSv) bin -> observed geometric NLOSv freq)")
    print(f"  {'bin':>10} {'count':>8} {'mean_pred':>10} {'obs_freq':>10}")
    for r in cal_rows:
        print(f"  {r['bin']:>10} {r['count']:>8} "
              f"{fmt(r['mean_pred']):>10} {fmt(r['obs_freq']):>10}")
    summary["brier_binary"] = b_bin
    summary["brier_multiclass"] = b_multi
    summary["ece"] = ece
    summary["calibration"] = cal_rows

    # ---- pathloss error ----
    ple = pathloss_error(df)
    print(f"\nPathloss error introduced by the probabilistic model (dB)")
    print(f"  mean |error| : {fmt(ple['mae'], 2)}")
    print(f"  RMSE         : {fmt(ple['rmse'], 2)}")
    print(f"  signed bias  : {fmt(ple['bias'], 2)}")
    print(f"  p95 |error|  : {fmt(ple['p95_abs'], 2)}")
    summary["pathloss_error_db"] = ple

    # ---- breakdowns ----
    print("\nBy distance bin:")
    print(f"  {'bin':>10} {'n':>7} {'geoNLOSv':>9} {'det.rate':>9} "
          f"{'falseAl.':>9} {'|dPL|dB':>9}")
    dist_break = {}
    for lbl in DIST_LABELS:
        sub = df[df["dist_bin"] == lbl]
        if len(sub) == 0:
            continue
        d = detection_metrics(sub)
        row = dict(n=int(len(sub)),
                   geo_nlosv_frac=float(sub["geo_nlosv"].mean()),
                   recall=d["recall"], false_alarm=d["false_alarm"],
                   mae_db=float(sub["loss_err_db"].abs().mean()))
        dist_break[lbl] = row
        print(f"  {lbl:>10} {row['n']:>7} {fmt(row['geo_nlosv_frac']):>9} "
              f"{fmt(row['recall']):>9} {fmt(row['false_alarm']):>9} "
              f"{fmt(row['mae_db'], 2):>9}")
    summary["by_distance"] = dist_break

    print("\nBy density tier / scenario:")
    print(f"  {'scenario':>13} {'density':>8} {'n':>7} {'geoNLOSv':>9} "
          f"{'det.rate':>9} {'falseAl.':>9} {'Brier':>8} {'|dPL|dB':>9}")
    grp_break = {}
    for (scen, dens), sub in df.groupby(["base_scenario", "density"]):
        d = detection_metrics(sub)
        bb, _ = brier(sub)
        row = dict(n=int(len(sub)),
                   geo_nlosv_frac=float(sub["geo_nlosv"].mean()),
                   recall=d["recall"], false_alarm=d["false_alarm"],
                   brier=bb, mae_db=float(sub["loss_err_db"].abs().mean()))
        grp_break[f"{scen}/{dens}"] = row
        print(f"  {scen:>13} {dens:>8} {row['n']:>7} "
              f"{fmt(row['geo_nlosv_frac']):>9} {fmt(row['recall']):>9} "
              f"{fmt(row['false_alarm']):>9} {fmt(row['brier']):>8} "
              f"{fmt(row['mae_db'], 2):>9}")
    summary["by_group"] = grp_break

    # ---- Monte-Carlo sanity: empirical vs closed-form ----
    mc_err = float((df["p_nlosv_emp"] - df["p_nlosv_cf"]).abs().mean())
    print(f"\nMonte-Carlo sanity: mean |p_nlosv_emp - p_nlosv_cf| = {fmt(mc_err, 4)}")
    summary["mc_emp_vs_cf_mae"] = mc_err

    # ---- conclusion ----
    geo_nlosv_frac = float(df["geo_nlosv"].mean())
    print("\n" + "=" * 64)
    print("  CONCLUSION")
    print("=" * 64)
    lines = []
    lines.append(
        f"Across {len(df)} TX-RX link-samples, the geometric (position-exact) "
        f"model finds {geo_nlosv_frac*100:.1f}% to be NLOSv. The density-based "
        f"probabilistic model, choosing its most-likely class, recovers only "
        f"{det['recall']*100:.1f}% of those (miss rate {det['miss']*100:.1f}%) "
        f"and raises false NLOSv alarms on {det['false_alarm']*100:.1f}% of the "
        f"genuinely clear links.")
    lines.append(
        f"It misclassifies {hard_mis*100:.1f}% of links outright (hard) and a "
        f"draw disagrees with the geometric truth {soft_mis*100:.1f}% of the "
        f"time (soft). Its P(NLOSv) is poorly calibrated against the actual "
        f"geometry: Brier score {b_bin:.3f}, ECE {ece:.3f}.")
    lines.append(
        f"This costs {ple['mae']:.1f} dB mean absolute pathloss error "
        f"(bias {ple['bias']:+.1f} dB, p95 {ple['p95_abs']:.1f} dB) versus the "
        f"position-exact model.")
    lines.append(
        "Because NLOSv blockage is governed by where the platoon vehicles "
        "actually are -- not by a global density tier -- no single density "
        "setting reproduces the per-link geometric verdict. For V2X groupcast "
        "adaptation, which depends on accurate per-link CSI, position-exact "
        "geometric NLOSv determination is the correct choice.")
    for ln in lines:
        # simple wrap
        words, cur = ln.split(), ""
        for w in words:
            if len(cur) + len(w) + 1 > 76:
                print("  " + cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        if cur:
            print("  " + cur)
        print()
    summary["conclusion"] = lines
    summary["geo_nlosv_fraction"] = geo_nlosv_frac

    with open(out_json, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"summary written to {out_json}")


if __name__ == "__main__":
    main()
