import os
import re
import unicodedata
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from digest_common import DATA, ROOT, date_parser, read_json, write_bytes

WIDTH, HEIGHT = 1440, 650
INK, MUTED, ACCENT = "#252720", "#646a60", "#a84128"
PAPER, PANEL, LINE, GREEN = "#faf9f6", "#fffefa", "#e1ded4", "#e8f0e4"


def local_fonts():
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    preferred = [
        (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"), 2),
        (Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"), 0),
        (Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"), 0),
        (Path("/System/Library/Fonts/PingFang.ttc"), 0),
        (windows / "msyh.ttc", 0),
    ]
    for path, index in preferred:
        try:
            ImageFont.truetype(str(path), size=24, index=index)
            return str(path), index
        except OSError:
            continue
    return None, 0


def latin(text):
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"}))
    return unicodedata.normalize("NFKD", text).encode("ascii", errors="ignore").decode().strip()


CLOSING_PUNCTUATION = "，。！？；：、）》”’」』】〕〉］｝…"
PUNCTUATION = re.escape(CLOSING_PUNCTUATION + "（《“‘「『【〔〈［｛")


def wrap_text(draw, text, font, width, max_lines):
    tokens = re.findall(fr"[\u3400-\u9fff{PUNCTUATION}]|[^\s\u3400-\u9fff{PUNCTUATION}]+|\s+", text)
    grouped = []
    for token in tokens:
        if token[0] in CLOSING_PUNCTUATION:
            if grouped and grouped[-1].isspace():
                grouped.pop()
            if grouped:
                grouped[-1] += token
        else:
            grouped.append(token)
    lines, line = [], ""
    for token in grouped:
        if draw.textlength((line + token).rstrip(), font=font) <= width:
            line += token
            continue
        if line.strip():
            lines.append(line.rstrip())
            line = ""
        # When a long Latin word must split, move its last glyph with any closing punctuation.
        for char in re.findall(fr".[{CLOSING_PUNCTUATION}]*", token.lstrip()):
            if draw.textlength(line + char, font=font) > width:
                if line:
                    lines.append(line)
                line = ""
            line += char
    if line.strip():
        lines.append(line.rstrip())
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and draw.textlength(lines[-1] + "…", font=font) > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] = (lines[-1] or "…") + ("" if lines[-1] == "…" else "…")
    return lines


def render(day, items, language="zh"):
    path, index = local_fonts()
    english_sources = read_json(ROOT / "assets" / "source-names.json") if language == "en" else {}
    visible_items = items[:3]
    story_count = len(visible_items)

    def font(size):
        return ImageFont.truetype(path, size=size, index=index) if path else ImageFont.load_default(size)

    image = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 12), fill=ACCENT)
    draw.text((56, 43), "ROBO DAILY DIGEST", font=font(23), fill=ACCENT)
    draw.text((WIDTH - 57, 43), day, font=font(23), fill=MUTED, anchor="ra")
    chinese = language == "zh" and path
    if story_count == 3:
        heading = "今日资讯 TOP 3" if chinese else "TOP 3 ROBOTICS STORIES"
    else:
        heading = "今日资讯精选" if chinese else "Featured Robotics Stories"
    draw.text((54, 82), heading, font=font(55), fill=INK)
    draw.line((56, 171, WIDTH - 56, 171), fill=LINE, width=2)

    card_width = {1: 720, 2: 620, 3: 427}.get(story_count, 427)
    gap = 24
    total_width = story_count * card_width + max(0, story_count - 1) * gap
    start_x = (WIDTH - total_width) // 2
    for position, raw in enumerate(visible_items):
        x = start_x + position * (card_width + gap)
        draw.rounded_rectangle((x, 200, x + card_width, 590), radius=8, fill=PANEL, outline=LINE, width=2)
        number = f"{position + 1:02d}"
        draw.text((x + 26, 215), number, font=font(61), fill=ACCENT)
        draw.line((x + 27, 300, x + card_width - 27, 300), fill=LINE, width=2)
        item = {**raw, **raw.get("i18n", {}).get(language, {})}
        if language == "en":
            item["source"] = english_sources.get(raw["source"], raw["source"])
        title = item["title"] if path else latin(item.get("original_title") or item["title"])
        title = title or (item.get("source") or "Robotics news")
        text_width = card_width - 54
        for line_number, line in enumerate(wrap_text(draw, title, font(33), text_width, 4)):
            draw.text((x + 27, 323 + line_number * 44), line, font=font(33), fill=INK)
        tag = item["tags"][0] if item["tags"] else ""
        if not path:
            tag = latin(tag) or ("Robotics" if tag else "")
        if tag:
            label = wrap_text(draw, tag, font(21), min(330, text_width - 20), 1)[0]
            pill_width = draw.textlength(label, font=font(21)) + 26
            draw.rounded_rectangle((x + 27, 512, x + 27 + pill_width, 547), radius=16, fill=GREEN)
            draw.text((x + 40, 515), label, font=font(21), fill="#365741")
        source = item["source"] if path else latin(item["source"])
        if source:
            line = wrap_text(draw, source, font(18), text_width, 1)[0]
            draw.text((x + 27, 557), line, font=font(18), fill=MUTED)
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def main():
    day = date_parser("Render the top robotics stories as a PNG.").parse_args().date
    directory = DATA / day
    items = read_json(directory / "news.json").get("items", [])
    if not items:
        print(f"Infographic: no news for {day}; skipped")
        return
    write_bytes(directory / "infographic.png", render(day, items[:3]))
    write_bytes(directory / "infographic.en.png", render(day, items[:3], "en"))
    print(f"Infographics: data/{day}/infographic.png, infographic.en.png")


if __name__ == "__main__":
    main()
