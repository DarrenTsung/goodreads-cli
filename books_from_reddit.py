import requests
import re
from bs4 import BeautifulSoup
import pprint as pp
from book import Book
from goodreads import requests_get_with_retry

LOWERCASE_MONTHS = set([
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december"
])

def find_books_from_table_in_reddit_releases_post(url):
    # www.reddit.com now serves a JS shell with no table; old.reddit.com renders the real HTML.
    response = requests_get_with_retry(_old_reddit_url(url))
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    table = soup.find('table')
    if not table:
        return []

    books = []
    for row in table.find_all('tr'):
        columns = row.find_all('td')
        if len(columns) == 3:
            title = columns[0].get_text().strip()
            # Remove series number (e.g. #6)
            title = re.sub(r'#\d+', '', title)
            # Remove content in parentheses 
            title = re.sub(r'\([^\)]+\)', '', title)
            title = title.strip()
            author = columns[1].get_text().strip()
            books.append(Book(title, author))
    return books

def _old_reddit_url(url):
    # The wiki only renders parseable HTML on old.reddit.com; www/new serve a JS shell.
    return re.sub(r'https?://(www|new)\.reddit\.com', 'https://old.reddit.com', url)

def _normalize_reddit_host(url):
    # Monthly post fetching (and its canonical-url redirect handling) expects www.reddit.com.
    return re.sub(r'https?://(old|new)\.reddit\.com', 'https://www.reddit.com', url)

def find_release_thread_urls_from_wiki(url):
    """Parse the new-releases wiki index page into an ordered list of monthly release thread URLs."""
    response = requests_get_with_retry(_old_reddit_url(url))
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    content = soup.find('div', class_='wiki-page-content')
    if not content:
        return []

    urls = []
    seen = set()
    for a_element in content.find_all('a'):
        href = a_element.get('href')
        if not href:
            continue
        # Only follow links to release threads (subreddit comment posts).
        if not re.match(r'https?://(www|new|old)\.reddit\.com/r/litrpg/comments/', href):
            continue
        href = _normalize_reddit_host(href)
        if href in seen:
            continue
        seen.add(href)
        urls.append(href)
    return urls

def follow_reddit_releases_link(url):
    response = requests_get_with_retry(_old_reddit_url(url))
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    for a_element in soup.find_all('a'):
        text = a_element.text.strip().lower()
        if text not in LOWERCASE_MONTHS:
            continue

        if 'href' not in a_element.attrs:
            continue

        if not re.match(r'https?://(www|new|old)\.reddit\.com/r/litrpg/comments/', a_element['href']):
            continue

        return _normalize_reddit_host(a_element['href'])
