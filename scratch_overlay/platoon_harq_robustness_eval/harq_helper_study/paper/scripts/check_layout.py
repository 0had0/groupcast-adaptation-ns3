"""Rough layout check: overlapping text boxes and artists outside the figure."""
import itertools, matplotlib, importlib.util, sys
matplotlib.use('Agg')
import matplotlib.pyplot as plt
spec = importlib.util.spec_from_file_location('mf', sys.argv[1]); mf = importlib.util.module_from_spec(spec); spec.loader.exec_module(mf)
orig = plt.Figure.savefig
def chk(self, fname, **kw):
    self.canvas.draw(); r = self.canvas.get_renderer()
    texts = [t for t in self.findobj(matplotlib.text.Text) if t.get_visible() and t.get_text().strip()]
    bb = [(t.get_text().replace('\n', ' ')[:30], t.get_window_extent(r)) for t in texts]
    fb = self.bbox
    bad = [(a[0], b[0]) for a, b in itertools.combinations(bb, 2) if a[1].overlaps(b[1]) and a[1].width > 0 and b[1].width > 0]
    print(fname.split('/')[-1], 'size_in', [round(v, 2) for v in self.get_size_inches()], 'text overlaps:', bad[:12])
    for a in self.axes:
        lg = a.get_legend()
    leg = [l for l in self.legends]
    for l in leg:
        e = l.get_window_extent(r)
        for a in self.axes:
            if e.overlaps(a.get_window_extent(r)): print('  legend overlaps axes', a.get_title()[:25])
    orig(self, fname, **kw)
plt.Figure.savefig = chk
for f in ['fig_scenario', 'fig_dist', 'fig_bars', 'fig_cost', 'fig_takeover']:
    getattr(mf, f)()
