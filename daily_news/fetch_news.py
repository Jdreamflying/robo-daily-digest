import hashlib
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from community_signals import news_attention
from daily_news.hacker_news import fetch_hn
from daily_news.news_events import (
    SOURCE_PRIORITY, event_key, launch_anchors, merge_engagement, named_launch_key, same_incident, title_key,
)
from digest_common import RAW, config, date_parser, recent_items, write_json, write_jsonl
from feed_utils import (
    canonical_link, feed_date, feed_tags, fetch_feed, matches_keyword, normalized_words, plain_text, safe_link,
)

FORUM_QUESTION = re.compile(
    r"\b(?:help|how to|errors?|issues?|questions?|looking for|off[- ]topic|anyone know|hiring|"
    r"job openings?|co[- ]founders?|full[- ]time|internship|WLED|LED strips?)\b", re.I,
)
FORUM_ANNOUNCEMENT = re.compile(
    r"\b(?:releas(?:e[ds]?|ing)|announc(?:e[ds]?|ing|ement)|launch(?:es|ed|ing)?|v[12]\.\d+|"
    r"alpha|beta|benchmarks?|open[- ]source|sim[- ]to[- ]real|datasets?|papers?|challenges?)\b",
    re.I,
)
FORUM_JOB = re.compile(r"\b(?:position|compensation|salary|employment type|reports to):", re.I)
RELEASE_VERSION_TITLE = re.compile(
    r"^\s*(?:v?\d+(?:\.\d+){1,4}(?:[-+._][0-9A-Za-z.-]+)?|"
    r"v\d+(?:[-+._][0-9A-Za-z.-]+)?)\s*$",
    re.I,
)


def forum_announcement(title, summary=""):
    return (
        not title.rstrip().endswith(("?", "？"))
        and not FORUM_QUESTION.search(title)
        and not FORUM_JOB.search(summary)
        and bool(FORUM_ANNOUNCEMENT.search(title))
    )


def keyword_text(row):
    return " ".join([row.get("title", ""), row.get("summary", ""), *row.get("tags", [])])


def issue_window(day, hours, zone):
    next_day = date.fromisoformat(day) + timedelta(days=1)
    day_end = datetime.combine(next_day, time(), ZoneInfo(zone)).astimezone(timezone.utc)
    upper = min(datetime.now(timezone.utc), day_end)
    return upper - timedelta(hours=hours), upper


def recent_published_keys(day):
    urls, titles = set(), set()
    for _, item in recent_items(day, "news.json"):
        if url := safe_link(item.get("url", "")):
            urls.add(canonical_link(url))
        if title := title_key(item.get("original_title") or item.get("title", "")):
            titles.add(title)
    return urls, titles


def hn_passes_threshold(row, topic, source):
    prefix = "robotics_" if topic == "robotics" else ""
    engagement = row["engagement"]
    return (
        engagement.get("points", 0) >= source.get(prefix + "min_points", source["min_points"])
        or engagement.get("comments", 0) >= source.get(prefix + "min_comments", source["min_comments"])
    )


def fetch_email_imap(settings):
    """Extension point. An implementation should return normalized news rows."""
    print("IMAP is enabled but no mailbox adapter has been configured.", file=sys.stderr)
    return []


def normalize(entry, source, source_config=None):
    link = safe_link(entry.get("link", ""))
    title = plain_text(entry.get("title"))
    if not title or not link:
        return None
    if source_config and source_config.get("source_type") == "release" and RELEASE_VERSION_TITLE.fullmatch(title):
        project = re.sub(r"\s+releases?$", "", source, flags=re.I).strip()
        if project:
            title = f"{project} {title}"
    url = canonical_link(link)
    summary = plain_text(entry.get("summary") or entry.get("description"))
    summary = re.sub(r"(?:^|\s)The post .+ appeared first on .+\.\s*$", "", summary).strip()
    tags = feed_tags(entry)
    return {
        "id": hashlib.sha256(url.encode()).hexdigest()[:24],
        "title": title,
        "summary": summary,
        "url": url,
        "published": feed_date(entry),
        "tags": tags,
        "source": source,
    }


def fetch_source(source):
    try:
        return fetch_feed(source["url"])
    except (requests.RequestException, ValueError) as exc:
        print(f"Warning: RSS failed ({source['name']}): {exc}", file=sys.stderr)
        return None


def exclusion_reason(row, cutoff, upper, excluded_tags, keywords, *, focus_keywords=None):
    if not row:
        return "malformed"
    if not row["published"] or not cutoff <= datetime.fromisoformat(row["published"]) <= upper:
        return "outside_window"
    tags = [normalized_words(tag) for tag in row["tags"]]
    if any(matches_keyword(tag, excluded) for excluded in excluded_tags for tag in tags):
        return "excluded"
    if re.search(
        r"\[\s*(?:sponsored|ad)\s*\]|\b(?:webinars?|podcasts?|sponsored content|robot talk episode)\b",
        row["title"], re.I,
    ):
        return "excluded"
    if focus_keywords is not None:
        title = normalized_words(row["title"])
        core_tags = set(tags)
        if not any(matches_keyword(title, word) or normalized_words(word) in core_tags for word in focus_keywords):
            return "robotics_focus_miss"
    elif keywords and not any(matches_keyword(normalized_words(keyword_text(row)), word) for word in keywords):
        return "keyword_miss"
    return None


def topic_for(row, source, news):
    title = normalized_words(row["title"])
    tags = {normalized_words(tag) for tag in row["tags"]}
    if any(matches_keyword(title, word) or normalized_words(word) in tags for word in news["robotics_focus_keywords"]):
        return "robotics"
    if source.get("robotics_only"):
        return None
    if source.get("ai_vertical"):
        return "ai_frontier"
    topical = source.get("require_robotics_focus") or source == news.get("hacker_news")
    if topical:
        if any(matches_keyword(title, word) or normalized_words(word) in tags for word in news["frontier_keywords"]):
            return "ai_frontier"
        return None
    return "robotics"


def collect_news(day, settings, cutoff, upper):
    news = settings["news"]
    feeds = {source["name"]: source for source in news["rss"]}
    hn = news.get("hacker_news")
    if hn:
        feeds[hn["name"]] = hn
    sources = {}
    filtered = Counter()
    rows, window_count = [], 0
    excluded_tags = news.get("exclude_tags", [])

    def accept(row, source):
        nonlocal window_count
        keywords = settings["keywords"] if source.get("require_keyword") else []
        reason = exclusion_reason(row, cutoff, upper, excluded_tags, keywords)
        if reason not in ("malformed", "outside_window"):
            window_count += 1
        if not reason:
            topic = topic_for(row, source, news)
            if not topic:
                reason = "topic_miss"
            elif source is hn and not hn_passes_threshold(row, topic, source):
                reason = "hn_below_threshold"
            elif source.get("require_release_or_announce") and not forum_announcement(row["title"], row["summary"]):
                reason = "forum_non_announcement"
            else:
                row["category"] = topic
                rows.append(row)
        if reason:
            filtered[reason] += 1

    with ThreadPoolExecutor(max_workers=7) as pool:
        hn_results = pool.submit(fetch_hn, hn, cutoff, upper) if hn else None
        for source, entries in zip(news["rss"], pool.map(fetch_source, news["rss"])):
            name = source["name"]
            sources[name] = {"fetched": entries is not None, "scanned": len(entries or [])}
            for entry in entries or []:
                accept(normalize(entry, name, source), source)
        if hn_results:
            hn_rows, ok = hn_results.result()
            sources[hn["name"]] = {"fetched": ok, "scanned": len(hn_rows)}
            if not ok:
                print("Warning: Hacker News API did not return all results.", file=sys.stderr)
            for row in hn_rows:
                accept(row, hn)
    fetched = sum(source["fetched"] for source in sources.values())
    total_scanned = sum(source["scanned"] for source in sources.values())
    if news.get("email_imap", {}).get("enabled"):
        email_rows = fetch_email_imap(news["email_imap"])
        total_scanned += len(email_rows)
        for row in email_rows:
            accept(row, {})
    if news["rss"] and not fetched and not rows:
        raise SystemExit("No news feed could be fetched.")

    previous_urls, previous_titles = recent_published_keys(day)
    launches = launch_anchors(rows)
    existing, unique = {}, []
    source_types = {name: source.get("source_type") for name, source in feeds.items()}
    for row in sorted(rows, key=lambda item: (
        SOURCE_PRIORITY.get(source_types.get(item["source"]), 0), item["published"],
    ), reverse=True):
        title = title_key(row["title"])
        event = event_key(row["title"])
        if row["url"] in previous_urls or title in previous_titles:
            filtered["cross_day_duplicate"] += 1
            continue
        keys = [row["url"], title, event, named_launch_key(row, launches)]
        duplicate = next((existing[key] for key in keys if key in existing), None)
        if duplicate is None:
            duplicate = next((item for item in unique if same_incident(item, row)), None)
        if duplicate:
            merge_engagement(duplicate, row)
            filtered["same_event_duplicate"] += 1
        else:
            unique.append(row)
        for key in filter(None, keys):
            existing[key] = duplicate or row
    news_attention(unique, source_types)
    eligible = Counter(item["source"] for item in unique)
    for name, source in sources.items():
        source["eligible"] = eligible[name]
    return unique, {
        "total_scanned": total_scanned,
        "window_count": window_count,
        "window_hours": news.get("max_age_hours", 48),
        "total_unique": len(unique),
        "sources_fetched": fetched,
        "sources_configured": len(feeds),
        "sources": sources,
        "filtered": dict(filtered),
        "cutoff": cutoff.isoformat(),
        "window_end": upper.isoformat(),
    }


def main():
    args = date_parser("Fetch and deduplicate robotics and AI news.").parse_args()
    settings = config()
    cutoff, upper = issue_window(args.date, float(settings["news"]["max_age_hours"]), settings["timezone"])
    rows, meta = collect_news(args.date, settings, cutoff, upper)
    output = RAW / f"{args.date}_news.jsonl"
    write_jsonl(output, rows)
    write_json(output.with_suffix(".meta.json"), meta)
    print(f"News: {meta['sources_fetched']}/{meta['sources_configured']} sources, "
          f"{meta['window_count']} in window, {len(rows)} unique -> {output.name}")


if __name__ == "__main__":
    main()
