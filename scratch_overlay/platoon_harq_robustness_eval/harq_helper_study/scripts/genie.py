"""Genie (oracle) helper arms: shared by study.py, helper_rx_campaign/campaign.py, timing_sweep.py, psfch_err_sweep.py.

A genie arm runs the command of its Kuo arm with the helper selection replaced by the per-window best-helper schedule
of the case (scripts/genie_schedule.py on the R3_probe run of the same route, background load and seed):
  --helperSelect=kuo  ->  --helperSelect=schedule --helperSchedule=<SCHED>/<route>_ivd30_periodic1tb_<bg>_s<seed>_genie.csv
The links do not depend on the traffic, so every traffic type replays the periodic probe's schedule.

SCHED: $GENIE_SCHED, else harq_helper_study/out/genie_probe_all/R3_probe (150 schedules, one per route x load x seed).
"""
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # harq_helper_study/
SCHED = os.path.abspath(os.environ.get('GENIE_SCHED') or os.path.join(HERE, 'out', 'genie_probe_all', 'R3_probe'))
SUFFIX = '_genie'


def is_genie(arm):
    return SUFFIX in arm


def base_arm(arm):
    """R6_genie -> R6, R4_bsc_fbs_genie -> R4_bsc_fbs, R6_genie_p2 -> R6_p2."""
    return arm.replace(SUFFIX, '')


def schedule_file(cell_tag, sched=None):
    """cell_tag: '<route>_ivd30_<traffic>_<bg>_s<seed>' (with or without a helper-miss part is not allowed)."""
    for tr in ('aperiodic1tb', 'periodic1tb'):
        cell_tag = cell_tag.replace(f'_{tr}_', '_periodic1tb_')
    return os.path.join(sched or SCHED, cell_tag + '_genie.csv')


def to_genie(cmd, cell_tag, sched=None):
    """The Kuo command of the same arm and cell -> the genie command (schedule replay)."""
    if '--helperSelect=kuo' not in cmd:
        raise ValueError('genie arm needs a Kuo-selected base command: ' + ' '.join(cmd[:3]))
    out = [x for x in cmd if not x.startswith('--helperSchedule=')]
    out = ['--helperSelect=schedule' if x == '--helperSelect=kuo' else x for x in out]
    return out + [f'--helperSchedule={schedule_file(cell_tag, sched)}']
