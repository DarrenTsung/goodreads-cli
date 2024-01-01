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
    response = requests_get_with_retry(url)
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

def follow_reddit_releases_link(url):
    response = requests_get_with_retry(url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    for a_element in soup.find_all('a'):
        text = a_element.text.strip().lower()
        if text not in LOWERCASE_MONTHS:
            continue

        if 'href' not in a_element.attrs:
            continue

        if not a_element['href'].startswith('https://www.reddit.com/r/litrpg/comments/'):
            continue

        return a_element['href']
