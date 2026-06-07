#!/usr/bin/env python3
"""Playwright UI crawler for producing LLM-friendly audit reports."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urlparse

from playwright.async_api import (
    BrowserContext,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)


ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = ROOT / ".abracapocus" / "audits" / "runs"

ACTION_TIMEOUT_MS = 3_000
PAGE_TIMEOUT_MS = 5_000
MAX_PAGES = 12
MAX_ELEMENTS_PER_PAGE = 60
MAX_INTERACTIONS_PER_PAGE = 35
MAX_OPTION_INTERACTIONS = 12
MIN_CRAWL_SECONDS = 30
DEFAULT_TIMEOUT_SECONDS = 120
TEST_CHAT_MESSAGE = "Hello, this is a test message"
TEST_USERNAME = "mike"
TEST_PASSWORD = "password123"
TEST_TEXT = "Playwright audit test"


@dataclass
class ElementInfo:
    index: int
    tag: str
    type: str
    role: str
    text: str
    aria_label: str
    placeholder: str
    title: str
    href: str
    selector: str
    visible: bool
    enabled: bool
    checked: bool | None
    value: str
    option_count: int
    category: str


@dataclass
class ScreenshotEntry:
    filename: str
    description: str
    url: str


@dataclass
class CrawlState:
    base_url: str
    run_dir: Path
    screenshot_dir: Path
    dom_dir: Path
    action_log_path: Path
    console_log_path: Path
    network_log_path: Path
    start_time: float = field(default_factory=time.monotonic)
    screenshot_count: int = 0
    dom_count: int = 0
    screenshots: list[ScreenshotEntry] = field(default_factory=list)
    pages_discovered: list[str] = field(default_factory=list)
    visited_pages: set[str] = field(default_factory=set)
    # Tracks (url + heading) combinations so content-only changes are re-explored once.
    visited_states: set[str] = field(default_factory=set)
    queued_pages: list[str] = field(default_factory=list)
    element_inventory: dict[str, list[ElementInfo]] = field(default_factory=dict)
    element_counts: Counter[str] = field(default_factory=Counter)
    console_errors: list[dict[str, str]] = field(default_factory=list)
    network_failures: list[dict[str, str]] = field(default_factory=list)

    def log_action(self, message: str) -> None:
        write_line(self.action_log_path, message)

    def log_console(self, message: str) -> None:
        write_line(self.console_log_path, message)

    def log_network(self, message: str) -> None:
        write_line(self.network_log_path, message)


def write_line(path: Path, message: str) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def normalize_url(url: str) -> str:
    cleaned, _fragment = urldefrag(url)
    return cleaned.rstrip("/") or cleaned


def same_origin(url: str, base_url: str) -> bool:
    parsed = urlparse(url)
    base = urlparse(base_url)
    return (parsed.scheme, parsed.netloc) == (base.scheme, base.netloc)


def safe_filename_part(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip())
    return value.strip("_")[:80] or "state"


def truncate(value: str, limit: int = 180) -> str:
    value = " ".join((value or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def describe_element(element: ElementInfo) -> str:
    label = element.text or element.aria_label or element.placeholder or element.title or element.value
    if not label and element.href:
        label = element.href
    label = truncate(label, 100) or "<unlabeled>"
    attrs = [
        f"tag={element.tag}",
        f"type={element.type or '-'}",
        f"role={element.role or '-'}",
        f"label={label!r}",
        f"selector={element.selector}",
    ]
    return ", ".join(attrs)


def flatten_accessibility_tree(node: dict[str, Any] | None, depth: int = 0) -> list[str]:
    if not node:
        return ["<empty accessibility tree>"]

    role = node.get("role", "")
    name = node.get("name", "")
    value = node.get("value", "")
    line = "  " * depth + f"- {role}"
    if name:
        line += f": {name}"
    if value not in ("", None):
        line += f" value={value}"

    lines = [line]
    for child in node.get("children", []) or []:
        lines.extend(flatten_accessibility_tree(child, depth + 1))
    return lines


def cdp_ax_value(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("value", ""))
    if value is None:
        return ""
    return str(value)


def flatten_cdp_accessibility_tree(nodes: list[dict[str, Any]]) -> list[str]:
    if not nodes:
        return ["<empty accessibility tree>"]

    children_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    node_by_id: dict[str, dict[str, Any]] = {}
    child_ids: set[str] = set()
    for node in nodes:
        node_id = str(node.get("nodeId", ""))
        node_by_id[node_id] = node
        for child_id in node.get("childIds", []) or []:
            child_ids.add(str(child_id))

    for node in nodes:
        node_id = str(node.get("nodeId", ""))
        for child_id in node.get("childIds", []) or []:
            child = node_by_id.get(str(child_id))
            if child:
                children_by_id[node_id].append(child)

    roots = [node for node in nodes if str(node.get("nodeId", "")) not in child_ids]
    if not roots:
        roots = nodes[:1]

    lines: list[str] = []

    def walk(node: dict[str, Any], depth: int) -> None:
        role = cdp_ax_value(node.get("role")) or "unknown"
        name = cdp_ax_value(node.get("name"))
        value = cdp_ax_value(node.get("value"))
        line = "  " * depth + f"- {role}"
        if name:
            line += f": {name}"
        if value:
            line += f" value={value}"
        lines.append(line)
        for child in children_by_id.get(str(node.get("nodeId", "")), []):
            walk(child, depth + 1)

    for root in roots:
        walk(root, 0)
    return lines


async def accessibility_snapshot(page: Page) -> tuple[list[str], Any]:
    accessibility = getattr(page, "accessibility", None)
    if accessibility is not None:
        snapshot = await accessibility.snapshot()
        return flatten_accessibility_tree(snapshot), snapshot

    session = await page.context.new_cdp_session(page)
    try:
        snapshot = await session.send("Accessibility.getFullAXTree")
    finally:
        await session.detach()
    nodes = snapshot.get("nodes", [])
    return flatten_cdp_accessibility_tree(nodes), snapshot


async def settle_page(page: Page) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        pass
    await page.wait_for_timeout(250)


async def capture_screenshot(state: CrawlState, page: Page, description: str) -> None:
    state.screenshot_count += 1
    filename = f"{state.screenshot_count:04d}_{safe_filename_part(description)}.png"
    path = state.screenshot_dir / filename
    try:
        await page.screenshot(path=str(path), full_page=True, timeout=PAGE_TIMEOUT_MS)
        state.screenshots.append(
            ScreenshotEntry(filename=f"screenshots/{filename}", description=description, url=page.url)
        )
        state.log_action(f"SCREENSHOT {filename}: {description} at {page.url}")
    except Exception as exc:  # noqa: BLE001 - crawler must not die on capture failures.
        state.log_action(f"SCREENSHOT FAILED {description}: {type(exc).__name__}: {exc}")


async def dump_accessibility_tree(state: CrawlState, page: Page, description: str) -> None:
    state.dom_count += 1
    filename = f"{state.dom_count:04d}_{safe_filename_part(description)}.txt"
    path = state.dom_dir / filename
    try:
        tree_lines, snapshot = await accessibility_snapshot(page)
        lines = [
            f"URL: {page.url}",
            f"Description: {description}",
            "",
            *tree_lines,
            "",
            "Raw JSON:",
            json.dumps(snapshot, indent=2, ensure_ascii=False),
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
        state.log_action(f"DOM SNAPSHOT dom_snapshots/{filename}: {description} at {page.url}")
    except Exception as exc:  # noqa: BLE001
        state.log_action(f"DOM SNAPSHOT FAILED {description}: {type(exc).__name__}: {exc}")


async def get_page_state_key(page: Page) -> str:
    """Return a string uniquely identifying the current UI state: URL + visible heading."""
    url = normalize_url(page.url)
    try:
        heading = await page.evaluate(
            """() => {
                const el = document.querySelector('h1, h2, [role="heading"]');
                if (el) return (el.textContent || '').trim().slice(0, 120);
                return document.title || '';
            }"""
        )
    except Exception:  # noqa: BLE001
        heading = ""
    return f"{url}|||{heading}"


async def discover_elements(page: Page) -> list[ElementInfo]:
    script = """
    () => {
      const interactiveSelector = [
        'button',
        'a[href]',
        'input',
        'select',
        'textarea',
        '[role="button"]',
        '[role="tab"]',
        '[role="link"]',
        '[role="menuitem"]',
        '[role="option"]',
        '[onclick]',
        '[tabindex]:not([tabindex="-1"])',
        'summary',
        'details > summary',
        '[contenteditable="true"]'
      ].join(',');

      function cssEscape(value) {
        if (window.CSS && window.CSS.escape) return window.CSS.escape(value);
        return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\\\$&');
      }

      function selectorFor(el) {
        if (el.id) return `#${cssEscape(el.id)}`;
        const testId = el.getAttribute('data-testid');
        if (testId) return `[data-testid="${testId.replaceAll('"', '\\\\"')}"]`;
        const dataTest = el.getAttribute('data-test');
        if (dataTest) return `[data-test="${dataTest.replaceAll('"', '\\\\"')}"]`;

        const parts = [];
        let current = el;
        while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 5) {
          const parent = current.parentElement;
          let part = current.tagName.toLowerCase();
          const role = current.getAttribute('role');
          const name = current.getAttribute('name');
          if (role) part += `[role="${role.replaceAll('"', '\\\\"')}"]`;
          if (name) part += `[name="${name.replaceAll('"', '\\\\"')}"]`;
          if (parent) {
            const sameTag = Array.from(parent.children).filter(
              child => child.tagName === current.tagName
            );
            if (sameTag.length > 1) {
              part += `:nth-of-type(${sameTag.indexOf(current) + 1})`;
            }
          }
          parts.unshift(part);
          current = parent;
        }
        return parts.join(' > ');
      }

      function isVisible(el) {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.visibility !== 'hidden' &&
          style.display !== 'none' &&
          rect.width > 0 &&
          rect.height > 0;
      }

      function isClickableDiv(el) {
        if (el.tagName.toLowerCase() !== 'div') return false;
        const style = window.getComputedStyle(el);
        return style.cursor === 'pointer' ||
          el.onclick ||
          el.getAttribute('role') === 'button' ||
          el.getAttribute('tabindex') !== null;
      }

      function categoryFor(el) {
        const tag = el.tagName.toLowerCase();
        const type = (el.getAttribute('type') || '').toLowerCase();
        const role = (el.getAttribute('role') || '').toLowerCase();
        if (tag === 'a' || role === 'link') return 'links';
        if (tag === 'select') return 'selects';
        if (tag === 'textarea' || el.isContentEditable) return 'inputs';
        if (tag === 'input') {
          if (type === 'range') return 'sliders';
          if (['button', 'submit', 'reset', 'image'].includes(type)) return 'buttons';
          return 'inputs';
        }
        if (role === 'tab') return 'tabs';
        if (tag === 'button' || role === 'button' || role === 'menuitem' || tag === 'summary') {
          return 'buttons';
        }
        return 'clickables';
      }

      const nodes = Array.from(document.querySelectorAll(interactiveSelector))
        .concat(Array.from(document.querySelectorAll('div')).filter(isClickableDiv));

      const seen = new Set();
      return nodes
        .filter(el => {
          if (seen.has(el)) return false;
          seen.add(el);
          return isVisible(el);
        })
        .map((el, index) => {
          const tag = el.tagName.toLowerCase();
          const type = (el.getAttribute('type') || '').toLowerCase();
          return {
            index,
            tag,
            type,
            role: el.getAttribute('role') || '',
            text: (el.innerText || el.textContent || '').trim(),
            aria_label: el.getAttribute('aria-label') || '',
            placeholder: el.getAttribute('placeholder') || '',
            title: el.getAttribute('title') || '',
            href: el.href || el.getAttribute('href') || '',
            selector: selectorFor(el),
            visible: isVisible(el),
            enabled: !el.disabled && el.getAttribute('aria-disabled') !== 'true',
            checked: typeof el.checked === 'boolean' ? el.checked : null,
            value: el.value || '',
            option_count: tag === 'select' ? el.options.length : 0,
            category: categoryFor(el)
          };
        });
    }
    """
    raw_elements = await page.evaluate(script)
    elements: list[ElementInfo] = []
    for raw in raw_elements:
        elements.append(
            ElementInfo(
                index=int(raw.get("index", 0)),
                tag=str(raw.get("tag", "")),
                type=str(raw.get("type", "")),
                role=str(raw.get("role", "")),
                text=str(raw.get("text", "")),
                aria_label=str(raw.get("aria_label", "")),
                placeholder=str(raw.get("placeholder", "")),
                title=str(raw.get("title", "")),
                href=str(raw.get("href", "")),
                selector=str(raw.get("selector", "")),
                visible=bool(raw.get("visible", False)),
                enabled=bool(raw.get("enabled", False)),
                checked=raw.get("checked"),
                value=str(raw.get("value", "")),
                option_count=int(raw.get("option_count", 0) or 0),
                category=str(raw.get("category", "clickables")),
            )
        )
    return elements


def log_element_inventory(state: CrawlState, url: str, elements: list[ElementInfo]) -> None:
    state.element_inventory[url] = elements
    state.element_counts.update(element.category for element in elements)
    state.log_action(f"FOUND {len(elements)} interactive elements on {url}")
    for element in elements:
        state.log_action(f"  [{element.index:03d}] {element.category}: {describe_element(element)}")


def interaction_priority(element: ElementInfo) -> tuple[int, int]:
    haystack = " ".join(
        [
            element.text,
            element.aria_label,
            element.placeholder,
            element.title,
            element.selector,
            element.type,
            element.role,
        ]
    ).lower()
    priority = 50
    if any(term in haystack for term in ["login", "sign in", "username", "password"]):
        priority = 0
    elif any(term in haystack for term in ["chat", "message", "prompt", "send"]):
        priority = 5
    elif any(term in haystack for term in ["companion", "persona", "character"]):
        priority = 10
    elif any(term in haystack for term in ["setting", "gear", "preferences"]):
        priority = 15
    elif element.category == "tabs":
        priority = 20
    elif any(term in haystack for term in ["new chat", "+", "add", "create"]):
        priority = 25
    elif element.category in {"selects", "sliders"}:
        priority = 30
    elif element.category in {"links", "buttons"}:
        priority = 35
    return priority, element.index


def should_submit_after_input(element: ElementInfo) -> bool:
    haystack = " ".join([element.text, element.aria_label, element.placeholder, element.type]).lower()
    return any(term in haystack for term in ["chat", "message", "prompt", "password", "username"])


async def focus_element(page: Page, element: ElementInfo):
    locator = page.locator(element.selector).first
    await locator.wait_for(state="visible", timeout=ACTION_TIMEOUT_MS)
    return locator


async def maybe_queue_current_url(state: CrawlState, page: Page) -> None:
    url = normalize_url(page.url)
    if not same_origin(url, state.base_url):
        return
    if url not in state.pages_discovered:
        state.pages_discovered.append(url)
    if url not in state.visited_pages and url not in state.queued_pages:
        state.queued_pages.append(url)


async def return_to_page(page: Page, original_url: str, state: CrawlState) -> None:
    if normalize_url(page.url) == normalize_url(original_url):
        return
    await maybe_queue_current_url(state, page)
    try:
        await page.go_back(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        await settle_page(page)
    except Exception:
        try:
            await page.goto(original_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
            await settle_page(page)
        except Exception as exc:  # noqa: BLE001
            state.log_action(f"RETURN FAILED to {original_url}: {type(exc).__name__}: {exc}")


async def type_in_input(page: Page, element: ElementInfo, state: CrawlState) -> None:
    locator = await focus_element(page, element)
    label = " ".join([element.type, element.placeholder, element.aria_label, element.text]).lower()
    text = TEST_TEXT
    if "password" in label or element.type == "password":
        text = TEST_PASSWORD
    elif "username" in label or "user name" in label or "email" in label or "login" in label:
        text = TEST_USERNAME
    elif "chat" in label or "message" in label or "prompt" in label:
        text = TEST_CHAT_MESSAGE

    try:
        await locator.fill("")
    except Exception:
        await locator.click(timeout=ACTION_TIMEOUT_MS)
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")

    await locator.fill(text, timeout=ACTION_TIMEOUT_MS)
    state.log_action(f"TYPE into {describe_element(element)}: {text!r}")
    await capture_screenshot(state, page, f"type element {element.index}")

    if should_submit_after_input(element):
        try:
            await locator.press("Enter", timeout=ACTION_TIMEOUT_MS)
            await settle_page(page)
            state.log_action(f"SUBMIT via Enter from {describe_element(element)}")
            await capture_screenshot(state, page, f"submit element {element.index}")
        except Exception as exc:  # noqa: BLE001
            state.log_action(
                f"SUBMIT FAILED for {describe_element(element)}: {type(exc).__name__}: {exc}"
            )


async def interact_with_select(page: Page, element: ElementInfo, state: CrawlState) -> None:
    locator = await focus_element(page, element)
    await locator.click(timeout=ACTION_TIMEOUT_MS)
    await capture_screenshot(state, page, f"open select {element.index}")

    options = await locator.evaluate(
        """el => Array.from(el.options).map(option => ({
          value: option.value,
          label: option.label || option.textContent || option.value
        }))"""
    )
    for option in options[:MAX_OPTION_INTERACTIONS]:
        value = str(option.get("value", ""))
        label = truncate(str(option.get("label", value)), 80)
        try:
            await locator.select_option(value=value, timeout=ACTION_TIMEOUT_MS)
            await settle_page(page)
            state.log_action(f"SELECT {value!r} ({label}) in {describe_element(element)}")
            await capture_screenshot(state, page, f"select {element.index} {label}")
        except Exception as exc:  # noqa: BLE001
            state.log_action(
                f"SELECT FAILED {value!r} in {describe_element(element)}: "
                f"{type(exc).__name__}: {exc}"
            )


async def interact_with_slider(page: Page, element: ElementInfo, state: CrawlState) -> None:
    locator = await focus_element(page, element)
    bounds = await locator.evaluate(
        """el => ({
          min: Number(el.min || 0),
          max: Number(el.max || 100),
          step: Number(el.step || 1)
        })"""
    )
    min_value = float(bounds.get("min", 0))
    max_value = float(bounds.get("max", 100))
    values = [min_value, min_value + ((max_value - min_value) / 2), max_value]
    for value in values:
        try:
            await locator.evaluate(
                """(el, value) => {
                  el.value = String(value);
                  el.dispatchEvent(new Event('input', { bubbles: true }));
                  el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                value,
            )
            await settle_page(page)
            state.log_action(f"SLIDER set {describe_element(element)} to {value:g}")
            await capture_screenshot(state, page, f"slider {element.index} {value:g}")
        except Exception as exc:  # noqa: BLE001
            state.log_action(
                f"SLIDER FAILED {describe_element(element)} to {value:g}: "
                f"{type(exc).__name__}: {exc}"
            )


async def click_element(page: Page, element: ElementInfo, state: CrawlState, original_url: str) -> None:
    locator = await focus_element(page, element)
    old_url = page.url
    await locator.click(timeout=ACTION_TIMEOUT_MS)
    await settle_page(page)
    state.log_action(f"CLICK {describe_element(element)}: {old_url} -> {page.url}")
    await capture_screenshot(state, page, f"click element {element.index}")
    await maybe_queue_current_url(state, page)
    # Navigation back to original_url (if needed) is handled by explore_state after this returns.


async def interact_with_element(page: Page, element: ElementInfo, state: CrawlState) -> None:
    original_url = page.url
    try:
        if not element.enabled:
            state.log_action(f"SKIP disabled {describe_element(element)}")
            return
        if element.category == "inputs" and element.type not in {"checkbox", "radio", "file"}:
            await type_in_input(page, element, state)
        elif element.category == "selects":
            await interact_with_select(page, element, state)
        elif element.category == "sliders":
            await interact_with_slider(page, element, state)
        else:
            await click_element(page, element, state, original_url)
    except Exception as exc:  # noqa: BLE001
        state.log_action(f"FAILED {describe_element(element)}: {type(exc).__name__}: {exc}")
        try:
            await return_to_page(page, original_url, state)
        except Exception as return_exc:  # noqa: BLE001
            state.log_action(
                f"FAILED returning after element {element.index}: "
                f"{type(return_exc).__name__}: {return_exc}"
            )


async def attempt_login(
    page: Page,
    state: CrawlState,
    username: str,
    password: str,
) -> bool:
    """Detect a login form, fill credentials, submit, and wait for success.

    Looks for (email or text input) + password input. Returns True if a form
    was found and submitted, regardless of whether login actually succeeded.
    """
    try:
        text_sel = 'input[type="email"], input[type="text"]'
        pass_sel = 'input[type="password"]'

        if not (await page.locator(text_sel).count() > 0
                and await page.locator(pass_sel).count() > 0):
            state.log_action("LOGIN: No login form detected — skipping")
            return False

        state.log_action(f"LOGIN: Form found — submitting as {username!r}")

        email_loc = page.locator(text_sel).first
        pass_loc = page.locator(pass_sel).first

        await email_loc.clear(timeout=ACTION_TIMEOUT_MS)
        await email_loc.fill(username, timeout=ACTION_TIMEOUT_MS)

        await pass_loc.clear(timeout=ACTION_TIMEOUT_MS)
        await pass_loc.fill(password, timeout=ACTION_TIMEOUT_MS)

        pre_url = page.url

        submit_sel = 'button[type="submit"], input[type="submit"]'
        if await page.locator(submit_sel).count() > 0:
            await page.locator(submit_sel).first.click(timeout=ACTION_TIMEOUT_MS)
        else:
            await pass_loc.press("Enter")

        # Wait up to 5s for URL change indicating successful login.
        try:
            await page.wait_for_url(lambda u: u != pre_url, timeout=5_000)
        except PlaywrightTimeoutError:
            # Some apps authenticate without changing the URL (token + DOM update).
            await page.wait_for_timeout(1_000)

        await settle_page(page)
        state.log_action(f"LOGIN: Post-submit URL: {page.url}")
        return True

    except Exception as exc:  # noqa: BLE001
        state.log_action(f"LOGIN FAILED: {type(exc).__name__}: {exc}")
        return False


async def explore_state(page: Page, state: CrawlState, deadline: float) -> None:
    """Discover and interact with all elements on the current UI state.

    A "state" is identified by URL + visible heading, so clicking a tab that
    changes the heading (but not the URL) is treated as a new state to explore.
    Recursively explores new states without revisiting ones already seen.
    """
    state_key = await get_page_state_key(page)
    if state_key in state.visited_states:
        return
    state.visited_states.add(state_key)

    current_url = page.url
    normalized = normalize_url(current_url)

    # Track URL-level visits so process_page skips already-explored pages.
    if normalized not in state.pages_discovered:
        state.pages_discovered.append(normalized)
    state.visited_pages.add(normalized)

    state.log_action(f"EXPLORE STATE: {state_key!r}")
    await capture_screenshot(state, page, f"state {len(state.visited_states)}")
    await dump_accessibility_tree(state, page, f"state {len(state.visited_states)}")

    try:
        elements = await discover_elements(page)
    except Exception as exc:  # noqa: BLE001
        state.log_action(f"ELEMENT DISCOVERY FAILED: {type(exc).__name__}: {exc}")
        return

    log_element_inventory(state, normalized, elements)
    prioritized = sorted(elements[:MAX_ELEMENTS_PER_PAGE], key=interaction_priority)

    for element in prioritized[:MAX_INTERACTIONS_PER_PAGE]:
        if time.monotonic() > deadline:
            state.log_action("TIMEOUT: deadline reached — stopping interactions")
            break

        # Recover to base page for this state before each interaction.
        if normalize_url(page.url) != normalize_url(current_url):
            await return_to_page(page, current_url, state)

        pre_key = await get_page_state_key(page)
        await interact_with_element(page, element, state)
        post_key = await get_page_state_key(page)

        # If the interaction moved us to a new UI state, explore it before continuing.
        if post_key != pre_key and post_key not in state.visited_states:
            if time.monotonic() < deadline:
                await explore_state(page, state, deadline)

        # Return to the page we were crawling so the next element is in context.
        if normalize_url(page.url) != normalize_url(current_url):
            await return_to_page(page, current_url, state)


async def process_page(page: Page, state: CrawlState, url: str, deadline: float) -> None:
    normalized = normalize_url(url)
    if normalized in state.visited_pages:
        return
    # visited_pages and pages_discovered are updated inside explore_state.

    state.log_action(f"VISIT {url}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        await settle_page(page)
    except Exception as exc:  # noqa: BLE001
        state.log_action(f"VISIT FAILED {url}: {type(exc).__name__}: {exc}")
        return

    await explore_state(page, state, deadline)


def attach_listeners(page: Page, state: CrawlState) -> None:
    async def on_response(response) -> None:
        try:
            status = response.status
            if status >= 400:
                entry = {
                    "url": response.url,
                    "status": str(status),
                    "page": page.url,
                    "method": response.request.method,
                }
                state.network_failures.append(entry)
                state.log_network(
                    f"HTTP {status} {response.request.method} {response.url} on page {page.url}"
                )
        except Exception as exc:  # noqa: BLE001
            state.log_network(f"RESPONSE LISTENER FAILED: {type(exc).__name__}: {exc}")

    async def on_request_failed(request) -> None:
        try:
            failure = request.failure or "request failed"
            entry = {
                "url": request.url,
                "status": "failed",
                "page": page.url,
                "method": request.method,
                "error": str(failure),
            }
            state.network_failures.append(entry)
            state.log_network(f"FAILED {request.method} {request.url}: {failure} on page {page.url}")
        except Exception as exc:  # noqa: BLE001
            state.log_network(f"REQUESTFAILED LISTENER FAILED: {type(exc).__name__}: {exc}")

    def on_console(message) -> None:
        text = truncate(message.text, 500)
        entry = {"type": message.type, "text": text, "page": page.url}
        if message.type in {"error", "warning"}:
            state.console_errors.append(entry)
        state.log_console(f"{message.type.upper()} on {page.url}: {text}")

    page.on("console", on_console)
    page.on("response", lambda response: asyncio.create_task(on_response(response)))
    page.on("requestfailed", lambda request: asyncio.create_task(on_request_failed(request)))


async def create_page(context: BrowserContext, state: CrawlState) -> Page:
    page = await context.new_page()
    page.set_default_timeout(ACTION_TIMEOUT_MS)
    page.set_default_navigation_timeout(PAGE_TIMEOUT_MS)
    attach_listeners(page, state)
    return page


def markdown_list(items: list[str], empty: str = "_None observed._") -> str:
    if not items:
        return empty
    return "\n".join(f"- {item}" for item in items)


def markdown_lines(items: list[str], empty: str = "_None observed._") -> str:
    if not items:
        return empty
    return "\n".join(items)


def element_summary_line(element: ElementInfo) -> str:
    label = truncate(
        element.text or element.aria_label or element.placeholder or element.title or element.value,
        100,
    )
    if not label:
        label = "<unlabeled>"
    return f"- [{element.index:03d}] {element.category}: {label} (`{element.selector}`)"


def write_summary(state: CrawlState) -> None:
    duration = time.monotonic() - state.start_time
    counts = defaultdict(int, state.element_counts)
    timestamp = datetime.now().isoformat(timespec="seconds")

    console_lines = []
    for entry in state.console_errors:
        if entry["type"] == "error":
            console_lines.append(f"- `error` on {entry['page']}: {entry['text']}")

    network_lines = []
    for entry in state.network_failures:
        status = entry.get("status", "failed")
        method = entry.get("method", "")
        url = entry.get("url", "")
        page = entry.get("page", "")
        error = entry.get("error", "")
        suffix = f" ({error})" if error else ""
        network_lines.append(f"- `{status}` {method} {url} on {page}{suffix}")

    screenshot_lines = [
        f"{index}. `{entry.filename}` - {entry.description} ({entry.url})"
        for index, entry in enumerate(state.screenshots, start=1)
    ]

    inventory_sections = []
    for url, elements in state.element_inventory.items():
        inventory_sections.append(f"### {url}")
        if elements:
            inventory_sections.extend(element_summary_line(element) for element in elements)
        else:
            inventory_sections.append("_No interactive elements discovered._")
        inventory_sections.append("")

    summary = f"""# UI Audit Report
**URL:** {state.base_url}
**Date:** {timestamp}
**Duration:** {duration:.1f}s
**States explored:** {len(state.visited_states)}

## Pages Discovered
{markdown_list(state.pages_discovered)}

## Interactive Elements Found
- {counts['buttons']} buttons
- {counts['links']} links
- {counts['inputs']} inputs
- {counts['selects']} selects
- {counts['tabs']} tabs
- {counts['sliders']} sliders
- {counts['clickables']} other clickables

## Console Errors
{markdown_lines(console_lines)}

## Network Failures
{markdown_lines(network_lines)}

## Screenshots Index
{markdown_lines(screenshot_lines)}

## Element Inventory
{chr(10).join(inventory_sections).rstrip()}
"""
    (state.run_dir / "summary.md").write_text(summary, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crawl a running web UI and create an LLM-friendly audit report."
    )
    parser.add_argument("url", help="Starting URL, for example http://localhost:5173")
    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode")
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES, help="Maximum routes to crawl")
    parser.add_argument("--user", default=TEST_USERNAME, help="Login username (default: mike)")
    parser.add_argument("--password", default=TEST_PASSWORD, help="Login password (default: password123)")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Maximum crawl time in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    return parser.parse_args()


async def crawl(
    url: str,
    headed: bool,
    max_pages: int,
    username: str,
    password: str,
    timeout: int,
) -> Path:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = RUNS_ROOT / timestamp
    screenshot_dir = run_dir / "screenshots"
    dom_dir = run_dir / "dom_snapshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    dom_dir.mkdir(parents=True, exist_ok=True)

    state = CrawlState(
        base_url=normalize_url(url),
        run_dir=run_dir,
        screenshot_dir=screenshot_dir,
        dom_dir=dom_dir,
        action_log_path=run_dir / "actions.log",
        console_log_path=run_dir / "console.log",
        network_log_path=run_dir / "network_errors.log",
        queued_pages=[],
    )

    for path in [state.action_log_path, state.console_log_path, state.network_log_path]:
        path.write_text("", encoding="utf-8")

    deadline = state.start_time + timeout

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=not headed)
        context = await browser.new_context(viewport={"width": 1440, "height": 1000})
        page = await create_page(context, state)
        try:
            # ── Initial navigation ──────────────────────────────────────
            state.log_action(f"NAVIGATE to {url}")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
                await settle_page(page)
            except Exception as exc:  # noqa: BLE001
                state.log_action(f"INITIAL NAVIGATE FAILED: {type(exc).__name__}: {exc}")

            await capture_screenshot(state, page, "initial state")

            # ── Login ───────────────────────────────────────────────────
            logged_in = await attempt_login(page, state, username, password)
            if logged_in:
                await capture_screenshot(state, page, "post-login state")
                await dump_accessibility_tree(state, page, "post-login state")

            # ── Explore from the current post-(login|start) state ───────
            # Mark the original URL as visited so the main loop doesn't re-navigate to it.
            original_normalized = normalize_url(url)
            state.visited_pages.add(original_normalized)
            if original_normalized not in state.pages_discovered:
                state.pages_discovered.append(original_normalized)

            await explore_state(page, state, deadline)

            # ── Continue crawling newly discovered URLs ──────────────────
            while time.monotonic() < deadline:
                elapsed = time.monotonic() - state.start_time
                if state.queued_pages and len(state.visited_pages) < max_pages:
                    next_url = state.queued_pages.pop(0)
                    await process_page(page, state, next_url, deadline)
                elif elapsed < MIN_CRAWL_SECONDS:
                    # Queue exhausted but we haven't hit the minimum time yet;
                    # pause briefly and allow queued_pages to grow from any
                    # in-flight async tasks before giving up.
                    await asyncio.sleep(1.0)
                else:
                    break

        finally:
            await context.close()
            await browser.close()

    write_summary(state)
    return run_dir


def main() -> int:
    args = parse_args()
    try:
        run_dir = asyncio.run(
            crawl(args.url, args.headed, args.max_pages, args.user, args.password, args.timeout)
        )
    except PlaywrightError as exc:
        print(f"Playwright failed: {exc}")
        return 1
    except KeyboardInterrupt:
        print("Interrupted.")
        return 130

    print(f"UI crawl complete: {run_dir}")
    print(f"Summary: {run_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
