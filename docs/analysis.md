# Analysis

Witness captures what your agent did. The analysis layer tells you whether what
it did was safe and on-track. Four analyzers run over a captured trace and write
their results as `Finding` rows you can read in the viewer, over the API, or from
the CLI.

```bash
witness analyze <trace_id>   # analyze one trace
witness analyze --all        # analyze every captured trace
```

Analysis is idempotent. Re-running it on a trace deletes the previous findings
for that trace and writes a fresh set, so you never accumulate duplicates.

---

## The four analyzers

### Prompt injection (`prompt_injection`)

Detects indirect prompt injection: content in a page the agent read that is
shaped like an attempt to hijack it. A finding fires whenever injection-shaped
content is present, and is escalated when a conjunction holds.

The two signals combined are:

1. Injection-shaped content in the captured DOM:
   - imperative text aimed at an agent ("ignore previous instructions", "you are
     now", "disregard the above", "do not tell the user").
   - hidden or offscreen text (`display:none`, `visibility:hidden`, `opacity:0`,
     `font-size:0`, large negative offsets, `aria-hidden`, color equal to
     background).
   - suspicious links and exfiltration-shaped URLs (`webhook`, `requestbin`,
     `ngrok`, `pastebin`, `data:text/html`, `javascript:`, etc.).
2. Post-injection action deviation: did the agent's next action obey the injected
   instruction or drift off task?

Severity is graded so benign pages do not produce false highs. Injection content
alone is reported at a lower severity. Only a strong deviation (the next action
echoing injection vocabulary, or navigating/submitting to a suspicious
destination) reaches high. A weak deviation (a strong injection phrase plus a
merely task-irrelevant navigation) is capped at medium. The DOM scan uses the
stdlib `html.parser` plus regexes, so there is no hard dependency on a parser
library.

### Trajectory health (`trajectory`)

Looks at the ordered step sequence and flags behaviour that suggests the agent is
not making progress:

- **Loops / thrash**: the same action repeated several times in a row while
  neither the URL nor the DOM changes.
- **Task drift**: recent actions and URLs growing lexically distant from the
  trace's stated task. Measured with a stdlib lexical similarity (token Jaccard
  blended with `difflib`), so there is no embedding dependency. A pluggable
  `embed_fn` hook is reserved for a future version.
- **Wasted / dead-end steps**: a step that errored or produced no observable
  state change.
- **Backtracking**: returning to a URL that was already visited.

Each concrete issue is a step-level finding. In addition, exactly one trace-level
finding (`step_id` is null) carries the overall trajectory health score, a
weighted rollup where loops and drift hurt most. The viewer badges the trace from
this trace-level finding.

### Outcome / risk prediction (`outcome`)

Emits one trace-level finding predicting the probability that a (possibly
partial) run will succeed. A low probability is itself the risk signal, so a low
score carries a high severity and lets a caller decide to stop early.

The default predictor is a transparent heuristic over cheap features: step count
relative to a typical run, error rate, repeated/looping actions, rising latency,
the presence of trajectory-drift signals, and the type of the last action. The
feature view (`extract_features`) is shared with the optional ML training harness
so a learned model and the heuristic never disagree about what a feature is. See
[bench.md](bench.md) for using outcome scores in the reliability harness and the
note below on training a model.

### PII / secret exfiltration (`exfiltration`)

Flags two channels of sensitive data leaving:

1. **Typed into a field**: the agent entering credential or PII shaped strings
   (emails, phone numbers, Luhn-valid credit cards, API keys and tokens,
   passwords, SSNs) into a page via an input-style action.
2. **DOM carry-over**: a secret present in an earlier captured DOM that later
   reappears in an action payload or a navigated URL query string. That is data
   lifted off one page and pushed somewhere else.

Detection regexes are shared with `witness.redact` so detection stays consistent
with redaction. Evidence never stores a raw secret in full: matches are masked
(short prefix kept, rest dropped) so a reviewer can correlate without the trace
leaking the value. Cookie and bearer label boilerplate is excluded from
DOM-secret extraction unless the value after the label looks like a real token,
to avoid false carry-over findings on consent-banner copy.

---

## The Finding model

Every analyzer returns plain `storage.Finding` objects. The runner persists them.

| field | meaning |
| --- | --- |
| `id` | row id |
| `trace_id` | the trace this finding belongs to |
| `step_id` | the step it attaches to, or null for a trace-level finding |
| `kind` | `prompt_injection`, `trajectory`, `exfiltration`, or `outcome` |
| `severity` | `info`, `low`, `medium`, `high`, or `critical` |
| `score` | a number in `0..1` (for outcome, the success probability) |
| `title` | one-line summary |
| `detail` | a short human-readable explanation |
| `evidence` | a dict with the matched snippets and which heuristics fired |
| `created_at` | when the finding was produced |

An analyzer never touches the database. It returns findings with `created_at`
set, and `witness.analysis.runner` persists them. One failing analyzer never
breaks the others: the runner logs it and moves on.

---

## API endpoints

The local viewer server exposes findings over HTTP:

- `GET /api/traces/{trace_id}/findings` returns the stored findings for a trace
  as `FindingOut[]`.
- `POST /api/traces/{trace_id}/analyze` runs the analyzers for a trace and
  returns the freshly written findings.
- `GET /api/traces/{trace_id}` includes `findings` on the `TraceDetail` payload.

---

## Adding an analyzer

An analyzer is a plain module under `witness/analysis/` exposing a `KIND`
constant and an `analyze(ctx: AnalysisContext) -> list[storage.Finding]`
function. Register it by adding its module path to `ANALYZER_MODULES` in
`witness/analysis/runner.py`. The `AnalysisContext` hands you the trace, its
ordered steps, LLM calls grouped by step, and a lazy `load_dom(step, which)`
callable so you only pay to read the DOM blobs you actually inspect.
