"""Form-fill recipe — exercises input_text + click actions.

Good trace for showing off the DOM diff (each field fill mutates the
form's state) and the per-step screenshot compare.

    python examples/form_fill.py
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
            "Open https://httpbin.org/forms/post. Fill in: "
            "customer name 'Ada Lovelace', telephone '+1-415-555-0123', "
            "email 'ada@example.com', pick 'Medium' size, and 'Mushroom' + "
            "'Cheese' toppings. Submit the form and report what the response "
            "page shows."
        ),
        llm=ChatAnthropic(model="claude-sonnet-4-5"),
    )
    witness.instrument(agent)
    await agent.run()
    print(f"\nTrace: {agent._witness_trace_id}")


if __name__ == "__main__":
    asyncio.run(main())
