#!/usr/bin/env python3
"""
SPITE Blue Post Tracker

Reads Warcraft Tavern's public blue-post RSS feed and posts any new Blizzard
blue posts to a Discord channel through a webhook. No AI, no bot account,
standard library only. Meant to run on a schedule (see the GitHub Actions
workflow), but it also runs fine from your own machine.

Configuration (environment variables):
  DISCORD_WEBHOOK_URL  required. The #blue-posts webhook URL.
  FEED_URL             optional. Defaults to the Warcraft Tavern "all" feed.
  GAMES                optional. Comma list of games to keep, e.g.
                       "forever,retail". Games come from the post link:
                       forever, retail, classic, ptr, ... plus "news" for
                       blizzard.com news articles. Empty = keep everything.
  REGIONS              optional. Comma list, "us", "eu" or "us,eu".
                       Defaults to "us" (Blizzard usually posts the same thing
                       to both regions, so one avoids duplicates).
  STATE_FILE           optional. Where seen post IDs are stored.
                       Defaults to state.json next to this script.
  TEST_POST            optional. "1" posts the newest matching item once, as a
                       connectivity test, without touching the state file.
"""

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

DEFAULT_FEED = "https://blue-tracker.warcrafttavern.com/rss/all"
USER_AGENT = "SPITE-BlueTracker/1.0 (+https://github.com)"
MAX_SEEN = 1000          # how many post IDs to remember
MAX_POSTS_PER_RUN = 10   # safety cap so a feed hiccup can't flood the channel
DESC_LIMIT = 400         # characters of post text shown in the embed

# Embed colour per game, so Forever / Retail / Classic are easy to tell apart.
GAME_COLORS = {
    "forever": 0xC79C6E,
    "retail": 0x148EFF,
    "classic": 0xE6CC80,
    "ptr": 0x8788EE,
    "news": 0x00AEFF,
}
GAME_LABELS = {
    "forever": "WoW: Forever",
    "retail": "Retail",
    "classic": "Classic",
    "ptr": "PTR / Beta",
    "news": "Blizzard News",
}

# Links look like https://www.warcrafttavern.com/blue-tracker/<game>/post/<region>/<id>
LINK_RE = re.compile(r"/blue-tracker/(?P<game>[^/]+)/post/(?P<region>[^/]+)/")


def log(msg):
    print(msg, flush=True)


def csv_env(name, default=""):
    raw = os.environ.get(name, default)
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


# --------------------------------------------------------------------------
# Feed
# --------------------------------------------------------------------------

def fetch(url, data=None, headers=None, timeout=20):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def clean_text(raw):
    """Strip HTML tags/entities from an RSS description and tidy whitespace."""
    text = html.unescape(raw or "")
    text = re.sub(r"<a [^>]*>\[(.*?)\]</a>", "", text)      # blog header image links
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    if len(text) > DESC_LIMIT:
        text = text[:DESC_LIMIT].rsplit(" ", 1)[0].rstrip(".,;: ") + "…"
    return text


def parse_feed(xml_bytes):
    """Return feed items oldest-first as dicts."""
    root = ET.fromstring(xml_bytes)
    items = []
    for node in root.iter("item"):
        link = (node.findtext("link") or "").strip()
        guid = (node.findtext("guid") or link).strip()
        m = LINK_RE.search(link)
        game = m.group("game").lower() if m else "news"
        region = (m.group("region") if m else (node.findtext("category") or "us")).lower()
        pub = node.findtext("pubDate")
        try:
            published = parsedate_to_datetime(pub) if pub else None
        except (TypeError, ValueError):
            published = None
        items.append({
            "id": guid,
            "title": html.unescape((node.findtext("title") or "Untitled").strip()),
            "link": link,
            "description": clean_text(node.findtext("description")),
            "game": game,
            "region": region,
            "published": published,
        })
    items.sort(key=lambda i: i["published"] or datetime.min.replace(tzinfo=timezone.utc))
    return items


def keep(item, games, regions):
    if games and item["game"] not in games:
        return False
    if regions and item["region"] not in regions:
        return False
    return True


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def load_state(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        log(f"warning: could not read {path}, starting fresh")
        return None


def save_state(path, seen):
    path.write_text(json.dumps({"seen": seen[-MAX_SEEN:]}, indent=2) + "\n")


# --------------------------------------------------------------------------
# Discord
# --------------------------------------------------------------------------

def build_payload(item, test=False):
    game_label = GAME_LABELS.get(item["game"], item["game"].title())
    embed = {
        "title": item["title"][:256],
        "url": item["link"],
        "description": item["description"],
        "color": GAME_COLORS.get(item["game"], 0x148EFF),
        "author": {"name": f"{game_label} · {item['region'].upper()}"},
        "footer": {"text": "Warcraft Tavern Blue Tracker" + (" · test post" if test else "")},
    }
    if item["published"]:
        embed["timestamp"] = item["published"].astimezone(timezone.utc).isoformat()
    return {
        "username": "WoW Blue Posts",
        "embeds": [embed],
        "allowed_mentions": {"parse": []},   # never ping anyone
    }


def post_to_discord(webhook, payload, attempts=3):
    body = json.dumps(payload).encode()
    for attempt in range(1, attempts + 1):
        try:
            status, _ = fetch(webhook, data=body, headers={"Content-Type": "application/json"})
            return 200 <= status < 300
        except urllib.error.HTTPError as e:
            if e.code == 429:                       # rate limited: wait and retry
                try:
                    wait = float(json.loads(e.read()).get("retry_after", 2))
                except Exception:
                    wait = 2.0
                log(f"rate limited, waiting {wait:.1f}s")
                time.sleep(wait + 0.5)
                continue
            log(f"Discord rejected the post: HTTP {e.code} {e.read()[:200]!r}")
            return False
        except urllib.error.URLError as e:
            log(f"network error posting to Discord (attempt {attempt}): {e}")
            time.sleep(2 * attempt)
    return False


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        log("error: DISCORD_WEBHOOK_URL is not set")
        return 1

    feed_url = os.environ.get("FEED_URL", DEFAULT_FEED)
    games = csv_env("GAMES")
    regions = csv_env("REGIONS", "us")
    state_path = Path(os.environ.get("STATE_FILE", Path(__file__).with_name("state.json")))
    test_mode = os.environ.get("TEST_POST", "") == "1"

    try:
        _, raw = fetch(feed_url)
        items = [i for i in parse_feed(raw) if keep(i, games, regions)]
    except Exception as e:  # feed down or malformed: try again next run
        log(f"error: could not read feed {feed_url}: {e}")
        return 1

    log(f"feed ok: {len(items)} matching items (games={games or 'all'}, regions={regions or 'all'})")

    if test_mode:
        if not items:
            log("test: no matching items to post")
            return 1
        ok = post_to_discord(webhook, build_payload(items[-1], test=True))
        log("test post sent" if ok else "test post FAILED")
        return 0 if ok else 1

    state = load_state(state_path)
    if state is None:
        # First run: remember what's already in the feed so the channel
        # isn't flooded with old posts. Only new posts from now on.
        save_state(state_path, [i["id"] for i in items])
        log(f"first run: recorded {len(items)} existing posts, nothing sent")
        return 0

    seen = list(state.get("seen", []))
    seen_set = set(seen)
    new = [i for i in items if i["id"] not in seen_set]
    if len(new) > MAX_POSTS_PER_RUN:
        log(f"{len(new)} new posts, only sending the latest {MAX_POSTS_PER_RUN}")
        skipped = new[:-MAX_POSTS_PER_RUN]
        seen.extend(i["id"] for i in skipped)
        new = new[-MAX_POSTS_PER_RUN:]

    sent = 0
    for item in new:
        if post_to_discord(webhook, build_payload(item)):
            seen.append(item["id"])
            sent += 1
            log(f"posted: [{item['game']}/{item['region']}] {item['title']}")
            time.sleep(1)                # stay well under webhook rate limits
        else:
            log(f"will retry next run: {item['title']}")
            break                        # keep order; retry the rest next time

    save_state(state_path, seen)
    log(f"done: {sent} posted, {len(new) - sent} pending")
    return 0


if __name__ == "__main__":
    sys.exit(main())
