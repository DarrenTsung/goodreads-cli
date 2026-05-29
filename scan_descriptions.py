#!/usr/bin/env python3
"""Fetch + cache Goodreads descriptions for a set of books.

Usage:
  python3 scan_descriptions.py --scope profile     # rated set + sampled dislikes (~218)
  python3 scan_descriptions.py --scope candidates  # all books with >=50 ratings (~2,701)

Resumable: descriptions already in descriptions_cache.json are skipped.
"""
import argparse
import logging

import theme_scan_lib as lib
from book import Book
from book_rating import BookRating


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Cache Goodreads descriptions.")
    parser.add_argument("--scope", choices=["profile", "candidates"], default="candidates")
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--min-pages", type=int, default=500, help="min pages (book or series) for candidate scope")
    args = parser.parse_args()

    books = Book.load_books_from_db()

    if args.scope == "profile":
        ratings = BookRating.load_ratings_from_db()
        split = lib.profile_split(books, ratings)
        wanted_ids = set(lib.profile_book_ids(split))
        targets = [b for b in books if b.id in wanted_ids]
        logging.info(
            f"Profile scope: {len(split['liked'])} liked, {len(split['disliked_f'])} F-tier, "
            f"{len(split['disliked_sample'])} sampled dislikes -> {len(targets)} books."
        )
    else:
        ratings = BookRating.load_ratings_from_db()
        targets = lib.recommendable_books(books, ratings, min_pages=args.min_pages)
        logging.info(
            f"Candidate scope (rate-continuous filters: >4 rating, >= {args.min_pages} pages, "
            f">= {lib.MIN_RATINGS_FOR_CANDIDATE} ratings, unrated): {len(targets)} books."
        )

    lib.backfill_descriptions(targets, workers=args.workers)


if __name__ == "__main__":
    main()
