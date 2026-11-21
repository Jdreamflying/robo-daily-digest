import hashlib
from datetime import datetime, timezone

from community_signals import api_json, counts
from feed_utils import canonical_link, plain_text, safe_link


def fetch_hn(settings, cutoff, upper):
    rows = {}
    dates = f"created_at_i>={int(cutoff.timestamp())},created_at_i<={int(upper.timestamp())}"
    for field, minimum in (("points", settings["min_points"]), ("num_comments", settings["min_comments"])):
        for page in range(10):
            data = api_json(settings["url"], {
                "tags": "story", "numericFilters": f"{dates},{field}>={minimum}",
                "page": page, "hitsPerPage": 100,
            })
            if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
                return list(rows.values()), False
            for hit in data["hits"]:
                if not isinstance(hit, dict) or not str(hit.get("objectID", "")).isdigit():
                    continue
                number = str(hit["objectID"])
                engagement = counts(hit, {"points": "points", "comments": "num_comments"})
                if engagement.get("points", 0) < settings["min_points"] and engagement.get("comments", 0) < settings["min_comments"]:
                    continue
                if type(hit.get("created_at_i")) is not int or not hit.get("title"):
                    continue
                discussion = f"https://news.ycombinator.com/item?id={number}"
                url = canonical_link(safe_link(hit.get("url")) or discussion)
                rows[number] = {
                    "id": hashlib.sha256(url.encode()).hexdigest()[:24],
                    "title": plain_text(hit["title"]), "summary": plain_text(hit.get("story_text")),
                    "url": url, "discussion_url": discussion, "source": settings["name"], "tags": [],
                    "published": datetime.fromtimestamp(hit["created_at_i"], timezone.utc).isoformat(),
                    "engagement": engagement,
                }
            if page + 1 >= data.get("nbPages", 0):
                break
    return list(rows.values()), True
