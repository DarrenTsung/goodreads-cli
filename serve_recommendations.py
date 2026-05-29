#!/usr/bin/env python3
"""Interactive recommendations server.

Serves the preference-fit ranking with S/A/B/F + "Not interested" buttons on each row.
Clicking a button records the rating in book_ratings.db (via the existing BookRating
model) and removes the book — and its whole series — from the list in real time.

  python3 serve_recommendations.py            # http://localhost:8765
  python3 serve_recommendations.py --port 9000 --rating-weight 4

No new dependencies (stdlib http.server). Re-reads ratings on every page load, so the
list always reflects what you've rated.
"""
import argparse
import json
import logging
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import classify_and_rank as cr
import theme_scan_lib as lib
from book import Book
from book_rating import BookRating, Tier

RATINGS_DB = "book_ratings.db"

PROFILE_JSON = "theme_profile.json"
CLASSIFICATIONS_JSON = "classifications.json"

RATE_JS = """
<div id="toast"></div>
<script>
let toastTimer;
function toast(msg, undoId) {
  const el = document.getElementById('toast');
  el.innerHTML = '';
  el.append(document.createTextNode(msg));
  if (undoId != null) {
    const a = document.createElement('a');
    a.textContent = 'Undo'; a.href = '#';
    a.style.cssText = 'color:#7bb1ff;margin-left:12px;font-weight:700;pointer-events:auto;text-decoration:underline';
    a.onclick = async (ev) => { ev.preventDefault();
      await fetch('/unrate', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({id: undoId})});
      location.reload();
    };
    el.append(a);
  }
  el.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove('show'), 5000);
}
document.getElementById('t').addEventListener('click', async (e) => {
  const btn = e.target.closest('button.rate');
  if (!btn) return;
  const tr = btn.closest('tr');
  const id = parseInt(tr.dataset.id), series = tr.dataset.series, act = btn.dataset.act;
  const cell = btn.closest('.rate-cell');
  cell.querySelectorAll('button').forEach(b => b.disabled = true);
  try {
    const res = await fetch('/rate', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id, action: act})
    });
    const data = await res.json();
    if (!data.ok) { toast('Error: ' + (data.error || 'failed'));
      cell.querySelectorAll('button').forEach(b => b.disabled = false); return; }
    const victims = series
      ? [...document.querySelectorAll('#t tbody tr')].filter(r => r.dataset.series === series)
      : [tr];
    victims.forEach(r => r.classList.add('removing'));
    setTimeout(() => { victims.forEach(r => r.remove()); renumber(); }, 250);
    const label = {skip:'not interested', interested:'saved (interested)'}[act] || act + '-tier';
    const extra = victims.length > 1 ? ` (+${victims.length - 1} from series)` : '';
    toast(`"${data.title}" \\u2192 ${label}${extra}`, data.id);
  } catch (err) {
    toast('Error: ' + err);
    cell.querySelectorAll('button').forEach(b => b.disabled = false);
  }
});
</script>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # quiet

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self._send(404, "not found")
            return
        page = self.server.render_page()
        self._send(200, page)

    def do_POST(self):
        if self.path not in ("/rate", "/unrate"):
            self._send(404, "not found")
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or "{}")
            if self.path == "/rate":
                result = self.server.record_rating(int(payload["id"]), payload["action"])
            else:
                result = self.server.unrate(int(payload["id"]))
            self._send(200, json.dumps(result), "application/json")
        except Exception as e:
            self._send(200, json.dumps({"ok": False, "error": str(e)}), "application/json")


class RecServer(ThreadingHTTPServer):
    def __init__(self, addr, profile, classifications, rating_weight):
        super().__init__(addr, Handler)
        self.profile = profile
        self.classifications = classifications
        self.rating_weight = rating_weight

    def render_page(self):
        books = Book.load_books_from_db()
        self.books_by_id = {b.id: b for b in books}
        ratings = BookRating.load_ratings_from_db()
        rows = cr.compute_rows(self.profile, self.classifications, self.books_by_id,
                               ratings=ratings, series_aware=True, rating_weight=self.rating_weight)
        out = [cr.HTML_HEAD.format(
            generated=datetime.now(timezone.utc).isoformat(),
            total=len(rows), rate_col="<th>Rate</th>")]
        for i, row in enumerate(rows, 1):
            out.append(cr.render_row(row, i, with_buttons=True))
        # HTML_TAIL closes the table + sort script + body; inject rate UI before </body>.
        out.append(cr.HTML_TAIL.replace("</body></html>", RATE_JS + "</body></html>"))
        return "\n".join(out)

    def _book(self, book_id):
        return getattr(self, "books_by_id", {}).get(book_id) or \
            {b.id: b for b in Book.load_books_from_db()}.get(book_id)

    def record_rating(self, book_id, action):
        book = self._book(book_id)
        if not book:
            return {"ok": False, "error": f"unknown book id {book_id}"}
        ratings = BookRating.load_ratings_from_db()
        disp = book.series or book.title
        if ratings.has_directly_rated_book(book):
            return {"ok": True, "title": disp, "id": book_id}  # already rated; just drop it
        if action == "skip":
            ratings.mark_book_as_uninterested(book)
        elif action == "interested":
            ratings.mark_book_as_interested(book)
        elif action in ("S", "A", "B", "F"):
            ratings.mark_book_with_tier(book, Tier(action))
        else:
            return {"ok": False, "error": f"bad action {action}"}
        logging.info(f"Rated '{book.title}' ({book.series}) -> {action}")
        return {"ok": True, "title": disp, "id": book_id}

    def unrate(self, book_id):
        """Delete the rating row for this book (undo). The series reappears on reload."""
        book = self._book(book_id)
        if not book:
            return {"ok": False, "error": f"unknown book id {book_id}"}
        conn = sqlite3.connect(RATINGS_DB)
        conn.execute("DELETE FROM book_ratings WHERE title = ?", (book.title,))
        conn.commit()
        conn.close()
        logging.info(f"Un-rated '{book.title}'")
        return {"ok": True, "title": book.series or book.title}


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Serve the interactive recommendations page.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--rating-weight", type=float, default=cr.RATING_WEIGHT)
    args = parser.parse_args()

    with open(PROFILE_JSON) as f:
        profile = json.load(f)
    with open(CLASSIFICATIONS_JSON) as f:
        classifications = json.load(f)

    server = RecServer(("127.0.0.1", args.port), profile, classifications, args.rating_weight)
    url = f"http://localhost:{args.port}"
    logging.info(f"Serving recommendations at {url}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Stopped.")


if __name__ == "__main__":
    main()
