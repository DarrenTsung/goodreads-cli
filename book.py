#!/usr/bin/env python3
import goodreads
import pprint as pp
import logging
import re
import sqlite3
from collections import defaultdict

DB_NAME = "books.db"

class BooksBySeries:
    def __init__(self, books_by_series):
        self.books_by_series = books_by_series

    @classmethod
    def from_books(cls, books):
        books_by_series = defaultdict(list)
        for book in books.values():
            if not book.series or not book.series_number:
                continue

            while len(books_by_series[book.series]) < book.series_number:
                books_by_series[book.series].append(None)
            books_by_series[book.series][book.series_number-1] = book
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

    def any_books_with_less_than_rating_in_series(self, series, rating):
        if series not in self.books_by_series:
            raise ValueError(f"Failed to find series ({series}) in BooksBySeries, programmer error!")

        for book in self.books_by_series:
            if book.average_rating < rating:
                return True
        return False
    

        

class Book:
    def __init__(self, title, author):
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
                     (title TEXT PRIMARY KEY,
                      series TEXT,
                      series_number INTEGER,
                      author TEXT,
                      pages_reported_by_kindle INTEGER,
                      goodreads_link TEXT,
                      average_rating REAL,
                      number_of_ratings INTEGER)''')
        conn.commit()
    except sqlite3.Error as e:
        logging.error(e)
        raise e

def book_exists(conn, title):
    """ Check if a book already exists in the database """
    sql = 'SELECT 1 FROM books WHERE title = ?'
    cur = conn.cursor()
    cur.execute(sql, (title,))
    return cur.fetchone() is not None

def insert_or_replace_book(conn, book):
    sql = ''' INSERT OR REPLACE INTO books(title,series,series_number,author,pages_reported_by_kindle,goodreads_link,average_rating,number_of_ratings)
              VALUES(?,?,?,?,?,?,?,?) '''
    cur = conn.cursor()
    cur.execute(sql, (book.title, book.series, book.series_number, book.author, book.pages_reported_by_kindle, book.goodreads_link, book.average_rating, book.number_of_ratings))
    conn.commit()
    return cur.lastrowid

def select_all_books(conn):
    """ Query all books in the database """
    cur = conn.cursor()
    cur.execute("SELECT title,series,series_number,author,pages_reported_by_kindle,goodreads_link,average_rating,number_of_ratings FROM books")

    books = []
    for row in cur.fetchall():
        book = Book(title=row[0], author=row[3])
        book.series = row[1]
        book.series_number = row[2]
        book.pages_reported_by_kindle = row[4]
        book.goodreads_link = row[5]
        book.average_rating = row[6]
        book.number_of_ratings = row[7]
        books.append(book)
    return books
