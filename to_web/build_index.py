import argparse
import os
from datetime import date, datetime, time, timezone
from email.utils import format_datetime
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

from digest_common import DATA, issue_date, load_env, read_json, write_bytes, write_json


def collect_issues():
    editions = []
    for directory in sorted(DATA.iterdir(), reverse=True):
        if not directory.is_dir():
            continue
        try:
            day = issue_date(directory.name)
        except argparse.ArgumentTypeError:
            continue
        entry = {"date": day}
        for kind in ("papers", "news"):
            path = directory / f"{kind}.json"
            if path.is_file():
                issue = read_json(path)
                entry[kind] = {
                    "path": f"./data/{day}/{kind}.json",
                    "count": len(issue["items"]),
                    "total_scanned": issue.get("window_count", issue["total_scanned"]),
                    "lead": issue["items"][0]["title"] if issue["items"] else "",
                }
        if len(entry) == 1:
            continue
        if entry.get("news", {}).get("count"):
            for key, name in (("infographic", "infographic.png"), ("infographic_en", "infographic.en.png")):
                if (directory / name).is_file():
                    entry[key] = f"./data/{day}/{name}"
        editions.append(entry)
    return editions


def build_rss(editions):
    base = os.getenv("SITE_URL", "").strip().rstrip("/")
    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text = "机器人研究日报"
    ET.SubElement(channel, "link").text = f"{base}/" if base else "./"
    ET.SubElement(channel, "description").text = "机器人行业资讯与 arXiv 论文精选"
    ET.SubElement(channel, "language").text = "zh-CN"
    for edition in editions[:30]:
        day = edition["date"]
        for kind, page, label in (
            ("news", "news.html", "机器人资讯"), ("papers", "index.html", "机器人论文")
        ):
            if not edition.get(kind, {}).get("count"):
                continue
            item = ET.SubElement(channel, "item")
            path = f"./{page}?date={day}"
            link = urljoin(f"{base}/", path) if base else path
            ET.SubElement(item, "title").text = f"{day} {label} | {edition[kind]['lead']}"
            ET.SubElement(item, "link").text = link
            ET.SubElement(item, "guid", isPermaLink="false").text = f"robo-daily-digest:{day}:{kind}"
            timestamp = datetime.combine(date.fromisoformat(day), time(), tzinfo=timezone.utc)
            ET.SubElement(item, "pubDate").text = format_datetime(timestamp, usegmt=True)
            ET.SubElement(item, "description").text = (
                f"从 {edition[kind]['total_scanned']} 条来源中精选 {edition[kind]['count']} 条。"
            )
    ET.indent(root)
    write_bytes(DATA.parent / "rss.xml", ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n")


def main():
    argparse.ArgumentParser(description="Build the static edition index and RSS feed.").parse_args()
    load_env()
    DATA.mkdir(exist_ok=True)
    editions = collect_issues()
    write_json(DATA / "index.json", {"editions": editions})
    build_rss(editions)
    print(f"Index: {len(editions)} editions -> data/index.json, rss.xml")


if __name__ == "__main__":
    main()
