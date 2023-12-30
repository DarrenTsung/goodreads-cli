import requests
import re
from bs4 import BeautifulSoup
import pprint as pp
from book import Book
from goodreads import requests_get_with_retry

def find_books_from_table_in_reddit_releases_post(url):
    if "releases_promotions" not in url:
        raise ValueError(f"Unexpected url: {url}, expected to find 'releases_promotions' within the url.")

    response = requests_get_with_retry(url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    table = soup.find('table')
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
