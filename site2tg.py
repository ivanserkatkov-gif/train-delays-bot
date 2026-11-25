#!/usr/bin/env python3
import html
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

RSS_URL = os.environ.get("RSS_URL", "https://rozdilnianskahromada.gov.ua/rss.xml")
TARGET = os.environ.get("TARGET_CHANNEL", "@rozdilnianskahromada")
MODE = os.environ.get("MODE", "review")
MAX_PER_RUN = int(os.environ.get("MAX_PER_RUN", "5"))
SNIPPET_LEN = int(os.environ.get("SNIPPET_LEN", "280"))
LINK_TEXT = os.environ.get("LINK_TEXT", "Читати на сайті")
FOOTER_HTML = (
    '<a href="https://t.me/RozdilnyanskaSpilnota">Чат</a> · '
    '<a href="https://t.me/rozdilnianskahromada">TG</a> · '
    '<a href="https://bit.ly/3PVnh4a">Viber</a> · '
    '<a href="https://www.instagram.com/rozdilnianskahromada">Insta</a> · '
    '<a href="https://www.facebook.com/rozdilnianskahromada">FB</a> · '
    '<a href="https://www.youtube.com/channel/UC1_o-RO_R65hTodir_rDHNQ">Youtube</a> · '
    '<a href="https://t.me/rozdilnianskahromada/11702">Інфобокс</a>'
)
STATE_FILE = Path("state_site.json")
UA = {"User-Agent": "Mozilla/5.0 (compatible; community-monitor)"}


def http_get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as resp:
        return resp.read()


def parse_rss(xml_bytes: bytes):
    root = ET.fromstring(xml_bytes)
    items = []
    for it in root.iter("item"):
        link = (it.findtext("link") or it.findtext("guid") or "").strip()
        if not link:
            continue
        items.append({
            "title": (it.findtext("title") or "").strip(),
            "link": link,
            "desc": (it.findtext("description") or "").strip(),
        })
    return items


def clean_snippet(desc: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", desc))
    text = text.replace("\u2800", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\.{3}$|…$", "", text).rstrip()
    if len(text) > SNIPPET_LEN:
        text = text[:SNIPPET_LEN].rsplit(" ", 1)[0].rstrip(",;:-") + "…"
    elif desc.strip().endswith(("...", "…")):
        text += "…"
    return text


def find_og_image(page_html: str):
    for pat in (
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    ):
        m = re.search(pat, page_html, re.IGNORECASE)
        if m:
            return html.unescape(m.group(1))
    return None


def build_caption(item: dict, prefix: str = "") -> str:
    parts = [f"<b>{html.escape(item['title'], quote=False)}</b>"]
    snippet = clean_snippet(item["desc"])
    if snippet:
        parts.append(html.escape(snippet, quote=False))
    parts.append(f'<a href="{html.escape(item["link"])}">{html.escape(LINK_TEXT, quote=False)}</a>')
    return prefix + "\n\n".join(parts)


def tg(method: str, data: dict, files: dict | None = None) -> None:
    import requests

    token = os.environ["TG_BOT_TOKEN"]
    r = requests.post(f"https://api.telegram.org/bot{token}/{method}", data=data, files=files, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Telegram {method}: {r.status_code} {r.text[:300]}")


def deliver(item: dict) -> None:
    if MODE == "auto":
        chat, prefix = TARGET, ""
    else:
        chat = os.environ.get("REVIEW_CHAT_ID")
        if not chat:
            raise RuntimeError("Для MODE=review потрібен REVIEW_CHAT_ID")
        prefix = "ЧЕРНЕТКА (перешліть у канал після перевірки):\n\n"
    caption = build_caption(item, prefix)

    img = None
    try:
        image_url = find_og_image(http_get(item["link"]).decode("utf-8", errors="replace"))
        if image_url:
            img = http_get(image_url)
    except Exception as exc:
        print("Зображення не отримано:", exc)

    if img and len(caption) <= 1024:
        try:
            tg("sendPhoto", {"chat_id": chat, "caption": caption, "parse_mode": "HTML"}, {"photo": ("news.jpg", img)})
            return
        except Exception as exc:
            print("sendPhoto не вдалося, шлемо текстом:", exc)
    tg("sendMessage", {"chat_id": chat, "text": caption[:4000], "parse_mode": "HTML"})


def save(state: dict) -> None:
    state["seen"] = state["seen"][-300:]
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    items = parse_rss(http_get(RSS_URL))
    if not items:
        print("У стрічці новин немає.")
        return
    links = [i["link"] for i in items]

    if not STATE_FILE.exists():
        if MODE == "review":
            deliver(items[0])
            print("Надіслано зразок:", items[0]["link"])
        else:
            print("Базову позначку встановлено, публікацій немає.")
        save({"seen": links[::-1]})
        return

    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    seen = set(state["seen"])
    new = [i for i in reversed(items) if i["link"] not in seen]
    if not new:
        print("Нових новин немає.")
        return
    for item in new[:MAX_PER_RUN]:
        deliver(item)
        state["seen"].append(item["link"])
        save(state)
        print("Надіслано:", item["link"])
    rest = len(new) - MAX_PER_RUN
    if rest > 0:
        print(f"Залишилось {rest} новин на наступний запуск.")


if __name__ == "__main__":
    main()
