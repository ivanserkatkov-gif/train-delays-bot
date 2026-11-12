#!/usr/bin/env python3
"""
Одноразовий запуск: перевіряє канал-джерело і, якщо є нові/змінені затримки
приміських потягів, надсилає пост із зображенням.
Запускається за розкладом у GitHub Actions.
Ключі API не потрібні: читається публічна веб-версія каналу https://t.me/s/<канал>.
"""

import html
import json
import os
import re
import urllib.request
from pathlib import Path

SOURCE = os.environ.get("SOURCE_CHANNEL", "vygodainfo")
TARGET = os.environ.get("TARGET_CHANNEL", "@rozdilnianskahromada")
MODE = os.environ.get("MODE", "review")
IMAGE_PATH = os.environ.get("IMAGE_PATH", "uz_notice.jpg")
CHANGE_MIN = int(os.environ.get("CHANGE_MIN", "10"))
TRAINS = [t.strip() for t in os.environ.get("TRAINS", "").split(",") if t.strip()]
FOOTER = os.environ.get(
    "FOOTER", "Актуальні дані про рух потягів — на сайті та в застосунку Укрзалізниці."
)

STATE_FILE = Path("state.json")
HEADER_MARK = "Затримки приміських поїздів"

LINE_RE = re.compile(
    r"^(?P<num>\d{3,5})\s+(?P<route>.+?)\s+затримка\s+(?P<delay>.+?)\s+з\s+ст\.\s*(?P<station>.+?)\s*$"
)
ASOF_RE = re.compile(r"Станом на\s+(\d{1,2}\s+\S+\s+\d{1,2}:\d{2})")


def fetch_page() -> str:
    req = urllib.request.Request(
        f"https://t.me/s/{SOURCE}", headers={"User-Agent": "Mozilla/5.0 (compatible; community-monitor)"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def extract_posts(page: str):
    posts = []
    for chunk in page.split('data-post="')[1:]:
        m = re.match(rf"{re.escape(SOURCE)}/(\d+)", chunk, re.IGNORECASE)
        if not m:
            continue
        t = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', chunk, re.DOTALL)
        if not t:
            continue
        raw = re.sub(r"<br\s*/?>", "\n", t.group(1))
        text = html.unescape(re.sub(r"<[^>]+>", "", raw))
        posts.append((int(m.group(1)), text))
    return sorted(posts)


def parse_delay_minutes(text: str) -> int:
    h = re.search(r"(\d+)\s*год", text)
    m = re.search(r"(\d+)\s*хв", text)
    return (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)


def fmt_minutes(total: int) -> str:
    h, m = divmod(total, 60)
    if h and m:
        return f"{h} год {m} хв"
    return f"{h} год" if h else f"{m} хв"


def parse_post(text: str):
    if HEADER_MARK not in text:
        return None
    asof = ASOF_RE.search(text)
    trains = {}
    for raw in text.splitlines():
        m = LINE_RE.match(raw.strip())
        if not m:
            continue
        num = m.group("num")
        if TRAINS and num not in TRAINS:
            continue
        trains[num] = {
            "route": m.group("route"),
            "minutes": parse_delay_minutes(m.group("delay")),
            "station": m.group("station"),
        }
    return (asof.group(1) if asof else ""), trains


def needs_publish(current: dict, published: dict) -> bool:
    """Публікуємо, якщо змінився список потягів, станція або затримка (на CHANGE_MIN хв і більше)."""
    if set(current) != set(published):
        return True
    return any(
        i["station"] != published[n]["station"]
        or abs(i["minutes"] - published[n]["minutes"]) >= CHANGE_MIN
        for n, i in current.items()
    )


def compose(asof: str, current: dict) -> str:
    time_part = asof.split()[-1] if asof else ""
    lines = ["🚆 Затримки приміських поїздів"]
    if time_part:
        lines.append(f"Станом на {time_part}")
    lines.append("")
    for num, info in sorted(current.items()):
               lines.append(
            f"▫️ №{num} {info['route']} — затримка {fmt_minutes(info['minutes'])}. "
            f"зі ст. {info['station']}"
        )
    lines += ["", FOOTER]
    return "\n".join(lines)[:1000]


def send_photo(chat_id: str, caption: str) -> None:
    import requests

    token = os.environ["TG_BOT_TOKEN"]
    with open(IMAGE_PATH, "rb") as img:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": img},
            timeout=60,
        )
    r.raise_for_status()


def publish(caption: str) -> None:
    if MODE == "auto":
        send_photo(TARGET, caption)
        return
    chat = os.environ.get("REVIEW_CHAT_ID")
    if not chat:
        raise RuntimeError("Для MODE=review потрібен REVIEW_CHAT_ID")
    send_photo(chat, "ЧЕРНЕТКА (перешліть у канал після перевірки):\n\n" + caption)


def main() -> None:
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {"published": {}, "last_id": 0}
    posts = extract_posts(fetch_page())
    latest = None
    for msg_id, text in reversed(posts):
        parsed = parse_post(text)
        if parsed:
            latest = (msg_id, parsed)
            break
    if not latest:
        print("Постів про затримки на сторінці немає.")
        return
    msg_id, (asof, trains) = latest
    if msg_id <= state.get("last_id", 0):
        print("Нових постів немає.")
        return
    if trains and needs_publish(trains, state["published"]):
        publish(compose(asof, trains))
        state["published"] = trains
        print("Надіслано:", asof, list(trains))
    else:
        print("Суттєвих змін немає.")
    state["last_id"] = msg_id
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
