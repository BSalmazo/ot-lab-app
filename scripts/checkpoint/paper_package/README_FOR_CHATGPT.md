# Paper Input Package (For ChatGPT)

This folder is a high-signal, paper-oriented package built from the final validated checkpoint run.

## What to provide to ChatGPT

At minimum, attach/provide these files:

1. `PAPER_MASTER_BRIEF.md`
2. `PAPER_METHODS_REPRODUCIBILITY.md`
3. `PAPER_RESULTS_TABLES.md`
4. `PAPER_LIMITATIONS_AND_FUTURE_WORK.md`
5. `PAPER_WRITING_PROMPT.md`
6. `PAPER_DATA_MANIFEST.json`

Optional but highly recommended:

7. `../CHECKPOINT_SUPER_DETALHADO.md`
8. `../../../artifacts/checkpoint_runs/checkpoint_20260331_182606/REPORT.md`
9. `../../../artifacts/checkpoint_runs/checkpoint_20260331_182606/checkpoint_result.json`
10. `../../../artifacts/checkpoint_runs/checkpoint_20260331_182606/derived_metrics.json`

Raw-data package (for deep audit):

11. `../../../artifacts/checkpoint_packages/checkpoint_20260331_182606.tar.gz`

## Suggested usage

- First ask ChatGPT to read all files and produce a structured paper outline.
- Then ask it to draft section by section (Abstract, Intro, Methods, Results, Threats to Validity, Conclusion).
- Ask for all claims to be explicitly tied to evidence IDs and metrics from the manifest.

## Important note

The project discussion language can be Portuguese, but all paper artifacts in this package are in English by design.
