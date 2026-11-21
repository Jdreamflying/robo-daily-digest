import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from itertools import islice
from math import log1p

import requests

from digest_common import DATA, RAW, ROOT, config, date_parser, load_env, read_json, read_jsonl, write_json
from feed_utils import matches_keyword, normalized_words

PROMPTS = ROOT / "ai" / "prompts"
ALIASES = {
    "VLA": ("vision language action", "vision-language-action", "VLAs"),
    "Embodied AI": ("embodied intelligence", "physical AI"),
    "Manipulation": ("grasping", "bimanual", "dexterous", "manipulator"),
    "Whole-Body Control": ("whole-body controller", "whole body control"),
    "Sim-to-Real": ("simulation to real", "sim to real"),
    "Humanoid": ("humanoids",),
    "机器人": ("robot", "robots", "robotics"), "灵巧手": ("dexterous hand", "dexterous hands"),
    "世界模型": ("world model", "world models"), "Isaac": ("isaaclab", "isaac sim"),
    "Robot Learning": ("robot learning", "robotic learning"),
}
LABELS = {
    "Humanoid": "人形机器人", "Humanoids": "人形机器人", "VLA": "VLA", "VLAs": "VLA",
    "Embodied AI": "具身智能", "Manipulation": "机器人操作", "Whole-Body Control": "全身控制",
    "Sim-to-Real": "仿真到现实", "Artificial Intelligence": "人工智能", "Investments": "投融资",
    "Financial": "投融资", "Logistics": "物流自动化", "Manufacturing": "智能制造",
    "Mobile Robots": "移动机器人", "Autonomous Vehicles": "自动驾驶", "Industrial Robots": "工业机器人",
    "Robotics": "机器人研究", "robotics": "机器人研究", "AI": "人工智能",
    "Robot": "机器人", "Robots": "机器人", "Robot Learning": "机器人学习",
    "Embodied Intelligence": "具身智能", "Vision-Language-Action": "VLA", "World Model": "世界模型",
}
LABELS_EN = {
    "人形机器人": "Humanoid", "具身智能": "Embodied AI", "机器人操作": "Manipulation",
    "全身控制": "Whole-body control", "仿真到现实": "Sim-to-real", "人工智能": "AI",
    "投融资": "Funding", "机器人研究": "Robotics", "移动机器人": "Mobile robots",
    "机器人": "Robot", "机器人学习": "Robot learning", "灵巧手": "Dexterous hands",
    "世界模型": "World models", "物流自动化": "Logistics automation", "智能制造": "Manufacturing",
    "自动驾驶": "Autonomous vehicles", "工业机器人": "Industrial robots", "机器人产业": "Robotics industry",
    "行业资讯": "Industry news", "RSS 摘要": "RSS summary", "论文摘要": "Paper abstract",
}
LABELS = {key.casefold(): value for key, value in LABELS.items()}


def keyword_hits(text, keywords):
    normalized = normalized_words(text)
    return [key for key in keywords if any(
        matches_keyword(normalized, term)
        for term in (key, *ALIASES.get(key, ()))
    )]


def entry_topics(row, keywords, title_only=False):
    text = row["title"] if title_only else " ".join([row["title"], row["summary"], *row["tags"]])
    return localized_tags(keyword_hits(text, keywords))


FIRST_HAND_SOURCES = {"official", "release"}
NEWS_SOFT_TITLE = re.compile(
    r"\b(?:press release|sponsored|advertorial|opinion|commentary|job openings?|hiring|"
    r"hobby project|personal project|toy robot|WLED|LED strips?|pre[- ]orders?|concept robot|"
    r"plans? to (?:ship|launch|deliver)|could (?:ship|launch)|what.s next)\b|软文|招聘|概念机|预计交付",
    re.I,
)
FUTURE_DELIVERY = re.compile(
    r"\b(?:expected|scheduled|planned|aims?)\s+to\s+(?:ship|launch|deliver)\b|"
    r"\b(?:ship(?:ping|s)?|deliver(?:y|ies)?|available)\s+(?:in|by|from)\s+"
    r"(?:[\w-]+\s+){0,6}20\d{2}\b",
    re.I,
)
FUNDING = re.compile(
    r"(?:\b(?:raises|raised|secures|secured|closes|closed)\s+(?:[\w-]+\s+){0,4}|(?:完成|获得?)融资\s*)"
    r"[$€£]\s*(\d[\d,]*(?:\.\d+)?)\s*(billion|million|[bm])\b",
    re.I,
)
NEWS_MILESTONE = re.compile(
    r"\b(?:deployed|roll(?:ed|s) out|mass production|production line|(?:beg(?:an|ins?|un)|start(?:ed|s)) shipping|"
    r"acquir(?:es|ed|ing)|closed (?:the |a )?deal|(?:completed|ran|passed) real[- ]world (?:trials?|tests?))\b|"
    r"已部署|投入使用|启动量产|开始交付|完成收购",
    re.I,
)
NEWS_PROGRESS = re.compile(
    r"\b(?:releas(?:es?|ed)|launch(?:es|ed)|available now|open[- ]sourc(?:es?|ed)|"
    r"announc(?:es?|ed)|introduc(?:es?|ed)|present(?:s|ed)|published|"
    r"demonstrat(?:es?|ed)|benchmarks?|datasets?|validated|(?:built|developed) a prototype|"
    r"flies without|tested on|robot trials?)\b|发布|开源|数据集|基准测试|真机|实测|验证",
    re.I,
)
RESEARCH_PROGRESS = re.compile(
    r"\b(?:developed|demonstrated|tested|designed|built)\b.{0,85}"
    r"\b(?:robots?|exosuits?|exoskeletons?|prototypes?|systems?|devices?)\b|"
    r"\b(?:robots?|exosuits?|exoskeletons?|prototypes?)\s+"
    r"(?:was |were )?(?:developed|demonstrated|tested|designed|built)\b|"
    r"\b(?:published|posted)\b.{0,55}\b(?:paper|study|results)\b",
    re.I,
)


def weak_news(row):
    return bool(
        NEWS_SOFT_TITLE.search(row["title"])
        or (
            FUTURE_DELIVERY.search(f"{row['title']} {row['summary']}")
            and not NEWS_MILESTONE.search(row["title"])
        )
    )


def news_rule_score(row, keywords, source_type):
    if weak_news(row):
        return 1
    if source_type == "release":
        return 4
    text = f"{row['title']} {row['summary']}"
    amount = FUNDING.search(text)
    if amount:
        millions = float(amount[1].replace(",", "")) * (1000 if amount[2].lower().startswith("b") else 1)
        if millions >= 20:
            return 5 if source_type == "official" else 4
    if NEWS_MILESTONE.search(text):
        return 5 if source_type == "official" else 3
    if source_type == "official" and NEWS_PROGRESS.search(text):
        return 4
    if source_type == "community" and (NEWS_PROGRESS.search(text) or RESEARCH_PROGRESS.search(text)):
        return 3
    if row.get("category") == "ai_frontier" and source_type == "official":
        return 3
    if row.get("discussion_url") and NEWS_PROGRESS.search(text) and engagement_weight(row.get("engagement", {})) >= 0.3:
        return 3
    return 2 if entry_topics(row, keywords) else 1


def rule_score(row, kind, keywords, source_type=""):
    if kind == "news":
        return news_rule_score(row, keywords, source_type)
    generic = {"机器人", "机器人研究"}
    title_hits = set(entry_topics(row, keywords, title_only=True)) - generic
    all_hits = set(entry_topics(row, keywords)) - generic
    weight = len(all_hits) + len(title_hits)
    return min(5, 2 + (weight + 1) // 2)


LATEX_WRAPPER = re.compile(r"\\(?:text|textnormal|textbf|textit|mathrm|mathbf|mathit|mathcal|emph|operatorname)\s*\{([^{}]*)\}")
LATEX_MATH = re.compile(
    r"\\\((.*?)\\\)|\\\[(.*?)\\\]|(?<!\\)\$\$(.+?)(?<!\\)\$\$"
    r"|(?<![\\$])\$(?![\s$])([^$\r\n]+?)(?<![\s\\])\$(?![\w$])",
    re.S,
)
LATEX_SYMBOLS = dict(zip(
    "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega".split(),
    "αβγδεζηθικλμνξοπρστυφχψω",
))
LATEX_SYMBOLS.update({name.title(): symbol.upper() for name, symbol in LATEX_SYMBOLS.items()})
LATEX_SYMBOLS.update({
    "varepsilon": "ϵ", "vartheta": "ϑ", "varphi": "ϕ", "varrho": "ϱ", "varsigma": "ς",
    "times": "×", "cdot": "·", "pm": "±", "leq": "≤", "geq": "≥", "infty": "∞",
})
SUBSCRIPT_DIGITS = str.maketrans("0123456789+-", "₀₁₂₃₄₅₆₇₈₉₊₋")
NUMERIC_SCRIPT = r"(?:\{([+-]?\d+(?:\.\d+)?)\}|([+-]?\d+(?:\.\d+)?)(?!\w|\.\d))"
MATH_SUBSCRIPT = re.compile(r"(?<=[\w)])\\?_" + NUMERIC_SCRIPT)
GREEK_SUBSCRIPT = re.compile(r"(?<=[\u0370-\u03ff])\\?_" + NUMERIC_SCRIPT)
LATEX_SUBSCRIPT_SYMBOL = re.compile(r"\\([A-Za-z]+)(?=\\?_[{\d+-])")


def numeric_subscript(match):
    return (match[1] or match[2]).translate(SUBSCRIPT_DIGITS)


def clean_text(text):
    value = str(text or "")
    while LATEX_WRAPPER.search(value):
        value = LATEX_WRAPPER.sub(r"\1", value)

    def render_math(match):
        math = next(group for group in match.groups() if group is not None)
        if match.group(4) is not None and re.match(r"\s*\d[\d,.]*\s?(?:[KMBT]\b|[A-Za-z]{2,}\b)", math, re.I):
            return match[0]
        math = re.sub(r"\\([A-Za-z]+)", lambda command: LATEX_SYMBOLS.get(command[1], command[0]), math)
        math = MATH_SUBSCRIPT.sub(numeric_subscript, math)
        return re.sub(r"([_^])\{([^{}]*)\}", r"\1\2", math)

    value = LATEX_MATH.sub(render_math, value)
    value = LATEX_SUBSCRIPT_SYMBOL.sub(lambda command: LATEX_SYMBOLS.get(command[1], command[0]), value)
    value = GREEK_SUBSCRIPT.sub(numeric_subscript, value)
    value = re.sub(r"\\([$%&_#])", r"\1", value)
    return re.sub(r"\s+", " ", value).strip()


def shorten(text, limit):
    text = clean_text(text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    before, sep, _ = cut.rpartition(" ")
    return (before if sep and len(before) >= limit - 12 else cut) + "…"


def split_sentences(text):
    return [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+", clean_text(text)) if part.strip()]


def paper_sections(summary):
    sentences = split_sentences(summary)
    joined = " ".join(sentences[:4])
    background = sentences[0] if sentences else "The paper states the robotics problem and its practical motivation."
    method = next((sentence for sentence in sentences if re.search(
        r"\b(we propose|we introduce|we present|we develop|we design)\b|提出|引入", sentence, re.I
    )), joined or "See the paper for details of the approach.")
    results = [sentence for sentence in sentences if re.search(
        r"\b(outperform\w*|achiev\w*|improv\w*|success rate|percentage points)\b|\d\s*%|提升|成功率", sentence, re.I
    )]
    conclusion = " ".join(results[-2:]) or (sentences[-1] if sentences else "See the paper for the reported results.")
    return {
        "background": shorten(background, 420),
        "conclusion": shorten(conclusion, 520),
        "method": shorten(method, 520),
        "takeaways": "Check the evaluation setup, baselines, and deployment conditions before transferring the method.",
    }


def localized_tags(tags, english=False):
    values = []
    for tag in tags:
        value = LABELS.get(tag.casefold(), tag)
        if english:
            value = LABELS_EN.get(value, value)
        if not any(value.casefold() == previous.casefold() for previous in values):
            values.append(value)
    return values


def rule_editor(row, kind, keywords):
    hits = keyword_hits(f"{row['title']} {row['summary']}", keywords)
    fallback = ["机器人研究", "论文摘要", "arXiv"] if kind == "papers" else ["机器人产业", "行业资讯", "RSS 摘要"]
    source_tags = [tag for tag in row["tags"] if tag not in {"News", "Featured", "Markets / Industries"} and " / " not in tag]
    topics = localized_tags(hits)
    specific = [topic for topic in topics if topic not in {"机器人", "机器人研究"}]
    terms = [*specific, *source_tags, *topics, *fallback, "研究进展"]
    tags = localized_tags(terms)[:3]
    en_tags = localized_tags(terms, True)[:3]
    title = clean_text(row["title"]) or row["title"]
    summary = shorten(row["summary"] or row["title"], 480)
    lede = shorten(row["title"].split(": ", 1)[0], 46)
    if kind == "papers":
        sections = paper_sections(row["summary"])
        zh_sections = {
            "background": f"摘要给出的研究背景。{sections['background']}",
            "conclusion": f"以下为摘要摘录，实验指标与比较条件以原论文为准。{sections['conclusion']}",
            "method": f"摘要中的方法描述。{sections['method']}",
            "takeaways": "可结合原文核对实验设定、对照基线和部署条件，判断方法能否迁移到其他平台。",
        }
        zh = {"title": title, "summary": summary, "sections": zh_sections, "tags": tags, "lede": lede}
        en = {"title": title, "summary": clean_text(row["summary"]) or title,
              "sections": sections, "tags": en_tags, "lede": lede}
        return {**zh, "i18n": {"zh": zh, "en": en}}
    followup = f"后续可关注「{tags[0]}」相关的实施范围和验证依据。"
    zh = {"title": title, "summary": summary, "analysis": [summary, followup], "tags": tags, "lede": lede}
    en = {"title": title, "summary": summary,
          "analysis": [summary, "Check the scope and supporting evidence in the source."],
          "tags": en_tags, "lede": lede}
    return {**zh, "i18n": {"zh": zh, "en": en}}


def valid_score(value):
    return type(value) is int and 1 <= value <= 5


def filter_result(result, ids):
    scores = {}
    for item in result.get("scores", []):
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"] in ids and valid_score(item.get("score")):
            scores[item["id"]] = item["score"]
    if not scores:
        raise ValueError("no valid scores")
    return scores


HAN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\U00020000-\U000323af]")
TECH_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,29}(?: [A-Za-z0-9][A-Za-z0-9._+-]{0,29})?")


def check_language(text, lang, field):
    han = sum("\u4e00" <= char <= "\u9fff" for char in text)
    if lang == "en":
        if HAN.search(text) or not re.search(r"[A-Za-z]", text):
            raise ValueError(f"English {field} contains untranslated text or lacks English")
    elif field == "tags" and TECH_TAG.fullmatch(text):
        return
    elif field in {"title", "lede"}:
        # A short Chinese phrase can legitimately be surrounded by long model names.
        minimum = 3 if field == "title" else 2
        model_name = re.search(r"[A-Za-z]-?\d|\d[A-Za-z]", text)
        if han < minimum and not (field == "title" and han == 2 and model_name):
            raise ValueError(f"Chinese {field} is missing sufficient Chinese text")
        if re.search(r"\b(?:[A-Za-z]+\s+){5}[A-Za-z]+\b", text):
            raise ValueError(f"Chinese {field} contains an untranslated English phrase")
    else:
        minimum = 4 if field == "summary" else 1
        letters = sum(char.isalpha() for char in text)
        if han < minimum or han / letters < 0.35:
            raise ValueError(f"Chinese {field} is missing sufficient Chinese text")


def valid_locale(value, kind, lang):
    required = ("title", "summary", "lede")
    if not isinstance(value, dict) or any(not isinstance(value.get(key), str) or not value[key].strip() for key in required):
        raise ValueError("invalid locale fields")
    tags = value.get("tags")
    if not isinstance(tags, list) or len(tags) != 3 or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
        raise ValueError("expected three locale tags")
    tags = [tag.strip() for tag in tags]
    if len({tag.casefold() for tag in tags}) != 3:
        raise ValueError("expected distinct locale tags")
    clean = {key: value[key].strip() for key in required}
    clean["tags"] = tags
    language_fields = [(key, clean[key]) for key in required] + [("tags", tag) for tag in tags]
    if kind == "papers":
        sections = value.get("sections")
        keys = ("background", "conclusion", "method", "takeaways")
        if not isinstance(sections, dict) or any(not isinstance(sections.get(key), str) or not sections[key].strip()
                                                   for key in keys):
            raise ValueError("invalid paper sections")
        clean["sections"] = {key: sections[key].strip() for key in keys}
        language_fields.extend(clean["sections"].items())
    else:
        analysis = value.get("analysis")
        if not isinstance(analysis, list) or not 2 <= len(analysis) <= 3 or any(
            not isinstance(part, str) or not part.strip() for part in analysis
        ):
            raise ValueError("invalid news analysis")
        clean["analysis"] = [part.strip() for part in analysis]
        language_fields.extend(("analysis", part) for part in clean["analysis"])
    for field, content in language_fields:
        check_language(content, lang, field)
    return clean


def editor_result(result, kind):
    if not isinstance(result, dict):
        raise ValueError("expected an object")
    locales = result.get("i18n")
    if not isinstance(locales, dict):
        raise ValueError("missing i18n")
    zh = valid_locale(locales.get("zh"), kind, "zh")
    en = valid_locale(locales.get("en"), kind, "en")
    selected = zh.copy()
    selected["i18n"] = {"zh": zh, "en": en}
    return selected


THINK_BLOCK = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.I | re.S)


def extract_json_object(content):
    text = THINK_BLOCK.sub("", content)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            result, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        return result
    raise ValueError("no complete JSON object in model reply")


class EditorAPI:
    def __init__(self):
        self.base = (os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.filter_model = os.getenv("FILTER_MODEL") or "gpt-4o-mini"
        self.editor_model = os.getenv("EDITOR_MODEL") or "gpt-4o-mini"
        self.editor_max_tokens = int(os.getenv("EDITOR_MAX_TOKENS") or "2048")
        if self.editor_max_tokens <= 0:
            raise ValueError("EDITOR_MAX_TOKENS must be positive")
        self.delay = max(0, float(os.getenv("API_DELAY_SECONDS") or "1"))
        self.json_mode = os.getenv("OPENAI_JSON_MODE", "").casefold() in {"1", "true", "yes", "on"}
        self.next_call = 0.0
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {os.environ['OPENAI_API_KEY'].strip()}"

    def ask(self, model, prompt, payload, validate, label, *, max_tokens=None):
        error = "unknown error"
        attempt = 0
        while attempt < 3:
            time.sleep(max(0, self.next_call - time.monotonic()))
            retry_delay = 2 ** attempt
            request = {"model": model, "temperature": 0.2, "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ]}
            if self.json_mode:
                request["response_format"] = {"type": "json_object"}
            if max_tokens is not None:
                request["max_tokens"] = max_tokens
            try:
                response = self.session.post(f"{self.base}/chat/completions", json=request, timeout=(10, 65))
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                return validate(extract_json_object(content))
            except requests.HTTPError as exc:
                status = exc.response.status_code
                error = f"HTTP {status}"
                if status == 400 and "response_format" in request:
                    self.json_mode = False
                    print("Model endpoint returned HTTP 400 with JSON mode; retrying without response_format.", file=sys.stderr)
                    continue
                if status not in {408, 429} and status < 500:
                    break
                retry_after = exc.response.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    retry_delay = max(retry_delay, int(retry_after))
            except ValueError as exc:
                error = str(exc) or type(exc).__name__
            except (requests.RequestException, KeyError, IndexError, TypeError, AttributeError) as exc:
                error = type(exc).__name__
            finally:
                self.next_call = time.monotonic() + self.delay
            attempt += 1
            if attempt < 3:
                self.next_call = max(self.next_call, time.monotonic() + retry_delay)
        print(f"Skipped {label}: {error}", file=sys.stderr)
        return None

    def cache_path(self, stage, model, prompt, payload):
        encoded = json.dumps([self.base, model, prompt, payload], ensure_ascii=False, sort_keys=True)
        return DATA / "cache" / stage / (hashlib.sha256(encoded.encode()).hexdigest() + ".json")


def safe_cache_read(path, validator):
    try:
        return validator(read_json(path))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def article_payload(row):
    return {key: row.get(key, "") for key in ("id", "title", "summary", "tags", "published")}


def model_scores(api, rows, kind, keywords, batch_size, source_types=None):
    prompt = (PROMPTS / "filter.txt").read_text(encoding="utf-8")
    scores, pending = {}, []
    for row in rows:
        article = article_payload(row)
        if kind == "news":
            article.update(
                source=row["source"], url=row["url"],
                source_type=(source_types or {}).get(row["source"], ""),
                category=row.get("category", "robotics"),
            )
        for field in ("attention", "engagement"):
            if row.get(field):
                article[field] = row[field]
        path = api.cache_path("filter", api.filter_model, prompt, [kind, keywords, article])
        score = safe_cache_read(path, lambda value: value if valid_score(value) else None) if path.is_file() else None
        if score is not None:
            scores[row["id"]] = score
        else:
            pending.append((article, path))

    def ask_for_scores(articles, label):
        ids = {article["id"] for article in articles}
        return api.ask(api.filter_model, prompt, {"kind": kind, "keywords": keywords, "items": articles},
                       lambda result: filter_result(result, ids), label) or {}

    for start in range(0, len(pending), batch_size):
        batch = pending[start:start + batch_size]
        result = ask_for_scores([article for article, _ in batch], f"{kind} batch {start // batch_size + 1}")
        for article, path in batch:
            identifier = article["id"]
            score = result.get(identifier)
            if score is None and len(batch) > 1:
                score = ask_for_scores([article], f"{kind} filter {identifier}").get(identifier)
            if score is not None:
                write_json(path, score)
                scores[identifier] = score
    return scores


def model_article(api, prompt, row, kind):
    payload = article_payload(row)
    path = api.cache_path("editor", api.editor_model, prompt, [kind, payload])
    cached = safe_cache_read(path, lambda value: editor_result(value, kind)) if path.is_file() else None
    if cached:
        return cached
    result = api.ask(
        api.editor_model, prompt, payload, lambda value: editor_result(value, kind), f"editor {row['id']}",
        max_tokens=api.editor_max_tokens,
    )
    if result:
        write_json(path, result)
    return result


def engagement_weight(metrics):
    def scaled(key, ceiling):
        value = metrics.get(key, 0)
        return min(1, log1p(value) / log1p(ceiling)) if type(value) is int and value >= 0 else 0

    return (
        0.34 * max(scaled("points", 250), scaled("hn_points", 250), scaled("hf_upvotes", 100))
        + 0.14 * max(scaled("comments", 100), scaled("hn_comments", 100), scaled("hf_comments", 30))
        + 0.07 * max(scaled("likes", 30), scaled("views", 2000))
        + 0.15 * scaled("github_stars", 3000)
    )


def diversity_rank(rows, scores, keywords):
    topics = {row["id"]: set(entry_topics(row, keywords)) - {"机器人"} for row in rows}
    attention = {row["id"]: engagement_weight(row.get("attention", {})) for row in rows}
    seen = set()
    remaining = list(rows)
    while remaining:
        index = max(range(len(remaining)), key=lambda i: (
            scores[remaining[i]["id"]] + attention[remaining[i]["id"]]
            + min(3, len(topics[remaining[i]["id"]] - seen)) * 0.08,
            remaining[i].get("published", ""),
        ))
        row = remaining.pop(index)
        seen.update(topics[row["id"]])
        yield row


def cleanup_cache(max_age_days=14):
    cutoff = time.time() - max_age_days * 86400
    for path in (DATA / "cache").rglob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def rank_rows(rows, scores, kind, keywords, source_types):
    if kind == "papers":
        return diversity_rank(rows, scores, keywords)
    return sorted(rows, key=lambda row: (
        scores[row["id"]],
        0.4 * (source_types.get(row["source"]) in FIRST_HAND_SOURCES)
        + engagement_weight(row.get("engagement", {})),
        source_types.get(row["source"]) in FIRST_HAND_SOURCES,
        row["published"],
    ), reverse=True)


def within_source_quota(rows, quotas, default):
    admitted = Counter()
    for row in rows:
        source = row["source"]
        if admitted[source] < quotas.get(source, default):
            admitted[source] += 1
            yield row


def select_articles(ranked, kind, limit, max_ai, quotas, default_quota, edit):
    selected = []
    source_counts = Counter()
    ai_count = 0

    def choose(row):
        nonlocal ai_count
        if len(selected) >= limit:
            return
        if kind == "news" and source_counts[row["source"]] >= quotas.get(row["source"], default_quota):
            return
        if article := edit(row):
            selected.append(article)
            source_counts[row["source"]] += 1
            ai_count += article.get("category") == "ai_frontier"

    if kind == "papers":
        for row in ranked:
            if len(selected) >= limit:
                break
            choose(row)
        return selected

    reserve = limit - min(max_ai, limit * 3 // 10)
    reserved = set()
    for row in ranked:
        if len(selected) >= reserve:
            break
        if row.get("category") != "ai_frontier":
            choose(row)
            reserved.add(row["id"])
    for row in ranked:
        if len(selected) >= limit:
            break
        if row["id"] in reserved:
            continue
        if row.get("category") == "ai_frontier" and (
            ai_count >= max_ai or (ai_count + 1) * 10 > (len(selected) + 1) * 3
        ):
            continue
        choose(row)

    positions = {row["id"]: i for i, row in enumerate(ranked)}
    selected.sort(key=lambda row: positions[row["id"]])
    # Reserve two lead slots for robotics while preserving rank within each group.
    if len(selected) >= 3:
        robot_ids = (row["id"] for row in selected if row["category"] == "robotics")
        lead_ids = set(islice(robot_ids, 2))
        lead_ids.add(next(row["id"] for row in selected if row["id"] not in lead_ids))
        selected.sort(key=lambda row: row["id"] not in lead_ids)
    return selected


def publish_issue(day, kind, source, settings, api, limit=None, candidate_limit=None):
    raw = RAW / f"{day}_{source}.jsonl"
    rows, meta = read_jsonl(raw), read_json(raw.with_suffix(".meta.json"))
    keywords = settings["keywords"]
    sample = limit is not None
    selection = settings["selection"]
    limit = selection[kind] if limit is None else limit
    if kind == "news":
        limit = min(limit, 10)
    news = settings["news"]
    default_quota = news.get("default_max_per_source", 3)
    news_sources = [*news["rss"]]
    if news.get("hacker_news"):
        news_sources.append(news["hacker_news"])
    quotas = {feed["name"]: feed["max_items"] for feed in news_sources if "max_items" in feed}
    source_types = {feed["name"]: feed.get("source_type", "") for feed in news_sources}
    max_ai = min(3, max(0, selection.get("max_ai_news", 3)))
    minimum = selection.get("min_news_score", 3) if kind == "news" else 1
    if not valid_score(minimum):
        raise ValueError("selection.min_news_score must be an integer between 1 and 5")
    filename = "paper_editor.txt" if kind == "papers" else "news_editor.txt"
    prompt = (PROMPTS / filename).read_text(encoding="utf-8")

    def score_by_rules(row):
        return rule_score(row, kind, keywords, source_types.get(row["source"], ""))

    candidates = rows if limit else []
    if candidates and (sample or candidate_limit is not None):
        cap = candidate_limit if candidate_limit is not None else max(limit * 3, 12)
        rule_scores = {row["id"]: score_by_rules(row) for row in candidates}
        ranked = rank_rows(candidates, rule_scores, kind, keywords, source_types)
        if kind == "news":
            # Keep room for alternative stories when the editor rejects a result.
            ranked = within_source_quota(ranked, {key: value * 3 for key, value in quotas.items()}, default_quota * 3)
            ranked = list(ranked)
            robotics = [row for row in ranked if row.get("category") != "ai_frontier"][:cap - cap * 3 // 10]
            reserved = {row["id"] for row in robotics}
            candidates = robotics + list(islice((row for row in ranked if row["id"] not in reserved), cap - len(robotics)))
        else:
            candidates = list(islice(ranked, cap))
    scores = model_scores(api, candidates, kind, keywords, max(1, selection["filter_batch_size"]), source_types) if api else {
        row["id"]: score_by_rules(row) for row in candidates
    }
    if api and kind == "news":
        for row in candidates:
            identifier = row["id"]
            if identifier in scores and weak_news(row):
                scores[identifier] = min(2, scores[identifier])
    qualified = [row for row in candidates if scores.get(row["id"], 0) >= minimum]
    ranked = rank_rows(qualified, scores, kind, keywords, source_types)

    def edit(row):
        edited = model_article(api, prompt, row, kind) if api else rule_editor(row, kind, keywords)
        if edited:
            item = {
                **edited, "id": row["id"], "original_title": row["title"], "original_summary": row["summary"],
                "url": row["url"], "source": row["source"], "published": row["published"],
                "authors": row.get("authors", []), "score": scores[row["id"]],
            }
            if kind == "papers":
                item.update(versioned_id=row.get("versioned_id", row["id"]), figure=None)
            else:
                item["category"] = row.get("category", "robotics")
            for field in ("attention", "engagement", "discussion_url"):
                if row.get(field):
                    item[field] = row[field]
            return item
        return None

    selected = select_articles(ranked, kind, limit, max_ai, quotas, default_quota, edit)
    output = DATA / day / f"{kind}.json"
    if api and candidates and not selected and (qualified or not scores) and output.is_file():
        saved = safe_cache_read(output, lambda value: value if isinstance(value, dict) else {}) or {}
        previous = saved.get("items") or []
        eligible = {row["id"] for row in qualified} if scores else {
            row["id"] for row in candidates if score_by_rules(row) >= minimum
        }
        try:
            for item in previous:
                editor_result(item, kind)
            previous_counts = Counter(item.get("source") for item in previous)
            previous_ai = sum(item.get("category") == "ai_frontier" for item in previous)
            mix_valid = previous_ai <= max_ai and previous_ai * 10 <= len(previous) * 3 and (
                sum(item.get("category") == "ai_frontier" for item in previous[:3]) <= 1
            )
            if previous and len(previous) <= limit and all(
                item.get("id") in eligible and item.get("score", 0) >= minimum for item in previous
            ) and (
                saved.get("paper_source_date") == meta.get("paper_source_date") if kind == "papers"
                else mix_valid and all(item.get("category") in {"robotics", "ai_frontier"} for item in previous)
                and all(count <= quotas.get(name, default_quota) for name, count in previous_counts.items())
            ):
                print(f"{kind}: keeping previously validated items; all requested edits failed", file=sys.stderr)
                return
        except (ValueError, TypeError):
            pass
    issue_meta = {key: meta[key] for key in (
        "total_scanned", "total_new", "total_unique", "window_count", "window_hours", "cutoff", "window_end",
        "paper_target_date", "paper_source_date", "paper_category",
    ) if key in meta}
    write_json(output, {"date": day, **issue_meta, "scored_count": len(scores), "qualified_count": len(qualified),
                        "mode": "api" if api else "rules", "items": selected})
    print(f"{kind}: {len(scores)}/{len(candidates)} candidates scored, {len(qualified)} met the cutoff, {len(selected)} picked -> {output.name}")


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("expected a nonnegative integer")
    return number


def main():
    parser = date_parser("Score feed entries and edit the top results.")
    parser.add_argument("--dry-run", action="store_true", help="use rules even when an API key is set")
    parser.add_argument("--limit-papers", type=nonnegative, help="maximum papers to pick and enable a small candidate pool")
    parser.add_argument("--limit-news", type=nonnegative, help="maximum news items to pick and enable a small candidate pool")
    parser.add_argument("--max-filter-candidates", type=nonnegative, help="override the candidate cap per content type")
    args = parser.parse_args()
    load_env()
    api = EditorAPI() if os.getenv("OPENAI_API_KEY", "").strip() and not args.dry_run else None
    print(f"Enhancement mode: {'API' if api else 'rules (no API calls)'}")
    settings = config()
    try:
        publish_issue(args.date, "papers", "arxiv", settings, api, args.limit_papers, args.max_filter_candidates)
        publish_issue(args.date, "news", "news", settings, api, args.limit_news, args.max_filter_candidates)
    finally:
        if api:
            api.session.close()
        cleanup_cache()



if __name__ == "__main__":
    main()
