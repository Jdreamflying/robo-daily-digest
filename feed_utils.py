import re
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit

import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class FeedText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in {"p", "div", "br", "li"}:
            self.parts.append(" ")


def plain_text(value):
    parser = FeedText()
    parser.feed(value or "")
    parser.close()
    return " ".join("".join(parser.parts).split())


def fetch_feed(url):
    retry = Retry(total=2, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504))
    with requests.Session() as client:
        client.mount("https://", HTTPAdapter(max_retries=retry))
        client.mount("http://", HTTPAdapter(max_retries=retry))
        response = client.get(
            url, headers={"User-Agent": "robo-daily-digest/1.0 (RSS reader)"}, timeout=(10, 20)
        )
        response.raise_for_status()
    feed = feedparser.parse(
        response.content,
        response_headers={"content-location": response.url},
    )
    if not feed.version or (feed.bozo and not feed.entries):
        raise ValueError(f"Invalid RSS or Atom response from {url}")
    return feed.entries


def safe_link(value):
    try:
        parsed = urlsplit((value or "").strip())
    except ValueError:
        return ""
    if parsed.scheme in {"http", "https"} and parsed.hostname and parsed.username is None:
        return parsed.geturl()
    return ""


def canonical_link(value):
    parsed = urlsplit(value)
    query = sorted(
        (key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    )
    return parsed._replace(
        netloc=parsed.netloc.lower(),
        path=parsed.path.rstrip("/") or "/",
        query=urlencode(query),
        fragment="",
    ).geturl()


def feed_date(entry):
    timestamp = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime(*timestamp[:6], tzinfo=timezone.utc).isoformat() if timestamp else ""


def feed_tags(entry):
    terms = [plain_text(tag.get("term")) for tag in entry.get("tags", [])]
    return list(dict.fromkeys(filter(None, terms)))


def normalized_words(value):
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold()))


def matches_keyword(normalized, keyword):
    term = normalized_words(keyword)
    if re.search(r"[\u3400-\u9fff]", term):
        return term in normalized
    return bool(term) and f" {term} " in f" {normalized} "
