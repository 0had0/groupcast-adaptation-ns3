"""Can an SL-RSSI threshold tell a transmitting helper from a silent one at the leader's fallback check?

Input: <out>_fbchk.csv of R6 runs (one row per fallback check: the received power on the handed slot's PSSCH
subchannels, split by transmitter, which only the simulator knows). Classes of the checks the protocol acted on:
  A  helper transmitted, its SCI decoded       -> no fallback (correct)
  B  helper transmitted, its SCI not decoded   -> fallback = double transmission with the helper
  C  helper silent                             -> fallback (correct: the recovery the fallback exists for)
A UE measures RSSI = helper + others + noise per RB (no identity). Rule tested: fall back only if no SCI was
decoded AND RSSI < thr. Per threshold: share of B suppressed (gain) and share of C suppressed (lost recoveries).

  python3 rssi_fallback_analysis.py out/rssi_v3 [--remote out/remote_v3/R6]
"""
import glob
import os
import sys

import numpy as np
import pandas as pd

NOISE_DBM_PER_RB = -174 + 10 * np.log10(12 * 30e3) + 5.0   # 30 kHz SCS, UE noise figure 5 dB (NrUePhy default)


def w(dbm):
    return np.where(dbm <= -998, 0.0, 10 ** ((dbm - 30) / 10))


def load(folder):
    rows = []
    for f in sorted(glob.glob(f'{folder}/*_fbchk.csv')):
        cell = os.path.basename(f)[:-len('_fbchk.csv')]
        r = pd.read_csv(f.replace('_fbchk.csv', '_run.csv')).iloc[0]
        d = pd.read_csv(f)
        d = d[(d.t >= r.kpi_start) & (d.t < r.kpi_end) & (d.outcome.isin([0, 1, 2]))].copy()
        d['cell'] = cell
        d['n_rb'] = 10 * d.sb_len   # n_rb in the log is set only when a signal arrived (10-PRB subchannels)
        rows.append(d)
    d = pd.concat(rows, ignore_index=True)
    helper_tx = d.p_data_helper_dbm > -998
    d['cls'] = np.where(helper_tx & (d.outcome == 0), 'A', np.where(helper_tx, 'B', 'C'))
    tot = w(d.p_data_helper_dbm) + w(d.p_data_other_dbm)
    d['rssi_rb_dbm'] = 10 * np.log10(tot / d.n_rb + 10 ** ((NOISE_DBM_PER_RB - 30) / 10)) + 30
    d['helper_rb_dbm'] = np.where(helper_tx, 10 * np.log10(w(d.p_data_helper_dbm) / d.n_rb) + 30, np.nan)
    d['other_rb_dbm'] = 10 * np.log10(w(d.p_data_other_dbm) / d.n_rb + 1e-30) + 30
    return d


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else 'out/rssi_v3'
    d = load(folder)
    print(f'noise per RB {NOISE_DBM_PER_RB:.1f} dBm')
    print('\nchecks per class (A: SCI heard, B: helper sent but SCI missed, C: helper silent)')
    print(d.groupby(['cell', 'cls']).size().unstack(fill_value=0).to_string())
    a, b, c = (d[d.cls == k] for k in 'ABC')
    print('\nB: helper sent, SCI not decoded (ground-truth diagnostics)')
    print(f'  PSCCH of the helper arrived but failed: {b.sci_failed.mean():.2%}')
    print(f'  helper power per RB, median {b.helper_rb_dbm.median():.1f} dBm; '
          f'others present {(b.p_data_other_dbm > -998).mean():.2%}')
    q = [5, 25, 50, 75, 95]
    print('\nRSSI per RB on the handed subchannels (dBm), percentiles', q)
    for k, g in (('A', a), ('B', b), ('C', c)):
        print(f'  {k} (n={len(g)}):', np.round(np.percentile(g.rssi_rb_dbm, q), 1) if len(g) else '-')
    print(f'  C with no other transmitter (pure noise): {(c.p_data_other_dbm <= -998).mean():.2%}')
    print('\nRule: fall back iff no SCI and RSSI < thr')
    print('  thr dBm | B (doubles) suppressed | C (true silences) suppressed')
    for thr in (-110, -105, -100, -95, -90, -85, -80):
        print(f'  {thr:6d} | {np.mean(b.rssi_rb_dbm >= thr):8.1%} ({int((b.rssi_rb_dbm >= thr).sum())}/{len(b)})'
              f' | {np.mean(c.rssi_rb_dbm >= thr):8.1%} ({int((c.rssi_rb_dbm >= thr).sum())}/{len(c)})')
    d.to_csv(f'{folder}/fbchk_all.csv', index=False)


if __name__ == '__main__':
    main()
