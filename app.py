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
        return cached["data"]

    encoded_user = quote(username)
    profile_url = f"https://rlstats.net/profile/{platform_norm.capitalize()}/{encoded_user}"

    # Use headless=False if display is available (Xvfb provides DISPLAY=:99)
    use_headless = "DISPLAY" not in os.environ

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=use_headless,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        try:
            page = await browser.new_page()
            await page.goto(profile_url, timeout=35000)

            frame = page.frames[0]
            loaded = False
            for _ in range(20):
                await page.wait_for_timeout(1000)
                try:
                    title = await frame.title()
                    if "Profile | RLStats" in title:
                        loaded = True
                        break
                except Exception:
                    pass

            if not loaded:
                return {
                    "found": False,
                    "error": f"Could not load profile for {username} on {platform_norm.upper()}. Profile may be private or not found."
                }

            html = await frame.content()
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
