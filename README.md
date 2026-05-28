# goodreads-cli

CLI tool for filtering and displaying ratings for books from some source (reddit, etc).

## Commands

```
source venv/bin/activate
pip install .
# Goodreads sits behind an AWS WAF challenge; we solve it once with a headless browser.
python3 -m playwright install chromium
```

Ingest every monthly release thread linked from the r/litrpg new-releases wiki (recommended):

```
python3 ./main.py input --reddit-releases-wiki-url https://www.reddit.com/r/litrpg/wiki/newreleases
```

Or ingest a single monthly post (optionally following links to previous months):

```
python3 ./main.py input --follow-reddit-releases --reddit-releases-url https://www.reddit.com/r/litrpg/comments/1eheyaq/august_2024_releases_promotions/
```

> Reddit now serves `www.reddit.com` post/wiki pages as a JS shell with no parseable
> table, so the CLI fetches these URLs via `old.reddit.com` under the hood. Pass the
> normal `www.reddit.com` URL — the rewrite is automatic.

Rating all books:

```
python3 ./main.py rate-continuous
```

Adding a book and rating it:

```
python3 ./main.py input --manual-goodreads "https://www.goodreads.com/book/show/207944665-a-soldier-s-life"
python3 ./main.py rate-continuous --author "Always RollsAOne"
```

Rating books from a specific series:

```
python3 ./main.py rate-continuous --series "Dungeon Core"
```

You can also combine filters:

```
python3 ./main.py rate-continuous --author "Dakota Krout" --series "Divine Dungeon"
```
