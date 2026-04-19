"""Minimal Witness + Browser Use example.

Run:
    pip install -e ".[browser-use]"
    playwright install chromium
    export ANTHROPIC_API_KEY=...
    python examples/hn_top_story.py

Then:
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
            "Go to https://news.ycombinator.com. Find the top story. "
            "Return its title and its point count."
        ),
        llm=ChatAnthropic(model="claude-sonnet-4-5"),
    )
    witness.instrument(agent)
    await agent.run()
    print(f"\nTrace saved. Run `witness view` to open the viewer.")
    print(f"Trace id: {agent._witness_trace_id}")


if __name__ == "__main__":
    asyncio.run(main())
