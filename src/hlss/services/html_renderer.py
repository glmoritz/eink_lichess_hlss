"""HTML to PNG rendering utilities."""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

DEFAULT_CDP_URL = "ws://localhost:3000"


def render_html_to_png(
    html: str,
    width: int = 800,
    height: int = 480,
    base_url: Optional[str] = None,
    cdp_url: Optional[str] = None,
) -> bytes:
    """Render an HTML string to PNG bytes.

    Args:
        html: HTML markup to render.
        width: Viewport width in pixels.
        height: Viewport height in pixels.
        base_url: Optional base URL for resolving relative assets.

    Returns:
        PNG image bytes.
    """
    return _run_async(
        _render_html_to_png_async(
            html=html,
            width=width,
            height=height,
            base_url=base_url,
            cdp_url=cdp_url,
        )
    )


def _run_async(coro):
    """Run an async coroutine from sync context safely."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()


async def _render_html_to_png_async(
    html: str,
    width: int = 800,
    height: int = 480,
    base_url: Optional[str] = None,
    cdp_url: Optional[str] = None,
) -> bytes:
    """Async HTML rendering via Playwright."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("playwright is required for HTML rendering") from exc

    async with async_playwright() as playwright:
        resolved_cdp = cdp_url or os.getenv("PLAYWRIGHT_CDP_URL") or DEFAULT_CDP_URL
        if resolved_cdp:
            browser = await playwright.chromium.connect_over_cdp(resolved_cdp)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()
            await page.set_viewport_size({"width": width, "height": height})
        else:
            browser = await playwright.chromium.launch()
            page = await browser.new_page(viewport={"width": width, "height": height})

        await page.set_content(html, wait_until="networkidle")
        png_bytes = await page.screenshot(type="png", full_page=False)
        await browser.close()
        return png_bytes


def render_html_file_to_png(
    html_path: str | Path,
    width: int = 800,
    height: int = 480,
    cdp_url: Optional[str] = None,
    replacements: Optional[dict[str, str]] = None,
) -> bytes:
    """Render an HTML file to PNG bytes.

    Args:
        html_path: Path to the HTML file.
        width: Viewport width in pixels.
        height: Viewport height in pixels.

    Returns:
        PNG image bytes.
    """
    path = Path(html_path)
    html = path.read_text(encoding="utf-8")
    if replacements:
        for key, value in replacements.items():
            html = html.replace(key, value)
    base_url = path.parent.as_uri()
    return render_html_to_png(
        html,
        width=width,
        height=height,
        base_url=base_url,
        cdp_url=cdp_url,
    )
