import re

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
