import random
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import feedparser
import requests

from digest_common import RAW, config, date_parser, recent_items, write_json, write_jsonl
from feed_utils import feed_date, feed_tags, plain_text, safe_link
from community_signals import paper_attention

ARXIV_ID = re.compile(r"(?:/abs/|oai:arXiv\.org:)([\w./-]+)", re.IGNORECASE)
USER_AGENT = "robo-daily-digest/1.0 (+https://github.com/Jdreamflying/robo-daily-digest)"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RETRIES = 5
BACKOFF_SECONDS = (5, 15, 30, 60, 60)


def unversioned(identifier):
    return re.sub(r"v\d+$", "", identifier.strip(), flags=re.I).casefold()


def published_ids(day):
    return {unversioned(item["id"]) for _, item in recent_items(day, "papers.json") if item.get("id")}


def normalize(entry, source):
    match = ARXIV_ID.search(entry.get("id", "")) or ARXIV_ID.search(entry.get("link", ""))
    title = plain_text(entry.get("title"))
    if not match or not title:
        return None
    versioned_id = match[1].rstrip("/")
    identifier = unversioned(versioned_id)
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


def retry_delay(response, retry_number):
    retry_after = response.headers.get("Retry-After") if response is not None else None
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    backoff = BACKOFF_SECONDS[min(retry_number - 1, len(BACKOFF_SECONDS) - 1)]
    return backoff + random.uniform(0, 1)


def query_day(feed, day, session):
    stamp = day.strftime("%Y%m%d")
    query = f"cat:{feed['category']} AND submittedDate:[{stamp}0000 TO {stamp}2359]"
    start, page_size = 0, 100
    entries = []
    while True:
        params = {
            "search_query": query, "start": start, "max_results": page_size,
            "sortBy": "submittedDate", "sortOrder": "descending",
        }
        for retry_number in range(MAX_RETRIES + 1):
            response = None
            try:
                response = session.get(feed["url"], params=params, timeout=(8, 25))
                if response.status_code in RETRYABLE_STATUS_CODES and retry_number < MAX_RETRIES:
                    delay = retry_delay(response, retry_number + 1)
                    response.close()
                    print(
                        f"arXiv HTTP {response.status_code} for {day} (start={start}); "
                        f"retry {retry_number + 1}/{MAX_RETRIES} in {delay:.1f}s",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    continue
                response.raise_for_status()
                break
            except (requests.Timeout, requests.ConnectionError) as exc:
                if retry_number >= MAX_RETRIES:
                    raise
                delay = retry_delay(None, retry_number + 1)
                print(
                    f"arXiv connection error for {day} (start={start}): {exc}; "
                    f"retry {retry_number + 1}/{MAX_RETRIES} in {delay:.1f}s",
                    file=sys.stderr,
                )
                time.sleep(delay)
        result = feedparser.parse(response.content)
        total = result.feed.get("opensearch_totalresults")
        if total is None or (not result.entries and int(total) > start):
            raise ValueError(f"Unexpected arXiv Atom response for {day} (start={start})")
        entries.extend(result.entries)
        start += len(result.entries)
        if start >= int(total):
            return entries
        time.sleep(3)


def find_source_day(feed, target, session, *, already_published=()):
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
        if any(row["id"] not in already_published for row in rows):
            return day, rows, len(entries)
    return None, [], 0


def main():
    args = date_parser("Query the arXiv submission day and collect public paper metrics.").parse_args()
    feed = config()["arxiv"]
    target = date.fromisoformat(args.date) - timedelta(days=feed.get("lag_days", 3))
    published = published_ids(args.date)
    with requests.Session() as session:
        session.headers["User-Agent"] = USER_AGENT
        day, rows, scanned = find_source_day(feed, target, session, already_published=published)
    papers = {}
    for row in rows:
        if row["id"] not in published:
            papers.setdefault(row["id"], row)
    if papers:
        paper_attention(papers, day)
    output = RAW / f"{args.date}_arxiv.jsonl"
    write_jsonl(output, papers.values())
    write_json(output.with_suffix(".meta.json"), {
        "total_scanned": scanned,
        "total_new": len(rows),
        "total_unique": len(papers),
        "previously_published": len({row["id"] for row in rows} & published),
        "paper_target_date": target.isoformat(),
        "paper_source_date": day.isoformat() if day else None,
        "paper_category": feed["category"],
        "feed": feed["url"],
    })
    print(f"arXiv: target {target}, source {day}, {scanned} scanned, {len(papers)} unique -> {output.name}")


if __name__ == "__main__":
    main()
