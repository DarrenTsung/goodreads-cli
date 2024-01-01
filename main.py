#!/usr/bin/env python3
import argparse
import requests
import re
from bs4 import BeautifulSoup
from prettytable import PrettyTable
from fuzzywuzzy import fuzz
import pprint as pp
import logging.config
import textwrap
from collections import defaultdict
from book import Book, BooksBySeries
from utils import stripped_title, stripped
import goodreads
from book_rating import BookRating, Tier
from book_refresh_metadata import BookRefreshMetadata
from books_from_reddit import find_books_from_table_in_reddit_releases_post, follow_reddit_releases_link

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

MIN_PAGES_FOR_CONSIDERATION = 600
MIN_RATINGS_FOR_CONSIDERATION = 50
DESCRIPTION_MAX_CHARACTERS = 1000 

def main():
    parser = argparse.ArgumentParser(description="goodreads-cli", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    subparsers = parser.add_subparsers(dest='command', required=True)

    # Subcommand 'input'
    input_parser = subparsers.add_parser('input', help='Process input from various sources')
    input_parser.add_argument('--reddit-releases-url', type=str, help='Input URL of a Reddit releases post')
    input_parser.add_argument('--follow-reddit-releases', action='store_true', help='Follow Reddit releases links to previous months')
    input_parser.add_argument('--manual', type=str, help='Input "Title, Author" as a string')

    # Subcommand 'refresh-books'
    refresh_books_parser = subparsers.add_parser('refresh-books', help='Refresh books DB')
    refresh_books_parser.add_argument('--reddit-releases-url', type=str, help='Input URL of a Reddit releases post')
    refresh_books_parser.add_argument('--manual', type=str, help='Input "Title, Author" as a string')

    # Subcommand 'rate-continuous'
    _rate_continuous_parser = subparsers.add_parser('rate-continuous', help='Rate books in the DB without a rating')

    args = parser.parse_args()

    # Load books from the database first
    books = {book.title: book for book in Book.load_books_from_db()}
    logging.info(f"Loaded {len(books)} books from the DB.")
    if args.command == 'input':
        if args.follow_reddit_releases and not args.reddit_releases_url:
            raise ValueError("Shouldn't specify --follow-reddit-releases without --reddit-releases-url!")

        new_books = {}
        if args.reddit_releases_url:
            current_releases_url = args.reddit_releases_url
            while current_releases_url:
                books_from_reddit_releases_post = find_books_from_table_in_reddit_releases_post(current_releases_url)
                logging.info(f"Found {len(books_from_reddit_releases_post)} books in reddit releases url: {current_releases_url}!")
                for new_book in books_from_reddit_releases_post:
                    if new_book.title in books:
                        continue

                    found_book_match = False
                    authors = new_book.author.split('&')
                    for book in books.values():
                        authors_match = True
                        for author in authors:
                            author_in_book = False
                            for book_author in book.author.split('&'):
                                if fuzz.partial_ratio(stripped(author), stripped(book_author)) >= 90:
                                    author_in_book = True

                            if not author_in_book:
                                authors_match = False
                                break
                        if authors_match:
                            exact_title_in = stripped_title(new_book.title) in stripped_title(book.title)
                            title_close_enough = fuzz.partial_ratio(stripped_title(new_book.title), stripped_title(book.title)) >= 90
                            if exact_title_in or title_close_enough:
                                found_book_match = True
                                break

                    if not found_book_match:
                        logging.debug(f"Found new book: {new_book.title} ({new_book.author}).")
                        new_books[new_book.title] = new_book

                process_new_books(new_books, books)
                new_books.clear()
                if args.follow_reddit_releases:
                    current_releases_url = follow_reddit_releases_link(current_releases_url)
                else:
                    current_releases_url = None
        elif args.manual:
            title_author = args.manual.split(',')
            if len(title_author) != 2:
                logging.error("Please provide the --manual parameter in 'Title, Author' format.")
                exit(1)
            title, author = title_author[0].strip(), title_author[1].strip()
            manual_book = Book(title=title, author=author)
            new_books[manual_book.title] = manual_book
            logging.info(f"Processing new book: {title} ({author})..")
            process_new_books(new_books, books)
        else:
            logging.error("Please provide an input parameter (e.g. --reddit-releases-url).")
            exit(1)

        logging.info(f"Finished processing books from input, DB now contains {len(books)} books.")
    elif args.command == 'refresh-books':
        books_by_series = BooksBySeries.from_books(books)
        book_refresh_metadata = BookRefreshMetadata.load_from_db()
        # Check if any new books in series.
        for series, books_in_series in books_by_series.items():
            if not book_refresh_metadata.should_refresh_series(series):
                continue

            logging.info(f"Refreshing series: {series}..")
            found_books_from_series = books_in_series[0].find_books_from_series()
            for book in found_books_from_series:
                if book.title in books:
                    continue
                logging.info(f"Found new book in series ({series}): {book.title}!")
                book.sync_with_db()
                books[book.title] = book
            book_refresh_metadata.handle_series_refreshed(series)

        for book in books.values():
            if book.series:
                continue
            
            if not book_refresh_metadata.should_refresh_title(book.title):
                continue

            logging.info(f"Refreshing book without series: {book.title}..")
            if book.refresh_if_part_of_series_now_on_goodreads():
                logging.info(f"Found new series: {book.series}! Refreshing series..")
                book.sync_with_db()
                found_books_from_series = book.find_books_from_series()
                for book in found_books_from_series:
                    if book.title in books:
                        continue
                    logging.info(f"Found new book in series ({series}): {book.title}!")
                    book.sync_with_db()
                    books[book.title] = book
            book_refresh_metadata.handle_title_refreshed(book.title)
    elif args.command == 'rate-continuous':
        book_ratings = BookRating.load_ratings_from_db()
        books_by_series = BooksBySeries.from_books(books)
    
        # Go through books in order so that first book of series is visited first.
        books_in_order = []
        for book in books.values():
            if not book.series:
                books_in_order.append(book)
        for _series, books_in_series in books_by_series.items():
            for book in books_in_series:
                books_in_order.append(book)

        for book in books_in_order:
            if book_ratings.has_directly_rated_book(book):
                continue

            # Note that this doesn't mark the book as uninterested twice because it checks if
            # the book has a direct rating above.
            if book_ratings.has_rated_book_or_series_as_f_tier_or_uninterested(book):
                book_ratings.mark_book_as_uninterested(book)
                continue

            if books_by_series.any_books_with_less_than_rating_in_series(book.series, 4):
                book_ratings.mark_book_as_uninterested(book)
                continue

            if book_ratings.has_rated_series(book.series):
                continue

            book_has_enough_pages = book.pages_reported_by_kindle and book.pages_reported_by_kindle >= MIN_PAGES_FOR_CONSIDERATION
            series_has_enough_pages = book.series and books_by_series.total_pages_reported_by_kindle_for_series(book.series) >= MIN_PAGES_FOR_CONSIDERATION
            has_enough_pages = book_has_enough_pages or series_has_enough_pages
            if not has_enough_pages:
                # Revisit when book / series has enough pages..
                continue

            book_has_enough_ratings = book.number_of_ratings >= MIN_RATINGS_FOR_CONSIDERATION
            series_has_enough_ratings = book.series and books_by_series.total_number_of_ratings_for_series(book.series) >= MIN_RATINGS_FOR_CONSIDERATION
            has_enough_ratings = book_has_enough_ratings or series_has_enough_ratings
            if not has_enough_ratings:
                # Revisit when book / series has enough ratings..
                continue

            # Present the book / series to the user.
            print(f"{book.title} ({book.author})")
            if book.series:
                series_books = books_by_series.books_by_series[book.series]
                series_avg_rating = sum(b.average_rating for b in series_books if b.average_rating) / len(series_books)
                total_series_ratings = sum(b.number_of_ratings for b in series_books if b.number_of_ratings)
                total_series_pages = books_by_series.total_pages_reported_by_kindle_for_series(book.series)
                print(f"\tBook #{book.series_number} of {len(series_books)} in {book.series}")
                print(f"\t{series_avg_rating:.2f} (total {total_series_ratings} ratings)")
                print(f"\t{total_series_pages} total pages in series")
            else:
                print(f"\t{book.average_rating:.2f} ({book.number_of_ratings} ratings)")
                print(f"\t{book.pages_reported_by_kindle} pages")
            print("")
            print(f"Goodreads link: {book.goodreads_link}")
            print("")

            description = goodreads.description_text_for_book(book)
            # Find the last complete word that fits in the DESCRIPTION_MAX_CHARACTERS character limit
            last_space = description.rfind(' ', 0, DESCRIPTION_MAX_CHARACTERS)
            if len(description) > DESCRIPTION_MAX_CHARACTERS and last_space != -1:
                description = description[:last_space] + '...'
            # Wrap the description at 100 characters
            wrapped_description = '\n'.join(textwrap.wrap(description, width=100))
            print(wrapped_description)
            print("")

            while True:
                user_input = input("Choose an option:\n"
                                    "1) I've read this\n"
                                    "2) I've tried reading this and it's F tier\n"
                                    "3) I'm not interested\n"
                                    "4) I'm interested\n"
                                    "Enter your choice: ")
                if user_input == '1':
                    while True:
                        tier_input = input("Enter the tier for the book (S, A, B, F): ")
                        try:
                            tier = Tier(tier_input.upper())
                            book_ratings.mark_book_with_tier(book, tier)
                            break
                        except ValueError:
                            print("Invalid tier. Please enter a valid tier.")
                elif user_input == '2':
                    book_ratings.mark_book_with_tier(book, Tier.F)
                elif user_input == '3':
                    book_ratings.mark_book_as_uninterested(book)
                elif user_input == '4':
                    book_ratings.mark_book_as_interested(book)
                else:
                    print("Invalid input. Please enter a number between 1 and 4.")
                    continue
                break
            print("")
            print("")

def process_new_books(new_books, books):
    # These authors have weird books that break the script, just ignore them for now.
    banned_authors = set(["Vasily Mahanenko"])
    new_books = [book for book in new_books.values() if book.author not in banned_authors]
    
    book_refresh_metadata = BookRefreshMetadata.load_from_db()
    for book in new_books:
        try:
            book.populate_from_goodreads()
            book_refresh_metadata.handle_title_refreshed(book.title)
        except ValueError:
            logging.info(f"Ignoring book not found on goodreads: {book.title} ({book.author}).")
            continue

        if book.title in books:
            continue

        logging.info(f"Added new book: {book.title}.")
        books[book.title] = book
        book.sync_with_db()

        if book.series:
            # Check if any new books in series.
            if not book_refresh_metadata.should_refresh_series(book.series):
                continue

            logging.info(f"Refreshing series: {book.series}..")
            found_books_from_series = book.find_books_from_series()
            for book in found_books_from_series:
                if book.title in books:
                    continue
                logging.info(f"Found new book in series ({book.series}): {book.title}!")
                book.sync_with_db()
                books[book.title] = book
            book_refresh_metadata.handle_series_refreshed(book.series)
    

    # Populate missing books in series found.
    books_by_series = BooksBySeries.from_books(books)

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
        pp.pp(books_in_series)

        logging.debug(f"Found series ({series}) with missing books, fetching books in series from goodreads..")
        
        found_books_from_series = any_book_in_series.find_books_from_series()
        for book in found_books_from_series:
            while len(books_in_series) < book.series_number:
                books_in_series.append(None)
            books_in_series[book.series_number-1] = book
            books[book.title] = book
            book.sync_with_db()
        book_refresh_metadata.handle_series_refreshed(series)

        # Check all books are populated now..
        for book in books_in_series:
            if not book:
                raise ValueError("Programmer error, how did we get an empty book in books_in_series after loading?")

if __name__ == "__main__":
    main()
