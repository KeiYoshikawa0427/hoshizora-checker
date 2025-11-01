import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

# ==============================
# 設定
# ==============================
NTFY_TOPIC = "HoshizoraChecker-Sagamihara"
JST = timezone(timedelta(hours=9))
DEBUG_FORCE_NOTIFY = True  # ← 手動実行(workflow_dispatch)なら必ず送る

# tenki.jp (相模原)
TENKI_URL_STAR = "https://tenki.jp/indexes/starry_sky/3/17/4620/14150/"
TENKI_URL_WEATHER = "https://tenki.jp/forecast/3/17/4620/14150/"

# Open-Meteo: 雲量はhourly、日の出/日没はdaily
LAT = 35.5714
LON = 139.3733
OPEN_METEO_URL = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={LAT}&longitude={LON}"
    "&hourly=cloudcover"
    "&daily=sunrise,sunset"
    "&timezone=Asia%2FTokyo"
)

# ==============================
# 星空指数・天気・降水確率
# ==============================
def fetch_starry_data():
    r = requests.get(TENKI_URL_STAR, timeout=10)
    soup = BeautifulSoup(r.text, "html.parser")
    days = soup.select(".index-table-day")
    data = []
    for d in days[:2]:
        idx = d.select_one(".index-point-telop").text.strip().replace("指数", "")
        wth = d.select_one(".weather-telop").text.strip()
        data.append((idx, wth))
    # 念のため2つにそろえる
    while len(data) < 2:
        data.append(("?", ""))
    return data

def fetch_weather_data():
    r = requests.get(TENKI_URL_WEATHER, timeout=10)
    soup = BeautifulSoup(r.text, "html.parser")
    rain_cells = soup.select(".rain-probability td")
    # 今日・明日の2つだけ取る。なければ "?"
    rains = [c.text.strip() for c in rain_cells[:2]]
    while len(rains) < 2:
        rains.append("?")
    return rains

# ==============================
# 日没・日の出（JST付きにする）
# ==============================
def fetch_sun_times():
    r = requests.get(OPEN_METEO_URL, timeout=10)
    data = r.json()
    daily = data.get("daily", {})
    sunset_str = daily["sunset"][0]
    sunrise_next_str = daily["sunrise"][1]
    sunset = datetime.fromisoformat(sunset_str).replace(tzinfo=JST)
    sunrise_next = datetime.fromisoformat(sunrise_next_str).replace(tzinfo=JST)
    return sunset, sunrise_next

# ==============================
# 月齢
# ==============================
def calc_moon_age(date=None):
    if date is None:
        date = datetime.now(JST)
    base = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    diff = (date.astimezone(timezone.utc) - base).total_seconds() / 86400
    return round(diff % 29.53058867, 1)

# ==============================
# 雲量データ取得
# ==============================
def fetch_night_cloudcover(sunset_jst, sunrise_next_jst):
    r = requests.get(OPEN_METEO_URL, timeout=10)
    data = r.json()
    times = data["hourly"]["time"]
    covers = data["hourly"]["cloudcover"]
    result = []
    for t, c in zip(times, covers):
        dt = datetime.fromisoformat(t).replace(tzinfo=JST)
        if sunset_jst <= dt <= sunrise_next_jst:
            result.append((dt, int(c)))
    return result

# ==============================
# 雲量グラフ生成（全角揃え）
# ==============================
def build_cloud_graph(cloud_data):
    lines = []
    MAX_BAR = 20
    to_zen = str.maketrans("0123456789%() ", "０１２３４５６７８９％（）　")

    def pad_percent(val: int) -> str:
        if val < 10:
            pad = "　　"  # 全角2
        elif val < 100:
            pad = "　"   # 全角1
        else:
            pad = ""
        return f"{pad}{val}".translate(to_zen) + "％"

    for dt, c in cloud_data:
        hour_zen = f"{dt.hour:02d}".translate(to_zen)
        pct_zen = pad_percent(c)
        bar = "▮" * int(c / 100 * MAX_BAR)
        lines.append(f"{hour_zen}時（{pct_zen}）: {bar}")
    return "\n".join(lines)

# ==============================
# 通知送信
# ==============================
def send_ntfy(msg: str):
    r = requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=msg.encode("utf-8"), timeout=10)
    r.raise_for_status()

# ==============================
# 通知本文生成（同じ通知内にテストを入れる）
# ==============================
def build_message(sunset_jst):
    now = datetime.now(JST)
    starry_data = fetch_starry_data()
    rain_data = fetch_weather_data()
    # 日の出はここで再取得しても軽いので素直に取る
    _, sunrise_next = fetch_sun_times()
    cloud_data = fetch_night_cloudcover(sunset_jst, sunrise_next)
    cloud_text = build_cloud_graph(cloud_data)
    moon_age = calc_moon_age(now)

    lines = [
        "🌌 相模原の天体観測情報（自動）",
        f"{now:%Y-%m-%d (%a)}",
        f"【今日】指数: {starry_data[0][0]} / 降水: {rain_data[0]} / {starry_data[0][1]}",
        f"【明日】指数: {starry_data[1][0]} / 降水: {rain_data[1]} / {starry_data[1][1]}",
        f"🌙 月齢: {moon_age}日",
        f"🕓 今日の日没（相模原）: {sunset_jst.strftime('%H:%M')}",
        f"🌅 明日の日の出（相模原）: {sunrise_next.strftime('%H:%M')}",
        f"\n☁️ 夜間雲量予報（{sunset_jst.strftime('%H:%M')}～{sunrise_next.strftime('%H:%M')}）",
        cloud_text,
        "\n🔗 星空指数: " + TENKI_URL_STAR,
        "🔗 天気: " + TENKI_URL_WEATHER,
        "🔗 雲量(元データ): " + OPEN_METEO_URL,
    ]

    # === ここからテスト表示（同じ通知の中） ===
    lines.append("")
    lines.append("🧪 雲量バー表示テスト")
    to_zen = str.maketrans("0123456789%() ", "０１２３４５６７８９％（）　")
    MAX_BAR = 20

    def pad_percent_test(val: int) -> str:
        if val < 10:
            pad = "　　"   # 全角2
        elif val < 100:
            pad = "　"    # 全角1
        else:
            pad = ""
        return f"{pad}{val}".translate(to_zen) + "％"

    for c in [0, 25, 50, 75, 100]:
        bar = "▮" * int(c / 100 * MAX_BAR)
        pct = pad_percent_test(c)
        lines.append(f"１７時（{pct}）: {bar}")
    # === ここまで ===

    return "\n".join(lines)

# ==============================
# メイン処理
# ==============================
def main():
    now = datetime.now(JST)
    sunset, _ = fetch_sun_times()

    # GitHub Actions 手動実行なら必ず送る
    event_name = os.getenv("GITHUB_EVENT_NAME", "")
    is_manual = (event_name == "workflow_dispatch")

    if is_manual and DEBUG_FORCE_NOTIFY:
        msg = build_message(sunset)
        send_ntfy(msg)
        print("[INFO] manual run -> force notify")
        return

    # ここからは通常の自動判定
    should_notify = False
    reason = ""

    # 日没1時間前を30分単位で切り下げ
    notify_time = sunset - timedelta(hours=1)
    notify_time = notify_time.replace(minute=(notify_time.minute // 30) * 30, second=0, microsecond=0)

    # 朝7時ごろ
    if now.hour == 7 and now.minute < 10:
        should_notify = True
        reason = "morning"
    # 日没前
    elif notify_time <= now < notify_time + timedelta(minutes=10):
        should_notify = True
        reason = "sunset-1h block"

    if should_notify:
        msg = build_message(sunset)
        send_ntfy(msg)
        print(f"[INFO] notify ({reason}) at {now.strftime('%H:%M')}")
    else:
        print(f"[INFO] skip at {now.strftime('%H:%M')}")

if __name__ == "__main__":
    main()
