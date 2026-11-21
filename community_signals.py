import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import urlsplit

import requests

from public_http import HEADERS, fetch_public

GITHUB_REPO = re.compile(r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)", re.I)


def api_json(url, params=None, headers=None):
    try:
        response = requests.get(url, params=params, headers={**HEADERS, **(headers or {})}, timeout=(5, 8))
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError):
        return None


def counts(raw, fields):
    if not isinstance(raw, dict):
        return {}
    return {key: raw[value] for key, value in fields.items()
            if type(raw.get(value)) is int and raw[value] >= 0}


def github_repo(text):
    match = GITHUB_REPO.search(text or "")
    if not match or match[1].lower() in {"topics", "orgs", "features", "sponsors", "marketplace"} or match[1] in (".", ".."):
        return None
    name = match[2].rstrip(".").removesuffix(".git")
    if name in ("", ".", ".."):
        return None
    return f"{match[1]}/{name}".lower()


def github_stars(repo):
    token = os.getenv("DIGEST_GITHUB_TOKEN", "").strip()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = api_json(f"https://api.github.com/repos/{repo}", headers=headers)
    return counts(data, {"github_stars": "stargazers_count"})


def hugging_face_attention(day):
    data = api_json("https://huggingface.co/api/daily_papers", {"date": day.isoformat()})
    found = {}
    for entry in data if isinstance(data, list) else []:
        if not isinstance(entry, dict) or not isinstance(paper := entry.get("paper"), dict):
            continue
        identifier = paper.get("id")
        if not isinstance(identifier, str):
            continue
        metrics = counts(paper, {"hf_upvotes": "upvotes"})
        metrics.update(counts(entry, {"hf_comments": "numComments"}))
        linked_repo = entry.get("githubRepo") or paper.get("githubRepo")
        if not isinstance(linked_repo, str):
            linked_repo = None
        found[re.sub(r"v\d+$", "", identifier)] = (metrics, linked_repo)
    return found


def paper_repo(row, linked_repo):
    from ai.enrich_papers import extract_links

    direct = github_repo(linked_repo) or github_repo(row["summary"])
    if direct:
        return direct
    _, project = extract_links(row["summary"])
    if project and (urlsplit(project).hostname or "").endswith(".github.io"):
        try:
            page = fetch_public(project, 800_000, ("text/html", "application/xhtml+xml"))
        except (requests.RequestException, OSError, ValueError):
            page = None
        if page:
            return github_repo(page[1].decode("utf-8", errors="replace"))
    return None


def paper_attention(papers, day):
    with ThreadPoolExecutor(max_workers=5) as pool:
        linked = {}
        dates = [day + timedelta(days=offset) for offset in (-1, 0, 1)]
        for result in pool.map(hugging_face_attention, dates):
            for identifier, (metrics, url) in result.items():
                if identifier not in papers:
                    continue
                attention = papers[identifier].setdefault("attention", {})
                for key, value in metrics.items():
                    attention[key] = max(attention.get(key, 0), value)
                if url:
                    linked[identifier] = url
        # Bound unauthenticated GitHub traffic and favor known repo links before fetching project pages.
        priority = sorted(papers.values(), key=lambda row: (
            bool(github_repo(linked.get(row["id"])) or github_repo(row["summary"])),
            row.get("attention", {}).get("hf_upvotes", 0),
        ), reverse=True)
        candidates = [row for row in priority if row["id"] in linked or github_repo(row["summary"])
                      or ".github.io" in row["summary"]][:20]
        lookup = list(pool.map(lambda row: paper_repo(row, linked.get(row["id"])), candidates))
        repos = list(dict.fromkeys(repo for repo in lookup if repo))
        stars = dict(zip(repos, pool.map(github_stars, repos)))
        for row, repo in zip(candidates, lookup):
            if repo and stars[repo]:
                row.setdefault("attention", {}).update(stars[repo])
                row["attention"]["github_url"] = f"https://github.com/{repo}"


def news_attention(rows, source_types):
    def key(row):
        source_type = source_types.get(row["source"])
        if source_type == "release" and (repo := github_repo(row["url"])):
            return "github", repo
        parts = urlsplit(row["url"])
        if parts.hostname in {"discourse.ros.org", "discourse.openrobotics.org"}:
            match = re.fullmatch(r"/t/(?:[^/]+/)?(\d+)", parts.path)
            if match:
                return "ros", match[1]
        return None

    def lookup(pair):
        provider, identifier = pair
        if provider == "github":
            return github_stars(identifier)
        data = api_json(f"https://discourse.ros.org/t/{identifier}.json")
        return counts(data, {"comments": "reply_count", "likes": "like_count", "views": "views"})

    keys = [key(row) for row in rows]
    distinct = list(dict.fromkeys(pair for pair in keys if pair is not None))
    with ThreadPoolExecutor(max_workers=4) as pool:
        metrics = dict(zip(distinct, pool.map(lookup, distinct)))
    for row, pair in zip(rows, keys):
        if pair and metrics[pair]:
            row.setdefault("engagement", {}).update(metrics[pair])
