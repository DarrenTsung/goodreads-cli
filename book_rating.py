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
        Returns array of `BookRating`s. 
        Contains either the direct rating for the book or the ratings for the series if present.
        """
        ratings = []
        if book.title in self.rating_by_title:
            ratings.append(self.rating_by_title[book.title])

        if book.series and book.series in self.ratings_by_series:
            ratings.extend(self.ratings_by_series[book.series])

        return ratings

    def has_directly_rated_book(self, book):
        return book.title in self.rating_by_title
    
    def has_rated_series(self, series):
        return series in self.ratings_by_series

    def _mark_book_helper(self, book, rating_fn):
        if book.title in self.rating_by_title:
            raise ValueError("Should not rate book with existing rating, programmer error!")

        rating = BookRating.from_book(book)
        rating_fn(rating)
        rating.sync_with_db()
        self.rating_by_title[book.title] = rating
        if rating.series:
            self.ratings_by_series[rating.series].append(rating)

    def mark_book_with_tier(self, book, tier):
        self._mark_book_helper(book, lambda rating: setattr(rating, 'tier', tier))

    def mark_book_as_uninterested(self, book):
        self._mark_book_helper(book, lambda rating: setattr(rating, 'interested', False))

    def mark_book_as_interested(self, book):
        self._mark_book_helper(book, lambda rating: setattr(rating, 'interested', True))
    
    def has_rated_book_or_series_as_f_tier_or_uninterested(self, book):
        matching_ratings = self.matching_ratings_for_book(book)
        for rating in matching_ratings:
            if rating.tier == Tier.F:
                return True
            if rating.interested == False:
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
        return BookRating(title=book.title, series=book.series, tier=None, interested=None)

    @classmethod
    def load_ratings_from_db(cls):
        conn = sqlite3.connect(DB_NAME)
        create_table_if_not_exists(conn)
        ratings = select_all_ratings(conn)
        conn.close()
        for rating in ratings:
            if rating.interested is None and rating.tier is None:
                raise ValueError(f"Found invalid rating in DB with title ({rating.title}), please removing!")

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
                    (title TEXT PRIMARY KEY,
                    series TEXT,
                    tier TEXT,
                    interested BOOLEAN
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
    tier_value = None
    if rating.tier:
        tier_value = rating.tier.value
    cur.execute(sql, (rating.title, rating.series, tier_value, rating.interested))
    conn.commit()
    return cur.lastrowid

def rating_exists(conn, title):
    """ Check if a rating already exists in the database """
    sql = 'SELECT 1 FROM book_ratings WHERE title = ?'
    cur = conn.cursor()
    cur.execute(sql, (title,))
    return cur.fetchone() is not None

def select_all_ratings(conn):
    """ Query all ratings in the database """
    cur = conn.cursor()
    cur.execute("SELECT title,series,tier,interested FROM book_ratings")

    ratings = []
    for row in cur.fetchall():
        tier = None
        if row[2]:
            tier = Tier(row[2])
        rating = BookRating(title=row[0], series=row[1], tier=tier, interested=row[3])
        ratings.append(rating)
    return ratings 
