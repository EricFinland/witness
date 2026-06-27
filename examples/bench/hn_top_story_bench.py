"""WitnessBench reference task: read the Hacker News top story.

A short, mostly deterministic navigation-and-extract task. The agent has to
land on the front page, identify the first story, and report two facts about
it (title and point count). This is the smallest WitnessBench task and serves
as the smoke test for the suite.

Success criterion (see SUCCESS_CRITERION below): the agent returns a non-empty
story title and a numeric point count. The exact values change minute to
minute, so the check is structural (did the agent produce both fields), not an
equality check against a frozen answer.

    pip install usewitness[browser-use]
    playwright install chromium
    # add ANTHROPIC_API_KEY to .env
    python examples/bench/hn_top_story_bench.py
    witness view
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

import witness

load_dotenv(override=True)

# WitnessBench task descriptor. The runner reads these module-level constants.
TASK_ID = "hn_top_story"
TASK = (
    "Go to https://news.ycombinator.com. Find the top story. "
    "Return its title and its point count."
)
SUCCESS_CRITERION = (
    "Final answer contains a non-empty story title and a numeric point count."
)
# Reference trajectory length: a competent run completes in roughly this many
# steps. Used by reliability scoring to flag runs that wander.
REFERENCE_STEPS = 4


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
