# goodreads-cli

CLI tool for filtering and displaying ratings for books from some source (reddit, etc).

## Commands

```
source venv/bin/activate
pip install .

python3 ./main.py input --follow-reddit-releases --reddit-releases-url https://www.reddit.com/r/litrpg/comments/1eheyaq/august_2024_releases_promotions/
```

Adding a book and rating it:

```
python3 ./main.py input --manual-goodreads "https://www.goodreads.com/book/show/207944665-a-soldier-s-life"
python3 ./main.py rate-continuous --author "Always RollsAOne"
```
