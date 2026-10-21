# Benchmark audit evidence

These are evaluator controls and a retrospective audit, not a new application
performance claim. Application source is unchanged from merged main
`6fa6826aa4c835001c0d2bc8f3bec838499e5b5d`.

- `main-aa-*`: first A/A smoke run, one variant, two repetitions, 52 observations.
- `main-aa-contracts-*`: expanded A/A run, three variants, three repetitions,
  234 observations. Each implementation passes all 117 of its oracle checks.
- `pr2-reassessment.json`: the new analyzer applied to a separate copy of the
  original 48 native sessions, using the original local summary and review labels.
  The original artifacts are unchanged. Five blocking gates fail.
- `verification.json`: local full-suite and evaluator-control verification.
- `manifest.json`: SHA256 of the other files in this directory.

Every A/A observation is retained, including the initial point-estimate timing
win. Both commands correctly exited 1: identical source is never an optimization.
Plans include workload and harness fingerprints recorded before application import.
The expanded run's fingerprints match the final local harness; the smoke run
predates the final malformed-packet checks and records its own fingerprints.

Public copies normalize text line endings to LF and replace private host prefixes
in summary import paths with paths relative to each run's runtime directory.
Packets, durations, counts, errors and source SHA256 values are unchanged. Private
runtime databases, keys and model caches are excluded.

```powershell
python scripts/benchmark_lookup.py --baseline-ref 6fa6826aa4c835001c0d2bc8f3bec838499e5b5d --feature-ref 6fa6826aa4c835001c0d2bc8f3bec838499e5b5d --runtime <fresh-runtime> --output <results> --name main-aa-contracts --seed 20261021 --variants 3 --repetitions 3
```

Use the locally provisioned embedding/reranker snapshots in
`COLONY_EMBED_MODEL_DIR` and `COLONY_RANK_MODEL_DIR`. Runs used Python 3.13.1,
FastEmbed 0.7.4 and ONNX Runtime 1.24.4; model IDs/revisions and fingerprints are
documented in [the original model report](../lookup-optimization/retrieval-models.json).
Both workers share the installed third-party environment and model configuration.
Hardware scheduling and timing variation mean another A/A run will have different
durations; the required outcome remains `lookup_improves=false`.

See [findings and limits](../../benchmark-audit.md) and the
[evaluation protocol](../../benchmark.md).
