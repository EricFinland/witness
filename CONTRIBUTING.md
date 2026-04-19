# Contributing to Witness

Thanks for considering a contribution. Witness is small and opinionated — the
v0 codebase is ~2k lines of Python + ~1.5k lines of TypeScript — so it's easy
to get oriented.

## Before you start

- **File an issue first** if the change is non-trivial (>50 lines or touching
  the SDK's span correlation). A 10-minute chat saves a wasted afternoon.
- **Small changes welcome without an issue** — typo fixes, doc tweaks, an
  extra model in `witness/pricing.py`, a new action-type icon in the viewer.

## Dev setup

```bash
git clone https://github.com/ericcatalano/witness
cd witness

# Python side
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev,browser-use]"

# Viewer side
cd viewer
npm install
cd ..
```

## Running things

| What | How |
|------|-----|
| Python tests | `pytest -q` |
| Lint | `ruff check .` |
| Seed realistic fake traces (zero API calls) | `python scripts/seed_demo.py` |
| Build the viewer | `python scripts/build_viewer.py` |
| Start the backend + viewer | `witness view` |
| Viewer dev server (hot reload) | `cd viewer && npm run dev`, then start the backend separately with `uvicorn witness.server:app --port 7842` |

## Project layout

```
witness/        Python package (SDK + FastAPI server + CLI)
  sdk.py          The instrument() wrapper — monkey-patches Agent.step
  otel_bridge.py  OTEL → LLMCall rows, correlated per step via ContextVar
  storage.py      SQLModel schema + ~/.witness/ location
  server.py       FastAPI app, 3 API routes + SPA fallback
  cli.py          Typer CLI: witness view / ls / rm / config
viewer/         Vite + React 19 + Tailwind v3 viewer
examples/       Runnable recipes (form-fill, multi-tab, deliberate-failure)
scripts/        Seed + viewer build + screenshot capture
tests/          Pytest
```

## Conventions

- Python: ruff defaults. No type ignores without a one-line comment explaining
  why.
- Viewer: strict TS, Tailwind classes colocated, no inline styles.
- Comments: only where the **why** isn't obvious. `# loops` is not a comment.
- PRs: one concern per PR. If you're tempted to `git commit -am "misc"`,
  split.

## Areas we'd love help on

- **More action-type icons** in `viewer/src/lib/utils.ts` — currently the
  long tail of Browser Use actions falls back to a generic label.
- **Additional model pricing entries** in `witness/pricing.py` as new
  providers ship.
- **Playwright-agent instrumentation** — right now only Browser Use is
  supported. Dropping the monkey-patch and going through a stable hook
  on Playwright would let us cover a second framework.
- **DOM-diff performance** on traces with very large pages (>2MB HTML).
  The current client-side diff is OK up to about 500 KB; past that it
  blocks the main thread.

## Reporting bugs

Please include:

1. Your OS + Python version.
2. The exact trace id that hit the bug.
3. What you expected vs. what happened.
4. If the viewer looked wrong, a screenshot (open DevTools for any
   console errors too).

## License

By contributing, you agree that your contributions are MIT licensed.
