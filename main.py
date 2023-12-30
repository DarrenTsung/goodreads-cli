#!/usr/bin/env python3
import argparse, logging
import requests
import re
from bs4 import BeautifulSoup
from prettytable import PrettyTable
import pprint as pp
import logging
import logging
from book import Book
from book_rating import BookRating
from books_from_reddit import find_books_from_table_in_reddit_releases_post

logging.basicConfig(level=logging.INFO)

def main():
    parser = argparse.ArgumentParser(description="goodreads-cli", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    subparsers = parser.add_subparsers(dest='command', required=True)

    # Subcommand 'input'
    input_parser = subparsers.add_parser('input', help='Process input from various sources')
    input_parser.add_argument('--reddit-url', type=str, help='Input URL of a Reddit post')

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
        else:
            logging.error("Please provide the --reddit-url parameter.")
            exit(1)

        invalid_books = []
        for book in books.values():
            try:
                book.populate_from_goodreads()
            except ValueError:
                invalid_books.append(book)
                continue

            book.sync_with_db()
        
        for invalid_book in invalid_books:
            del books[invalid_book.title]

        logging.info(f"Finished processing books from input, DB now contains {len(books)} books.")
    elif args.command == 'rate-continuous':
        book_ratings = BookRating.load_ratings_from_db()

        for book in books.values():
            if not book_ratings.matching_ratings_for_book(book):
                continue


if __name__ == "__main__":
    main()
