#!/usr/bin/env python3
import requests
import time
from bs4 import BeautifulSoup
from fuzzywuzzy import fuzz
from urllib.parse import urlparse
import re
import pprint as pp

class GoodreadsBook:
    def __init__(self, title, author, pages_reported_by_kindle, goodreads_link, average_rating, number_of_ratings, series, series_number):
        self.title = title
        self.author = author
        self.pages_reported_by_kindle = pages_reported_by_kindle
        self.goodreads_link = goodreads_link
        self.average_rating = average_rating
        self.number_of_ratings = number_of_ratings
        self.series = series
        self.series_number = series_number

def load_goodreads_book_from_url(url):
    response = requests_get_with_retry(url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    # Extract the number of pages and kindle edition text
    pages_text = soup.find('p', {'data-testid': 'pagesFormat'}).text.strip()
    pages_number_match = re.search(r'\d+', pages_text)
    pages_number = int(pages_number_match.group()) if pages_number_match else None
    # Extract the title
    book_title = soup.find('h1', {'data-testid': 'bookTitle'}).text.strip()
    # Extract the author
    authors = set()
    for author_a_element in soup.find_all('a', {'class': 'ContributorLink'}):
        authors.add(author_a_element.find('span', {'class': 'ContributorLink__name', 'data-testid': 'name'}).text)
    author_text = ", ".join(authors)
    author = re.sub(r'\s+', ' ', author_text).strip()
    # Extract the series if it exists
    series_info = soup.find('h3', class_='Text Text__title3 Text__italic Text__regular Text__subdued')
    series_name = None
    series_number = None
    if series_info and 'aria-label' in series_info.attrs:
        series_text = series_info['aria-label']
        series_match = re.match(r'Book (\d+) in the (.+) series', series_text)
        if series_match:
            series_number = int(series_match.group(1))
            series_name = series_match.group(2)

    # Extract the average rating
    average_rating_div = soup.find('div', class_='RatingStatistics__rating')
    average_rating = float(average_rating_div.text) if average_rating_div else 'Unknown'
    # Extract the number of ratings
    ratings_count_span = soup.find('span', {'data-testid': 'ratingsCount'})
    ratings_count_text = ratings_count_span.text.strip() if ratings_count_span else ''
    ratings_count_match = re.search(r'\d+', ratings_count_text.replace(',', ''))
    number_of_ratings = int(ratings_count_match.group()) if ratings_count_match else 0
    parsed_url = urlparse(url)
    stripped_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
    return GoodreadsBook(
        title=book_title, 
        author=author,
        pages_reported_by_kindle=pages_number, 
        goodreads_link=stripped_url, 
        average_rating=average_rating, 
        number_of_ratings=number_of_ratings,
        series=series_name,
        series_number=series_number,
    )

def find_book_on_goodreads(book):
    # Use the search_result_for_book to get the best match
    best_match = search_result_for_book(book)
    if best_match:
        return load_goodreads_book_from_url(best_match['link'])
    else:
        return None

def series_link_from_book(book):
    response = requests_get_with_retry(book.goodreads_link)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    series_link_element = soup.find('h3', class_='Text Text__title3 Text__italic Text__regular Text__subdued').find('a')
    if series_link_element and 'href' in series_link_element.attrs:
        return series_link_element['href']
    else:
        return None

def description_text_for_book(book):
    response = requests_get_with_retry(book.goodreads_link)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    description_text = soup.find('div', {'data-testid': 'description'}).text
    return description_text

def book_urls_from_series_url(series_url):
    response = requests_get_with_retry(series_url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    book_urls = []
    for book_element in soup.find_all('div', {'class': 'listWithDividers__item'}):
        found_any_book_match = False
        for h3_element in book_element.find_all('h3'):
            if re.match(r'^Book (\d+)$', h3_element.text.strip()):
                found_any_book_match = True

        # There are some collections / random things in the series list, ignore.
        if not found_any_book_match:
            continue

        book_link = book_element.find('a', itemprop='url')
        book_urls.append(f"https://www.goodreads.com/{book_link['href']}")
    return book_urls

def search_result_for_book(book):
    # Sanitize the search query by removing special characters
    sanitized_title = re.sub(r'[^\w\s]', '', book.title)
    sanitized_author = re.sub(r'[^\w\s]', '', book.author)
    search_query = f"{sanitized_title}+{sanitized_author}"
    response = requests_get_with_retry(f"https://www.goodreads.com/search?q={search_query}")
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    max_score = 0
    best_match = None
    for tr in soup.find_all('tr', {'itemtype': 'http://schema.org/Book'}):
        title_link = tr.find('a', title=True)
        author_link = tr.find('a', class_='authorName')
        if title_link and author_link:
            title_text = title_link.get('title')
            title_text = re.sub(r'\s+', ' ', title_text).strip()
            author_name = author_link.find('span', itemprop='name').text
            author_name = re.sub(r'\s+', ' ', author_name).strip()
            # Perform fuzzy matching for title and author
            title_ratio = fuzz.partial_ratio(book.title.lower(), title_text.lower())
            author_ratio = fuzz.partial_ratio(book.author.lower(), author_name.lower())
            # You can adjust the threshold according to your needs
            # Combine the scores
            combined_score = title_ratio + author_ratio
            if combined_score > 180 and combined_score > max_score:
                max_score = combined_score
                best_match = {
                    'title': title_text,
                    'author': author_name,
                    'link': f"https://www.goodreads.com{title_link.get('href')}"
                }
    return best_match

def requests_get_with_retry(url, max_retries=5, backoff_factor=0.3):
    """Send a GET request and retry on 5xx errors with exponential backoff."""
    retries = 0
    while True:
        response = requests.get(url)
        if response.status_code // 100 != 5 or retries >= max_retries:
            response.raise_for_status()
            return response
        retries += 1
        time.sleep(backoff_factor * (2 ** (retries - 1)))
