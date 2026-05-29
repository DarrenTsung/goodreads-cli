#!/usr/bin/env python3
"""Shared helpers for the local theme-preference scan.

Provides:
  - a resumable JSON description cache (descriptions_cache.json)
  - parallel Goodreads description backfill (WAF cookie warmed once, then fanned out)
  - call_claude(): a thin wrapper around `claude -p ... --output-format json`

This is intentionally standalone: no DB schema changes, no CLI surface changes.
"""
import json
import logging
import os
import random
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import goodreads
from book import Book
from book_rating import BookRating, Tier

MIN_RATINGS_FOR_CANDIDATE = 50
PROFILE_DISLIKE_SAMPLE = 150
PROFILE_SAMPLE_SEED = 42

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(_HERE, "descriptions_cache.json")

# Serialize WAF challenge solves so a mid-run re-challenge can't launch several
# headless browsers at once across worker threads.
_waf_lock = threading.Lock()
# Guards the in-memory description cache dict against concurrent mutation by worker
# threads while the main thread serializes it.
_cache_lock = threading.Lock()
_orig_solve_waf = goodreads._solve_waf_challenge


def _locked_solve_waf(url):
    with _waf_lock:
        return _orig_solve_waf(url)


goodreads._solve_waf_challenge = _locked_solve_waf


# ---------------------------------------------------------------------------
# Description cache
# ---------------------------------------------------------------------------
def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logging.warning("Description cache was unreadable; starting fresh.")
    return {}


def save_cache(cache):
    with _cache_lock:
        snapshot = dict(cache)
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(snapshot, f)
    os.replace(tmp, CACHE_FILE)


def get_description(cache, book_id):
    entry = cache.get(str(book_id))
    return entry["description"] if entry else None


def parse_pubdate(raw):
    """Parse a Goodreads publicationInfo string into (iso_date, year).

    Handles "First published April 5, 2021" / "Published 2019" / "Expected publication
    October 1, 2024". Returns (YYYY-MM-DD or None, year int or None).
    """
    if not raw:
        return None, None
    m = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", raw)
    if m:
        try:
            d = datetime.strptime(m.group(1), "%B %d, %Y")
            return d.strftime("%Y-%m-%d"), d.year
        except ValueError:
            pass
    y = re.search(r"\b(\d{4})\b", raw)
    return None, (int(y.group(1)) if y else None)


# ---------------------------------------------------------------------------
# Parallel backfill
# ---------------------------------------------------------------------------
def backfill_descriptions(books, workers=5, save_every=25):
    """Fetch + cache descriptions for `books` (objects with .id / .goodreads_link).

    Resumable: books already in the cache are skipped. The WAF cookie is warmed
    once single-threaded before fanning out, so worker threads rarely need to
    solve a challenge themselves.
    """
    cache = load_cache()
    todo = [b for b in books if b.goodreads_link and str(b.id) not in cache]
    skipped = len(books) - len(todo)
    logging.info(f"Backfill: {len(todo)} to fetch, {skipped} already cached.")
    if not todo:
        return cache

    # Warm the WAF cookie in the main thread (Playwright sync API dislikes
    # running inside worker threads), fetching the first book serially.
    first = todo[0]
    _fetch_one(cache, first)
    save_cache(cache)
    todo = todo[1:]

    done = 0
    failures = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_fetch_one, cache, b): b for b in todo}
        for fut in as_completed(futures):
            b = futures[fut]
            try:
                ok = fut.result()
                failures += 0 if ok else 1
            except Exception as e:  # pragma: no cover - defensive
                failures += 1
                logging.warning(f"Failed to fetch '{b.title}' ({b.id}): {e}")
            done += 1
            if done % save_every == 0:
                save_cache(cache)
                logging.info(f"  ...{done}/{len(todo)} fetched ({failures} failures)")

    save_cache(cache)
    logging.info(f"Backfill complete: {done} fetched, {failures} failures.")
    return cache


def _fetch_one(cache, book):
    """Fetch and cache one description + publication date. Returns True on success."""
    try:
        description, pub_raw = goodreads.description_and_pubdate_for_book(book)
    except Exception as e:
        logging.warning(f"  fetch failed for '{book.title}' ({book.id}): {e}")
        return False
    if not description or not description.strip():
        return False
    pub_date, pub_year = parse_pubdate(pub_raw)
    with _cache_lock:
        cache[str(book.id)] = {
            "description": description.strip(),
            "goodreads_link": book.goodreads_link,
            "published_raw": pub_raw,
            "published_date": pub_date,
            "published_year": pub_year,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    return True


# ---------------------------------------------------------------------------
# Book selection (shared so scan + build_profile agree on the exact same set)
# ---------------------------------------------------------------------------
def candidate_books(books, min_ratings=MIN_RATINGS_FOR_CANDIDATE):
    """Released, reasonably-popular books we'll auto-rate."""
    return [b for b in books if (b.number_of_ratings or 0) >= min_ratings]


def recommendable_books(books, ratings, min_pages=500, min_ratings=MIN_RATINGS_FOR_CANDIDATE):
    """Books that pass the user's normal `rate-continuous` filters.

    Mirrors main.py's rate-continuous chain: skips already-rated books, books in an
    F-tier/uninterested/already-rated series, any series containing a book rated <4
    stars, and requires the book *or its series* to clear the page and rating minimums.
    """
    from book import BooksBySeries
    bbs = BooksBySeries.from_books(books)
    out = []
    for book in books:
        if ratings.has_directly_rated_book(book):
            continue
        if ratings.has_rated_book_or_series_as_f_tier_or_uninterested(book):
            continue
        if bbs.any_books_with_less_than_rating_in_series(book, 4):
            continue
        if book.series and ratings.has_rated_series(book.series):
            continue

        book_pages = book.pages_reported_by_kindle and book.pages_reported_by_kindle >= min_pages
        series_pages = book.series and bbs.total_pages_reported_by_kindle_for_series(book.series) >= min_pages
        if not (book_pages or series_pages):
            continue

        book_ratings_ok = (book.number_of_ratings or 0) >= min_ratings
        series_ratings_ok = book.series and bbs.total_number_of_ratings_for_series(book.series) >= min_ratings
        if not (book_ratings_ok or series_ratings_ok):
            continue

        out.append(book)
    return out


def profile_split(books, ratings, sample_n=PROFILE_DISLIKE_SAMPLE, seed=PROFILE_SAMPLE_SEED):
    """Partition the user's rated books into liked / disliked sets for the profile.

    Returns a dict:
      liked            -> list of (Book, Tier) for S/A/B direct ratings
      disliked_f       -> list of Book for F-tier direct ratings
      disliked_sample  -> list of Book for a deterministic sample of `interested=0`

    Ratings are keyed by title; matched to books by exact title (unmatched skipped).
    """
    by_title = {b.title: b for b in books}

    liked, disliked_f, uninterested = [], [], []
    for rating in ratings.rating_by_title.values():
        book = by_title.get(rating.title)
        if not book:
            continue
        if rating.tier in (Tier.S, Tier.A, Tier.B):
            liked.append((book, rating.tier))
        elif rating.tier == Tier.F:
            disliked_f.append(book)
        elif rating.interested in (0, False):
            uninterested.append(book)

    rng = random.Random(seed)
    rng.shuffle(uninterested)
    disliked_sample = uninterested[:sample_n]

    return {"liked": liked, "disliked_f": disliked_f, "disliked_sample": disliked_sample}


def profile_book_ids(split):
    ids = [b.id for b, _ in split["liked"]]
    ids += [b.id for b in split["disliked_f"]]
    ids += [b.id for b in split["disliked_sample"]]
    return ids


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------
def call_claude(prompt, model="sonnet", retries=1, timeout=300):
    """Run `claude -p` and return the model's text output (the `.result` field).

    Raises RuntimeError if the CLI fails after retries.
    """
    last_err = None
    for attempt in range(retries + 1):
        try:
            proc = subprocess.run(
                ["claude", "-p", prompt, "--model", model, "--output-format", "json"],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if proc.returncode != 0:
                last_err = f"claude exited {proc.returncode}: {proc.stderr[:500]}"
                continue
            wrapper = json.loads(proc.stdout)
            if wrapper.get("is_error"):
                last_err = f"claude reported error: {wrapper.get('result')!r}"
                continue
            return wrapper["result"]
        except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError) as e:
            last_err = str(e)
    raise RuntimeError(f"call_claude failed after {retries + 1} attempts: {last_err}")


def parse_json_response(text):
    """Extract a JSON value from a model response, tolerating ``` fences/prose."""
    text = text.strip()
    if text.startswith("```"):
        # strip leading ```json / ``` and trailing ```
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.rstrip().endswith("```"):
            text = text.rsplit("```", 1)[0]
    # Fall back to slicing between the outermost JSON delimiters.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for opener, closer in (("[", "]"), ("{", "}")):
            start = text.find(opener)
            end = text.rfind(closer)
            if start != -1 and end > start:
                return json.loads(text[start:end + 1])
        raise
