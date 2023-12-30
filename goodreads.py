#!/usr/bin/env python3
import requests
from bs4 import BeautifulSoup
from fuzzywuzzy import fuzz
from urllib.parse import urlparse
import re

class GoodreadsBook:
    def __init__(self, pages_reported_by_kindle, goodreads_link, average_rating, number_of_ratings):
        self.pages_reported_by_kindle = pages_reported_by_kindle
        self.goodreads_link = goodreads_link
        self.average_rating = average_rating
        self.number_of_ratings = number_of_ratings

def find_book_on_goodreads(book):
    # Use the search_result_for_book to get the best match
    best_match = search_result_for_book(book)
    if best_match:
        # Navigate to the book's Goodreads page
        response = requests.get(best_match['link'])
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        # Extract the number of pages and kindle edition text
        pages_info = soup.find('p', {'data-testid': 'pagesFormat'})
        pages_text = pages_info.text if pages_info else ''
        pages_number_match = re.search(r'\d+', pages_text)
        pages_number = int(pages_number_match.group()) if pages_number_match else None
        # Extract the average rating
        average_rating_div = soup.find('div', class_='RatingStatistics__rating')
        average_rating = float(average_rating_div.text) if average_rating_div else 'Unknown'
        # Extract the number of ratings
        ratings_count_span = soup.find('span', {'data-testid': 'ratingsCount'})
        ratings_count_text = ratings_count_span.text if ratings_count_span else ''
        ratings_count_match = re.search(r'\d+', ratings_count_text.replace(',', ''))
        number_of_ratings = int(ratings_count_match.group()) if ratings_count_match else 0
        parsed_url = urlparse(best_match['link'])
        stripped_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
        return GoodreadsBook(pages_reported_by_kindle=pages_number, goodreads_link=stripped_url, average_rating=average_rating, number_of_ratings=number_of_ratings)
    else:
        return None

def search_result_for_book(book):
    response = requests.get(f"https://www.goodreads.com/search?q={book.title}")
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    max_score = 0
    best_match = None
    for tr in soup.find_all('tr', {'itemtype': 'http://schema.org/Book'}):
        title_link = tr.find('a', title=True)
        author_link = tr.find('a', class_='authorName')
        if title_link and author_link:
            title_text = title_link.get('title')
            author_name = author_link.find('span', itemprop='name').text
            # Perform fuzzy matching for title and author
            title_ratio = fuzz.partial_ratio(book.title.lower(), title_text.lower())
            author_ratio = fuzz.partial_ratio(book.author.lower(), author_name.lower())
            # You can adjust the threshold according to your needs
            # Combine the scores
            combined_score = title_ratio + author_ratio
            if combined_score > max_score:
                max_score = combined_score
                best_match = {
                    'title': title_text,
                    'author': author_name,
                    'link': f"https://www.goodreads.com{title_link.get('href')}"
                }
    return best_match

def search_results_for_book(book):
    response = requests.get(f"https://www.goodreads.com/search?q={book.title}")
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')