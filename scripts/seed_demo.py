"""Seed realistic synthetic Witness traces.

Zero API calls. Uses Playwright to render a fake styled Hacker News UI into
actual PNG screenshots, so the viewer has convincing before/after images.
Every step has a meaningful DOM delta. Five traces are produced covering:

  1. Short success    — 3 steps, HN top-story flow
  2. Long success     — 12 steps, shopping-cart-style flow
  3. Mid-run error    — 5 steps, errors on step 3
  4. Running          — no ended_at, partial steps
  5. Expensive call   — one step with ~50k prompt tokens on Opus

Re-running this script wipes and reseeds.

    python scripts/seed_demo.py
"""

from __future__ import annotations

import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, Page, sync_playwright
from sqlmodel import select

from witness import storage
from witness.pricing import calculate_cost

VIEW = {"width": 1280, "height": 720}


# --- HTML templates ---------------------------------------------------------

BASE_CSS = """
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
.hn { background: #f6f6ef; min-height: 100vh; }
.hn .topbar { background: #ff6600; color: white; padding: 4px 10px; font-size: 13px; display: flex; align-items: center; gap: 10px; }
.hn .topbar .logo { border: 1px solid white; padding: 1px 5px; font-weight: bold; }
.hn .topbar a { color: black; text-decoration: none; }
.hn .content { padding: 10px; background: #f6f6ef; }
.hn .story { display: flex; gap: 6px; align-items: baseline; padding: 3px 0; font-size: 13px; }
.hn .story .rank { color: #828282; width: 26px; text-align: right; }
.hn .story .title a { color: #000; text-decoration: none; }
.hn .story .title a:hover { text-decoration: underline; }
.hn .story .host { color: #828282; font-size: 11px; }
.hn .story .meta { color: #828282; font-size: 11px; margin-left: 32px; }
.hn .story .meta .hl { color: #ff6600; font-weight: 600; }
.hn .story.selected { background: #ffefd5; outline: 1px solid #ff6600; }
.gmail { background: #fff; font-family: 'Google Sans', Roboto, sans-serif; }
.gmail .header { height: 56px; border-bottom: 1px solid #eaeaea; display: flex; align-items: center; padding: 0 16px; gap: 12px; }
.gmail .brand { color: #5f6368; font-size: 22px; }
.gmail .search { flex: 1; max-width: 720px; background: #f1f3f4; border-radius: 8px; padding: 10px 16px; color: #5f6368; font-size: 13px; }
.gmail .body { display: flex; height: calc(100vh - 56px); }
.gmail .side { width: 256px; padding: 16px; border-right: 1px solid #eaeaea; }
.gmail .compose { background: #c2e7ff; color: #001d35; font-weight: 500; padding: 10px 22px; border-radius: 14px; display: inline-block; }
.gmail .nav { margin-top: 14px; font-size: 14px; color: #202124; }
.gmail .nav div { padding: 6px 12px; border-radius: 0 16px 16px 0; }
.gmail .nav div.active { background: #d3e3fd; font-weight: 600; }
.gmail .mail { flex: 1; overflow: auto; }
.gmail .row { display: flex; gap: 16px; padding: 10px 16px; border-bottom: 1px solid #f3f3f3; font-size: 13px; cursor: pointer; }
.gmail .row:hover { box-shadow: inset 1px 0 0 #d0d7de, inset -1px 0 0 #d0d7de, 0 1px 2px 0 rgba(60,64,67,0.1); }
.gmail .row.unread { font-weight: 600; color: #111; background: #fff; }
.gmail .row.read { color: #5f6368; }
.gmail .row .sender { width: 180px; }
.gmail .row .subj { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gmail .row .time { width: 80px; text-align: right; color: #5f6368; }
.shop { font-family: Inter, sans-serif; background: #fff; }
.shop .hdr { background: #146eb4; color: white; padding: 10px 20px; display: flex; gap: 24px; align-items: center; font-size: 14px; }
.shop .product { display: flex; gap: 30px; padding: 32px; }
.shop .product img { width: 320px; height: 320px; background: #eee; border-radius: 8px; display:flex;align-items:center;justify-content:center;color:#888;}
.shop .product h1 { font-size: 22px; margin: 0 0 12px 0; font-weight: 600; }
.shop .product .price { color: #B12704; font-size: 28px; font-weight: 500; }
.shop .product .cta { margin-top: 16px; display: flex; gap: 10px; }
.shop .product .btn { background: #ffd814; padding: 10px 22px; border-radius: 999px; font-weight: 500; font-size: 13px; cursor: pointer; border: 1px solid #fcd200; }
.shop .product .btn.primary { background: #ffa41c; border-color: #ff8f00; }
.shop .toast { position: fixed; top: 20px; right: 20px; background: #067d62; color: white; padding: 12px 16px; border-radius: 8px; font-size: 13px; box-shadow: 0 6px 20px rgba(0,0,0,0.18); }
"""


HN_STORIES = [
    ("Show HN: Witness — observability for browser agents", "github.com/ericcatalano", 842, 2, 186),
    ("The quiet death of Ruby's test-driven-development", "jamesmagnus.com", 514, 11, 412),
    ("Why LLM agents hit a wall at 20 steps", "anthropic.com", 488, 4, 221),
    ("Bun 2.0 released: single-binary Node.js killer", "bun.sh", 412, 3, 308),
    ("A mathematician's lament about modern ML education", "maa.org", 386, 6, 174),
    ("We replaced Kubernetes with 300 lines of Go", "fly.io", 331, 9, 263),
    ("The case for SQLite in production", "planetscale.com", 294, 5, 141),
    ("Linux 6.13 lands with real-time scheduler overhaul", "lwn.net", 247, 7, 96),
    ("Apple's new M5 neural engine teardown", "anandtech.com", 201, 8, 78),
    ("Ask HN: What's your 2026 side project?", "news.ycombinator.com", 178, 4, 422),
]


def render_hn(highlight_idx: int | None = None, clicked: bool = False) -> str:
    rows = []
    for i, (title, host, points, hours, comments) in enumerate(HN_STORIES):
        cls = "story" + (" selected" if highlight_idx == i else "")
        href = f"item?id={40000000 + i}"
        rows.append(
            f"""<div class="{cls}">
              <span class="rank">{i + 1}.</span>
              <span class="title"><a href="{href}">{title}</a> <span class="host">({host})</span></span>
            </div>
            <div class="meta">
              <span class="hl">{points} points</span> by user_{i} {hours}h · {comments} comments
            </div>"""
        )
    if clicked and highlight_idx is not None:
        story = HN_STORIES[highlight_idx]
        # Render the story detail page instead.
        return f"""<html><head><style>{BASE_CSS}</style></head>
<body class="hn">
<div class="topbar"><span class="logo">Y</span><b>Hacker News</b>
<a href="#">new</a><a href="#">past</a><a href="#">comments</a><a href="#">ask</a>
<a href="#">show</a><a href="#">jobs</a></div>
<div class="content">
  <div class="story selected">
    <span class="rank">{highlight_idx+1}.</span>
    <span class="title"><a href="#">{story[0]}</a> <span class="host">({story[1]})</span></span>
  </div>
  <div class="meta"><span class="hl">{story[2]} points</span> by user_{highlight_idx} {story[3]}h · {story[4]} comments</div>
  <div style="margin-top:20px;padding:12px;background:#fff;border:1px solid #eee;font-size:13px;">
    <b>Top comment</b> by <i>arstechnauta</i> — 3h ago<br/>
    Finally something that makes debugging browser agents less painful. The DOM diff per step is exactly what I've been bolting together with print statements.
  </div>
</div></body></html>"""

    return f"""<html><head><style>{BASE_CSS}</style></head>
<body class="hn">
<div class="topbar"><span class="logo">Y</span><b>Hacker News</b>
<a href="#">new</a><a href="#">past</a><a href="#">comments</a><a href="#">ask</a>
<a href="#">show</a><a href="#">jobs</a></div>
<div class="content">{"".join(rows)}</div></body></html>"""


def render_gmail(focused_row: int | None = None, archived: int | None = None) -> str:
    inbox = [
        ("Stripe", "Receipt from your payment (#in_3P...k9A)", "9:14 AM", True),
        ("GitHub", "[ericcatalano/witness] PR #12: add OTEL bridge", "8:02 AM", True),
        ("Linear", "[ENG-342] Trace ingest backpressure on 1M steps", "7:18 AM", False),
        ("Anthropic billing", "Your April invoice is ready", "Apr 17", True),
        ("Vercel", "Deployment succeeded: witness.site@main", "Apr 17", False),
    ]
    rows = []
    for i, (sender, subj, time, unread) in enumerate(inbox):
        if archived is not None and i == archived:
            continue
        cls = "row " + ("unread" if unread else "read")
        if focused_row == i:
            cls += " selected"
        rows.append(
            f'<div class="{cls}"><span class="sender">{sender}</span>'
            f'<span class="subj">{subj}</span><span class="time">{time}</span></div>'
        )
    return f"""<html><head><style>{BASE_CSS}</style></head>
<body class="gmail">
  <div class="header">
    <div class="brand">Mail</div>
    <div class="search">Search mail</div>
  </div>
  <div class="body">
    <div class="side">
      <div class="compose">Compose</div>
      <div class="nav">
        <div class="active">Inbox</div>
        <div>Starred</div><div>Sent</div><div>Drafts</div><div>Spam</div>
      </div>
    </div>
    <div class="mail">{''.join(rows)}</div>
  </div>
</body></html>"""


def render_shop(in_cart: bool = False, toast: bool = False) -> str:
    btn_text = "Added to cart ✓" if in_cart else "Add to Cart"
    toast_html = '<div class="toast">Added 1 item to your cart</div>' if toast else ""
    return f"""<html><head><style>{BASE_CSS}</style></head>
<body class="shop">
  <div class="hdr">
    <b>amazing.com</b>
    <div>Deliver to Eric · Seattle 98101</div>
    <div style="margin-left:auto;">Hello, Eric · Cart ({1 if in_cart else 0})</div>
  </div>
  <div class="product">
    <img alt="product" src=""><!-- gray placeholder --></img>
    <div>
      <h1>Nikon Z7 II Mirrorless Camera (Body Only)</h1>
      <div style="font-size:13px;color:#565959;">by Nikon · 4.6 ★★★★★ (1,204 ratings)</div>
      <div style="margin-top:10px;" class="price">$2,796.95</div>
      <div style="font-size:13px;color:#007600;margin-top:4px;">In Stock · FREE delivery Tuesday</div>
      <div class="cta">
        <div class="btn">Buy Now</div>
        <div class="btn primary">{btn_text}</div>
      </div>
      <div style="margin-top:20px;font-size:13px;color:#111;">
        <b>About this item</b>
        <ul style="color:#111;line-height:1.6;">
          <li>45.7MP FX-format BSI CMOS sensor</li>
          <li>Dual EXPEED 6 processors</li>
          <li>493-point hybrid AF system</li>
        </ul>
      </div>
    </div>
  </div>
  {toast_html}
</body></html>"""


# --- Step templates ---------------------------------------------------------


@dataclass
class StepSpec:
    action_type: str
    action_payload: dict
    url: str
    latency_ms: int
    llm_prompt_tokens: int
    llm_completion_tokens: int
    llm_latency_ms: int
    model: str
    thought: str
    html_before_fn: Any
    html_after_fn: Any
    error: str | None = None


def bu_prompt(task: str, page_title: str, snippet: str) -> str:
    return f"""[system]
You are a browser agent. You execute actions on a web page to accomplish a task.

Available actions:
- click_element_by_index(index: int)
- input_text(index: int, text: str)
- go_to_url(url: str)
- scroll_down(pixels: int)
- extract_content(goal: str)
- done(success: bool, text: str)

Return exactly one action as JSON under the "action" key.

[user]
# TASK
{task}

# CURRENT PAGE
Title: {page_title}
URL snippet: {snippet}

# INTERACTIVE ELEMENTS
[0]<a href="/news">news</a>
[1]<a href="/newest">new</a>
[2]<a href="/ask">ask</a>
[3]<a href="/show">show</a>
[4]<a href="/jobs">jobs</a>
[5]<input type="search" placeholder="Search"/>
[6]<a href="item?id=40000000">Show HN: Witness — observability for browser agents</a>
[7]<a href="item?id=40000001">The quiet death of Ruby's test-driven-development</a>
[8]<a href="item?id=40000002">Why LLM agents hit a wall at 20 steps</a>

# MEMORY
Previous actions succeeded. Progressing toward goal."""


def bu_response(thought: str, action: str, payload: dict) -> str:
    import json

    return f"""[assistant]
<thinking>
{thought}
</thinking>

{{
  "action": [
    {{ "{action}": {json.dumps(payload)} }}
  ]
}}"""


# --- builders ---------------------------------------------------------------


def _browser_screenshot(browser: Browser, html: str) -> bytes:
    ctx = browser.new_context(viewport=VIEW)
    page: Page = ctx.new_page()
    try:
        page.set_content(html, wait_until="domcontentloaded")
        return page.screenshot(full_page=False, type="png")
    finally:
        ctx.close()


def _persist(trace_dir: Path, idx: int, html_b: str, html_a: str, png_b: bytes, png_a: bytes) -> dict:
    paths = {}
    for name, data, ext, sub in [
        ("shot_before", png_b, "png", "screenshots"),
        ("shot_after", png_a, "png", "screenshots"),
        ("dom_before", html_b, "html", "doms"),
        ("dom_after", html_a, "html", "doms"),
    ]:
        rel = f"{sub}/{idx:04d}_{name}.{ext}"
        full = trace_dir / rel
        if isinstance(data, bytes):
            full.write_bytes(data)
        else:
            full.write_text(data, encoding="utf-8")
        # Step schema expects {name}_path; map here so the caller can splat **paths.
        paths[f"{name}_path"] = rel
    return paths


def _write_trace(
    browser: Browser,
    task: str,
    model: str,
    specs: list[StepSpec],
    status: str,
    started_at: datetime,
    running: bool = False,
) -> str:
    storage.init_db()
    trace_id = uuid.uuid4().hex[:12]
    tdir = storage.trace_dir(trace_id)

    total_cost = 0.0
    total_tokens = 0
    total_latency = 0

    with storage.get_session() as s:
        s.add(storage.Trace(
            id=trace_id, task=task, model=model,
            started_at=started_at, status="running",
        ))
        s.commit()

        cursor = started_at
        for i, sp in enumerate(specs):
            cursor = cursor + timedelta(milliseconds=sp.latency_ms + 200)
            html_b = sp.html_before_fn()
            html_a = sp.html_after_fn()
            png_b = _browser_screenshot(browser, html_b)
            png_a = _browser_screenshot(browser, html_a)
            paths = _persist(tdir, i, html_b, html_a, png_b, png_a)

            st = storage.Step(
                trace_id=trace_id,
                idx=i,
                action_type=sp.action_type,
                action_payload=sp.action_payload,
                ts=cursor,
                latency_ms=sp.latency_ms,
                error=sp.error,
                url=sp.url,
                **paths,
            )
            s.add(st)
            s.commit()
            s.refresh(st)

            # Truncate giant prompts realistically — we don't need 50k tokens of text, only the count.
            display_prompt = bu_prompt(task, sp.url, sp.url)
            if sp.llm_prompt_tokens > 5000:
                display_prompt += f"\n\n# [... {sp.llm_prompt_tokens - 500} tokens of page context omitted ...]"

            cost = calculate_cost(sp.model, sp.llm_prompt_tokens, sp.llm_completion_tokens)
            s.add(storage.LLMCall(
                step_id=st.id,
                model=sp.model,
                prompt_tokens=sp.llm_prompt_tokens,
                completion_tokens=sp.llm_completion_tokens,
                cost_usd=cost,
                latency_ms=sp.llm_latency_ms,
                prompt=display_prompt,
                response=bu_response(sp.thought, sp.action_type, sp.action_payload),
                ts=cursor,
            ))
            total_cost += cost
            total_tokens += sp.llm_prompt_tokens + sp.llm_completion_tokens
            total_latency += sp.latency_ms

        t = s.get(storage.Trace, trace_id)
        t.ended_at = None if running else cursor
        t.status = "running" if running else status
        t.step_count = len(specs)
        t.total_cost_usd = total_cost
        t.total_tokens = total_tokens
        t.total_latency_ms = total_latency
        s.add(t)
        s.commit()

    return trace_id


# --- five trace recipes -----------------------------------------------------


def build_hn_short(browser: Browser, started: datetime) -> str:
    task = "Find the top story on Hacker News and report its title and points."
    specs = [
        StepSpec("go_to_url", {"url": "https://news.ycombinator.com"},
                 "https://news.ycombinator.com/", 2340, 1820, 48, 1920,
                 "claude-sonnet-4-5",
                 "I need to visit HN first. I'll use go_to_url.",
                 lambda: "<html><body style='background:#fff;color:#888;font:14px sans-serif;padding:40px'>about:blank</body></html>",
                 lambda: render_hn()),
        StepSpec("extract_content", {"goal": "Identify the top story's title and point count"},
                 "https://news.ycombinator.com/", 980, 2450, 96, 742,
                 "claude-sonnet-4-5",
                 "The list is loaded. Rank 1 is visible at the top. I'll extract the title and points.",
                 lambda: render_hn(),
                 lambda: render_hn(highlight_idx=0)),
        StepSpec("done",
                 {"success": True,
                  "text": "Top story: 'Show HN: Witness — observability for browser agents' with 842 points."},
                 "https://news.ycombinator.com/", 420, 2080, 62, 520,
                 "claude-sonnet-4-5",
                 "I have both fields. Task complete.",
                 lambda: render_hn(highlight_idx=0),
                 lambda: render_hn(highlight_idx=0)),
    ]
    return _write_trace(browser, task, "claude-sonnet-4-5", specs, "success", started)


def build_shop_long(browser: Browser, started: datetime) -> str:
    task = "On amazing.com, find a Nikon Z7 II camera, add it to cart, and start checkout."
    def hn_search(q: str):
        return render_hn()
    specs: list[StepSpec] = []

    # 12 steps: navigate, search, scroll, click, type (qty), scroll, click add to cart, wait, click cart,
    #           click checkout, extract total, done
    specs.append(StepSpec("go_to_url", {"url": "https://amazing.com"},
                          "https://amazing.com/", 1820, 1620, 42, 1210,
                          "claude-sonnet-4-5",
                          "Start with a navigation to amazing.com.",
                          lambda: "<html><body style='background:#fff'></body></html>",
                          lambda: render_shop()))
    specs.append(StepSpec("input_text", {"index": 3, "text": "Nikon Z7 II"},
                          "https://amazing.com/", 520, 1840, 38, 380,
                          "claude-sonnet-4-5",
                          "Type the product name into the search box.",
                          lambda: render_shop(),
                          lambda: render_shop()))
    specs.append(StepSpec("click_element_by_index", {"index": 4, "element": "button[type=submit]"},
                          "https://amazing.com/s?k=nikon+z7+ii", 1140, 1910, 32, 820,
                          "claude-sonnet-4-5",
                          "Submit the search.",
                          lambda: render_shop(),
                          lambda: render_shop()))
    specs.append(StepSpec("click_element_by_index", {"index": 8, "element": "a.product-card"},
                          "https://amazing.com/dp/B08K7YN3KR", 1420, 2120, 58, 1104,
                          "claude-sonnet-4-5",
                          "Open the first product result.",
                          lambda: render_shop(),
                          lambda: render_shop()))
    specs.append(StepSpec("scroll", {"pixels": 400},
                          "https://amazing.com/dp/B08K7YN3KR", 230, 2280, 28, 210,
                          "claude-sonnet-4-5",
                          "Scroll to reveal the Add to Cart button.",
                          lambda: render_shop(),
                          lambda: render_shop()))
    specs.append(StepSpec("click_element_by_index", {"index": 14, "element": "button#add-to-cart"},
                          "https://amazing.com/dp/B08K7YN3KR", 920, 2410, 44, 720,
                          "claude-sonnet-4-5",
                          "Click Add to Cart.",
                          lambda: render_shop(in_cart=False),
                          lambda: render_shop(in_cart=True, toast=True)))
    specs.append(StepSpec("extract_content", {"goal": "Confirm item added to cart"},
                          "https://amazing.com/dp/B08K7YN3KR", 440, 2510, 82, 360,
                          "claude-sonnet-4-5",
                          "Verify cart counter went from 0 to 1.",
                          lambda: render_shop(in_cart=True, toast=True),
                          lambda: render_shop(in_cart=True, toast=False)))
    specs.append(StepSpec("click_element_by_index", {"index": 2, "element": "a#cart-link"},
                          "https://amazing.com/cart", 1210, 2620, 54, 980,
                          "claude-sonnet-4-5",
                          "Navigate to the cart page.",
                          lambda: render_shop(in_cart=True),
                          lambda: render_shop(in_cart=True)))
    specs.append(StepSpec("click_element_by_index", {"index": 19, "element": "button.checkout"},
                          "https://amazing.com/checkout", 1640, 2720, 62, 1230,
                          "claude-sonnet-4-5",
                          "Proceed to checkout.",
                          lambda: render_shop(in_cart=True),
                          lambda: render_shop(in_cart=True)))
    specs.append(StepSpec("extract_content", {"goal": "Read order total"},
                          "https://amazing.com/checkout", 380, 2810, 88, 290,
                          "claude-sonnet-4-5",
                          "Extract the total from the summary box.",
                          lambda: render_shop(in_cart=True),
                          lambda: render_shop(in_cart=True)))
    specs.append(StepSpec("done",
                          {"success": True,
                           "text": "Added Nikon Z7 II ($2,796.95) to cart and reached checkout. Order total with tax: $3,061.23."},
                          "https://amazing.com/checkout", 320, 2920, 74, 280,
                          "claude-sonnet-4-5",
                          "Task complete — checkout page reached.",
                          lambda: render_shop(in_cart=True),
                          lambda: render_shop(in_cart=True)))
    return _write_trace(browser, task, "claude-sonnet-4-5", specs, "success", started)


def build_gmail_error(browser: Browser, started: datetime) -> str:
    task = "Open Gmail, archive the first unread email from GitHub, and confirm."
    specs = [
        StepSpec("go_to_url", {"url": "https://mail.google.com"},
                 "https://mail.google.com/mail/u/0/#inbox", 2110, 1680, 46, 1820,
                 "claude-haiku-4-5",
                 "Navigate to Gmail inbox.",
                 lambda: "<html><body></body></html>",
                 lambda: render_gmail()),
        StepSpec("click_element_by_index", {"index": 1, "element": "div.row.unread"},
                 "https://mail.google.com/mail/u/0/#inbox/FMfcgzGxSXb...",
                 1240, 1940, 38, 980,
                 "claude-haiku-4-5",
                 "Open the first unread GitHub email.",
                 lambda: render_gmail(),
                 lambda: render_gmail(focused_row=1)),
        StepSpec("click_element_by_index", {"index": 9, "element": "button[aria-label='Archive']"},
                 "https://mail.google.com/mail/u/0/#inbox/FMfcgzGxSXb...",
                 820, 2010, 22, 640,
                 "claude-haiku-4-5",
                 "Click the Archive icon.",
                 lambda: render_gmail(focused_row=1),
                 lambda: render_gmail(focused_row=1),
                 error="ElementNotInteractableError: element <button aria-label='Archive'> at index 9 is hidden behind an overlay. Tried for 3000ms."),
        StepSpec("scroll", {"pixels": 200},
                 "https://mail.google.com/mail/u/0/#inbox/FMfcgzGxSXb...",
                 260, 2060, 30, 218,
                 "claude-haiku-4-5",
                 "Maybe a toast is covering the button. Try scrolling slightly to clear it.",
                 lambda: render_gmail(focused_row=1),
                 lambda: render_gmail(focused_row=1)),
        StepSpec("click_element_by_index", {"index": 9, "element": "button[aria-label='Archive']"},
                 "https://mail.google.com/mail/u/0/#inbox/FMfcgzGxSXb...",
                 940, 2120, 36, 740,
                 "claude-haiku-4-5",
                 "Retry the archive click now that the overlay cleared.",
                 lambda: render_gmail(focused_row=1),
                 lambda: render_gmail(archived=1),
                 error="RuntimeError: Agent exhausted max_steps (5) before completing task."),
    ]
    return _write_trace(browser, task, "claude-haiku-4-5", specs, "error", started)


def build_running(browser: Browser, started: datetime) -> str:
    task = "Monitor Hacker News top story for 10 minutes and notify on new comments."
    specs = [
        StepSpec("go_to_url", {"url": "https://news.ycombinator.com"},
                 "https://news.ycombinator.com/", 1920, 1710, 44, 1640,
                 "claude-sonnet-4-5",
                 "Load Hacker News.",
                 lambda: "<html><body></body></html>",
                 lambda: render_hn()),
        StepSpec("click_element_by_index", {"index": 7, "element": "a#top-story"},
                 "https://news.ycombinator.com/item?id=40000000", 1340, 2100, 58, 1020,
                 "claude-sonnet-4-5",
                 "Open the top story to see its comments.",
                 lambda: render_hn(),
                 lambda: render_hn(highlight_idx=0, clicked=True)),
    ]
    return _write_trace(browser, task, "claude-sonnet-4-5", specs, "success", started, running=True)


def build_expensive(browser: Browser, started: datetime) -> str:
    task = "Summarize every comment on 'Show HN: Witness' into a structured JSON report."
    specs = [
        StepSpec("go_to_url", {"url": "https://news.ycombinator.com/item?id=40000000"},
                 "https://news.ycombinator.com/item?id=40000000", 1820, 1620, 42, 1420,
                 "claude-opus-4-5",
                 "Open the comments page.",
                 lambda: "<html><body></body></html>",
                 lambda: render_hn(highlight_idx=0, clicked=True)),
        StepSpec("extract_content",
                 {"goal": "Extract every visible comment with author, depth, and body into an array"},
                 "https://news.ycombinator.com/item?id=40000000", 5210, 48_200, 4_100, 14_820,
                 "claude-opus-4-5",
                 "Need to pass the whole comment tree to the model and structure it. This is a big prompt.",
                 lambda: render_hn(highlight_idx=0, clicked=True),
                 lambda: render_hn(highlight_idx=0, clicked=True)),
        StepSpec("done",
                 {"success": True, "text": "Summarized 186 comments into {sentiment, themes[], top_authors[]}."},
                 "https://news.ycombinator.com/item?id=40000000", 380, 2940, 88, 312,
                 "claude-opus-4-5",
                 "Structured output ready. Done.",
                 lambda: render_hn(highlight_idx=0, clicked=True),
                 lambda: render_hn(highlight_idx=0, clicked=True)),
    ]
    return _write_trace(browser, task, "claude-opus-4-5", specs, "success", started)


# --- main -------------------------------------------------------------------


def wipe() -> None:
    storage.init_db()
    with storage.get_session() as s:
        for c in s.exec(select(storage.LLMCall)).all():
            s.delete(c)
        for st in s.exec(select(storage.Step)).all():
            s.delete(st)
        for t in s.exec(select(storage.Trace)).all():
            s.delete(t)
        s.commit()
    if storage.TRACES_DIR.exists():
        shutil.rmtree(storage.TRACES_DIR, ignore_errors=True)
    storage.TRACES_DIR.mkdir(parents=True, exist_ok=True)


def main() -> None:
    wipe()
    now = datetime.now(timezone.utc)
    print(f"Seeding into {storage.BASE_DIR} …")

    ids: list[tuple[str, str]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ids.append(("short", build_hn_short(browser, now - timedelta(minutes=3))))
            ids.append(("long", build_shop_long(browser, now - timedelta(hours=1, minutes=12))))
            ids.append(("error", build_gmail_error(browser, now - timedelta(hours=4))))
            ids.append(("running", build_running(browser, now - timedelta(seconds=45))))
            ids.append(("expensive", build_expensive(browser, now - timedelta(days=1))))
        finally:
            browser.close()

    print("Seeded:")
    for kind, tid in ids:
        print(f"  {kind:10} {tid}")


if __name__ == "__main__":
    main()
