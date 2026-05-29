#!/usr/bin/env python3
"""Auto-rate candidate books against the verified theme profile and rank by preference fit.

  python3 classify_and_rank.py                 # classify via claude, then write ranking
  python3 classify_and_rank.py --limit 20      # smoke test on the 20 most-rated candidates
  python3 classify_and_rank.py --rerank        # recompute scores from edited weights only (no claude)

Reads theme_profile.json (run build_profile.py first) and descriptions_cache.json.
Writes classifications.json (cache of claude output) and recommendations.md.
"""
import argparse
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


def rank_and_write(profile, classifications, books_by_id, series_aware=False):
    weights = profile["weights"]
    rows = []
    for bid, c in classifications.items():
        book = books_by_id.get(int(bid))
        if not book:
            continue
        rows.append({
            "book": book,
            "tags": c["tags"],
            "fit_score": fit_score(c["tags"], weights),
            "predicted_tier": c.get("predicted_tier"),
            "reasoning": c.get("reasoning", ""),
            "series_count": 1,
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
    write_md(profile, rows, len(rows))
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
    args = parser.parse_args()

    with open(PROFILE_JSON) as f:
        profile = json.load(f)

    books = Book.load_books_from_db()
    books_by_id = {b.id: b for b in books}

    if args.rerank:
        with open(CLASSIFICATIONS_JSON) as f:
            classifications = json.load(f)
        rows = rank_and_write(profile, classifications, books_by_id, series_aware=args.series_aware)
        logging.info(f"Re-ranked {len(rows)} books from edited weights -> {RECOMMENDATIONS_MD}")
        return

    ratings = BookRating.load_ratings_from_db()
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

    rows = rank_and_write(profile, classifications, books_by_id, series_aware=args.series_aware)
    logging.info(f"Classified {len(classifications)} books. Wrote {RECOMMENDATIONS_MD} (top by fit).")


if __name__ == "__main__":
    main()
