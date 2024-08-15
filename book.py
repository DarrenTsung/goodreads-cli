#!/usr/bin/env python3
import goodreads
import pprint as pp
import logging
import re
import sqlite3
from collections import defaultdict
from utils import stripped, stripped_title
from fuzzywuzzy import fuzz

DB_NAME = "books.db"

class BooksBySeries:
    def __init__(self, books_by_series):
        self.books_by_series = books_by_series

    @classmethod
    def from_books(cls, books):
        books_by_series = defaultdict(list)
        for book in books:
            if not book.series:
                continue

            books_by_series[book.series].append(book)
        return BooksBySeries(books_by_series)

    def items(self):
        return self.books_by_series.items()

    def _accumulate_series_attribute(self, series, attr_lambda):
        if series not in self.books_by_series:
            raise ValueError(f"Failed to find series ({series}) in BooksBySeries, programmer error!")

        return sum(attr_lambda(book) for book in self.books_by_series[series] if book)

    def total_pages_reported_by_kindle_for_series(self, series):
        return self._accumulate_series_attribute(series, lambda book: book.pages_reported_by_kindle or 0)

    def total_number_of_ratings_for_series(self, series):
        return self._accumulate_series_attribute(series, lambda book: book.number_of_ratings)

    def any_books_with_less_than_rating_in_series(self, book, rating):
        if book.average_rating < rating:
            return True

        if book.series:
            if book.series not in self.books_by_series:
                raise ValueError(f"Failed to find series ({book.series}) in BooksBySeries, programmer error!")

            for book in self.books_by_series[book.series]:
                if book.average_rating < rating:
                    return True

        return False
    
def stripped_authors(authors):
    return [stripped(a) for a in re.split('&\s,', authors)]

def authors_match(stripped_authors_a, authors_b):
    stripped_authors_b = stripped_authors(authors_b)
    for author_a in stripped_authors_a:
        author_in_book = False
        for author_b in stripped_authors_b:
            if author_a == author_b:
                author_in_book = True
                break

            if fuzz.partial_ratio(author_a, author_b) >= 90:
                author_in_book = True
                break

        if not author_in_book:
            return False

    return True

def has_book(books_by_id, books_by_title, book):
    # if there's an id, then it's populated from goodreads.
    if book.id:
        # If the book in the DB doesn't have a series and this one
        # does, let's pretend like we don't have a book so it'll get updated.
        if book.series and book.id in books_by_id and not books_by_id[book.id].series:
            return False

        return book.id in books_by_id
    else:
        return books_by_title.has_book(book)

class BooksByTitle:
    def __init__(self, books):
        self.books_by_title = defaultdict(list)
        for book in books:
            self.books_by_title[book.title].append(book)
    
    def has_book(self, query_book):
        stripped_query_authors = stripped_authors(query_book.author)
        if query_book.title in self.books_by_title:
            for book in self.books_by_title[query_book.title]:
                if authors_match(stripped_query_authors, book.author):
                    return True

        for title, books in self.books_by_title.items():
            for book in books:
                if authors_match(stripped_query_authors, book.author):
                    query_book_stripped_title = stripped_title(query_book.title)
                    exact_title_in = query_book_stripped_title in stripped_title(title)
                    title_close_enough = fuzz.partial_ratio(query_book_stripped_title, stripped_title(book.title)) >= 90
                    if exact_title_in or title_close_enough:
                        return True
        return False
    
    def add(self, book):
        self.books_by_title[book.title].append(book)

        

class Book:
    def __init__(self, title, author):
        self.id = None
        self.title = title
        self.author = author
        self.series = None
        self.series_number = None
        self.pages_reported_by_kindle = None
        self.goodreads_link = None
        self.average_rating = None
        self.number_of_ratings = None

    @classmethod
    def load_books_from_db(cls):
        conn = sqlite3.connect(DB_NAME)
        create_table_if_not_exists(conn)
        books = select_all_books(conn)
        conn.close()
        return books

    def sync_with_db(self):
        conn = sqlite3.connect(DB_NAME)
        insert_or_replace_book(conn, self)
        conn.close()

    def populate_from_goodreads(self):
        if self.goodreads_link is not None:
            return

        goodreads_book = goodreads.find_book_on_goodreads(self)
        if not goodreads_book:
            raise ValueError("Failed to find book on goodreads.")
        self._populate_from_goodreads_book(goodreads_book)
    
    def _populate_from_goodreads_book(self, goodreads_book):
        if not goodreads_book.goodreads_link:
            raise ValueError("GoodreadsBook missing goodreads_link!")

        id = None
        match = re.search(r'/book/show/(\d+)', goodreads_book.goodreads_link)
        if match:
            id = int(match.group(1))
        if not id:
            raise ValueError(f"Failed to derive id from goodreads_link: {goodreads_book.goodreads_link}!")

        self.id = id
        self.title = goodreads_book.title
        self.author = goodreads_book.author
        self.pages_reported_by_kindle = goodreads_book.pages_reported_by_kindle
        self.goodreads_link = goodreads_book.goodreads_link
        self.average_rating = goodreads_book.average_rating
        self.number_of_ratings = goodreads_book.number_of_ratings
        self.series = goodreads_book.series
        self.series_number = goodreads_book.series_number
        logging.debug(f"Populated book from goodreads: {self.title} ({self.author}).")

    def refresh_if_part_of_series_now_on_goodreads(self):
        """ Returns True if refreshed. """
        if self.series:
            raise ValueError("refresh_if_part_of_series_now_on_goodreads should only be called when book has no series, programmer error!")

        goodreads_book = goodreads.load_goodreads_book_from_url(self.goodreads_link)
        if goodreads_book.series:
            self._populate_from_goodreads_book(goodreads_book)
            return True
        else:
            return False
    
    def find_books_from_series(self):
        series_link = goodreads.series_link_from_book(self)
        if not series_link:
            raise ValueError(f"Failed to find series link for book ({self.title}).")

        books = []
        for book_url in goodreads.book_urls_from_series_url(series_link):
            goodreads_book = goodreads.load_goodreads_book_from_url(book_url)

            book = Book('', '')
            book._populate_from_goodreads_book(goodreads_book)
            books.append(book)

        return books


def create_table_if_not_exists(conn):
    """ create a table for storing book data """
    try:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS books
                     (id INTEGER PRIMARY KEY,
                      title TEXT,
                      series TEXT,
                      series_number TEXT,
                      author TEXT,
                      pages_reported_by_kindle INTEGER,
                      goodreads_link TEXT,
                      average_rating REAL,
                      number_of_ratings INTEGER)''')
        conn.commit()
    except sqlite3.Error as e:
        logging.error(e)
        raise e

def insert_or_replace_book(conn, book):
    sql = ''' INSERT OR REPLACE INTO books(id,title,series,series_number,author,pages_reported_by_kindle,goodreads_link,average_rating,number_of_ratings)
              VALUES(?,?,?,?,?,?,?,?,?) '''
    cur = conn.cursor()
    cur.execute(sql, (book.id, book.title, book.series, book.series_number, book.author, book.pages_reported_by_kindle, book.goodreads_link, book.average_rating, book.number_of_ratings))
    conn.commit()
    return cur.lastrowid

def select_all_books(conn):
    """ Query all books in the database """
    cur = conn.cursor()
    cur.execute("SELECT id,title,series,series_number,author,pages_reported_by_kindle,goodreads_link,average_rating,number_of_ratings FROM books")

    books = []
    for row in cur.fetchall():
        book = Book(title=row[1], author=row[4])
        book.id = row[0]
        book.series = row[2]
        book.series_number = row[3]
        book.pages_reported_by_kindle = row[5]
        book.goodreads_link = row[6]
        book.average_rating = row[7]
        book.number_of_ratings = row[8]
        books.append(book)
    return books
