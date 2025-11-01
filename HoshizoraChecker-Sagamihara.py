import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import math

# ======= 設定 =======
NTFY_TOPIC = "HoshizoraChecker-Sagamihara"
JST = timezone(timedelta(hours=9))
TENKI_URL_STAR = "https://tenki.jp/indexes/starry_sky/3/17/4620/14150/"
TENKI_URL_WEATHER = "https://tenki.jp/forecast/3/17/4620/14150/"
LAT, LON = 35.5714, 139.3733
OPEN_METEO_URL = f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}&hourly=cloudcover,sunrise,sunset&timezone=Asia/Tokyo"

# ======= 星空指数と降水確率 =======
def fetch_starry_data():
    res = requests.get(TENKI_URL_STAR)
    soup = BeautifulSoup(res.text, "html.parser")
    days = soup.select(".index-table-day")
    data = []
    for d in days[:2]:
        idx = d.select_one(".index-point-telop").text.strip().replace("指数", "")
        wth = d.select_one(".weather-telop").text.strip()
        data.append((idx, wth))
    return data

def fetch_weather_data():
    res = requests.get(TENKI_URL_WEATHER)
    soup = BeautifulSoup(res.text, "html.parser")
    rain_cells = soup.select(".rain-probability td")
    return [c.text.strip() for c in rain_cells[:2]]

# ======= 日没・日の出取得 =======
def fetch_sun_times():
    res = requests.get(OPEN_METEO_URL)
    data = res.json()
    sunset_str = data["hourly"]["sunset"][0]
    sunrise_next_str = data["hourly"]["sunrise"][1]
    sunset_jst = datetime.fromisoformat(sunset_str)
    sunrise_next_jst = datetime.fromisoformat(sunrise_next_str)
    return sunset_jst, sunrise_next_jst

# ======= 月齢計算 =======
def calc_moon_age(date=None):
    if date is None:
        date = datetime.now(JST)
    known_new_moon = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    synodic_month = 29.53058867
    days_since_new_moon = (date - known_new_moon.astimezone(JST)).total_seconds() / 86400
    return round(days_since_new_moon % synodic_month, 1)

# ======= 雲量データ取得 =======
def fetch_night_cloudcover(sunset_jst, sunrise_next_jst):
    res = requests.get(OPEN_METEO_URL)
    data = res.json()
    hours = [datetime.fromisoformat(t) for t in data["hourly"]["time"]]
    clouds = data["hourly"]["cloudcover"]
    subset = [(h, c) for h, c in zip(hours, clouds) if sunset_jst <= h <= sunrise_next_jst]
    return subset

# ======= 雲量バー描画（全角桁揃え版） =======
def build_cloud_graph(cloud_data):
    lines = []
    to_zen = str.maketrans("0123456789%() ", "０１２３４５６７８９％（）　")
    MAX_BAR = 20

    def pad_percent(val: int) -> str:
        if val < 10:
            pad = "　　"  # 全角スペース2個
        elif val < 100:
            pad = "　"   # 全角スペース1個
        else:
            pad = ""
        return f"{pad}{val}".translate(to_zen) + "％"

    for h, c in cloud_data:
        bar = "▮" * int(c / 100 * MAX_BAR)
        hour_zen = str(h.hour).rjust(2, "　").translate(to_zen)
        pct_zen = pad_percent(c)
        lines.append(f"{hour_zen}時（{pct_zen}）: {bar}")
    return "\n".join(lines)

# ======= 通知送信 =======
def send_ntfy(msg: str):
    requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=msg.encode("utf-8"))

# ======= メイン通知メッセージ構築 =======
def build_message(sunset_jst):
    today = datetime.now(JST)
    tomorrow = today + timedelta(days=1)
    starry_data = fetch_starry_data()
    rain_data = fetch_weather_data()
    moon_age = calc_moon_age()
    sunset, sunrise_next = fetch_sun_times()
    cloud_data = fetch_night_cloudcover(sunset, sunrise_next)
    cloud_text = build_cloud_graph(cloud_data)

    msg_lines = [
        "🌌 相模原の天体観測情報（自動）",
        f"{today:%Y-%m-%d (%a)}",
        f"【今日】指数: {starry_data[0][0]} / 降水: {rain_data[0]} / {starry_data[0][1]}",
        f"【明日】指数: {starry_data[1][0]} / 降水: {rain_data[1]} / {starry_data[1][1]}",
        f"🌙 月齢: {moon_age}日",
        f"🕓 今日の日没（相模原）: {sunset.strftime('%H:%M')}",
        f"🌅 明日の日の出（相模原）: {sunrise_next.strftime('%H:%M')}",
        f"\n☁️ 夜間雲量予報（{sunset.strftime('%H:%M')}～{sunrise_next.strftime('%H:%M')}）",
        cloud_text,
        "\n🔗 星空指数: " + TENKI_URL_STAR,
        "🔗 天気: " + TENKI_URL_WEATHER,
        "🔗 雲量(元データ): " + OPEN_METEO_URL,
        "",
        "🧪 雲量バー表示テスト"
    ]

    # ===== テスト行 (桁揃え対応) =====
    def pad_percent(val: int) -> str:
        if val < 10:
            pad = "　　"
        elif val < 100:
            pad = "　"
        else:
            pad = ""
        return f"{pad}{val}".translate(to_zen) + "％"

    to_zen = str.maketrans("0123456789%() ", "０１２３４５６７８９％（）　")
    MAX_BAR = 20
    for c in [0, 25, 50, 75, 100]:
        bar = "▮" * int(c / 100 * MAX_BAR)
        pct_zen = pad_percent(c)
        msg_lines.append(f"１７時（{pct_zen}）: {bar}")

    return "\n".join(msg_lines)

# ======= 実行ロジック =======
def main():
    now = datetime.now(JST)
    sunset, _ = fetch_sun_times()

    # 朝7時 or 日没1時間前通知
    should_notify = False
    reason = ""

    # 日没1時間前を30分単位で切り下げ
    notify_time = sunset - timedelta(hours=1)
    notify_time = notify_time.replace(minute=(notify_time.minute // 30) * 30, second=0, microsecond=0)

    if now.hour == 7 and now.minute < 10:
        should_notify = True
        reason = "朝7時"
    elif notify_time <= now < notify_time + timedelta(minutes=10):
        should_notify = True
        reason = "日没前"

    if should_notify:
        msg = build_message(sunset)
        send_ntfy(msg)
        print(f"[INFO] 通知送信 ({reason}) at {now.strftime('%H:%M')}")
    else:
        print(f"[INFO] 通知スキップ at {now.strftime('%H:%M')}")

if __name__ == "__main__":
    main()
