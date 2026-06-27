# WitnessBench

WitnessBench is a small, named benchmark of browser tasks with reference
trajectories. It is the travel companion to ProofBench: where ProofBench scores
reasoning, WitnessBench scores whether a browser agent can reliably *do the
thing* and whether Witness captured a clean, analyzable trace while it did.

Each task is a real, deterministic-ish browser job with a documented success
criterion. The scripts here are reference tasks, not unit tests. They drive a
live browser and an LLM, so they are meant to be run by hand (or by `witness
bench`), not in CI.

## The task set

| Script | Task | Success criterion | Reference steps |
|--------|------|-------------------|-----------------|
| [`hn_top_story_bench.py`](hn_top_story_bench.py) | Read the Hacker News top story | Final answer has a non-empty title and a numeric point count | ~4 |
| [`form_fill_bench.py`](form_fill_bench.py) | Fill and submit the httpbin POST form | Response reflects the typed customer name | ~9 |
| [`search_extract_bench.py`](search_extract_bench.py) | Search Wikipedia, extract a fixed fact | Final answer contains "Guido van Rossum" | ~6 |

Each script exposes the same module-level descriptor so a runner can introspect
it without executing it:

- `TASK_ID` (str): stable short id for the task.
- `TASK` (str): the natural-language instruction handed to the agent.
- `SUCCESS_CRITERION` (str): how to decide if the run passed.
- `REFERENCE_STEPS` (int): how many steps a competent run takes. Useful for
  flagging runs that wander far past the reference trajectory.
- `async def main() -> str | None`: runs the task and returns the Witness
  trace id.

The mix is intentional. There are two **structural** checks (the answer has the
right *shape* even though the live value drifts) and one **fixed-answer** check
(the value never changes), so reliability scoring covers both styles of task.

## Running

```bash
pip install usewitness[browser-use]
playwright install chromium
# add ANTHROPIC_API_KEY to .env
```

Run a single task directly:

```bash
python examples/bench/hn_top_story_bench.py
witness view
```

Or drive the whole suite through the CLI:

```bash
witness bench            # run every task in the suite
witness bench hn_top_story   # run one task by TASK_ID
witness bench --json     # machine-readable results for dashboards
```

Every task uses Anthropic Claude by default. Swap in `ChatOpenAI` if you prefer
a different model; the success criteria are model-agnostic.

## How reliability metrics are produced

WitnessBench is about reliability, not a single pass/fail. The intended flow:

1. **Run each task N times.** Browser agents are stochastic, so a single run
   tells you little. Reliability is measured across repeated runs.
2. **Score each run against its `SUCCESS_CRITERION`.** Pass rate (passes / runs)
   is the headline number per task, and the mean across tasks is the suite
   score.
3. **Compare the captured trace to the reference trajectory.** Every run
   produces a Witness trace. `witness analyze <trace_id>` adds findings
   (trajectory loops, exfiltration, outcome mismatch). A run can technically
   reach the answer while taking a noisy, looping path. Step count versus
   `REFERENCE_STEPS` and the trajectory findings turn "it passed" into "it
   passed cleanly."
4. **Aggregate into a scorecard.** Per task: pass rate, median step count, and
   any high-severity findings. Per suite: mean pass rate and total findings.
   `witness bench --json` emits this shape so it can feed a dashboard or a
   regression gate.

Because the tasks point at stable public endpoints (Hacker News, httpbin,
Wikipedia) and use fixed or structurally-checkable answers, score changes
across model or agent versions reflect the agent, not churn in the target
sites.
