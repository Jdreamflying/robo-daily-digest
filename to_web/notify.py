import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from urllib.parse import urlencode, urlsplit

import requests

from digest_common import DATA, date_parser, load_env, read_json


def setting(name):
    return os.getenv(name, "").strip()


def issue_items(day, kind):
    path = DATA / day / f"{kind}.json"
    return read_json(path)["items"] if path.is_file() else []


def digest_payload(day, site):
    news = issue_items(day, "news")[:3]
    papers = issue_items(day, "papers")[:1]
    if not (news or papers):
        return None
    base = site.rstrip("/")
    query = urlencode({"date": day})
    news_url = f"{base}/news.html?{query}" if news else ""
    papers_url = f"{base}/index.html?{query}" if papers else ""
    ledes = [item.get("lede") or item["title"] for item in news]
    paper = papers[0]["title"] if papers else ""
    subject = f"机器人研究日报 · {day}"
    lines = [subject, *(f"{i}. {lede}" for i, lede in enumerate(ledes, 1))]
    if paper:
        lines.append(f"论文精选 · {paper}")
    if news_url:
        lines.append(f"资讯 · {news_url}")
    if papers_url:
        lines.append(f"论文 · {papers_url}")
    return {
        "date": day, "news": ledes, "paper": paper, "url": news_url or papers_url,
        "news_url": news_url, "papers_url": papers_url, "text": "\n".join(lines),
    }


def post(url, payload):
    response = requests.post(url, json=payload, timeout=(5, 10), allow_redirects=False)
    response.raise_for_status()
    if response.is_redirect:
        raise ValueError("Unexpected redirect")
    try:
        result = response.json()
    except ValueError:
        return
    if isinstance(result, dict) and (
        result.get("ok") is False
        or any(result.get(key) not in (None, 0, "0") for key in ("code", "errcode", "StatusCode"))
    ):
        raise ValueError("Channel rejected the message")


def send_webhook(url, digest):
    text = digest["text"]
    host = urlsplit(url).hostname
    if host and (host.endswith(".feishu.cn") or host.endswith(".larksuite.com")):
        payload = {"msg_type": "text", "content": {"text": text}}
    elif host and host.endswith(".dingtalk.com"):
        payload = {"msgtype": "text", "text": {"content": text}}
    elif host in {"hooks.slack.com", "hooks.slack-gov.com"}:
        payload = {"text": text}
    else:
        payload = {**digest, "content": text}
    post(url, payload)


def send_telegram(token, chat, text):
    post(f"https://api.telegram.org/bot{token}/sendMessage", {
        "chat_id": chat, "text": text, "link_preview_options": {"is_disabled": True},
    })


def send_email(host, recipients, text, day):
    port = int(setting("SMTP_PORT") or "587")
    user, password = setting("SMTP_USER"), setting("SMTP_PASS")
    if not user or not password:
        raise ValueError("SMTP_USER and SMTP_PASS are required")
    message = EmailMessage()
    message["Subject"] = f"机器人研究日报 · {day}"
    message["From"] = user
    message["To"] = recipients
    message.set_content(text)
    tls = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=10, context=tls) as server:
            server.login(user, password)
            server.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls(context=tls)
            server.login(user, password)
            server.send_message(message)


def main():
    parser = date_parser("Send an optional digest notification. Defaults to off (NOTIFY_ENABLED).")
    parser.add_argument("--dry-run", action="store_true", help="skip all external notifications")
    args = parser.parse_args()
    load_env()
    if args.dry_run or setting("NOTIFY_ENABLED").casefold() != "true":
        print("Notification skipped (dry run or disabled).")
        return
    webhook = setting("NOTIFY_WEBHOOK_URL")
    token, chat = setting("TELEGRAM_BOT_TOKEN"), setting("TELEGRAM_CHAT_ID")
    smtp, recipients = setting("SMTP_HOST"), setting("SMTP_TO")
    if not (webhook or (token and chat) or (smtp and recipients)):
        print("Notification skipped (no channel configured).")
        return
    try:
        site = setting("SITE_URL")
        parts = urlsplit(site)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.query or parts.fragment:
            raise ValueError("SITE_URL must be an absolute site URL")
        digest = digest_payload(args.date, site)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        print("Warning: notification skipped (check SITE_URL and edition data).", file=sys.stderr)
        return
    if not digest:
        print("Notification skipped (empty edition).")
        return
    channels = []
    if webhook:
        channels.append(("Webhook", send_webhook, (webhook, digest)))
    if token and chat:
        channels.append(("Telegram", send_telegram, (token, chat, digest["text"])))
    if smtp and recipients:
        channels.append(("SMTP", send_email, (smtp, recipients, digest["text"], args.date)))
    for name, send, arguments in channels:
        try:
            send(*arguments)
        except (requests.RequestException, OSError, ValueError, smtplib.SMTPException) as error:
            print(f"Warning: {name} notification failed ({type(error).__name__}).", file=sys.stderr)
        else:
            print(f"{name} notification sent.")


if __name__ == "__main__":
    main()
