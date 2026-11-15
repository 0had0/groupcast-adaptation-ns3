"""Folder layout of harq_helper_study/ and the path-setup cell every notebook starts with.

  harq_helper_study/
    scripts/      every script (this file)
    notebooks/    every notebook; a notebook from a subfolder keeps its path, e.g.
                  notebooks/paper_v2/eval_sections_v3/eval_figures.ipynb (its outputs stay in paper_v2/eval_sections_v3)
    out/, figures/, paper_v2/, paper_notes/, ...   data, figures, tex, unchanged

The path-setup cell finds harq_helper_study/ from the notebook's folder, changes the working directory to the
folder the notebook belongs to (harq_helper_study/ itself, or e.g. paper_v2/eval_sections_v3), and puts scripts/
and that folder on sys.path. Relative paths in the notebook (out/..., figures/..., tex files) therefore keep their
old meaning.
"""
import os

import nbformat as nbf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, 'scripts')
NOTEBOOKS = os.path.join(ROOT, 'notebooks')
TAG = 'path-setup'

CELL = '''# path setup (scripts/nb_layout.py): run from the folder this notebook belongs to, with scripts/ importable
import os, sys
if '_HHS_ROOT' not in globals():
    _d = os.getcwd()
    while os.path.basename(_d) != 'notebooks' and os.path.dirname(_d) != _d:
        _d = os.path.dirname(_d)
    _HHS_ROOT = os.path.dirname(_d) if os.path.basename(_d) == 'notebooks' else os.getcwd()
    os.chdir(os.path.join(_HHS_ROOT, %r))
    sys.path[:0] = [os.path.join(_HHS_ROOT, 'scripts'), os.getcwd()]'''


def notebook_path(name, rel=''):
    """Where a notebook of folder `rel` (relative to harq_helper_study/) lives."""
    return os.path.join(NOTEBOOKS, rel, name)


def add_preamble(nb, rel=''):
    """Insert (or refresh) the path-setup cell as the first cell; returns nb."""
    cell = nbf.v4.new_code_cell(CELL % (rel or '.'))
    cell.metadata['tags'] = [TAG]
    if nb.cells and TAG in nb.cells[0].get('metadata', {}).get('tags', []):
        nb.cells[0] = cell
    else:
        nb.cells.insert(0, cell)
    return nb
