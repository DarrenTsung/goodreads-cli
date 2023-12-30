#!/usr/bin/env python3
from collections import defaultdict
import logging
import goodreads
import pprint as pp
import sqlite3
from enum import Enum

DB_NAME = "book_ratings.db"

class Tier(Enum):
    F = 'F'
    B = 'B'
    A = 'A'
    S = 'S'

class BookRatings:
    def __init__(self, rating_by_title, ratings_by_series):
        self.rating_by_title = rating_by_title
        self.ratings_by_series = ratings_by_series
    
    def matching_ratings_for_book(self, book):
        """ 
        Returns array of `BookRating`s if any ratings present. 
        Contains either the direct rating for the book or the ratings for the series.
        """
        if book.title in self.rating_by_title:
            return [self.rating_by_title[book.title]]

        if book.series:
            if book.series in self.ratings_by_series:
                return self.ratings_by_series[book.series]

        return None

    def has_directly_rated_book(self, book):
        return book.title in self.rating_by_title
    
    def has_rated_book_or_series_as_f_tier(self, book):
        matching_ratings = self.matching_ratings_for_book(book)
        for rating in matching_ratings:
            if rating.tier == Tier.F:
                return True

        return False

class BookRating:
    def __init__(self, title, series, tier, interested):
        self.title = title
        self.series = series
        self.tier = tier
        self.interested = interested

    @classmethod
    def from_book(cls, book):
        BookRating(title=book.title, series=book.series, tier=None, interested=None)

    @classmethod
    def load_ratings_from_db(cls):
        conn = sqlite3.connect(DB_NAME)
        create_table_if_not_exists(conn)
        ratings = select_all_ratings(conn)
        conn.close()

        rating_by_title = { rating.title: rating for rating in ratings }
        ratings_by_series = defaultdict(list)
        for rating in ratings:
            if rating.series is None:
                continue
            ratings_by_series[rating.series].append(rating)

        return BookRatings(rating_by_title=rating_by_title, ratings_by_series=ratings_by_series)

    def sync_with_db(self):
        conn = sqlite3.connect(DB_NAME)
        if not rating_exists(conn, self.title):
            insert_rating(conn, self)
        conn.close()

def create_table_if_not_exists(conn):
    try:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS book_ratings 
                    (
                        id INTEGER PRIMARY KEY,
                        title TEXT,
                        series TEXT,
                        rating INTEGER,
                        interested BOOLEAN,
                    )''')
        conn.commit()
    except sqlite3.Error as e:
        logging.error(e)
        raise e

def insert_rating(conn, rating):
    """ Insert a new rating into the ratings table """
    sql = ''' INSERT INTO book_ratings(title,series,tier,interested)
              VALUES(?,?,?,?) '''
    cur = conn.cursor()
    cur.execute(sql, (rating.title,rating.series,rating.tier.value,rating.interested))
    conn.commit()
    return cur.lastrowid

def rating_exists(conn, title):
    """ Check if a rating already exists in the database """
    sql = 'SELECT 1 FROM book_ratings WHERE title = ?'
    cur = conn.cursor()
    cur.execute(sql, (title))
    return cur.fetchone() is not None

def select_all_ratings(conn):
    """ Query all ratings in the database """
    cur = conn.cursor()
    cur.execute("SELECT title,series,tier,interested FROM book_ratings")

    ratings = []
    for row in cur.fetchall():
        rating = BookRating(title=row[0], series=row[1], tier=Tier(row[2]), interested=row[3])
        ratings.append(rating)
    return ratings 
