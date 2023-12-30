import requests
import re
from bs4 import BeautifulSoup
import pprint as pp
from book import Book

def find_books_from_table_in_reddit_releases_post(url):
    if "releases_promotions" not in url:
        raise ValueError(f"Unexpected url: {url}, expected to find 'releases_promotions' within the url.")

    response = requests.get(url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    table = soup.find('table')
    books = []
    for row in table.find_all('tr'):
        columns = row.find_all('td')
        if len(columns) == 3:
            full_title = columns[0].get_text().strip()
            series_match = re.search(r'(.*)\s+\(([^)#]+)#?(\d+)\)$|(.+?)\s+#(\d+)', full_title)
            if series_match:
                if series_match.lastindex == 3:  # Matched with parentheses
                    title = series_match.group(1).strip()
                    series_name = series_match.group(2).strip()
                    series_number = int(series_match.group(3).strip())
                else:  # Matched without parentheses
                    title = series_match.group(0).strip()
                    series_name = series_match.group(4).strip()
                    series_number = int(series_match.group(5).strip())
            else:
                title = full_title
                series_name = None
                series_number = None
            author = columns[1].get_text().strip()
            books.append(Book(title, series_name, series_number, author))
    return books
