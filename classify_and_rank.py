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
<title>Recommendations by preference fit</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 2rem; }}
  h1 {{ margin-bottom: .2rem; }}
  .meta {{ color: #888; margin-bottom: 1.2rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ padding: 8px 10px; border-bottom: 1px solid #8884; vertical-align: top; text-align: left; }}
  th {{ position: sticky; top: 0; background: Canvas; cursor: pointer; user-select: none; border-bottom: 2px solid #8888; }}
  th:hover {{ color: #4a90d9; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .fit-pos {{ color: #1a7f37; font-weight: 600; }}
  .fit-neg {{ color: #c0392b; font-weight: 600; }}
  .fit-zero {{ color: #888; }}
  .tier {{ font-weight: 700; border-radius: 4px; padding: 1px 7px; }}
  .tier-S {{ background: #6f42c1; color: #fff; }}
  .tier-A {{ background: #1a7f37; color: #fff; }}
  .tier-B {{ background: #b08800; color: #fff; }}
  .tier-F {{ background: #c0392b; color: #fff; }}
  .title {{ font-weight: 600; }}
  .series {{ color: #888; font-size: 13px; }}
  .themes {{ font-size: 12px; color: #666; }}
  .themes .pos {{ color: #1a7f37; }} .themes .neg {{ color: #c0392b; }}
  .why {{ color: #444; max-width: 40rem; }}
  tr:hover td {{ background: #4a90d915; }}
</style></head><body>
<h1>Recommendations by preference fit</h1>
<div class="meta">Generated {generated} from {total} classified candidates. Score = sum of your theme weights for each matched theme. Click a header to sort.</div>
<table id="t"><thead><tr>
<th data-type="num">#</th><th data-type="num">Fit</th><th>Pred</th><th>Title</th><th>Series</th><th data-type="num">Rating</th><th data-type="num">Pages</th><th>Themes</th><th>Why</th>
</tr></thead><tbody>
"""

HTML_TAIL = """</tbody></table>
<script>
const t = document.getElementById('t');
t.querySelectorAll('th').forEach((th, i) => th.addEventListener('click', () => {
  const num = th.dataset.type === 'num';
  const rows = [...t.tBodies[0].rows];
  const dir = th._asc = !th._asc;
  rows.sort((a, b) => {
    let x = a.cells[i].dataset.sort ?? a.cells[i].innerText;
    let y = b.cells[i].dataset.sort ?? b.cells[i].innerText;
    if (num) { x = parseFloat(x); y = parseFloat(y); }
    return (x > y ? 1 : x < y ? -1 : 0) * (dir ? 1 : -1);
  });
  rows.forEach(r => t.tBodies[0].appendChild(r));
}));
</script></body></html>"""


def write_html(profile, rows, total_classified):
    weights = profile["weights"]
    out = [HTML_HEAD.format(generated=datetime.now(timezone.utc).isoformat(), total=total_classified)]
    for i, row in enumerate(rows, 1):
        b = row["book"]
        fit = row["fit_score"]
        fit_cls = "fit-pos" if fit > 0 else "fit-neg" if fit < 0 else "fit-zero"
        tier = row.get("predicted_tier") or "?"
        theme_html = ", ".join(
            f'<span class="{"pos" if weights.get(t,0)>0 else "neg" if weights.get(t,0)<0 else ""}">'
            f'{html.escape(t)}{weights.get(t,0):+d}</span>'
            for t in row["tags"]
        )
        series = html.escape(b.series or "")
        if row.get("series_count", 1) > 1:
            series += f' <span class="series">(+{row["series_count"] - 1} more)</span>'
        title = html.escape(b.title)
        if b.goodreads_link:
            title = f'<a href="{html.escape(b.goodreads_link)}" target="_blank" rel="noopener">{title}</a>'
        out.append(
            f'<tr>'
            f'<td class="num">{i}</td>'
            f'<td class="num {fit_cls}" data-sort="{fit}">{fit:+d}</td>'
            f'<td><span class="tier tier-{tier}">{tier}</span></td>'
            f'<td class="title" data-sort="{html.escape(b.title)}">{title}</td>'
            f'<td class="series">{series}</td>'
            f'<td class="num" data-sort="{row["avg_rating"]}">{row["avg_rating"]:.2f} '
            f'<span class="series">({row["num_ratings"]:,})</span></td>'
            f'<td class="num" data-sort="{row["pages"]}">{row["pages"]:,}</td>'
            f'<td class="themes">{theme_html}</td>'
            f'<td class="why">{html.escape(row["reasoning"])}</td>'
            f'</tr>'
        )
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


def rank_and_write(profile, classifications, books_by_id, series_aware=False, fmt="html", ratings=None):
    weights = profile["weights"]
    bbs = BooksBySeries.from_books(books_by_id.values())
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
        else:
            pages = book.pages_reported_by_kindle or 0
            num_ratings = book.number_of_ratings or 0
        rows.append({
            "book": book,
            "tags": c["tags"],
            "fit_score": fit_score(c["tags"], weights),
            "predicted_tier": c.get("predicted_tier"),
            "reasoning": c.get("reasoning", ""),
            "series_count": 1,
            "avg_rating": book.average_rating or 0,
            "num_ratings": num_ratings,
            "pages": pages,
        })
    rows.sort(
        key=lambda r: (
            r["fit_score"],
            TIER_RANK.get(r["predicted_tier"], 1),
            r["book"].average_rating or 0,
        ),
        reverse=True,
    )
    if series_aware:
        rows = collapse_by_series(rows)
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
                              series_aware=args.series_aware, fmt=args.format, ratings=ratings)
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
                          series_aware=args.series_aware, fmt=args.format, ratings=ratings)
    logging.info(f"Classified {len(classifications)} books. Wrote {out_file} ({len(rows)} ranked).")


if __name__ == "__main__":
    main()
