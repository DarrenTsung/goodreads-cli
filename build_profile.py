#!/usr/bin/env python3
"""Derive a theme-preference profile from the user's likes/dislikes.

Reads cached descriptions for the rated set, asks claude (sonnet) to build a theme
taxonomy with per-theme preference weights, and writes:
  - theme_profile.json  (machine-readable; consumed by classify_and_rank.py)
  - theme_profile.md    (human verification report -- read & edit weights before classifying)

Run `python3 scan_descriptions.py --scope profile` first to populate the cache.
"""
import argparse
import json
import logging
from datetime import datetime, timezone

import theme_scan_lib as lib
from book import Book
from book_rating import BookRating

DESC_CHARS = 600  # per-book description budget in the prompt

PROFILE_JSON = "theme_profile.json"
PROFILE_MD = "theme_profile.md"

PROMPT_TEMPLATE = """You are analyzing a reader's taste in LitRPG / progression-fantasy books.

Below are books they LIKED (with the tier they gave: S=loved, A=great, B=good) and books \
they DISLIKED (F = started and gave up; SKIP = rejected without reading, often a softer \
signal based on premise/blurb). Each entry has the book's description.

Derive a concise THEME TAXONOMY (roughly 12-25 tags) capturing the recurring themes, \
tropes, tones, structures, and content elements that distinguish what this reader likes \
from what they dislike. Tags must be reusable to classify *other* books, so make them \
general (e.g. "dungeon_core", "harem", "slice_of_life", "system_apocalypse", \
"crunchy_progression", "grimdark", "comedic_tone", "kingdom_building"). Use lowercase \
snake_case tags.

For each theme assign an integer preference weight from this reader's perspective:
  +2 = strongly drawn to it,  +1 = likes it,  0 = neutral,  -1 = dislikes it,  -2 = actively avoids it.

Respond with ONLY valid JSON (no prose, no markdown fences) of the form:
{{
  "summary": "2-4 sentence summary of this reader's taste",
  "themes": [
    {{
      "tag": "snake_case_tag",
      "description": "what this theme means",
      "weight": -2,
      "rationale": "why you inferred this weight for this reader",
      "evidence": ["A liked or disliked title that supports this"]
    }}
  ]
}}

=== LIKED BOOKS ===
{liked}

=== DISLIKED BOOKS ===
{disliked}
"""


def _fmt(book, cache, prefix):
    desc = lib.get_description(cache, book.id) or "(no description cached)"
    if len(desc) > DESC_CHARS:
        desc = desc[:DESC_CHARS].rsplit(" ", 1)[0] + "..."
    series = f" [series: {book.series}]" if book.series else ""
    return f"- {prefix} | {book.title}{series}\n  {desc}"


def build_prompt(split, cache):
    liked_lines = [_fmt(b, cache, f"tier {t.value}") for b, t in split["liked"]]
    disliked_lines = [_fmt(b, cache, "F") for b in split["disliked_f"]]
    disliked_lines += [_fmt(b, cache, "SKIP") for b in split["disliked_sample"]]
    return PROMPT_TEMPLATE.format(liked="\n".join(liked_lines), disliked="\n".join(disliked_lines))


def write_md(profile, split):
    themes = sorted(profile["themes"], key=lambda t: t["weight"], reverse=True)
    lines = [
        "# Theme Preference Profile",
        "",
        f"_Generated {profile['generated_at']} from {len(split['liked'])} liked, "
        f"{len(split['disliked_f'])} F-tier, and {len(split['disliked_sample'])} sampled-dislike books._",
        "",
        "## Taste summary",
        "",
        profile["summary"],
        "",
        "## How to verify",
        "",
        "Review the weights below. To correct the model, **edit the `weights` map in "
        "`theme_profile.json`** (-2 avoid … +2 love), then run `classify_and_rank.py`. "
        "The ranking score is the sum of the weights of the themes each book matches, so "
        "your edits directly reshape recommendations.",
        "",
        "## Themes",
        "",
        "| Weight | Theme | Meaning | Why | Evidence |",
        "|:------:|-------|---------|-----|----------|",
    ]
    for t in themes:
        ev = ", ".join(t.get("evidence", [])[:4])
        rationale = t.get("rationale", "").replace("|", "\\|")
        desc = t.get("description", "").replace("|", "\\|")
        lines.append(
            f"| {t['weight']:+d} | `{t['tag']}` | {desc} | {rationale} | {ev} |"
        )
    lines.append("")
    with open(PROFILE_MD, "w") as f:
        f.write("\n".join(lines))


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Build the theme preference profile.")
    parser.add_argument("--model", default="sonnet", help="claude model (e.g. sonnet, opus)")
    args = parser.parse_args()

    books = Book.load_books_from_db()
    ratings = BookRating.load_ratings_from_db()
    split = lib.profile_split(books, ratings)
    cache = lib.load_cache()

    wanted = lib.profile_book_ids(split)
    missing = [bid for bid in wanted if not lib.get_description(cache, bid)]
    if missing:
        logging.warning(
            f"{len(missing)}/{len(wanted)} profile books have no cached description. "
            f"Run: python3 scan_descriptions.py --scope profile"
        )

    logging.info(
        f"Building profile from {len(split['liked'])} liked, {len(split['disliked_f'])} F-tier, "
        f"{len(split['disliked_sample'])} sampled dislikes via claude ({args.model})..."
    )
    prompt = build_prompt(split, cache)
    raw = lib.call_claude(prompt, model=args.model, timeout=600)
    parsed = lib.parse_json_response(raw)

    weights = {t["tag"]: int(t["weight"]) for t in parsed["themes"]}
    profile = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "summary": parsed.get("summary", ""),
        "taxonomy": [{"tag": t["tag"], "description": t.get("description", "")} for t in parsed["themes"]],
        "weights": weights,
        "themes": parsed["themes"],
    }
    with open(PROFILE_JSON, "w") as f:
        json.dump(profile, f, indent=2)
    write_md(profile, split)

    logging.info(f"Wrote {PROFILE_JSON} ({len(weights)} themes) and {PROFILE_MD}.")
    logging.info("Review theme_profile.md, edit weights in theme_profile.json, then run classify_and_rank.py.")


if __name__ == "__main__":
    main()
