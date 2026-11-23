# Build check (2026-10-24)
- Command: pdflatex main; bibtex main; pdflatex main; pdflatex main (TeX Live 2022, pdfTeX 1.40.22)
- Result: 6 pages, no errors, no overfull boxes, no undefined citations/references.
- Overleaf: upload the zip as a new project; compiler pdfLaTeX; main document main.tex.
  IEEEtran.cls / IEEEtran.bst are included (Overleaf also ships them).
- Figures: regenerate with `python3 scripts/make_figs.py` (reads data/*.csv);
  table III with `python3 scripts/gen_tables.py`. data/*.csv come from scripts/agg.py + scripts/summ.py
  run on out/remote_full and out/remote_density.
- Citations: 14 scholarly entries verified against DOI/arXiv (OpenAlex); 7 are 3GPP specs.
- Residual risks (author to check):
  1. 60-car level saturates the channel (CBR 0.98); presented as a stress test, 30 cars as operating point.
  2. Venue page limit assumed 6 pages (IEEE conference).
  3. Pages rendered and checked by eye on 2026-10-26.
