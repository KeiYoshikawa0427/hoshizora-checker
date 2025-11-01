import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

# ==============================
# 設定
# ==============================
NTFY_TOPIC = "HoshizoraChecker-Sagamihara"
JST = timezone(timedelta(hours=9))

# 手動実行（workflow_dispatch）のときに必ず通知を送るかどうか
DEBUG_FORCE_NOTIFY = True  # ←テスト中は True、本番は False に

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
# 1. 星空指数（元に戻した版）
# ==============================
def fetch_starry_data():
    """tenki.jpの「星空指数」ページから今日・明日ぶんを取る"""
    res = requests.get(TENKI_URL_STAR, timeout=10)
    soup = BeautifulSoup(res.text, "html.parser")

    days = soup.select(".index-table-day")
    result = []
    for d in days[:2]:
        # 以前うまく出ていたセレクタに戻す
        idx_el = d.select_one(".index-point-telop")
        wx_el = d.select_one(".weather-telop")
        if idx_el:
            idx = idx_el.text.strip().replace("指数", "").replace(":", "")
        else:
            idx = "?"
        if wx_el:
            wx = wx_el.text.strip()
        else:
            wx = ""
        result.append((idx, wx))

    # 念のため2件にそろえる
    while len(result) < 2:
        result.append(("?", ""))

    return result  # [(今日指数, 今日コメント), (明日指数, 明日コメント)]

# ==============================
# 2. 降水確率（シンプル版に戻す）
# ==============================
def fetch_weather_data():
    """tenki.jpの相模原の天気ページから、降水確率を上から2つだけ取る"""
    res = requests.get(TENKI_URL_WEATHER, timeout=10)
    soup = BeautifulSoup(res.text, "html.parser")

    cells = soup.select(".rain-probability td")
    rains = [c.text.strip() for c in cells[:2]]

    # 値がないとき '---' が来ることがあるので最低限の補正
    fixed = []
    for r in rains:
        if not r or r == "---":
            fixed.append("?")
        else:
            fixed.append(r)
    while len(fixed) < 2:
        fixed.append("?")
    return fixed  # [今日降水, 明日降水]

# ==============================
# 3. 日没・翌日の日の出（JST付き）
# ==============================
def fetch_sun_times():
    res = requests.get(OPEN_METEO_URL, timeout=10)
    data = res.json()
    daily = data.get("daily", {})
    sunset_str = daily["sunset"][0]
    sunrise_next_str = daily["sunrise"][1]
    sunset = datetime.fromisoformat(sunset_str).replace(tzinfo=JST)
    sunrise_next = datetime.fromisoformat(sunrise_next_str).replace(tzinfo=JST)
    return sunset, sunrise_next

# ==============================
# 4. 月齢（簡易）
# ==============================
def calc_moon_age(date=None):
    if date is None:
        date = datetime.now(JST)
    base = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    diff_days = (date.astimezone(timezone.utc) - base).total_seconds() / 86400.0
    synodic_month = 29.53058867
    return round(diff_days % synodic_month, 1)

# ==============================
# 5. 夜間の雲量を取得（sunset〜sunrise_next）
# ==============================
def fetch_night_cloudcover(sunset_jst, sunrise_next_jst):
    res = requests.get(OPEN_METEO_URL, timeout=10)
    data = res.json()
    times = data["hourly"]["time"]
    covers = data["hourly"]["cloudcover"]

    result = []
    for t_str, c in zip(times, covers):
        dt = datetime.fromisoformat(t_str).replace(tzinfo=JST)
        if sunset_jst <= dt <= sunrise_next_jst:
            result.append((dt, int(c)))
    return result  # [(dt(JST), cloud%), ...]

# ==============================
# 6. 雲量グラフ（全角でそろえるやつ）
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

    return "\n".join(lines) if lines else "データなし"

# ==============================
# 7. 通知本文の組み立て（テスト行なし）
# ==============================
def build_message(sunset_jst):
    now = datetime.now(JST)
    starry = fetch_starry_data()
    rains = fetch_weather_data()
    _, sunrise_next = fetch_sun_times()
    cloud_data = fetch_night_cloudcover(sunset_jst, sunrise_next)
    cloud_text = build_cloud_graph(cloud_data)
    moon_age = calc_moon_age(now)

    lines = [
        "🌌 相模原の天体観測情報（自動）",
        f"{now:%Y-%m-%d (%a)}",
        f"【今日】 指数: {starry[0][0]} / 降水: {rains[0]} / {starry[0][1]}",
        f"【明日】 指数: {starry[1][0]} / 降水: {rains[1]} / {starry[1][1]}",
        f"🌙 月齢: {moon_age}日",
        f"🕓 今日の日没（相模原）: {sunset_jst.strftime('%H:%M')}",
        f"🌅 明日の日の出（相模原）: {sunrise_next.strftime('%H:%M')}",
        f"\n☁️ 夜間雲量予報（{sunset_jst.strftime('%H:%M')}～{sunrise_next.strftime('%H:%M')}）",
        cloud_text,
        "\n🔗 星空指数: " + TENKI_URL_STAR,
        "🔗 天気: " + TENKI_URL_WEATHER,
        "🔗 雲量(元データ): " + OPEN_METEO_URL,
    ]

    return "\n".join(lines)

# ==============================
# 8. ntfyに送る
# ==============================
def send_ntfy(msg: str):
    r = requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=msg.encode("utf-8"), timeout=10)
    r.raise_for_status()

# ==============================
# 9. メイン処理
# ==============================
def main():
    now = datetime.now(JST)
    sunset, _ = fetch_sun_times()

    # GitHub Actions からの手動実行なら強制で送る
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

    # 日没1時間前を30分に切り下げ
    notify_time = sunset - timedelta(hours=1)
    notify_time = notify_time.replace(
        minute=(notify_time.minute // 30) * 30,
        second=0,
        microsecond=0,
    )

    # 朝7:00ごろ
    if now.hour == 7 and now.minute < 10:
        should_notify = True
        reason = "morning"
    # 日没1時間前ブロック内
    elif notify_time <= now < notify_time + timedelta(minutes=10):
        should_notify = True
        reason = "sunset-1h"

    if should_notify:
        msg = build_message(sunset)
        send_ntfy(msg)
        print(f"[INFO] notify ({reason}) at {now.strftime('%H:%M')}")
    else:
        print(f"[INFO] skip at {now.strftime('%H:%M')}")

if __name__ == "__main__":
    main()
