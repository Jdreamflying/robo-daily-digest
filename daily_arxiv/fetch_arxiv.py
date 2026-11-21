import re
import time
from datetime import date, timedelta

import feedparser
import requests

from digest_common import RAW, config, date_parser, write_json, write_jsonl
from feed_utils import feed_date, feed_tags, plain_text, safe_link
from community_signals import paper_attention

ARXIV_ID = re.compile(r"(?:/abs/|oai:arXiv\.org:)([\w./-]+)", re.IGNORECASE)


def normalize(entry, source):
    match = ARXIV_ID.search(entry.get("id", "")) or ARXIV_ID.search(entry.get("link", ""))
    title = plain_text(entry.get("title"))
    if not match or not title:
        return None
    versioned_id = match[1].rstrip("/")
    identifier = re.sub(r"v\d+$", "", versioned_id)
    abstract = plain_text(entry.get("summary"))
    if abstract.startswith("arXiv:") and "Abstract:" in abstract:
        abstract = abstract.split("Abstract:", 1)[1].strip()
    authors = []
    for author in entry.get("authors", []):
        name = plain_text(author.get("name"))
        authors.extend(part.strip() for part in name.split(",") if part.strip())
    authors = list(dict.fromkeys(authors))
    return {
        "id": identifier,
        "versioned_id": versioned_id,
        "title": title,
        "summary": abstract,
        "url": safe_link(entry.get("link", "")) or f"https://arxiv.org/abs/{identifier}",
        "published": feed_date(entry),
        "authors": authors,
        "tags": feed_tags(entry),
        "source": f"arXiv {source}",
        "announcement": (entry.get("arxiv_announce_type") or "").strip().lower(),
    }


def query_day(feed, day, session):
    stamp = day.strftime("%Y%m%d")
    query = f"cat:{feed['category']} AND submittedDate:[{stamp}0000 TO {stamp}2359]"
    start, page_size = 0, 100
    entries = []
    while True:
        response = session.get(feed["url"], params={
            "search_query": query, "start": start, "max_results": page_size,
            "sortBy": "submittedDate", "sortOrder": "descending",
        }, timeout=(8, 25))
        response.raise_for_status()
        result = feedparser.parse(response.content)
        total = result.feed.get("opensearch_totalresults")
        if total is None or (not result.entries and int(total) > start):
            raise ValueError(f"Unexpected arXiv Atom response for {day} (start={start})")
        entries.extend(result.entries)
        start += len(result.entries)
        if start >= int(total):
            return entries
        time.sleep(3)


def find_source_day(feed, target, session):
    queried = False
    for offset in range(feed.get("fallback_days", 3) + 1):
        day = target - timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        if queried:
            time.sleep(3)
        queried = True
        entries = query_day(feed, day, session)
        rows = [normalize(entry, feed["category"]) for entry in entries]
        rows = [
            row for row in rows if row and row["published"][:10] == day.isoformat()
            and row["announcement"] not in {"replace", "replace-cross"}
            and feed["category"] in row["tags"]
        ]
        if rows:
            return day, rows, len(entries)
    return None, [], 0


def main():
    args = date_parser("Query the arXiv submission day and collect public paper metrics.").parse_args()
    feed = config()["arxiv"]
    target = date.fromisoformat(args.date) - timedelta(days=feed.get("lag_days", 3))
    with requests.Session() as session:
        session.headers["User-Agent"] = "robo-daily-digest/1.0 (arXiv news reader)"
        day, rows, scanned = find_source_day(feed, target, session)
    papers = {}
    for row in rows:
        papers.setdefault(row["id"], row)
    if papers:
        paper_attention(papers, day)
    output = RAW / f"{args.date}_arxiv.jsonl"
    write_jsonl(output, papers.values())
    write_json(output.with_suffix(".meta.json"), {
        "total_scanned": scanned,
        "total_new": len(rows),
        "total_unique": len(papers),
        "paper_target_date": target.isoformat(),
        "paper_source_date": day.isoformat() if day else None,
        "paper_category": feed["category"],
        "feed": feed["url"],
    })
    print(f"arXiv: target {target}, source {day}, {scanned} scanned, {len(papers)} unique -> {output.name}")


if __name__ == "__main__":
    main()
