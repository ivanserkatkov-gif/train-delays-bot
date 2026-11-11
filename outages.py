#!/usr/bin/env python3
import html
import json
import os
import re
import urllib.request
from pathlib import Path

SOURCE = os.environ.get("SOURCE_CHANNEL", "svitlobot_rozd")
TARGET = os.environ.get("TARGET_CHANNEL", "@rozdilnianskahromada")
MODE = os.environ.get("MODE", "review")
KEY_PHRASE = os.environ.get("KEY_PHRASE", "Оновлено графік відключень")
STATE_FILE = Path("state_outages.json")
UA = {"User-Agent": "Mozilla/5.0 (compatible; community-monitor)"}


def http_get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as resp:
        return resp.read()


def extract_posts(page: str):
    posts = []
    for chunk in page.split('data-post="')[1:]:
        m = re.match(rf"{re.escape(SOURCE)}/(\d+)", chunk, re.IGNORECASE)
        if not m:
            continue
        t = re.search(r'tgme_widget_message_text js-message_text[^>]*>(.*?)</div>', chunk, re.DOTALL)
        text = ""
        if t:
            raw = re.sub(r"<br\s*/?>", "\n", t.group(1))
            text = html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()
        p = re.search(r"tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'\)", chunk)
        posts.append({"id": int(m.group(1)), "text": text, "photo": p.group(1) if p else None})
    return sorted(posts, key=lambda x: x["id"])


def clean_body(text: str) -> str:
    i = text.lower().find(KEY_PHRASE.lower())
    return (text[i:] if i >= 0 else text).strip()


def build_message(body: str, prefix: str = "") -> str:
    link = f'<a href="https://t.me/{SOURCE}">@{SOURCE}</a>'
    return f"{prefix}{html.escape(body, quote=False)}\n\nДжерело: {link}"


def tg(method: str, data: dict, files: dict | None = None) -> None:
    import requests

    token = os.environ["TG_BOT_TOKEN"]
    r = requests.post(f"https://api.telegram.org/bot{token}/{method}", data=data, files=files, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Telegram {method}: {r.status_code} {r.text[:300]}")


def deliver(post: dict) -> None:
    if MODE == "auto":
        chat, prefix = TARGET, ""
    else:
        chat = os.environ.get("REVIEW_CHAT_ID")
        if not chat:
            raise RuntimeError("Для MODE=review потрібен REVIEW_CHAT_ID")
        prefix = "ЧЕРНЕТКА (перешліть у канал після перевірки):\n\n"
    full = build_message(clean_body(post["text"]), prefix)
    if post["photo"]:
        img = http_get(post["photo"])
        if len(full) <= 1024:
            tg("sendPhoto", {"chat_id": chat, "caption": full, "parse_mode": "HTML"}, {"photo": ("graph.jpg", img)})
        else:
            tg("sendPhoto", {"chat_id": chat}, {"photo": ("graph.jpg", img)})
            tg("sendMessage", {"chat_id": chat, "text": full[:4000], "parse_mode": "HTML",
                               "disable_web_page_preview": "true"})
    else:
        tg("sendMessage", {"chat_id": chat, "text": full[:4000], "parse_mode": "HTML",
                           "disable_web_page_preview": "true"})


def save(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def main() -> None:
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    posts = extract_posts(http_get(f"https://t.me/s/{SOURCE}").decode("utf-8", errors="replace"))
    if not posts:
        print("На сторінці каналу постів не знайдено.")
        return
    newest = posts[-1]["id"]
    matching = [p for p in posts if KEY_PHRASE.lower() in p["text"].lower()]

    if "last_id" not in state:
        if MODE == "auto":
            save({"last_id": newest})
            print("Базову позначку встановлено, публікацій немає.")
            return
        candidates = matching[-1:]
    else:
        candidates = [p for p in matching if p["id"] > state["last_id"]]

    for p in candidates:
        deliver(p)
        state["last_id"] = p["id"]
        save(state)
        print("Надіслано пост", p["id"])
    state["last_id"] = max(newest, state.get("last_id", 0))
    save(state)
    if not candidates:
        print("Нових графіків немає.")


if __name__ == "__main__":
    main()
