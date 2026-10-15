import asyncio
import os
import sys
import time
import re
from urllib.parse import quote
from typing import Dict, Any
from fastapi import FastAPI, Query
from bs4 import BeautifulSoup
from patchright.async_api import async_playwright

app = FastAPI(title="RLTitles API")

_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL = 300  # 5-minute cache

@app.get("/")
def index():
    return {"status": "online", "service": "RLTitles Microservice"}

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.get("/titles")
async def get_titles(platform: str = Query(...), username: str = Query(...)):
    platform_norm = platform.strip().lower()
    cache_key = f"{platform_norm}:{username.lower()}"

    cached = _cache.get(cache_key)
    if cached and (time.time() - cached["timestamp"] < CACHE_TTL):
        print(f"[{cache_key}] Returning cached titles", flush=True)
        return cached["data"]

    encoded_user = quote(username)
    profile_url = f"https://rlstats.net/profile/{platform_norm.capitalize()}/{encoded_user}"

    # Use headless=False if display is available (Xvfb provides DISPLAY=:99)
    use_headless = "DISPLAY" not in os.environ
    print(f"[{cache_key}] Fetching {profile_url} (headless={use_headless}, DISPLAY={os.environ.get('DISPLAY')})", flush=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=use_headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-extensions",
                "--disable-default-apps",
                "--renderer-process-limit=2",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1024,768"
            ]
        )
        try:
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
                viewport={"width": 1024, "height": 768}
            )
            page = await context.new_page()

            # Abort heavy third-party ad networks (proper async handler)
            async def handle_route(route):
                try:
                    url = route.request.url.lower()
                    if any(ad in url for ad in ["googlesyndication", "doubleclick", "google-analytics", "googletagmanager", "adservice", "amazon-adsystem"]):
                        await route.abort()
                    else:
                        await route.continue_()
                except Exception:
                    try:
                        await route.continue_()
                    except Exception:
                        pass

            await page.route("**/*", handle_route)

            try:
                await page.goto(profile_url, timeout=30000, wait_until="domcontentloaded")
            except Exception as nav_err:
                print(f"[{cache_key}] Navigation notice: {nav_err}", flush=True)

            loaded = False
            already_clicked = False
            for i in range(30):
                await page.wait_for_timeout(1000)
                try:
                    title = await page.title()
                    print(f"[{cache_key}] Sec {i+1}: Title='{title}'", flush=True)

                    if "loading" in title.lower():
                        print(f"[{cache_key}] Turnstile cleared! Waiting for profile to render...", flush=True)

                    # Check for completed profile load
                    if "profile | rlstats" in title.lower() and not title.lower().startswith("loading"):
                        loaded = True
                        break
                    elif "rlstats" in title.lower() and "moment" not in title.lower() and not title.lower().startswith("loading"):
                        loaded = True
                        break
                except Exception:
                    pass

                # Click the Turnstile checkbox ONCE at second 4 if still on "Just a moment..."
                if not already_clicked and i >= 3:
                    try:
                        for iframe in await page.query_selector_all("iframe"):
                            src = await iframe.get_attribute("src") or ""
                            if "challenges.cloudflare" in src or "turnstile" in src:
                                box = await iframe.bounding_box()
                                if box:
                                    cx = box["x"] + 30
                                    cy = box["y"] + box["height"] / 2
                                    print(f"[{cache_key}] Clicking Turnstile widget at ({cx}, {cy}) (one-shot)", flush=True)
                                    await page.mouse.click(cx, cy)
                                    already_clicked = True
                                    break
                    except Exception as click_err:
                        print(f"[{cache_key}] Click attempt error: {click_err}", flush=True)

            if not loaded:
                print(f"[{cache_key}] Failed: page did not load in time", flush=True)
                return {
                    "found": False,
                    "error": f"Could not load profile for {username} on {platform_norm.upper()}. Profile may be private or not found."
                }

            # Wait for any in-flight navigation to settle
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass

            # Safe HTML retrieval with retry in case navigation was still finalizing
            html = ""
            for attempt in range(5):
                try:
                    await page.wait_for_timeout(500)
                    html = await page.content()
                    if "id=\"titles\"" in html or "username" in html:
                        break
                except Exception as content_err:
                    print(f"[{cache_key}] Content read attempt {attempt+1} notice: {content_err}", flush=True)
                    await page.wait_for_timeout(1000)

            if not html:
                try:
                    html = await page.content()
                except Exception:
                    html = ""
        finally:
            await browser.close()

    soup = BeautifulSoup(html, "html.parser")

    # Display Name
    display_name = username
    name_tag = soup.find("p", class_="username") or soup.find("h1", class_="username")
    if name_tag:
        display_name = name_tag.get_text(strip=True) or username

    # Avatar
    avatar_url = None
    user_img = soup.find("img", class_="user-img")
    if user_img:
        src = user_img.get("src", "")
        if src.startswith("http"):
            avatar_url = src
        elif src:
            avatar_url = "https://rlstats.net" + src

    # Titles
    titles = []
    titles_div = soup.find("div", id="titles")
    if titles_div:
        for tag in titles_div.find_all("p", class_="title"):
            style = tag.get("style", "")
            color = "#FFFFFF"
            shadow = None

            color_match = re.search(r"color:\s*(#[0-9A-Fa-f]{3,6}|[a-z]+)", style)
            if color_match:
                color = color_match.group(1)

            shadow_match = re.search(r"text-shadow:[^;]+?(#[0-9A-Fa-f]{3,6})", style)
            if shadow_match:
                shadow = shadow_match.group(1)

            badge_img = None
            badge_img_tag = tag.find("img", class_="title_img")
            if badge_img_tag:
                bsrc = badge_img_tag.get("src", "")
                if bsrc.startswith("http"):
                    badge_img = bsrc
                elif bsrc:
                    badge_img = "https://rlstats.net" + bsrc

            parts = []
            for child in tag.children:
                if hasattr(child, "get_text"):
                    t = child.get_text(strip=True)
                    if t and t != (badge_img_tag.get("alt", "") if badge_img_tag else ""):
                        parts.append(t)
                elif isinstance(child, str):
                    t = child.strip()
                    if t:
                        parts.append(t)
            title_text = " ".join(parts).strip() or tag.get_text(strip=True)

            if "{stat_value}" in title_text or "{flag_" in title_text:
                continue

            if title_text:
                titles.append({
                    "text": title_text,
                    "color": color,
                    "text_shadow": shadow,
                    "badge_img": badge_img
                })

    print(f"[{cache_key}] Success! Found {len(titles)} titles for {display_name}", flush=True)
    result = {
        "found": True,
        "display_name": display_name,
        "avatar_url": avatar_url,
        "platform": platform_norm,
        "profile_url": profile_url,
        "titles": titles
    }

    _cache[cache_key] = {"timestamp": time.time(), "data": result}
    return result

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
