"""Multi-tab recipe — the agent opens several tabs to compare data.

Uses Browser Use's built-in multi-tab support. Witness captures the active
page at each step, so you see the context switch reflected in the URL
column and screenshot diff.

    python examples/multi_tab.py
    witness view
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

import witness

load_dotenv()


async def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set (add to .env)", file=sys.stderr)
        sys.exit(1)

    from browser_use import Agent
    from browser_use.llm import ChatAnthropic

    agent = Agent(
        task=(
            "Open three tabs: https://news.ycombinator.com, "
            "https://lobste.rs, and https://old.reddit.com/r/programming. "
            "From each, extract the single top story's title and points. "
            "Return a comparison table."
        ),
        llm=ChatAnthropic(model="claude-sonnet-4-5"),
    )
    witness.instrument(agent)
    await agent.run()
    print(f"\nTrace: {agent._witness_trace_id}")


if __name__ == "__main__":
    asyncio.run(main())
