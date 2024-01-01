#!/usr/bin/env python3
import requests
import time
from bs4 import BeautifulSoup
from fuzzywuzzy import fuzz
from urllib.parse import urlparse
import re
import pprint as pp
from utils import stripped_title, stripped

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
    pages_number = None
    pages_element = soup.find('p', {'data-testid': 'pagesFormat'})
    if pages_element:
        pages_text = pages_element.text.strip()
        pages_number_match = re.search(r'\d+', pages_text)
        if pages_number_match:
            pages_number = int(pages_number_match.group()) 
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
        return load_goodreads_book_from_url(best_match)
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
    book_authors = [a.strip() for a in book.author.split('&')]
    search_queries = [f"{stripped(book.title)}+{stripped(book.author)}", stripped(book.title)]
    for search_query in search_queries:
        response = requests_get_with_retry(f"https://www.goodreads.com/search?q={search_query}")
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        max_score = 0
        best_match = None
        for tr in soup.find_all('tr', {'itemtype': 'http://schema.org/Book'}):
            title_link = tr.find('a', title=True)
            title_text = title_link.get('title')
            title_text = re.sub(r'\s+', ' ', title_text).strip()

            book_item_authors = []
            for a_element in tr.find_all('a', class_='authorName'):
                author = a_element.find('span', itemprop='name').text 
                author = re.sub(r'\s+', ' ', author).strip()
                book_item_authors.append(author)

            max_author_ratio = 0
            for item_author in book_item_authors:
                for book_author in book_authors:
                    author_ratio = fuzz.partial_ratio(stripped(book_author), stripped(item_author))
                    if author_ratio > max_author_ratio:
                        max_author_ratio = author_ratio

            title_ratio = fuzz.partial_ratio(stripped_title(book.title), stripped_title(title_text))

            # pp.pp(title_text)
            # pp.pp(book_item_authors)

            # pp.pp(f"max_author_ratio: {max_author_ratio}")
            # pp.pp(f"title_ratio: {title_ratio}")

            # Combine the scores with more weight on the title score
            combined_score = title_ratio + max_author_ratio
            # Adjust the threshold according to the new scoring system
            if max_author_ratio >= 90 and combined_score > max_score:
                max_score = combined_score
                best_match = f"https://www.goodreads.com{title_link.get('href')}"
        if best_match:
            return best_match

def requests_get_with_retry(url, max_retries=5, backoff_factor=0.3, headers=None):
    """Send a GET request with a session and retry on errors with exponential backoff, with browser-like headers."""
    session = requests.Session()
    # Set default headers to mimic a browser if none are provided
    if headers is None:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'DNT': '1',  # Do Not Track Request Header
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
    session.headers.update(headers)
    retries = 0
    while True:
        response = session.get(url, allow_redirects=True, headers=headers)
        if response.status_code // 100 == 2:
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # Dunno what this is, but implementing this for reddit redirects.
            shreddit_redirect = soup.find("shreddit-redirect")
            if not shreddit_redirect:
                return response

            if 'href' not in shreddit_redirect.attrs:
                return response
            
            url = f"https://reddit.com{shreddit_redirect['href']}"
        elif retries >= max_retries:
            response.raise_for_status()
            return response
        elif response.status_code // 100 == 5:
            retries += 1
            time.sleep(backoff_factor * (2 ** (retries - 1)))
        else:
            response.raise_for_status()
