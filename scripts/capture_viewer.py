"""Render each viewer screen to PNG for visual review."""

from __future__ import annotations

import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(".witness-dev/tmp")
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:7842"


def main() -> None:
    trace_id = sys.argv[1] if len(sys.argv) > 1 else None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=2,
        )
        page = ctx.new_page()

        # 1. Trace list
        page.goto(BASE + "/", wait_until="networkidle")
        page.wait_for_selector("table", timeout=5000)
        page.screenshot(path=str(OUT / "01_list.png"), full_page=False)
        print("wrote 01_list.png")

        if not trace_id:
            # Pick the 11-step "long" trace for the richest detail view.
            rows = page.query_selector_all("tr a[href^='/traces/']")
            # Find the long trace link (task starts with 'On amazing.com')
            for a in rows:
                href = a.get_attribute("href") or ""
                text = a.inner_text()
                if "amazing.com" in text:
                    trace_id = href.split("/")[-1]
                    break
            if not trace_id and rows:
                href = rows[0].get_attribute("href") or ""
                trace_id = href.split("/")[-1]

        if not trace_id:
            print("no trace id — aborting")
            return

        page.goto(f"{BASE}/traces/{trace_id}", wait_until="networkidle")
        page.wait_for_timeout(1500)  # let TanStack Router settle

        # Select step with the most visual change (step 5 = add-to-cart with toast)
        step_buttons = page.query_selector_all("aside button, nav button, ul li button")
        if not step_buttons:
            # fallback: find all buttons inside the 320px-wide timeline rail
            step_buttons = page.query_selector_all("div.w-\\[320px\\] button")
        target_idx = min(5, max(0, len(step_buttons) - 1))
        if step_buttons:
            step_buttons[target_idx].click()
            page.wait_for_timeout(300)

        def click_tab(label: str) -> None:
            btn = page.get_by_role("button", name=label, exact=False)
            btn.first.click()
            page.wait_for_timeout(500)

        click_tab("Screenshots")
        page.screenshot(path=str(OUT / "02_detail_screenshots.png"))
        print("wrote 02_detail_screenshots.png")

        click_tab("DOM Diff")
        page.screenshot(path=str(OUT / "03_detail_dom.png"))
        print("wrote 03_detail_dom.png")

        click_tab("Action")
        page.screenshot(path=str(OUT / "04_detail_action.png"))
        print("wrote 04_detail_action.png")

        click_tab("LLM Calls")
        page.screenshot(path=str(OUT / "05_detail_llm.png"))
        print("wrote 05_detail_llm.png")

        # LLM detail modal
        row = page.query_selector("tbody tr")
        if row:
            row.click()
            page.wait_for_timeout(400)
            page.screenshot(path=str(OUT / "06_detail_llm_modal.png"))
            print("wrote 06_detail_llm_modal.png")

        browser.close()


if __name__ == "__main__":
    main()
