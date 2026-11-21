import re
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import urljoin, urlsplit

import requests
from PIL import Image, ImageOps

from digest_common import DATA, date_parser, read_json, write_bytes, write_json
from public_http import fetch_public

IMAGE_MARKERS = re.compile(
    r"(?:^|[/_.\s-])(?:logos?|logotype|wordmark|brandmark|icons?|favicon|avatar|badge|placeholder|tracking|spacer)"
    r"(?:[/_.\s-]|$)", re.I,
)
MEDIA_HOSTS_TO_SKIP = {"opengraph.githubassets.com", "github.githubassets.com", "avatars.githubusercontent.com"}
GEMINI_WORDMARK = re.compile(r"/gemini-[^/]+__blog-header(?:__|[.])", re.I)


class ArticleImages(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.body = []
        self.preview = []
        self.regions = []

    def handle_starttag(self, tag, attributes):
        attrs = {key: value or "" for key, value in attributes}
        if tag in {"article", "main"}:
            self.regions.append(tag)
        elif tag == "img" and self.regions:
            url = attrs.get("data-src") or attrs.get("data-original") or attrs.get("src")
            source = attrs.get("srcset", "").partition(",")[0].split()
            if not url and source:
                url = source[0]
            if url:
                self.body.append((url, attrs.get("alt", ""), attrs.get("class", "")))
        elif tag == "meta" and (attrs.get("property") or attrs.get("name")) in {
            "og:image", "og:image:secure_url", "twitter:image",
        } and attrs.get("content"):
            self.preview.append((attrs["content"], "", ""))

    def handle_endtag(self, tag):
        if self.regions and self.regions[-1] == tag:
            self.regions.pop()


def suitable_url(url, alt="", css_class=""):
    parts = urlsplit(url)
    return bool(
        parts.scheme in {"http", "https"} and parts.hostname not in MEDIA_HOSTS_TO_SKIP
        and not parts.path.lower().endswith((".svg", ".ico"))
        and not IMAGE_MARKERS.search(f"{parts.path} {alt} {css_class}")
        and not (parts.hostname == "storage.googleapis.com" and GEMINI_WORDMARK.search(parts.path))
    )


def webp(payload):
    with Image.open(BytesIO(payload)) as original:
        width, height = original.size
        if width < 450 or height < 250 or not 0.55 <= width / height <= 3 or width * height > 25_000_000:
            return None
        image = ImageOps.exif_transpose(original).convert("RGB")
        image.thumbnail((1200, 800), Image.Resampling.LANCZOS)
        result = BytesIO()
        image.save(result, "WEBP", quality=82, method=4)
        return result.getvalue(), image.width, image.height


def news_image(item, day):
    if not re.fullmatch(r"[0-9a-f]{24}", item["id"]):
        return None
    try:
        article = fetch_public(item["url"], 2_500_000, ("text/html", "application/xhtml+xml"))
        if not article:
            return None
        article_url, html = article
        parser = ArticleImages()
        parser.feed(html.decode("utf-8", errors="replace"))
        parser.close()
    except (requests.RequestException, OSError, ValueError):
        return None
    tried = set()
    for src, alt, css_class in [*parser.body[:3], *parser.preview, *parser.body[3:]]:
        try:
            url = urljoin(article_url, src)
            if url in tried or not suitable_url(url, alt, css_class):
                continue
            if len(tried) >= 7:
                break
            tried.add(url)
            image = fetch_public(url, 8_000_000, ("image/",))
            if not image or not suitable_url(image[0]):
                continue
            if not (encoded := webp(image[1])):
                continue
            payload, width, height = encoded
            filename = f"{item['id']}.webp"
            write_bytes(DATA / day / "news_images" / filename, payload)
            return {
                "path": f"./data/{day}/news_images/{filename}", "width": width, "height": height,
                "source_url": article_url, "image_url": image[0], "alt": alt,
            }
        except (requests.RequestException, OSError, ValueError, Image.DecompressionBombError):
            continue
    return None


def main():
    day = date_parser("Fetch relevant original images for the news lead cards.").parse_args().date
    file = DATA / day / "news.json"
    issue = read_json(file)
    top = issue["items"][:3]
    with ThreadPoolExecutor(max_workers=3) as pool:
        images = list(pool.map(lambda item: news_image(item, day), top))
    for item, image in zip(top, images):
        item.pop("image", None)
        if image:
            item["image"] = image
    if top:
        write_json(file, issue)
    print(f"News images: {sum(image is not None for image in images)}/{len(top)} lead stories")


if __name__ == "__main__":
    main()
