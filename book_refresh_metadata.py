#!/usr/bin/env python3
import goodreads
import pprint as pp
import logging
import sqlite3
from collections import defaultdict
from datetime import date, timedelta 

DB_NAME = "book_refresh_metadata.db"

MIN_TIME_BEFORE_NEXT_REFRESH = timedelta(days=15)

class BookRefreshMetadata:
    def __init__(self, book_refreshes_by_title, book_refreshes_by_series):
        self.book_refreshes_by_title = book_refreshes_by_title
        self.book_refreshes_by_series = book_refreshes_by_series

    def should_refresh_title(self, title):
        return self.should_refresh_from_last_refresh(self.book_refreshes_by_title.get(title))

    def should_refresh_series(self, series):
        return self.should_refresh_from_last_refresh(self.book_refreshes_by_series.get(series))

    def handle_book_newly_populated(self, book):
        if book.series:
            self.handle_series_refreshed(book.series)
        else:
            self.handle_title_refreshed(book.title)
    
    def handle_title_refreshed(self, title):
        refresh_date = date.today()
        self.book_refreshes_by_title[title] = refresh_date
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO book_refresh_by_title (title, last_refresh) VALUES (?, ?)", (title, refresh_date.isoformat()))
        conn.commit()
        conn.close()
    
    def handle_series_refreshed(self, series):
        refresh_date = date.today()
        self.book_refreshes_by_series[series] = refresh_date
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO book_refresh_by_series (series, last_refresh) VALUES (?, ?)", (series, refresh_date.isoformat()))
        conn.commit()
        conn.close()
    
    def should_refresh_from_last_refresh(self, last_refresh):
        if last_refresh is None:
            return True
        return (date.today() - last_refresh) >= MIN_TIME_BEFORE_NEXT_REFRESH

    @classmethod
    def load_from_db(cls):
        conn = sqlite3.connect(DB_NAME)

        create_table_if_not_exists(conn)

        cur = conn.cursor()
        cur.execute("SELECT title, last_refresh FROM book_refresh_by_title")
        book_refreshes_by_title = {}
        for row in cur.fetchall():
            book_refreshes_by_title[row[0]] = date(*map(int, row[1].split('-')))

        cur = conn.cursor()
        cur.execute("SELECT series, last_refresh FROM book_refresh_by_series")
        book_refreshes_by_series = {}
        for row in cur.fetchall():
            book_refreshes_by_series[row[0]] = date(*map(int, row[1].split('-')))

        conn.close()
        return BookRefreshMetadata(book_refreshes_by_title, book_refreshes_by_series)

def create_table_if_not_exists(conn):
    try:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS book_refresh_by_title
                     (title TEXT PRIMARY KEY,
                      last_refresh TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS book_refresh_by_series
                     (series TEXT PRIMARY KEY,
                      last_refresh TEXT)''')
        conn.commit()
    except sqlite3.Error as e:
        logging.error(e)
        raise e