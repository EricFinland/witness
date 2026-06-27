"""WitnessBench reference task: search a site and extract a fact.

A two-hop task: use an on-page search box, then read a specific value off the
result page. Wikipedia is the reference target because its article structure is
stable and the answer for a well-established topic does not drift.

Success criterion (see SUCCESS_CRITERION below): the agent's final answer names
the programming language Python's original author (Guido van Rossum). This is a
fixed-answer task, so the check is an equality/contains match, unlike the
structural checks in the navigation tasks.

    python examples/bench/search_extract_bench.py
    witness view
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

import witness

load_dotenv(override=True)

# WitnessBench task descriptor.
TASK_ID = "search_extract"
EXPECTED_ANSWER = "Guido van Rossum"
TASK = (
    "Go to https://en.wikipedia.org. Use the search box to look up "
    "'Python (programming language)'. On the article page, find who "
    "originally designed the language and return that person's full name."
)
SUCCESS_CRITERION = (
    f"Final answer contains '{EXPECTED_ANSWER}' (case-insensitive)."
)
REFERENCE_STEPS = 6


async def main() -> str | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set (add to .env)", file=sys.stderr)
        sys.exit(1)

    from browser_use import Agent
    from browser_use.llm import ChatAnthropic

    agent = Agent(task=TASK, llm=ChatAnthropic(model="claude-sonnet-4-5"))
    witness.instrument(agent)
    await agent.run()
    trace_id = getattr(agent, "_witness_trace_id", None)
    print(f"\n[{TASK_ID}] trace: {trace_id}")
    return trace_id


if __name__ == "__main__":
    asyncio.run(main())
