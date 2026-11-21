import re
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import urljoin, urlsplit

import requests
from PIL import Image

from digest_common import DATA, config, date_parser, read_json, write_bytes, write_json
from feed_utils import safe_link

GITHUB_RE = re.compile(r"(?<![\w./@-])(?:https?://)?(?:www\.)?github\.com/[\w.-]+/[\w.-]+(?:/[^\s)\]}<>\"\\]*)?", re.I)
PROJECT_RE = re.compile(r"(?<![\w./@-])(?:https?://)?(?:[\w-]+\.github\.io(?::443)?(?:/[^\s)\]}<>\"\\]*)?|(?:www\.)?huggingface\.co/[^\s)\]}<>\"\\]+)", re.I)
CAPTION_HINTS = (
    (r"real[- ]world|hardware|deployment|physical robot", 9),
    (r"experiment|qualitative|teaser|overview", 5),
    (r"\brobots?\b|\bfigure\s*1\b", 2),
)
BAD_CAPTION = re.compile(r"^\s*(?:figure\s*\d+\s*[:.]?\s*)?(?:formula|equation|symbol)\b", re.I)
ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7})(?:v\d+)?", re.I)
HEADERS = {"User-Agent": "robo-daily-digest/1.0 (public arXiv paper enrichment)"}


class PaperHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.figures, self._figures = [], [], []
        self._caption = False
        self._annotation_depth = 0
        self._sections = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"annotation", "annotation-xml"}:
            self._annotation_depth += 1
        if self._annotation_depth:
            return
        attrs = dict(attrs)
        href = attrs.get("href")
        if tag == "section":
            self._sections += 1
        if tag == "a" and href and self._sections <= 2 and not self._figures:
            self.links.append(href)
        if tag == "figure":
            self._figures.append({"sources": [], "caption": ""})
        if self._figures and tag == "img":
            src = attrs.get("data-src") or attrs.get("src")
            classes = (attrs.get("class") or "").split()
            if src and not any("math" in name.casefold() for name in classes):
                self._figures[-1]["sources"].append(src)
        if self._figures and tag == "figcaption":
            self._caption = True

    def handle_endtag(self, tag):
        if self._annotation_depth:
            if tag in {"annotation", "annotation-xml"}:
                self._annotation_depth -= 1
            return
        if tag == "figcaption":
            self._caption = False
        if tag == "figure" and self._figures:
            self.figures.append(self._figures.pop())

    def handle_data(self, data):
        if self._figures and self._caption and not self._annotation_depth:
            self._figures[-1]["caption"] += data


def clean_caption(value):
    return " ".join(re.sub(r"\s+", " ", value or "").split()).strip()


def extract_links(text):
    urls = []
    for pattern in (GITHUB_RE, PROJECT_RE):
        match = pattern.search(text or "")
        url = match[0].rstrip(".,;:!?") if match else ""
        if url and not url.lower().startswith(("https://", "http://")):
            url = f"https://{url}"
        urls.append(url or None)
    return tuple(urls)


def html_details(versioned_id):
    try:
        response = requests.get(
            f"https://arxiv.org/html/{versioned_id}",
            headers=HEADERS, timeout=10,
        )
        response.raise_for_status()
        parts = urlsplit(response.url)
        if parts.hostname != "arxiv.org" or not parts.path.startswith("/html/"):
            return None, None
        parser = PaperHTML()
        parser.feed(response.text)
        parser.close()
        return response.url, parser
    except (requests.RequestException, ValueError):
        return None, None


def choose_figure(parser, base_url):
    candidates = []
    for figure in parser.figures:
        caption = clean_caption(figure["caption"])
        if not caption or BAD_CAPTION.search(caption):
            continue
        score = sum(weight for pattern, weight in CAPTION_HINTS if re.search(pattern, caption, re.I))
        for src in figure["sources"]:
            url = safe_link(urljoin(base_url, src))
            parts = urlsplit(url)
            if parts.hostname == "arxiv.org" and parts.path.startswith("/html/") and not parts.path.endswith(".svg"):
                candidates.append((score, url, caption))
    candidates.sort(key=lambda candidate: candidate[0], reverse=True)
    tried = set()
    for _, url, caption in candidates:
        if url in tried:
            continue
        if len(tried) >= 5:
            break
        tried.add(url)
        try:
            with requests.get(url, headers=HEADERS, timeout=10, stream=True, allow_redirects=False) as response:
                if response.status_code != 200:
                    continue
                raw = bytearray()
                for chunk in response.iter_content(64 * 1024):
                    raw.extend(chunk)
                    if len(raw) > 12 * 1024 * 1024:
                        break
            if len(raw) > 12 * 1024 * 1024:
                continue
            with Image.open(BytesIO(raw)) as image:
                width, height = image.size
                if width < 180 or height < 100 or width / height > 7 or height / width > 5:
                    continue
                image.thumbnail((960, 960), Image.Resampling.LANCZOS)
                result = BytesIO()
                image.convert("RGBA" if "transparency" in image.info or "A" in image.mode else "RGB").save(
                    result, format="WEBP", quality=82, method=6,
                )
                return caption, result.getvalue()
        except (requests.RequestException, OSError, ValueError, Image.DecompressionBombError):
            continue
    return None


def enrich_item(item, day):
    identifier = item["id"]
    version = item.get("versioned_id") or identifier
    code_url, project_url = extract_links(item.get("original_summary", ""))
    figure = item.get("figure")
    item.update(code_url=code_url, project_url=project_url, figure=figure, pdf_url=None)
    if not ARXIV_ID.fullmatch(identifier) or not ARXIV_ID.fullmatch(version):
        return item
    item["pdf_url"] = f"https://arxiv.org/pdf/{identifier}"
    if not code_url or not project_url or not figure:
        base_url, parser = html_details(version)
        if parser:
            for link in parser.links:
                link = urljoin(base_url, link)
                if not code_url and GITHUB_RE.fullmatch(link):
                    code_url = link
                if not project_url and PROJECT_RE.fullmatch(link):
                    project_url = link
            if not figure:
                selected = choose_figure(parser, base_url)
                if selected:
                    caption, image = selected
                    filename = identifier.replace("/", "-") + ".webp"
                    try:
                        write_bytes(DATA / day / "figures" / filename, image)
                    except OSError:
                        pass
                    else:
                        figure = {"path": f"./data/{day}/figures/{filename}", "caption": caption}
    item["code_url"] = code_url
    item["project_url"] = project_url
    item["figure"] = figure
    return item


def main():
    parser = date_parser("Extract paper links and figures from arXiv HTML.")
    parser.add_argument("--limit-papers", "--limit", dest="limit", type=int, default=config()["selection"]["papers"])
    args = parser.parse_args()
    path = DATA / args.date / "papers.json"
    if not path.is_file():
        print(f"No papers file for {args.date}; skipping enrichment.")
        return
    payload = read_json(path)
    items = payload.get("items", [])
    limit = max(0, args.limit)
    with ThreadPoolExecutor(max_workers=4) as pool:
        enriched = list(pool.map(lambda item: enrich_item(item, args.date), items[:limit]))
    payload["items"] = enriched + items[len(enriched):]
    write_json(path, payload)
    hits = {field: sum(bool(item.get(field)) for item in enriched) for field in ("figure", "code_url", "project_url")}
    print(f"Paper enrichment: {len(enriched)} papers, {hits}")


if __name__ == "__main__":
    main()
