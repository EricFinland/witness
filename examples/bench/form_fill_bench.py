"""WitnessBench reference task: fill and submit a web form.

Exercises the input_text and click action mix on a stable, dependency-free
endpoint (httpbin's POST form). The reference site echoes the submitted values
back on the response page, which gives the success check something concrete to
look for.

Success criterion (see SUCCESS_CRITERION below): after submitting, the response
page (or the agent's final answer) reflects the customer name that was typed in.
That confirms the form actually round-tripped rather than the agent merely
claiming success.

    python examples/bench/form_fill_bench.py
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
TASK_ID = "form_fill"
CUSTOMER_NAME = "Ada Lovelace"
TASK = (
    "Open https://httpbin.org/forms/post. Fill in: "
    f"customer name '{CUSTOMER_NAME}', telephone '+1-415-555-0123', "
    "email 'ada@example.com', pick 'Medium' size, and 'Mushroom' + "
    "'Cheese' toppings. Submit the form and report what the response "
    "page shows."
)
SUCCESS_CRITERION = (
    f"The submitted response reflects the customer name '{CUSTOMER_NAME}', "
    "confirming the form round-tripped."
)
REFERENCE_STEPS = 9


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
