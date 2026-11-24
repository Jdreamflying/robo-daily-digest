import re
from urllib.parse import urlsplit

from feed_utils import normalized_words

SOURCE_PRIORITY = {"official": 4, "release": 4, "specialist_media": 3, "community": 2}
FUNDING_EVENT = re.compile(
    r"^(.{2,100}?)\s+(?:raises|raised|secures|secured|closes|closed)\s+"
    r"(?:[\w-]+\s+){0,3}([$€£])\s*(\d[\d,.]*)\s*(billion|million|[mb])\b", re.I,
)
RELEASE_EVENT = re.compile(
    r"^(.{2,70}?)\s+(?:version\s+)?v?(\d+\.\d+(?:\.\d+)*(?:-[\w.]+)?)"
    r"(?=$|\s|:|,)", re.I,
)
SKIP_PREFIX = re.compile(r"^(?:Show HN|Launch HN)\s*:\s*", re.I)
MODEL_EVENT = re.compile(
    r"^(?:(?:introducing|announcing|meet)\s+)?"
    r"((?:gemini|claude|gpt|deepseek|qwen|llama)[ -]\d+(?:\.\d+)?)"
    r"(?:[ -](neon|opus|sonnet|haiku|flash|pro|mini|max))?"
    r"(?=$|[:–—]|\s+(?:is (?:here|the new|now available)|looks like|launch(?:ed|es)?|releas(?:e|ed)))",
    re.I,
)
LAUNCH_SLUG = re.compile(r"^(?:introducing|announcing)-([a-z0-9]+(?:-[a-z0-9]+){1,4})$", re.I)


def title_key(title):
    return normalized_words(SKIP_PREFIX.sub("", title)).strip()


def event_key(title):
    title = SKIP_PREFIX.sub("", title)
    if match := FUNDING_EVENT.search(title):
        company = normalized_words(re.split(r"['’]s\s+", match[1])[-1])
        try:
            amount = float(match[3].replace(",", ""))
        except ValueError:
            return ""
        if match[4].lower().startswith("b"):
            amount *= 1000
        return f"funding:{' '.join(company.split()[-3:])}:{match[2]}:{amount:g}"
    if match := MODEL_EVENT.search(title):
        return f"model:{normalized_words(' '.join(part for part in match.groups() if part))}"
    if match := RELEASE_EVENT.search(title):
        return f"release:{normalized_words(match[1])}:{match[2].lower()}"
    return ""


def launch_anchors(rows):
    anchors = {}
    for row in rows:
        slug = urlsplit(row["url"]).path.rsplit("/", 1)[-1]
        if match := LAUNCH_SLUG.fullmatch(slug):
            name = normalized_words(match[1])
            if title_key(row["title"]) not in (name, f"introducing {name}", f"announcing {name}"):
                continue
            product = re.escape(name)
            announcement = re.compile(
                rf"\b(?:introduc\w*|announc\w*|launch\w*|releas\w*) (?:the |new )?{product}\b"
                rf"|\bnew {product} (?:product |tool |model )?(?:being |was )?announced\b"
            )
            anchors[name] = announcement
    return anchors


def named_launch_key(row, anchors):
    title = title_key(row["title"])
    summary = normalized_words(row["summary"])
    slug = normalized_words(urlsplit(row["url"]).path.rsplit("/", 1)[-1])
    for name, announcement in anchors.items():
        if announcement.search(title) or (
            f" {name} " in f" {slug} " and (announcement.search(slug) or announcement.search(summary))
        ):
            return f"launch:{name}"
    return ""


DRONE_INCIDENT = re.compile(
    r"^([\w][\w.'’-]*)\s+(?:delivery\s+)?drone\s+(?:crash\w*|damag\w*|hits?|struck)\b(.*)",
    re.I,
)


def same_incident(left, right):
    a, b = (DRONE_INCIDENT.match(row["title"]) for row in (left, right))
    if not a or not b or a[1].casefold() != b[1].casefold():
        return False
    # Require a named detail from one headline that also appears in both article slugs.
    names = set(re.findall(r"\b[A-Z][a-z]{3,}\b", f"{a[2]} {b[2]}"))
    slug_words = [set(normalized_words(urlsplit(row["url"]).path).split()) for row in (left, right)]
    return bool({word.casefold() for word in names} & slug_words[0] & slug_words[1])


def merge_engagement(canonical, duplicate):
    if duplicate.get("discussion_url"):
        signal = duplicate.get("engagement", {})
        canonical_signal = canonical.get("engagement", {})
        if not canonical.get("discussion_url") or (signal.get("points", 0), signal.get("comments", 0)) > (
            canonical_signal.get("hn_points", 0), canonical_signal.get("hn_comments", 0),
        ):
            canonical["discussion_url"] = duplicate["discussion_url"]
            engagement = canonical.setdefault("engagement", {})
            engagement["hn_points"] = signal.get("points", 0)
            engagement["hn_comments"] = signal.get("comments", 0)
