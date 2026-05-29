#!/usr/bin/env python3
import requests
import time
import json
import os
from bs4 import BeautifulSoup
import logging
from fuzzywuzzy import fuzz
from urllib.parse import urlparse
import re
import pprint as pp
from utils import stripped_title, stripped

# Goodreads now sits behind an AWS WAF JavaScript challenge (responds with HTTP 202 and
# x-amzn-waf-action: challenge). Plain `requests` can't solve it, so we use a headless
# browser once to mint an `aws-waf-token` cookie and reuse it across requests. The token
# is bound to the User-Agent, so the same UA must be used everywhere.
_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
_WAF_COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".goodreads_waf_cookies.json")
_session = None


def _apply_cookies(session, cookies):
    for cookie in cookies:
        session.cookies.set(cookie['name'], cookie['value'], domain=cookie.get('domain'))


def _get_session():
    """Return a process-wide session, seeded with any WAF cookies cached from a previous run."""
    global _session
    if _session is None:
        _session = requests.Session()
        if os.path.exists(_WAF_COOKIE_FILE):
            try:
                with open(_WAF_COOKIE_FILE) as f:
                    _apply_cookies(_session, json.load(f))
            except (json.JSONDecodeError, OSError):
                pass
    return _session


def _is_waf_challenge(response):
    return response.status_code == 202 or response.headers.get('x-amzn-waf-action') == 'challenge'


def _solve_waf_challenge(url):
    """Launch a headless browser to solve the AWS WAF challenge for `url`, persist and return its cookies."""
    from playwright.sync_api import sync_playwright

    logging.info("Goodreads returned a WAF challenge; solving it with a headless browser..")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(user_agent=_USER_AGENT)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            # Give the challenge JS time to run, mint the token, and reload.
            page.wait_for_timeout(6000)
            cookies = context.cookies()
        finally:
            browser.close()

    try:
        with open(_WAF_COOKIE_FILE, 'w') as f:
            json.dump(cookies, f)
    except OSError:
        pass
    return cookies

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

def load_goodreads_book_from_url(url, max_retries=3, backoff_factor=0.5):
    def fetch_data():
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
        book_title_element = soup.find('h1', {'data-testid': 'bookTitle'})
        if not book_title_element:
            raise AttributeError("Title element not found after retries")
        book_title = book_title_element.text.strip()

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
            series_match = re.match(r'Book (.*) in the (.+) series', series_text)
            if series_match:
                series_number = series_match.group(1)
                series_name = series_match.group(2)
            else:
                logging.debug(f"Found unmatched series text: {series_text}, ignoring series information!")

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

    return retry_with_backoff(fetch_data, max_retries, backoff_factor)

def find_book_on_goodreads(book):
    # Use the search_result_for_book to get the best match
    best_match = search_result_for_book(book)
    if best_match:
        return load_goodreads_book_from_url(best_match)
    else:
        return None

def series_link_from_book(book, max_retries=3, backoff_factor=0.5):
    def fetch_data():
        response = requests_get_with_retry(book.goodreads_link)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        series_link_element = soup.find('h3', class_='Text Text__title3 Text__italic Text__regular Text__subdued').find('a')
        if not series_link_element or 'href' not in series_link_element.attrs:
            raise AttributeError("Series link element not found after retries")
        return series_link_element['href']

    return retry_with_backoff(fetch_data, max_retries, backoff_factor)

def description_text_for_book(book, max_retries=3, backoff_factor=0.5):
    def fetch_data():
        response = requests_get_with_retry(book.goodreads_link)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        description_element = soup.find('div', {'data-testid': 'description'})
        if not description_element:
            raise AttributeError("Description element not found after retries")
        return description_element.text

    return retry_with_backoff(fetch_data, max_retries, backoff_factor)

def description_and_pubdate_for_book(book, max_retries=3, backoff_factor=0.5):
    """Fetch the book page once and return (description_text, publication_info_text).

    publication_info_text is the raw Goodreads string (e.g. "First published April 5,
    2021") or None if not present.
    """
    def fetch_data():
        response = requests_get_with_retry(book.goodreads_link)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        description_element = soup.find('div', {'data-testid': 'description'})
        if not description_element:
            raise AttributeError("Description element not found after retries")

        pub_element = soup.find('p', {'data-testid': 'publicationInfo'})
        pub_text = pub_element.text.strip() if pub_element else None
        return description_element.text, pub_text

    return retry_with_backoff(fetch_data, max_retries, backoff_factor)

def book_urls_from_series_url(series_url, max_retries=3, backoff_factor=0.5):
    def fetch_data():
        response = requests_get_with_retry(series_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        book_urls = []
        for book_element in soup.find_all('div', {'class': 'listWithDividers__item'}):
            found_any_book_match = False
            for h3_element in book_element.find_all('h3'):
                if h3_element.text.strip().startswith('Book '):
                    found_any_book_match = True

            # There are some collections / random things in the series list, ignore.
            if not found_any_book_match:
                continue

            book_link = book_element.find('a', itemprop='url')
            book_urls.append(f"https://www.goodreads.com/{book_link['href']}")
        return book_urls

    return retry_with_backoff(fetch_data, max_retries, backoff_factor)

def search_result_for_book(book, max_retries=3, backoff_factor=0.5):
    book_authors = [a.strip() for a in book.author.split('&')]
    search_queries = [f"{stripped(book.title)}+{stripped(book.author)}", stripped(book.title)]
    for search_query in search_queries:
        def fetch_data():
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

        return retry_with_backoff(fetch_data, max_retries, backoff_factor)

def requests_get_with_retry(url, max_retries=10, backoff_factor=0.5, headers=None):
    """Send a GET request with a session and retry on errors with exponential backoff, with browser-like headers."""
    # Reuse a process-wide session so a solved WAF token (cookie) is shared across requests.
    session = _get_session()
    # Set default headers to mimic a browser if none are provided. The User-Agent must match
    # the one used to solve the WAF challenge, since the token cookie is bound to it.
    if headers is None:
        headers = {
            'User-Agent': _USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'DNT': '1',  # Do Not Track Request Header
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
    session.headers.update(headers)
    retries = 0
    waf_solve_attempts = 0
    while True:
        response = session.get(url, allow_redirects=True, headers=headers)
        if _is_waf_challenge(response):
            if waf_solve_attempts >= 2:
                raise RuntimeError(f"Unable to clear Goodreads WAF challenge for {url}")
            waf_solve_attempts += 1
            _apply_cookies(session, _solve_waf_challenge(url))
            continue
        if response.status_code // 100 == 2:
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # This is used so https://www.reddit.com/r/litrpg/comments/1l1mosi/ gets
            # redirected to https://www.reddit.com/r/litrpg/comments/1l1mosi/june_2025_releases_promotions/
            canonical_url_div = soup.find("div", id="canonical-url-updater")
            if not canonical_url_div:
                return response

            if 'value' not in canonical_url_div.attrs:
                return response

            if url == canonical_url_div['value']:
                return response

            print(f"Redirected from {url} to {canonical_url_div['value']}")
            url = canonical_url_div['value']
        elif retries >= max_retries:
            response.raise_for_status()
            return response
        elif response.status_code // 100 == 5:
            retries += 1
            time.sleep(backoff_factor * (2 ** (retries - 1)))
        else:
            response.raise_for_status()

def retry_with_backoff(fetch_data_fn, max_retries=3, backoff_factor=0.5):
    retries = 0
    while retries < max_retries:
        try:
            return fetch_data_fn()
        except Exception as e:
            retries += 1
            if retries >= max_retries:
                raise e
            time.sleep(backoff_factor * (2 ** (retries - 1)))
