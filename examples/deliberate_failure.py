"""Deliberate failure — shows what Witness captures when the agent gets stuck.

This task is impossible on purpose: the page has no element matching the
agent's task. Browser Use will burn through its step budget and raise.
The trace ends with status="error", and you can see in the viewer exactly
which step hung, what the LLM was thinking when it tried, and the full
DOM it was looking at.

    python examples/deliberate_failure.py
    witness view

Why keep this in examples/ — seeing what failure looks like before it
happens to you is half the value of an observability tool.
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

    # Tight step budget so we fail fast and cheap.
    agent = Agent(
        task=(
            "Open https://example.com and click the big red 'Download Invoice "
            "#INV-42' button, then upload your tax return. "
            "(example.com has none of these controls — this is expected to fail.)"
        ),
        llm=ChatAnthropic(model="claude-haiku-4-5"),
        max_steps=4,
    )
    witness.instrument(agent)
    try:
        await agent.run()
    except Exception as e:
        print(f"\nExpected failure: {e!r}")

    print(f"Trace: {agent._witness_trace_id} (status=error)")
    print("Open `witness view` and inspect the final step — the DOM shows why.")


if __name__ == "__main__":
    asyncio.run(main())
