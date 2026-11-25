"""Plot layer of notebooks/SCHEMES_COMPARISON.ipynb: one style per retransmission scheme, scenario x traffic grids,
resource-class bars and the R6 gain heatmaps."""
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np
import pandas as pd

STYLE = {   # arm: label, colour, marker, line style, line width
    'R3':         dict(label='R3 (Opt. 2 HARQ)',  color='#4d4d4d', marker='s', ls='--',            lw=1.6),
    'R4_bsc':     dict(label='R4+C',              color='#1f78b4', marker='o', ls=':',             lw=1.8),
    'R4_bsc_fbs': dict(label='R4+C+fb',           color='#ff7f00', marker='^', ls='-.',            lw=1.8),
    'R6':         dict(label='R6 (coop. HARQ)',   color='#e31a1c', marker='D', ls='-',             lw=2.4),
}
CLASS_STYLE = {   # resource class: label, colour, hatch
    'L1':   dict(label='leader 1st tx',            color='#a6cee3', hatch=''),
    'Lrep': dict(label='leader HARQ retx',         color='#4d4d4d', hatch=''),
    'H':    dict(label='helper relay',             color='#33a02c', hatch=''),
    'T1':   dict(label='leader takeover (NACK)',   color='#fb9a99', hatch='//'),
    'T2':   dict(label='leader fallback (silent)', color='#fdbf6f', hatch='\\\\'),
    'L':    dict(label='leader (all, untraced)',   color='#1f78b4', hatch='..'),
}
SC_LABEL = {'hwy': 'Highway', 'rbt': 'Roundabout W', 'rbtE': 'Roundabout E', 'Highway': 'Highway',
            'Roundabouts': 'Roundabouts'}
TR_LABEL = {'periodic1tb': 'periodic', 'aperiodic1tb': 'aperiodic'}
METRIC_LABEL = {'prr_group': 'Group PRR', 'prr_worst': 'Worst-receiver PRR', 'prr_p5': 'P5 worst-receiver PRR',
                'pir_mean': 'Group PIR (ms)', 'pir_worst': 'Worst-receiver PIR (ms)', 'pir_p95': 'P95 worst PIR (ms)',
                'ss_kB': 'subch.-slots / kB'}
SC_ORDER = ['hwy', 'rbt', 'rbtE']
TR_ORDER = ['periodic1tb', 'aperiodic1tb']

plt.rcParams.update({'figure.dpi': 110, 'axes.grid': True, 'grid.alpha': 0.3, 'axes.spines.top': False,
                     'axes.spines.right': False, 'legend.frameon': False, 'font.size': 9})


def cells(df, sc_col='sc'):
    have = set(map(tuple, df[[sc_col, 'tr']].drop_duplicates().values))
    scs = [s for s in SC_ORDER + ['Highway', 'Roundabouts'] if any(h[0] == s for h in have)]
    return [(s, t) for t in TR_ORDER for s in scs if (s, t) in have]


def _grid(nrows, cl, w=3.0, h=2.3, sharey='row'):
    fig, ax = plt.subplots(nrows, len(cl), figsize=(w * len(cl), h * nrows), squeeze=False, sharey=sharey)
    for j, (s, t) in enumerate(cl):
        ax[0, j].set_title(f'{SC_LABEL[s]} · {TR_LABEL[t]}')
    return fig, ax


def _legend(fig, ax, ncol=6):
    hs, ls = [], []
    for a in ax.flat:
        for h, l in zip(*a.get_legend_handles_labels()):
            if l not in ls:
                hs.append(h); ls.append(l)
    fig.legend(hs, ls, loc='lower center', ncol=ncol, bbox_to_anchor=(0.5, -0.02))
    fig.tight_layout(rect=(0, 0.05, 1, 1))


def _xl(xlabel, s):
    """x label: a string, or a dict scenario -> label (units that differ by scenario)."""
    return xlabel.get(s, '') if isinstance(xlabel, dict) else xlabel


def _xpos(vals, xcat):
    vals = sorted(set(vals))
    return (lambda v: vals.index(v)) if xcat else (lambda v: v), vals


def plot_x(summ, x, ys, arms, xlabel, title, sc_col='sc', xcat=False, xticklabels=None, ref=None, pct=True,
           ref_label='error-free'):
    """Metric(s) `ys` (rows) vs `x` per scenario x traffic (columns), one line per arm.
    ref: summary without x, drawn as horizontal lines (arms not simulated on this axis)."""
    d = summ[summ.arm.isin(arms)]
    cl = cells(d, sc_col)
    fig, ax = _grid(len(ys), cl)
    pos, xv = _xpos(d[x].values, xcat)
    for i, y in enumerate(ys):
        for j, (s, t) in enumerate(cl):
            a = ax[i, j]
            for arm in arms:
                g = d[(d.arm == arm) & (d[sc_col] == s) & (d.tr == t)].sort_values(x)
                st = STYLE[arm]
                if len(g):
                    f = 100 if pct and y.startswith('prr') else 1
                    ci = g[y + '_ci'] * f if y + '_ci' in g else None
                    a.errorbar([pos(v) for v in g[x]], g[y] * f, yerr=ci, color=st['color'], marker=st['marker'],
                               ls=st['ls'], lw=st['lw'], ms=4, capsize=2, label=st['label'])
                if ref is not None:
                    r = ref[(ref.arm == arm) & (ref[sc_col] == s) & (ref.tr == t)]
                    if len(r) and not len(g):
                        f = 100 if pct and y.startswith('prr') else 1
                        a.axhline(r[y].iloc[0] * f, color=st['color'], ls=st['ls'], lw=1, alpha=0.7,
                                  label=f"{st['label']} ({ref_label})")
            if xcat:
                a.set_xticks(range(len(xv)))
                a.set_xticklabels(xticklabels or xv)
            if j == 0:
                a.set_ylabel(METRIC_LABEL.get(y, y) + (' (%)' if pct and y.startswith('prr') else ''))
            if i == len(ys) - 1:
                a.set_xlabel(_xl(xlabel, s))
    fig.suptitle(title, y=1.0)
    _legend(fig, ax)
    return fig


def plot_series(ser, xcol, ys, arms, xlabel, title, sc_col='sc'):
    """Time or distance series (from hhs_data.time_series / dist_series): rows = metrics."""
    d = ser[ser.arm.isin(arms)]
    cl = cells(d, sc_col)
    fig, ax = _grid(len(ys), cl)
    for i, y in enumerate(ys):
        for j, (s, t) in enumerate(cl):
            a = ax[i, j]
            for arm in arms:
                g = d[(d.arm == arm) & (d[sc_col] == s) & (d.tr == t)].sort_values(xcol)
                st = STYLE[arm]
                a.plot(g[xcol], 100 * g[y], color=st['color'], ls=st['ls'], lw=st['lw'] * 0.8,
                       marker=st['marker'] if xcol == 'dbin' else None, ms=3, label=st['label'])
            if j == 0:
                a.set_ylabel(METRIC_LABEL[y] + ' (%)')
            if i == len(ys) - 1:
                a.set_xlabel(xlabel)
    fig.suptitle(title, y=1.0)
    _legend(fig, ax)
    return fig


def plot_resources(summ, x, arms, xlabel, title, sc_col='sc', xcat=False, xticklabels=None, classes=None):
    """Row 1: subchannel-slots transmitted per run (KPI window; PSCCH + PSSCH, no PSFCH) vs x.
    Row 2: subchannel-slots per kB generated, split by resource class, stacked bars (arms side by side at every x)."""
    d = summ[summ.arm.isin(arms)]
    classes = classes or ['L1', 'Lrep', 'H', 'T1', 'T2']
    classes = [c for c in classes if f'ss_{c}_kB' in d and d[f'ss_{c}_kB'].fillna(0).abs().sum() > 0]
    cl = cells(d, sc_col)
    fig, ax = _grid(2, cl, h=2.6)
    bw = 0.8 / len(arms)
    for j, (s, t) in enumerate(cl):
        xv = sorted(d[(d[sc_col] == s) & (d.tr == t)][x].unique())     # per panel: x may differ by scenario
        a = ax[0, j]
        for arm in arms:
            g = d[(d.arm == arm) & (d[sc_col] == s) & (d.tr == t)].sort_values(x)
            st = STYLE[arm]
            xs = [xv.index(v) for v in g[x]] if xcat else g[x]
            a.errorbar(xs, g.ss_tot, yerr=g.get('ss_tot_ci'), color=st['color'], marker=st['marker'], ls=st['ls'],
                       lw=st['lw'], ms=4, capsize=2, label=st['label'])
        b = ax[1, j]
        for k, arm in enumerate(arms):
            g = d[(d.arm == arm) & (d[sc_col] == s) & (d.tr == t)].set_index(x)
            bottom = np.zeros(len(xv))
            for c in classes:
                v = g.reindex(xv)[f'ss_{c}_kB'].fillna(0).values
                cs = CLASS_STYLE[c]
                b.bar(np.arange(len(xv)) - 0.4 + bw * (k + 0.5), v, bw * 0.95, bottom=bottom, color=cs['color'],
                      hatch=cs['hatch'], edgecolor=STYLE[arm]['color'], lw=0.8, label=cs['label'])
                bottom += v
        b.set_xticks(range(len(xv)))
        b.set_xticklabels(xticklabels or [f'{v:g}' for v in xv])
        b.set_xlabel(_xl(xlabel, s))
        if xcat:
            a.set_xticks(range(len(xv)))
            a.set_xticklabels(xticklabels or [f'{v:g}' for v in xv])
    ax[0, 0].set_ylabel('subch.-slots per run')
    ax[1, 0].set_ylabel('subch.-slots / kB, by class')
    fig.suptitle(title + '\nbars left to right: ' + ', '.join(STYLE[a]['label'].split(' ')[0] for a in arms), y=1.02)
    _legend(fig, ax, ncol=7)
    return fig


# ------------------------------------------------------------------------------------------------ gains
GAIN_COLS = [('prr_group', 'ΔGroup PRR (pp)'), ('prr_worst', 'ΔWorst PRR (pp)'), ('prr_p5', 'ΔP5 PRR (pp)'),
             ('ss_kB', 'ΔRes./kB (%)')]


def gains(summ, x, others, base='R6', sc_col='scg'):
    """R6 - X per (scenario group, traffic, x). PRR in pp, resources in % of X (negative = R6 uses fewer)."""
    keys = [sc_col, 'tr', x]
    b = summ[summ.arm == base].set_index(keys)
    cols = {}
    for o in others:
        q = summ[summ.arm == o].set_index(keys)
        for m, lab in GAIN_COLS:
            if m == 'ss_kB':
                v = 100 * (b[m] - q[m]) / q[m]
            else:
                v = 100 * (b[m] - q[m])
            cols[(STYLE[o]['label'], lab)] = v
    out = pd.DataFrame(cols)
    out.columns = pd.MultiIndex.from_tuples(out.columns)
    out = out.dropna(how='all')
    so = {s: i for i, s in enumerate(SC_ORDER + ['Highway', 'Roundabouts'])}
    to = {t: i for i, t in enumerate(TR_ORDER)}
    order = sorted(out.index, key=lambda k: (so.get(k[0], 9), to.get(k[1], 9), k[2]))
    return out.loc[order]


def gain_heatmap(g, title, xlabel, fmt='{:+.1f}', xfmt='{:g}'):
    """Heatmap of gains(): one colour scale per metric (green = R6 better; resources: fewer = green)."""
    v = g.values.astype(float)
    col = np.zeros_like(v)
    for k, (_, lab) in enumerate(g.columns):
        s = -1 if lab.startswith('ΔRes') else 1
        m = np.nanmax(np.abs(g.xs(lab, axis=1, level=1).values)) or 1
        col[:, k] = s * v[:, k] / m
    fig, ax = plt.subplots(figsize=(1.0 * v.shape[1] + 3, 0.32 * v.shape[0] + 1.8))
    ax.imshow(col, cmap='RdYlGn', vmin=-1, vmax=1, aspect='auto')
    for i in range(v.shape[0]):
        for k in range(v.shape[1]):
            if not np.isnan(v[i, k]):
                ax.text(k, i, fmt.format(v[i, k]), ha='center', va='center', fontsize=7.5)
    ax.set_xticks(range(v.shape[1]))
    ax.set_xticklabels([b.replace('Δ', '').replace(' PRR', '') for _, b in g.columns], rotation=45, ha='right',
                       fontsize=7.5)
    nm0 = len(GAIN_COLS)
    for k in range(0, v.shape[1], nm0):
        ax.text(k + (nm0 - 1) / 2, -0.8, 'R6 - ' + g.columns[k][0].split(' ')[0], ha='center', va='bottom',
                fontsize=9, fontweight='bold')
    ax.set_yticks(range(v.shape[0]))
    ax.set_yticklabels([f'{SC_LABEL[s]} · {TR_LABEL[t]} · {xlabel}{xfmt.format(x)}' for s, t, x in g.index],
                       fontsize=7.5)
    ax.grid(False)
    nm = len(GAIN_COLS)
    for k in range(nm, v.shape[1], nm):
        ax.axvline(k - 0.5, color='k', lw=1.2)
    prev = None
    for i, (s, t, _) in enumerate(g.index):
        if prev is not None and (s, t) != prev:
            ax.axhline(i - 0.5, color='k', lw=1.2 if s != prev[0] else 0.5)
        prev = (s, t)
    ax.set_title(title, pad=22)
    fig.tight_layout()
    return fig
