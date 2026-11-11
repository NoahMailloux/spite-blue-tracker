# SPITE Blue Post Tracker

Posts new Blizzard blue posts into the SPITE Discord's `#blue-posts` channel.
No bot account, no AI, free: a GitHub Actions job runs every 10 minutes, reads
[Warcraft Tavern's public blue-tracker RSS feed](https://blue-tracker.warcrafttavern.com/rss/all),
and sends anything new to a Discord webhook.

## Setup (one time)

1. **Webhook:** in Discord, `#blue-posts` → Edit Channel → Integrations →
   Webhooks → New Webhook → Copy Webhook URL.
2. **Secret:** in this repo, Settings → Secrets and variables → Actions →
   New repository secret, name `DISCORD_WEBHOOK_URL`, paste the URL.
3. **Test:** Actions tab → *Blue post tracker* → Run workflow → tick
   *test_post* → Run. The newest blue post should appear in `#blue-posts`
   marked "test post".
4. Done. The schedule takes over. The first scheduled run only records what's
   already in the feed, so the channel won't get flooded with old posts.

## Filters (optional)

Settings → Secrets and variables → Actions → **Variables** tab:

| Variable  | Example          | Default | Meaning |
|-----------|------------------|---------|---------|
| `GAMES`   | `forever,retail` | `forever` | Games to keep: `forever`, `retail`, `classic`, `ptr`, `news` (blizzard.com articles) |
| `REGIONS` | `us` or `us,eu`  | `us`    | Blizzard posts most things to both regions, so `us` avoids duplicates |

## How it works

- `tracker.py`: standard-library Python. Fetches the feed, filters it, posts
  new items (oldest first) as Discord embeds colour-coded by game, and records
  posted IDs in `state.json`.
- `.github/workflows/blue-tracker.yml`: runs the script on a 10-minute cron
  and commits `state.json` when it changes.
- If Discord rejects a post, it isn't marked as seen and is retried next run.
  At most 10 posts go out per run, so a feed glitch can't spam the channel.

## Run locally

```bash
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/... python3 tracker.py
python3 tests/test_tracker.py   # end-to-end test with a fake feed + webhook
```

## Notes

- **Keep this repo public.** Actions minutes are unlimited for public repos.
  A private repo gets 2,000 free minutes a month, and every run bills at least
  a minute, so a 10-minute schedule (~4,300 runs) would run out. Nothing
  sensitive is in the repo: the webhook URL lives in an encrypted secret.
  If it must be private, change the cron to `*/30 * * * *` (~1,450 runs).
- GitHub's scheduler can run a few minutes late during busy periods.
- GitHub pauses scheduled workflows in repos with no activity for 60 days.
  The job commits `state.json` whenever new posts arrive, which normally keeps
  it active; if it ever stops, re-enable it from the Actions tab.
- The data comes from Warcraft Tavern's feed; if they change or retire it,
  set `FEED_URL` to another RSS source.
