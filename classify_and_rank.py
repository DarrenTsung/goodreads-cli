#!/usr/bin/env python3
"""Auto-rate candidate books against the verified theme profile and rank by preference fit.

  python3 classify_and_rank.py                 # classify via claude, then write ranking
  python3 classify_and_rank.py --limit 20      # smoke test on the 20 most-rated candidates
  python3 classify_and_rank.py --rerank        # recompute scores from edited weights only (no claude)

Reads theme_profile.json (run build_profile.py first) and descriptions_cache.json.
Writes classifications.json (cache of claude output) and recommendations.md.
"""
import argparse
import html
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import theme_scan_lib as lib
from book import Book, BooksBySeries
from book_rating import BookRating

PROFILE_JSON = "theme_profile.json"
CLASSIFICATIONS_JSON = "classifications.json"
RECOMMENDATIONS_MD = "recommendations.md"
RECOMMENDATIONS_HTML = "recommendations.html"

DESC_CHARS = 700
BATCH_SIZE = 10
TIER_RANK = {"S": 4, "A": 3, "B": 2, "F": 0}

# How strongly the Goodreads start-of-series rating factors into the score.
# rating_term = RATING_WEIGHT * (start_rating - RATING_BASELINE); added to theme fit.
RATING_WEIGHT = 4.0
RATING_BASELINE = 4.0

CLASSIFY_TEMPLATE = """You classify LitRPG / progression-fantasy books for one specific reader.

This reader's taste: {summary}

Use ONLY these theme tags (assign every tag that genuinely applies to a book):
{taxonomy}

For each book below, respond with its applicable tags, a holistic fit score (0-100) for
THIS reader, a predicted tier (S=would love, A=great, B=good, F=would dislike/DNF), and a
one-sentence reason.

Respond with ONLY valid JSON (no prose, no fences): a JSON array where each element is:
{{"id": <the integer id given>, "tags": ["tag", ...], "fit": <0-100>, "tier": "S|A|B|F", "reason": "..."}}

=== BOOKS ===
{books}
"""


def build_batch_prompt(profile, batch, cache):
    taxonomy = "\n".join(f"- {t['tag']}: {t['description']}" for t in profile["taxonomy"])
    book_blocks = []
    for book in batch:
        desc = lib.get_description(cache, book.id) or ""
        if len(desc) > DESC_CHARS:
            desc = desc[:DESC_CHARS].rsplit(" ", 1)[0] + "..."
        series = f" [series: {book.series}]" if book.series else ""
        book_blocks.append(f"id={book.id} | {book.title}{series}\n{desc}")
    return CLASSIFY_TEMPLATE.format(
        summary=profile.get("summary", ""),
        taxonomy=taxonomy,
        books="\n\n".join(book_blocks),
    )


def classify_batch(profile, batch, cache, model):
    prompt = build_batch_prompt(profile, batch, cache)
    raw = lib.call_claude(prompt, model=model, timeout=300)
    results = lib.parse_json_response(raw)
    out = {}
    for r in results:
        out[str(r["id"])] = {
            "tags": r.get("tags", []),
            "llm_fit": r.get("fit"),
            "predicted_tier": r.get("tier"),
            "reasoning": r.get("reason", ""),
        }
    return out


def fit_score(tags, weights):
    return sum(weights.get(t, 0) for t in tags)


def write_md(profile, rows, total_classified):
    weights = profile["weights"]
    lines = [
        "# Recommendations by preference fit",
        "",
        f"_Generated {datetime.now(timezone.utc).isoformat()} from {total_classified} classified "
        f"candidates. Score = sum of your theme weights for each book's matched themes._",
        "",
        "| # | Fit | Pred | Title | Series | Themes | Why |",
        "|--:|--:|:--:|-------|--------|--------|-----|",
    ]
    for i, row in enumerate(rows, 1):
        b = row["book"]
        themes = ", ".join(
            f"{t}{'+' if weights.get(t,0)>0 else ''}{weights.get(t,0)}" for t in row["tags"]
        )
        reason = row["reasoning"].replace("|", "\\|")
        series = (b.series or "").replace("|", "\\|")
        if row.get("series_count", 1) > 1:
            series += f" (+{row['series_count'] - 1} more)"
        lines.append(
            f"| {i} | {row['fit_score']:+d} | {row.get('predicted_tier') or '?'} | "
            f"{b.title.replace('|', chr(92)+'|')} | {series} | {themes} | {reason} |"
        )
    lines.append("")
    with open(RECOMMENDATIONS_MD, "w") as f:
        f.write("\n".join(lines))


HTML_HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Recommendations by preference fit</title>
<style>
  :root {{
    --bg: #f6f7f9; --card: #fff; --ink: #1f2328; --muted: #6b7280;
    --line: #e6e8eb; --accent: #4a90d9; --hover: #f0f6fd;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0d1117; --card:#161b22; --ink:#e6edf3; --muted:#8b949e; --line:#272c33; --hover:#1b2330; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          margin: 0; padding: 2.5rem clamp(1rem, 4vw, 3rem); background: var(--bg); color: var(--ink); }}
  header {{ max-width: 1200px; margin: 0 auto 1.5rem; }}
  h1 {{ margin: 0 0 .3rem; font-size: 1.7rem; letter-spacing: -.02em; }}
  .meta {{ color: var(--muted); font-size: .9rem; }}
  .wrap {{ max-width: 1200px; margin: 0 auto; background: var(--card); border: 1px solid var(--line);
           border-radius: 14px; overflow: hidden; box-shadow: 0 1px 3px #0000000d, 0 8px 24px #0000000a; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ padding: 12px 16px; text-align: left; vertical-align: top; }}
  thead th {{ position: sticky; top: 0; z-index: 1; background: var(--card); cursor: pointer;
              user-select: none; font-size: .72rem; text-transform: uppercase; letter-spacing: .06em;
              color: var(--muted); border-bottom: 1px solid var(--line); white-space: nowrap; }}
  thead th:hover {{ color: var(--accent); }}
  thead th::after {{ content: " \\2195"; opacity: .3; font-size: .8em; }}
  tbody tr {{ border-top: 1px solid var(--line); }}
  tbody tr:first-child {{ border-top: none; }}
  tbody tr:hover {{ background: var(--hover); }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; color: var(--muted); }}
  td.rank {{ color: var(--muted); font-size: .85rem; }}
  .fit {{ display: inline-block; min-width: 2.4em; text-align: center; font-weight: 700;
          border-radius: 999px; padding: 2px 9px; font-variant-numeric: tabular-nums; }}
  .fit-pos {{ background: #1a7f3719; color: #1a7f37; }}
  .fit-neg {{ background: #c0392b19; color: #c0392b; }}
  .fit-zero {{ background: #8884; color: var(--muted); }}
  @media (prefers-color-scheme: dark) {{ .fit-pos {{ color:#3fb950; }} .fit-neg {{ color:#f85149; }} }}
  .tier {{ display: inline-block; min-width: 1.6em; text-align: center; font-weight: 700; font-size: .8rem;
           border-radius: 6px; padding: 2px 8px; color: #fff; }}
  .tier-S {{ background: #8957e5; }} .tier-A {{ background: #1a7f37; }}
  .tier-B {{ background: #bf8700; }} .tier-F {{ background: #c0392b; }}
  .title {{ font-weight: 600; font-size: 1rem; }}
  .title a {{ color: inherit; text-decoration: none; }}
  .title a:hover {{ color: var(--accent); text-decoration: underline; }}
  .sub {{ color: var(--muted); font-size: .8rem; margin-top: 2px; }}
  .rating-stars {{ color: #e3a008; }}
  .why {{ color: var(--muted); max-width: 32rem; font-size: .88rem; }}
  .schips {{ display: flex; flex-wrap: wrap; gap: 3px; }}
  .rchip {{ font-size: .72rem; font-variant-numeric: tabular-nums; border-radius: 4px;
            padding: 1px 5px; background: #8882; color: var(--ink); }}
  .rchip.start {{ outline: 1.5px solid var(--accent); font-weight: 700; }}
  .rchip.hi {{ background: #1a7f3722; color: #1a7f37; }}
  .rchip.lo {{ background: #c0392b22; color: #c0392b; }}
  @media (prefers-color-scheme: dark) {{ .rchip.hi {{ color:#3fb950; }} .rchip.lo {{ color:#f85149; }} }}
  .score-sub {{ font-size: .72rem; color: var(--muted); margin-top: 2px; white-space: nowrap; }}
  .rate-cell {{ white-space: nowrap; }}
  button.rate {{ font: inherit; font-size: .8rem; font-weight: 700; cursor: pointer; margin: 1px;
                 border: 1px solid var(--line); background: var(--card); color: var(--ink);
                 border-radius: 6px; padding: 3px 8px; }}
  button.rate:hover {{ border-color: var(--accent); color: var(--accent); }}
  button.rate[data-act="S"]:hover {{ background:#8957e5; color:#fff; border-color:#8957e5; }}
  button.rate[data-act="A"]:hover {{ background:#1a7f37; color:#fff; border-color:#1a7f37; }}
  button.rate[data-act="B"]:hover {{ background:#bf8700; color:#fff; border-color:#bf8700; }}
  button.rate[data-act="F"]:hover {{ background:#c0392b; color:#fff; border-color:#c0392b; }}
  tr.removing {{ transition: opacity .25s, background .25s; opacity: 0; background: #1a7f3722; }}
  #toast {{ position: fixed; bottom: 1.2rem; left: 50%; transform: translateX(-50%); background: #1f2328;
            color: #fff; padding: 8px 16px; border-radius: 8px; opacity: 0; transition: opacity .2s;
            pointer-events: none; font-size: .9rem; }}
  #toast.show {{ opacity: .95; }}
  .controls {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-top: 1rem; }}
  .controls input, .controls select {{ font: inherit; padding: 6px 10px; border-radius: 8px;
       border: 1px solid var(--line); background: var(--card); color: var(--ink); }}
  .controls input[type=search] {{ min-width: 240px; }}
  .controls label {{ font-size: .85rem; color: var(--muted); display: flex; align-items: center; gap: 5px; }}
  #count {{ margin-left: auto; color: var(--muted); font-size: .85rem; }}
</style></head><body>
<header>
  <h1>Recommendations by preference fit</h1>
  <div class="meta">Generated {generated} &middot; {total} books ranked &middot; score = theme fit + start-of-series rating &middot; click a header to sort</div>
  <div class="controls">
    <input type="search" id="f-text" placeholder="Search title / series…" autocomplete="off">
    <label>Min score <input type="number" id="f-score" step="1" style="width:5em">
    </label>
    <label>Min year <input type="number" id="f-year" step="1" placeholder="any" style="width:6em">
    </label>
    <label><input type="checkbox" id="f-hidef"> Hide predicted F</label>
    <label><select id="f-tier"><option value="">Any tier</option><option>S</option><option>A</option><option>B</option><option>F</option></select></label>
    <span id="count"></span>
  </div>
</header>
<div class="wrap"><table id="t"><thead><tr>
<th data-type="num">#</th><th data-type="num">Score</th><th>Pred</th><th>Title / Series</th><th data-type="num">Ratings</th><th data-type="num">Pages</th><th>Why</th>{rate_col}
</tr></thead><tbody>
"""

HTML_TAIL = """</tbody></table></div>
<script>
const t = document.getElementById('t');
const allRows = () => [...t.tBodies[0].rows];

// Renumber the rank cells over currently-visible rows.
function renumber() {
  let n = 0;
  allRows().forEach(r => { if (r.style.display !== 'none') r.cells[0].textContent = ++n; });
  const total = allRows().length, shown = n;
  const c = document.getElementById('count');
  if (c) c.textContent = shown === total ? `${total} shown` : `${shown} of ${total} shown`;
}

// Click-to-sort columns.
t.querySelectorAll('th').forEach((th, i) => th.addEventListener('click', () => {
  if (!th.dataset.type && th.textContent === 'Rate') return;
  const num = th.dataset.type === 'num';
  const rows = allRows();
  const dir = th._asc = !th._asc;
  rows.sort((a, b) => {
    let x = a.cells[i].dataset.sort ?? a.cells[i].innerText;
    let y = b.cells[i].dataset.sort ?? b.cells[i].innerText;
    if (num) { x = parseFloat(x); y = parseFloat(y); }
    return (x > y ? 1 : x < y ? -1 : 0) * (dir ? 1 : -1);
  });
  rows.forEach(r => t.tBodies[0].appendChild(r));
  renumber();
}));

// Filtering.
const F = {
  text: document.getElementById('f-text'), score: document.getElementById('f-score'),
  year: document.getElementById('f-year'), hidef: document.getElementById('f-hidef'),
  tier: document.getElementById('f-tier'),
};
function applyFilters() {
  const q = (F.text.value || '').trim().toLowerCase();
  const minScore = F.score.value === '' ? -Infinity : parseFloat(F.score.value);
  const minYear = F.year.value === '' ? null : parseInt(F.year.value);
  const tierWant = F.tier.value;
  allRows().forEach(r => {
    const d = r.dataset;
    let ok = true;
    if (q && !d.text.includes(q) && !r.querySelector('.why').textContent.toLowerCase().includes(q)) ok = false;
    if (ok && parseFloat(d.score) < minScore) ok = false;
    if (ok && F.hidef.checked && d.tier === 'F') ok = false;
    if (ok && tierWant && d.tier !== tierWant) ok = false;
    if (ok && minYear !== null && (!d.year || parseInt(d.year) < minYear)) ok = false;
    r.style.display = ok ? '' : 'none';
  });
  renumber();
}
Object.values(F).forEach(el => el && el.addEventListener('input', applyFilters));
renumber();
</script></body></html>"""


RATE_ACTIONS = [("S", "S"), ("A", "A"), ("B", "B"), ("F", "F"),
                ("interested", "★ Save"), ("skip", "Not interested")]


def render_row(row, i, with_buttons=False):
    """Render one <tr>. Set with_buttons=True to append a rating-actions cell (server)."""
    b = row["book"]
    score = row["score"]
    score_cls = "fit-pos" if score > 0 else "fit-neg" if score < 0 else "fit-zero"
    tier = row.get("predicted_tier") or "?"

    if b.series:
        primary = html.escape(b.series)
        n = row.get("series_count", 1)
        sub = f'{n} book{"s" if n != 1 else ""} in series'
    else:
        primary = html.escape(b.title)
        sub = "standalone"
    if b.goodreads_link:
        primary = f'<a href="{html.escape(b.goodreads_link)}" target="_blank" rel="noopener">{primary}</a>'

    # Per-book ratings across the series, in order; the first (start) is outlined.
    chips = []
    for idx, (snum, avg, cnt) in enumerate(row.get("series_ratings", [])):
        cls = "rchip" + (" start" if idx == 0 else "")
        cls += " hi" if avg >= 4.3 else " lo" if (avg and avg < 4.0) else ""
        tip = f"Book {html.escape(str(snum))}: {avg:.2f} ({cnt:,})" if snum else f"{avg:.2f} ({cnt:,} ratings)"
        chips.append(f'<span class="{cls}" title="{tip}">{avg:.2f}</span>')
    chips_html = f'<div class="schips">{"".join(chips)}</div>' if chips else "—"

    pub_year = row.get("published_year")  # captured for future use; column hidden while sparse

    breakdown = f'themes {row["fit_score"]:+d}, start-rating {row["rating_term"]:+.1f}'
    buttons = ""
    if with_buttons:
        btns = "".join(
            f'<button class="rate" data-act="{act}">{label}</button>' for act, label in RATE_ACTIONS
        )
        buttons = f'<td class="rate-cell">{btns}</td>'

    return (
        f'<tr data-id="{b.id}" data-series="{html.escape(b.series or "")}"'
        f' data-score="{row["score"]}" data-tier="{tier}" data-year="{pub_year or ""}"'
        f' data-text="{html.escape((b.series or b.title).lower())}">'
        f'<td class="num rank">{i}</td>'
        f'<td class="num" data-sort="{score}"><span class="fit {score_cls}">{score:+g}</span>'
        f'<div class="score-sub" title="{breakdown}">{breakdown}</div></td>'
        f'<td><span class="tier tier-{tier}">{tier}</span></td>'
        f'<td><div class="title">{primary}</div><div class="sub">{sub}</div></td>'
        f'<td class="num" data-sort="{row["start_rating"]}">{chips_html}'
        f'<div class="sub">{row["num_ratings"]:,} ratings total</div></td>'
        f'<td class="num" data-sort="{row["pages"]}">{row["pages"]:,}</td>'
        f'<td class="why">{html.escape(row["reasoning"])}</td>'
        f'{buttons}'
        f'</tr>'
    )


def write_html(profile, rows, total_classified):
    out = [HTML_HEAD.format(generated=datetime.now(timezone.utc).isoformat(),
                            total=total_classified, rate_col="")]
    for i, row in enumerate(rows, 1):
        out.append(render_row(row, i))
    out.append(HTML_TAIL)
    with open(RECOMMENDATIONS_HTML, "w") as f:
        f.write("\n".join(out))


def collapse_by_series(rows):
    """Keep one row per series (the highest-ranked, since rows are pre-sorted best-first).

    Standalones (no series) are always kept. The kept row records how many other books
    from the same series were folded into it.
    """
    seen = {}
    collapsed = []
    for r in rows:
        s = r["book"].series
        if not s:
            collapsed.append(r)
            continue
        if s in seen:
            seen[s]["series_count"] += 1
            continue
        r["series_count"] = 1
        seen[s] = r
        collapsed.append(r)
    return collapsed


def _now_excluded(book, ratings):
    """True if the book has since been rated, or its series rated/F-tier/uninterested."""
    if ratings is None:
        return False
    if ratings.has_directly_rated_book(book):
        return True
    if ratings.has_rated_book_or_series_as_f_tier_or_uninterested(book):
        return True
    if book.series and ratings.has_rated_series(book.series):
        return True
    return False


def compute_rows(profile, classifications, books_by_id, ratings=None,
                 series_aware=False, rating_weight=RATING_WEIGHT):
    """Build the ranked rows. Score = theme fit + rating term (from the start rating)."""
    weights = profile["weights"]
    bbs = BooksBySeries.from_books(books_by_id.values())
    cache = lib.load_cache()
    rows = []
    for bid, c in classifications.items():
        book = books_by_id.get(int(bid))
        if not book:
            continue
        if _now_excluded(book, ratings):
            continue
        if book.series:
            pages = bbs.total_pages_reported_by_kindle_for_series(book.series)
            num_ratings = bbs.total_number_of_ratings_for_series(book.series)
            series_books = bbs.books_by_series[book.series]
            series_ratings = [
                (sb.series_number, sb.average_rating or 0, sb.number_of_ratings or 0)
                for sb in series_books
            ]
            # The "start" rating is the earliest volume's — the entry point the reader judges.
            start_rating = series_books[0].average_rating or 0
        else:
            pages = book.pages_reported_by_kindle or 0
            num_ratings = book.number_of_ratings or 0
            series_ratings = [(book.series_number, book.average_rating or 0, book.number_of_ratings or 0)]
            start_rating = book.average_rating or 0

        fit = fit_score(c["tags"], weights)
        rating_term = round(rating_weight * (start_rating - RATING_BASELINE), 2)
        entry = cache.get(str(book.id), {})
        rows.append({
            "book": book,
            "tags": c["tags"],
            "fit_score": fit,
            "rating_term": rating_term,
            "score": round(fit + rating_term, 2),
            "start_rating": start_rating,
            "series_ratings": series_ratings,
            "predicted_tier": c.get("predicted_tier"),
            "reasoning": c.get("reasoning", ""),
            "series_count": 1,
            "avg_rating": book.average_rating or 0,
            "num_ratings": num_ratings,
            "pages": pages,
            "published_date": entry.get("published_date"),
            "published_year": entry.get("published_year"),
        })
    rows.sort(
        key=lambda r: (r["score"], TIER_RANK.get(r["predicted_tier"], 1), r["start_rating"]),
        reverse=True,
    )
    if series_aware:
        rows = collapse_by_series(rows)
    return rows


def rank_and_write(profile, classifications, books_by_id, series_aware=False, fmt="html",
                   ratings=None, rating_weight=RATING_WEIGHT):
    rows = compute_rows(profile, classifications, books_by_id, ratings=ratings,
                        series_aware=series_aware, rating_weight=rating_weight)
    if fmt == "md":
        write_md(profile, rows, len(rows))
    else:
        write_html(profile, rows, len(rows))
    return rows


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Classify candidates and rank by preference fit.")
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0, help="classify only the N most-rated candidates (0 = all)")
    parser.add_argument("--min-pages", type=int, default=500, help="min pages (book or series) filter")
    parser.add_argument("--rerank", action="store_true", help="recompute scores from edited weights; no claude calls")
    parser.add_argument("--series-aware", action="store_true", help="collapse each series to its single best-ranked book")
    parser.add_argument("--format", choices=["html", "md"], default="html", help="output format (default html)")
    parser.add_argument("--rating-weight", type=float, default=RATING_WEIGHT,
                        help=f"how strongly the start-of-series rating factors into score (default {RATING_WEIGHT})")
    args = parser.parse_args()

    out_file = RECOMMENDATIONS_MD if args.format == "md" else RECOMMENDATIONS_HTML

    with open(PROFILE_JSON) as f:
        profile = json.load(f)

    books = Book.load_books_from_db()
    books_by_id = {b.id: b for b in books}
    ratings = BookRating.load_ratings_from_db()

    if args.rerank:
        with open(CLASSIFICATIONS_JSON) as f:
            classifications = json.load(f)
        rows = rank_and_write(profile, classifications, books_by_id,
                              series_aware=args.series_aware, fmt=args.format, ratings=ratings,
                              rating_weight=args.rating_weight)
        logging.info(f"Re-ranked {len(rows)} books from edited weights -> {out_file}")
        return

    cache = lib.load_cache()

    recommendable = lib.recommendable_books(books, ratings, min_pages=args.min_pages)
    candidates = [b for b in recommendable if lib.get_description(cache, b.id)]
    missing_desc = len(recommendable) - len(candidates)
    if missing_desc:
        logging.warning(
            f"{missing_desc} recommendable books have no cached description and will be skipped. "
            f"Run: python3 scan_descriptions.py --scope candidates --min-pages {args.min_pages}"
        )
    candidates.sort(key=lambda b: b.number_of_ratings or 0, reverse=True)
    if args.limit:
        candidates = candidates[:args.limit]

    # Resume: keep any classifications we already have.
    try:
        with open(CLASSIFICATIONS_JSON) as f:
            classifications = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        classifications = {}

    todo = [b for b in candidates if str(b.id) not in classifications]
    logging.info(
        f"{len(candidates)} candidates with descriptions; {len(todo)} to classify "
        f"({len(candidates) - len(todo)} already done) via claude ({args.model})."
    )

    batches = [todo[i:i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(classify_batch, profile, batch, cache, args.model): batch for batch in batches}
        for fut in as_completed(futures):
            try:
                classifications.update(fut.result())
            except Exception as e:
                logging.warning(f"Batch failed: {e}")
            done += 1
            if done % 5 == 0 or done == len(batches):
                with open(CLASSIFICATIONS_JSON, "w") as f:
                    json.dump(classifications, f, indent=2)
                logging.info(f"  ...{done}/{len(batches)} batches classified")

    with open(CLASSIFICATIONS_JSON, "w") as f:
        json.dump(classifications, f, indent=2)

    rows = rank_and_write(profile, classifications, books_by_id,
                          series_aware=args.series_aware, fmt=args.format, ratings=ratings,
                          rating_weight=args.rating_weight)
    logging.info(f"Classified {len(classifications)} books. Wrote {out_file} ({len(rows)} ranked).")


if __name__ == "__main__":
    main()
