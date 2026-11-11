"""
End-to-end test: serves the sample feed and a fake Discord webhook locally,
then runs tracker.py through first run, a no-op run, a run with new posts,
a failing webhook, and test mode.

Run: python3 tests/test_tracker.py
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = (ROOT / "tests" / "feed_sample.xml").read_text()


class Handler(BaseHTTPRequestHandler):
    feed = SAMPLE
    posts = []
    fail_webhook = False

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = Handler.feed.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/rss+xml")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        data = self.rfile.read(int(self.headers["Content-Length"]))
        if Handler.fail_webhook:
            self.send_response(500)
            self.end_headers()
            return
        Handler.posts.append(json.loads(data))
        self.send_response(204)
        self.end_headers()


def run(port, state, **extra):
    env = {**os.environ,
           "FEED_URL": f"http://127.0.0.1:{port}/feed",
           "DISCORD_WEBHOOK_URL": f"http://127.0.0.1:{port}/hook",
           "STATE_FILE": str(state),
           "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
           **extra}
    out = subprocess.run([sys.executable, str(ROOT / "tracker.py")], env=env, capture_output=True, text=True)
    print(out.stdout.strip())
    return out.returncode


def titles():
    return [p["embeds"][0]["title"] for p in Handler.posts]


def main():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    tmp = Path(tempfile.mkdtemp())
    state = tmp / "state.json"

    print("\n1) first run seeds state, posts nothing")
    assert run(port, state, REGIONS="us") == 0
    assert Handler.posts == [], Handler.posts
    seen = json.loads(state.read_text())["seen"]
    assert len(seen) == 5 and "forum-6490858" not in seen, seen  # EU item filtered out

    print("\n2) second run with same feed posts nothing")
    assert run(port, state, REGIONS="us") == 0
    assert Handler.posts == []

    print("\n3) two new posts appear, posted oldest-first")
    new_items = """
    <item><title>Class Tuning Incoming - November 12</title>
      <link>https://www.warcrafttavern.com/blue-tracker/forever/post/us/9000001</link>
      <description>&lt;p&gt;Mage &amp;amp; Warlock changes.&lt;/p&gt;</description>
      <guid isPermaLink="false">forum-9000001</guid><category>us</category>
      <pubDate>Thu, 12 Nov 2026 01:00:00 GMT</pubDate></item>
    <item><title>Server maintenance</title>
      <link>https://www.warcrafttavern.com/blue-tracker/forever/post/us/9000002</link>
      <description>Realms restart at 3am.</description>
      <guid isPermaLink="false">forum-9000002</guid><category>us</category>
      <pubDate>Thu, 12 Nov 2026 02:00:00 GMT</pubDate></item>
    <item><title>EU only post</title>
      <link>https://www.warcrafttavern.com/blue-tracker/forever/post/eu/9000003</link>
      <description>x</description>
      <guid isPermaLink="false">forum-9000003</guid><category>eu</category>
      <pubDate>Thu, 12 Nov 2026 03:00:00 GMT</pubDate></item>"""
    Handler.feed = SAMPLE.replace("<channel>", "<channel>" + new_items, 1)
    assert run(port, state, REGIONS="us") == 0
    assert titles() == ["Class Tuning Incoming - November 12", "Server maintenance"], titles()
    emb = Handler.posts[0]["embeds"][0]
    assert emb["description"] == "Mage & Warlock changes.", emb["description"]
    assert emb["author"]["name"] == "WoW: Forever · US"
    assert Handler.posts[0]["allowed_mentions"] == {"parse": []}

    print("\n4) rerun posts nothing new")
    assert run(port, state, REGIONS="us") == 0
    assert len(Handler.posts) == 2

    print("\n5) webhook failure keeps the post for next run")
    Handler.feed = Handler.feed.replace("<channel>", """<channel>
    <item><title>Retry me</title>
      <link>https://www.warcrafttavern.com/blue-tracker/retail/post/us/9000004</link>
      <description>y</description><guid isPermaLink="false">forum-9000004</guid>
      <category>us</category><pubDate>Thu, 12 Nov 2026 04:00:00 GMT</pubDate></item>""", 1)
    Handler.fail_webhook = True
    run(port, state, REGIONS="us")
    assert "forum-9000004" not in json.loads(state.read_text())["seen"]
    Handler.fail_webhook = False
    assert run(port, state, REGIONS="us") == 0
    assert titles()[-1] == "Retry me"

    print("\n6) GAMES filter: forever only")
    state2 = tmp / "state2.json"
    run(port, state2, REGIONS="us", GAMES="forever")
    kept = json.loads(state2.read_text())["seen"]
    assert all(k in kept for k in ["forum-9000001", "forum-30849309"]) and "forum-9000004" not in kept and "blog-24316097" not in kept

    print("\n7) test mode posts newest item and leaves state alone")
    before = state.read_text()
    assert run(port, state, REGIONS="us", TEST_POST="1") == 0
    assert Handler.posts[-1]["embeds"][0]["footer"]["text"].endswith("test post")
    assert state.read_text() == before

    print("\n8) blog-header link is stripped from retail news text")
    from importlib import util
    spec = util.spec_from_file_location("tracker", ROOT / "tracker.py")
    tr = util.module_from_spec(spec); spec.loader.exec_module(tr)
    items = {i["id"]: i for i in tr.parse_feed(SAMPLE.encode())}
    assert items["forum-30829178"]["description"].startswith("From the global launch"), items["forum-30829178"]["description"]
    assert "<img" not in items["forum-30782728"]["description"]
    assert items["blog-24316097"]["game"] == "news"

    server.shutdown()
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    main()
