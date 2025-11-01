import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

# ==============================
# 設定
# ==============================
NTFY_TOPIC = "HoshizoraChecker-Sagamihara"
STARRY_URL = "https://tenki.jp/indexes/starry_sky/3/17/4620/14150/"
FORECAST_URL = "https://tenki.jp/forecast/3/17/4620/14150/"
LAT = 35.5714
LON = 139.3733
CLOUD_URL = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={LAT}&longitude={LON}&hourly=cloudcover&timezone=Asia/Tokyo"
)
JST = timezone(timedelta(hours=9))
LAST_FILE = ".last_sent"
DEBUG_FORCE_NOTIFY = True  # 手動実行でも送る

# ==============================
# 共通
# ==============================
def _make_soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")

def calc_moon_age(date: datetime.date) -> float:
    base = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    dt_utc = datetime(date.year, date.month, date.day, tzinfo=timezone.utc)
    days = (dt_utc - base).total_seconds() / 86400.0
    return days % 29.53058867

def fetch_sunrise_jst(for_tomorrow: bool = False) -> datetime:
    target_date = datetime.now(JST).date() + (timedelta(days=1) if for_tomorrow else timedelta(days=0))
    url = f"https://api.sunrise-sunset.org/json?lat={LAT}&lng={LON}&date={target_date.isoformat()}&formatted=0"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    sunrise_utc = datetime.fromisoformat(r.json()["results"]["sunrise"].replace("Z", "+00:00"))
    return sunrise_utc.astimezone(JST)

def fetch_sunset_jst() -> datetime:
    url = f"https://api.sunrise-sunset.org/json?lat={LAT}&lng={LON}&formatted=0"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    sunset_utc = datetime.fromisoformat(r.json()["results"]["sunset"].replace("Z", "+00:00"))
    return sunset_utc.astimezone(JST)

def floor_to_30(dt: datetime) -> datetime:
    minute = 0 if dt.minute < 30 else 30
    return dt.replace(minute=minute, second=0, microsecond=0)

# ==============================
# 星空指数・降水
# ==============================
def fetch_starry_today_tomorrow():
    r = requests.get(STARRY_URL, timeout=10)
    r.raise_for_status()
    soup = _make_soup(r.text)
    imgs = soup.find_all("img", alt=lambda x: x and "指数:" in x)

    today_date = datetime.now(JST).date()
    entries = []
    for i, img in enumerate(imgs[:2]):
        alt = img.get("alt", "")
        index_val = alt.split("指数:")[-1].strip() if "指数:" in alt else "?"
        comment = ""
        parent = img.parent
        for _ in range(5):
            if parent is None:
                break
            for p in parent.find_all("p"):
                txt = p.get_text(strip=True)
                if txt:
                    comment = txt
                    break
            if comment:
                break
            parent = parent.parent

        label = "今日" if i == 0 else "明日"
        date_str = (today_date if label == "今日" else today_date + timedelta(days=1)).strftime("%Y-%m-%d (%a)")
        entries.append({"label": label, "date": date_str, "index": index_val, "comment": comment})
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
    today_prob = tomorrow_prob = "?"
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

# ==============================
# 雲量（全角時間＋全角％＋半角ブロック▮20個）
# ==============================
def fetch_night_cloudcover(sunset_jst: datetime, sunrise_next_jst: datetime) -> str:
    r = requests.get(CLOUD_URL, timeout=10)
    r.raise_for_status()
    data = r.json()

    times = data["hourly"]["time"]
    covers = data["hourly"]["cloudcover"]

    lines = []
    MAX_BAR = 20
    to_zen = str.maketrans("0123456789%() ", "０１２３４５６７８９％（）　")

    for t, c in zip(times, covers):
        dt = datetime.fromisoformat(t).replace(tzinfo=JST)
        if sunset_jst <= dt <= sunrise_next_jst:
            bar_len = int(c / 100 * MAX_BAR)
            bar = "▮" * bar_len + " "
            hour_zen = f"{dt.hour:02d}".translate(to_zen)
            pct = f"{c:3d}%".translate(to_zen)
            line = f"{hour_zen}時（{pct}）: {bar}"
            lines.append(line)

    return "\n".join(lines) if lines else "データなし"

# ==============================
# 通知・メッセージ生成
# ==============================
def should_send(now_jst: datetime, sunset_jst: datetime) -> bool:
    if (now_jst.hour == 6 and now_jst.minute >= 30) or (now_jst.hour == 7 and now_jst.minute < 30):
        return True
    target = floor_to_30(sunset_jst - timedelta(hours=1))
    now_block = floor_to_30(now_jst)
    return now_jst < sunset_jst and now_block == target

def build_message(sunset_jst: datetime) -> str:
    today = datetime.now(JST).date()
    moon_age = calc_moon_age(today)
    sunrise_next = fetch_sunrise_jst(for_tomorrow=True)
    cloud_text = fetch_night_cloudcover(sunset_jst, sunrise_next)

    try:
        star_rows = fetch_starry_today_tomorrow()
    except Exception:
        star_rows = []

    try:
        today_rain, tomorrow_rain = fetch_rain_today_tomorrow()
    except Exception:
        today_rain = tomorrow_rain = "?"

    lines = [
        "🌌 相模原の天体観測情報（自動）",
        f"📅 {today.strftime('%Y-%m-%d (%a)')}",
    ]

    if star_rows:
        for r in star_rows:
            if r["label"] == "今日":
                lines.append(f"【今日】 指数: {r['index']} / 降水: {today_rain} / {r['comment']}")
            elif r["label"] == "明日":
                lines.append(f"【明日】 指数: {r['index']} / 降水: {tomorrow_rain} / {r['comment']}")
    else:
        lines += ["【今日】星空指数取得失敗", "【明日】星空指数取得失敗"]

    lines.append(f"🌙 月齢: {moon_age:.1f}日")
    lines.append(f"🕗 今日の日没（相模原）: {sunset_jst.strftime('%H:%M')}")
    lines.append(f"🌅 明日の日の出（相模原）: {sunrise_next.strftime('%H:%M')}")
    lines.append("")
    lines.append(f"☁️ 夜間雲量予報（{sunset_jst.strftime('%H:%M')}〜{sunrise_next.strftime('%H:%M')}）")
    lines.append(cloud_text)

    # === ここからテスト表示ブロック ===
    lines.append("")
    lines.append("🧪 雲量バー表示テスト")
    MAX_BAR = 20
    for c in [0, 25, 50, 75, 100]:
        bar = "▮" * int(c / 100 * MAX_BAR)
        lines.append(f"１７時（{str(c).rjust(3)}％）: {bar}")
    # === ここまで ===

    lines.append("")
    lines.append(f"🔗 星空指数: {STARRY_URL}")
    lines.append(f"🔗 天気: {FORECAST_URL}")
    lines.append(f"🔗 雲量(元データ): {CLOUD_URL}")
    return "\n".join(lines)

def send_ntfy(text: str):
    r = requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=text.encode("utf-8"), timeout=10)
    r.raise_for_status()

def already_sent_today(block_label: str) -> bool:
    return os.path.exists(LAST_FILE) and open(LAST_FILE).read().strip() == block_label

def mark_sent(block_label: str):
    with open(LAST_FILE, "w") as f:
        f.write(block_label)

def main():
    now_jst = datetime.now(JST)
    sunset_jst = fetch_sunset_jst()
    event_name = os.getenv("GITHUB_EVENT_NAME", "")
    is_manual = event_name == "workflow_dispatch"

    if DEBUG_FORCE_NOTIFY and is_manual:
        msg = build_message(sunset_jst)
        send_ntfy(msg)
        mark_sent("manual_test")
        print("[DEBUG] Manual run: notification sent")
        return

    if not should_send(now_jst, sunset_jst):
        print(f"[{now_jst}] skip: not in window")
        return

    target_block = floor_to_30(sunset_jst - timedelta(hours=1))
    block_label = f"{now_jst.date()}_{target_block.strftime('%H%M')}"
    if already_sent_today(block_label):
        print(f"skip: already sent for block {block_label}")
        return

    msg = build_message(sunset_jst)
    send_ntfy(msg)
    mark_sent(block_label)

if __name__ == "__main__":
    main()
