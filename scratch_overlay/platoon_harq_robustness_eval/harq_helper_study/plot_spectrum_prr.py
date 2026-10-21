#!/usr/bin/env python3
"""Spectrum use per packet by component (stacked bars, left axis) with group-mean and worst-member PRR
(lines, right axis), one panel per scenario. Writes figures/spectrum_prr_{prb,subch}_<profile>.png.

Usage: plot_spectrum_prr.py [--profile local1|full|smoke] [--out DIR] [--show]
Uses the same loading / accounting code as the study notebook (build_notebook.py).
"""
import argparse, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_notebook as bn  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--profile', default='local1')
    ap.add_argument('--out', default=None)
    ap.add_argument('--show', action='store_true', help='display instead of only saving (notebook use)')
    a = ap.parse_args()
    out = a.out or os.path.join(HERE, 'out', a.profile)
    import matplotlib
    if not a.show:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    ns = {'display': print}
    exec(bn.LOAD % {'out': out}, ns)
    exec(bn.SUMMARY, ns)
    exec(bn.SPECTRUM, ns)
    os.makedirs(os.path.join(HERE, 'figures'), exist_ok=True)
    show = plt.show
    for unit, scale, ylabel in (('prb', 1.0, 'PRB x symbols per packet'),
                                ('subch', ns['SUBCH_SLOT'], 'subchannel-slots per packet')):
        path = os.path.join(HERE, 'figures', f'spectrum_prr_{unit}_{a.profile}.png')
        plt.show = lambda p=path: (plt.savefig(p, dpi=130, bbox_inches='tight'), show() if a.show else plt.close())
        ns['plt'] = plt
        ns['spectrum_plot'](ns['COMP'], f'Spectrum use per packet ({ylabel.split(" per")[0]}, bars) and PRR (lines, right)',
                            ylabel, scale=scale, prr=True)
        print(path)
    plt.show = show


if __name__ == '__main__':
    main()
