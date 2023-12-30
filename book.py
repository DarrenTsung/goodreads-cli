#!/usr/bin/env python3
import goodreads
import pprint as pp
import logging
import sqlite3

DB_NAME = "books.db"

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
        if not book_exists(conn, self.title):
            insert_book(conn, self)
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

def insert_book(conn, book):
    """ Insert a new book into the books table """
    sql = ''' INSERT INTO books(title,series,series_number,author,pages_reported_by_kindle,goodreads_link,average_rating,number_of_ratings)
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
