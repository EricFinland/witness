# Reliability benchmarking

Browser agents are flaky. The same task can pass on one run and fail on the next
because of timing, layout shifts, model nondeterminism, or a retry loop that
spirals. The bench harness runs a task repeatedly (optionally across model
variants) and reports how reliable it actually is.

```bash
witness bench              # summarize all captured traces as one report
witness bench <suite>      # filter to traces whose task contains <suite>
witness bench --json       # machine-readable output for CI
```

The CLI mode summarizes already-captured traces, so you can benchmark runs you
recorded earlier without launching a browser. The programmatic API
(`witness.bench.run_bench(make_agent, task, n, models=...)`) launches `n`
instrumented runs (per model variant), captures real traces, and returns a
`BenchReport`.

---

## Reliability metrics

`witness.bench.summarize(traces)` is a pure function over captured `Trace` rows.
It produces a `BenchReport` with:

- **Success rate** - fraction of runs that finished with `status == "success"`,
  plus per-model success rate.
- **Flakiness** - a task is flaky when it neither always passes nor always fails.
  The flakiness number is the Bernoulli variance scaled to peak at `0.5` when
  exactly half the runs pass, and `0` for a deterministic task.
- **Distributions** for step count, cost, latency, and tokens. Each distribution
  carries min, max, mean, median, population stdev, and the coefficient of
  variation (`cv = stdev / mean`), which doubles as a spread/flakiness proxy for
  that metric.
- **Trajectory divergences** - consecutive runs are diffed step-by-step with
  `witness.diff` to find where two runs of the same task first diverged (the
  first differing step, how many leading steps matched, and a one-line summary).

A `BenchReport` renders to JSON (`to_dict`) or to a Markdown table
(`to_markdown`) for dropping into a PR or a report.

---

## WitnessBench

WitnessBench is a small set of reference tasks for measuring agent reliability in
a repeatable way. `witness.bench.load_reference_tasks()` returns the task specs.
It reads one entry per file from `examples/bench/` when that directory exists
(`.py`, `.txt`, `.json`, `.yaml`, `.yml`), and otherwise falls back to a built-in
list covering a simple lookup, a form fill, and a multi-tab task.

To add a reference task, drop a file into `examples/bench/`. A `.json` file with
a `task` field has its task string read directly; other file types are recorded
by name and path so a caller can drive them.

---

## Outcome scores in the harness

The outcome analyzer (see [analysis.md](analysis.md)) predicts a success
probability per run. Across a benchmarked suite those predictions can be compared
against actual outcomes to calibrate the predictor, or used as an early-stop
signal so a doomed run is abandoned before it finishes paying. The optional ML
training harness (`witness/analysis/training.py`) can learn a classifier from the
labelled runs the bench harness captures; install it with `pip install
usewitness[ml]` and train with `witness.analysis.training.train()`.
