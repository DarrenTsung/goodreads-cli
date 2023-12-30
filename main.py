#!/usr/bin/env python3
import argparse
import requests
import re
from bs4 import BeautifulSoup
from prettytable import PrettyTable
import pprint as pp
import logging.config
from collections import defaultdict
from book import Book
from book_rating import BookRating
from books_from_reddit import find_books_from_table_in_reddit_releases_post

log_level = 'DEBUG'
logging.config.dictConfig({
    'version': 1,
    'disable_existing_loggers': True,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'level': log_level,
            'formatter': 'standard',
            'stream': 'ext://sys.stdout',
        },
    },
    'formatters': {
        'standard': {
            'format': '%(levelname)s - %(message)s',
        },
    },
    'loggers': { 
        '':   {'level': log_level, 
                'handlers': ['console'], 
                'propagate': False },
    },
})

def main():
    parser = argparse.ArgumentParser(description="goodreads-cli", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    subparsers = parser.add_subparsers(dest='command', required=True)

    # Subcommand 'input'
    input_parser = subparsers.add_parser('input', help='Process input from various sources')
    input_parser.add_argument('--reddit-url', type=str, help='Input URL of a Reddit post')
    input_parser.add_argument('--manual', type=str, help='Input "Title, Author" as a string')

    # Subcommand 'rate-continuous'
    _rate_continuous_parser = subparsers.add_parser('rate-continuous', help='Rate books in the DB without a rating')

    args = parser.parse_args()

    # Load books from the database first
    books = {book.title: book for book in Book.load_books_from_db()}
    logging.info(f"Loaded {len(books)} books from the DB.")
    if args.command == 'input':
        if args.reddit_url:
            for new_book in find_books_from_table_in_reddit_releases_post(args.reddit_url):
                if new_book.title in books:
                    continue

                books[new_book.title] = new_book
        elif args.manual:
            title_author = args.manual.split(',')
            if len(title_author) != 2:
                logging.error("Please provide the --manual parameter in 'Title, Author' format.")
                exit(1)
            title, author = title_author[0].strip(), title_author[1].strip()
            manual_book = Book(title=title, author=author)
            try:
                manual_book.populate_from_goodreads()
                books[manual_book.title] = manual_book
                manual_book.sync_with_db()
            except ValueError as e:
                logging.error(f"Failed to process manual input: {e}")
                exit(1)
        else:
            logging.error("Please provide the --reddit-url parameter.")
            logging.error("Please provide either the --reddit-url or --manual parameter.")
            exit(1)

        invalid_books = []
        for book in books.values():
            try:
                book.populate_from_goodreads()
            except ValueError:
                invalid_books.append(book)
                continue

            book.sync_with_db()
        

        # Populate missing books in series found.
        books_by_series = defaultdict(list)
        for book in books.values():
            if not book.series or not book.series_number:
                continue

            while len(books_by_series[book.series]) < book.series_number:
                books_by_series[book.series].append(None)
            books_by_series[book.series][book.series_number-1] = book

        for series, books_in_series in books_by_series.items():
            any_book_in_series = None
            all_books_populated = True
            for book in books_in_series:
                if book:
                    any_book_in_series = book
                else:
                    all_books_populated = False

            if not any_book_in_series:
                raise ValueError("Programmer error, how did we get an empty books_in_series array?")

            if all_books_populated:
                continue

            logging.debug(f"Found series ({series}) with missing books, fetching books in series from goodreads..")
            
            found_books_from_series = any_book_in_series.find_books_from_series()
            for book in found_books_from_series:
                while len(books_by_series[book.series]) < book.series_number:
                    books_by_series[book.series].append(None)
                books_in_series[book.series_number-1] = book
                book.sync_with_db()

            # Check all books are populated now..
            for book in books_in_series:
                if not book:
                    raise ValueError("Programmer error, how did we get an empty book in books_in_series after loading?")
            

        for invalid_book in invalid_books:
            del books[invalid_book.title]

        logging.info(f"Finished processing books from input, DB now contains {len(books)} books.")
    elif args.command == 'rate-continuous':
        book_ratings = BookRating.load_ratings_from_db()

        for book in books.values():
            if book_ratings.has_rated_book_or_series_as_f_tier(book):
                continue

            if book_ratings.has_directly_rated_book(book):
                continue

            # TODO: check if book series is long enough

            # Book 


if __name__ == "__main__":
    main()
