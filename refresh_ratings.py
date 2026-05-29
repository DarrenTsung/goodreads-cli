#!/usr/bin/env python3
"""Refresh stale Goodreads ratings for candidate books.

The DB stores a rating snapshot from when each book was first ingested; for newer
books that snapshot is inflated (early ratings skew high) and drifts over time.
Neither `refresh-books` (only finds new books/series) nor `refresh-unreleased` (only
0-rating books) updates an existing released book's rating, so they go stale forever.

This refreshes average_rating + number_of_ratings (and pages/series) for candidate
books whose last refresh is older than --max-age-days (default ~5 months), reusing the
shared book_refresh_by_title timer so repeat runs skip recently-refreshed titles.

  python3 refresh_ratings.py                  # refresh candidates not refreshed in 150 days
  python3 refresh_ratings.py --max-age-days 90 --workers 6
  python3 refresh_ratings.py --limit 50       # just the 50 most-popular stale ones

Run classify_and_rank.py --rerank afterwards to re-rank with the fresh ratings.
"""
import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import goodreads
import theme_scan_lib as lib  # noqa: F401 - imports patch goodreads._solve_waf_challenge with a lock
from book import Book
from book_rating import BookRating
from book_refresh_metadata import BookRefreshMetadata

DEFAULT_MAX_AGE_DAYS = 150  # ~5 months


def _is_stale(title, meta, max_age):
    last = meta.book_refreshes_by_title.get(title)
    return last is None or (date.today() - last) >= timedelta(days=max_age)


def _fetch(book):
    """Network-only (thread-safe): return (book, GoodreadsBook) or (book, None) on failure."""
    try:
        return book, goodreads.load_goodreads_book_from_url(book.goodreads_link)
    except Exception as e:
        logging.warning(f"  fetch failed for '{book.title}': {e}")
        return book, None


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Refresh stale Goodreads ratings for candidates.")
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS,
                        help=f"refresh titles not refreshed in this many days (default {DEFAULT_MAX_AGE_DAYS})")
    parser.add_argument("--min-pages", type=int, default=500)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0, help="only the N most-popular stale candidates")
    args = parser.parse_args()

    books = Book.load_books_from_db()
    ratings = BookRating.load_ratings_from_db()
    meta = BookRefreshMetadata.load_from_db()

    candidates = lib.recommendable_books(books, ratings, min_pages=args.min_pages)
    todo = [b for b in candidates if b.goodreads_link and _is_stale(b.title, meta, args.max_age_days)]
    todo.sort(key=lambda b: b.number_of_ratings or 0, reverse=True)
    if args.limit:
        todo = todo[:args.limit]

    logging.info(
        f"{len(candidates)} candidates; {len(todo)} stale (>{args.max_age_days}d) to refresh "
        f"with {args.workers} workers."
    )
    if not todo:
        return

    # Warm the WAF cookie single-threaded before fanning out.
    first, gb = _fetch(todo[0])
    _apply(first, gb, meta)
    rest = todo[1:]

    changed = 0
    done = 1
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(_fetch, b) for b in rest]
        for fut in as_completed(futures):
            book, gb = fut.result()
            if _apply(book, gb, meta):
                changed += 1
            done += 1
            if done % 50 == 0:
                logging.info(f"  ...{done}/{len(todo)} refreshed ({changed} ratings changed)")

    logging.info(f"Done: refreshed {done} books, {changed} had changed ratings. "
                 f"Run: python3 classify_and_rank.py --rerank --series-aware")


def _apply(book, gb, meta):
    """Apply a fetched GoodreadsBook to the DB (main thread). Returns True if rating changed."""
    if gb is None:
        return False
    old = book.average_rating
    book._populate_from_goodreads_book(gb)
    book.sync_with_db()
    meta.handle_title_refreshed(book.title)
    return old != book.average_rating


if __name__ == "__main__":
    main()
