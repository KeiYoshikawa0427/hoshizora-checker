import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

# =========================================================
# 設定
# =========================================================
NTFY_TOPIC = "HoshizoraChecker-Sagamihara"
STARRY_URL = "https://tenki.jp/indexes/starry_sky/3/17/4620/14150/"
FORECAST_URL = "https://tenki.jp/forecast/3/17/4620/14150/"
LAT = 35.5714   # 相模原近辺
LON = 139.3733
JST = timezone(timedelta(hours=9))
SLOT_MIN = 15
LAST_FILE = ".last_sent"

# デバッグ用（Trueにすると手動起動で強制送信できる）
DEBUG_FORCE_NOTIFY = True
# =========================================================


def _make_soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def calc_moon_age(date: datetime.date) -> float:
    base = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    dt_utc = datetime(date.year, date.month, date.day, tzinfo=timezone.utc)
    days = (dt_utc - base).total_seconds() / 86400.0
    synodic = 29.53058867
    return days % synodic


def fetch_sunset_jst() -> datetime:
    url = f"https://api.sunrise-sunset.org/json?lat={LAT}&lng={LON}&formatted=0"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()
    sunset_utc = datetime.fromisoformat(data["results"]["sunset"].replace("Z", "+00:00"))
    return sunset_utc.astimezone(JST)


def floor_to_30(dt: datetime) -> datetime:
    """30分単位に切り下げ（例：16:47→16:30, 16:15→16:00）"""
    minute = 0 if dt.minute < 30 else 30
    return dt.replace(minute=minute, second=0, microsecond=0)


def fetch_starry_today_tomorrow():
    r = requests.get(STARRY_URL, timeout=10)
    r.raise_for_status()
    soup = _make_soup(r.text)
    imgs = soup.find_all("img", alt=lambda x: x and "指数:" in x)
    entries = []
    today_date = datetime.now(JST).date()

    for i, img in enumerate(imgs[:2]):
        alt = img.get("alt", "")
        index_val = alt.split("指数:")[-1].strip() if "指数:" in alt else "?"
        comment = ""
        parent = img.parent
        for _ in range(5):
            if parent is None:
                break
            ps = parent.find_all("p")
            for p in ps:
                txt = p.get_text(strip=True)
                if txt:
                    comment = txt
                    break
            if comment:
                break
            parent = parent.parent

        label = "今日" if i == 0 else "明日"
        date_str = (
            today_date.strftime("%Y-%m-%d (%a)")
            if label == "今日"
            else (today_date + timedelta(days=1)).strftime("%Y-%m-%d (%a)")
        )
        entries.append({"date": date_str, "label": label, "index": index_val, "comment": comment})

    return entries


def _extract_first_percent(block) -> str:
    for tag in block.find_all(["td", "span", "p", "div", "li"]):
        txt = tag.get_text(strip=True)
        if txt.endswith("%") and txt[:-1].isdigit():
            return txt
    return "?"


def fetch_rain_today_tomorrow():
    r = requests.get(FORECAST_URL, timeout=10)
    r.raise_for_status()
    soup = _make_soup(r.text)

    today_prob = "?"
    tomorrow_prob = "?"
    for sec in soup.find_all(["section", "article", "div"]):
        heading = sec.find(["h2", "h3", "p", "h4"])
        if not heading:
            continue
        title = heading.get_text(strip=True)
        if "今日" in title and today_prob == "?":
            today_prob = _extract_first_percent(sec)
        if "明日" in title and tomorrow_prob == "?":
            tomorrow_prob = _extract_first_percent(sec)
        if today_prob != "?" and tomorrow_prob != "?":
            break

    return today_prob, tomorrow_prob


def should_send(now_jst: datetime, sunset_jst: datetime) -> bool:
    """朝 or 日没1h前ブロックなら通知"""
    # 朝の緩い窓：6:30〜7:30
    if (now_jst.hour == 6 and now_jst.minute >= 30) or (now_jst.hour == 7 and now_jst.minute < 30):
        return True

    # 日没1h前を切り下げたブロック
    raw_start = sunset_jst - timedelta(hours=1)
    target = floor_to_30(raw_start)
    now_block = floor_to_30(now_jst)

    if now_jst < sunset_jst and now_block == target:
        return True
    return False


def build_message(sunset_jst: datetime) -> str:
    today = datetime.now(JST).date()
    moon_age = calc_moon_age(today)
    try:
        star_rows = fetch_starry_today_tomorrow()
    except Exception as e:
        star_rows = []
        star_err = str(e)
    else:
        star_err = ""
    try:
        today_rain, tomorrow_rain = fetch_rain_today_tomorrow()
    except Exception as e:
        today_rain = tomorrow_rain = "?"
        rain_err = str(e)
    else:
        rain_err = ""

    lines = [
        "🌌 相模原の天体観測情報（自動）",
        f"📅 {today.strftime('%Y-%m-%d (%a)')}",
        f"🌙 月齢: {moon_age:.1f}日",
    ]

    if star_rows:
        for r in star_rows:
            if r["label"] == "今日":
                lines.append(f"【今日】 指数: {r['index']} / 降水: {today_rain} / {r['comment']}")
            elif r["label"] == "明日":
                lines.append(f"【明日】 指数: {r['index']} / 降水: {tomorrow_rain} / {r['comment']}")
    else:
        lines += ["【今日】 星空指数取得失敗", "【明日】 星空指数取得失敗"]

    lines.append(f"🕗 今日の日没（相模原）: {sunset_jst.strftime('%H:%M')}")
    lines.append("")
    lines.append(f"🔗 星空指数: {STARRY_URL}")
    lines.append(f"🔗 天気: {FORECAST_URL}")

    if star_err or rain_err:
        lines.append("")
        lines.append("⚠ 取得時のエラー:")
        if star_err:
            lines.append(f"- 星空指数: {star_err}")
        if rain_err:
            lines.append(f"- 降水: {rain_err}")

    return "\n".join(lines)


def send_ntfy(text: str):
    url = f"https://ntfy.sh/{NTFY_TOPIC}"
    r = requests.post(url, data=text.encode("utf-8"), timeout=10)
    r.raise_for_status()


def already_sent_today(block_label: str) -> bool:
    if not os.path.exists(LAST_FILE):
        return False
    with open(LAST_FILE) as f:
        last = f.read().strip()
    return last == block_label


def mark_sent(block_label: str):
    with open(LAST_FILE, "w") as f:
        f.write(block_label)


def main():
    now_jst = datetime.now(JST)
    sunset_jst = fetch_sunset_jst()
    event_name = os.getenv("GITHUB_EVENT_NAME", "")
    is_manual = event_name == "workflow_dispatch"

    # 手動強制送信
    if DEBUG_FORCE_NOTIFY and is_manual:
        msg = build_message(sunset_jst)
        send_ntfy(msg)
        mark_sent("manual_test")
        return

    if not should_send(now_jst, sunset_jst):
        print(f"[{now_jst}] skip: not in window")
        return

    # --- 重複防止 ---
    target_block = floor_to_30(sunset_jst - timedelta(hours=1))
    block_label = f"{now_jst.date()}_{target_block.strftime('%H%M')}"
    if already_sent_today(block_label):
        print(f"skip: already sent for block {block_label}")
        return
    # ----------------

    msg = build_message(sunset_jst)
    send_ntfy(msg)
    mark_sent(block_label)


if __name__ == "__main__":
    main()

