import argparse
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("DIGEST_DATA_DIR") or ROOT / "data").resolve()
RAW = DATA / "raw"


def config():
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def issue_date(value=None):
    if value is None:
        return datetime.now(ZoneInfo(config()["timezone"])).date().isoformat()
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() == value:
            return value
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("expected a date in YYYY-MM-DD format")


def date_parser(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--date", type=issue_date, default=issue_date())
    return parser


def load_env():
    load_dotenv(ROOT / ".env")


def write_bytes(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        try:
            handle.write(payload)
        except BaseException:
            handle.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def write_json(path, payload):
    write_bytes(path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode())


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def recent_items(day, filename, days=7):
    issue = date.fromisoformat(day)
    for offset in range(1, days + 1):
        path = DATA / (issue - timedelta(days=offset)).isoformat() / filename
        if not path.is_file():
            continue
        for item in read_json(path)["items"]:
            yield path.parent.name, item


def write_jsonl(path, rows):
    content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    write_bytes(path, content.encode())


def read_jsonl(path):
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


if __name__ == "__main__":
    print(date_parser("Resolve the digest issue date.").parse_args().date)
