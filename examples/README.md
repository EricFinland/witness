# Examples

Real recipes showing common Witness use cases. Every script is <60 lines
and runs end-to-end.

| File | What it shows |
|------|---------------|
| [`hn_top_story.py`](hn_top_story.py) | Minimal success case — a short task that completes in 3–4 steps. Good for a first look at the viewer. |
| [`form_fill.py`](form_fill.py) | Exercising `input_text` and `click` actions. The DOM diff tab is dramatic on this one — each field fill is visible. |
| [`multi_tab.py`](multi_tab.py) | Agent juggles three tabs and extracts data from each. The URL column in the timeline makes the context switching obvious. |
| [`intentional_failure.py`](intentional_failure.py) | Task is impossible on purpose. Shows what error capture looks like — trace status=`error`, offending step highlighted red, full DOM available for post-mortem. |

## Running any of them

```bash
pip install witness[browser-use]
playwright install chromium
# add ANTHROPIC_API_KEY to .env
python examples/hn_top_story.py
witness view
```

Every example uses Anthropic Claude by default. Swap in `ChatOpenAI` if you prefer.
