import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

# ==============================
# 設定
# ==============================
NTFY_TOPIC = "HoshizoraChecker-Sagamihara"
JST = timezone(timedelta(hours=9))

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
# 1. tenki.jp から星空指数とコメント
# ==============================
def fetch_starry_data():
    r = requests.get(TENKI_URL_STAR, timeout=10)
    soup = BeautifulSoup(r.text, "html.parser")

    # ここの構造はたまに変わるので、最初の2日だけ柔らかく拾う
    days = soup.select(".index-table-day")
    result = []
    for d in days[:2]:
        # 「指数: 80」とかが入っている要素を拾う
        num = d.select_one(".index-point-telop")
        idx = num.text.strip().replace("指数", "").replace(":", "").strip() if num else "?"
        # コメント（晴れ, 晴れ時々曇 など）
        wth = d.select_one(".weather-telop")
        comment = wth.text.strip() if wth else ""
        result.append((idx, comment))
    # データが少なかったときの保険
    while len(result) < 2:
        result.append(("?", ""))
    return result  # [(今日idx, 今日コメント), (明日idx, 明日コメント)]

# ==============================
# 2. tenki.jp から降水確率（今日/明日）
# ==============================
def fetch_rain_today_tomorrow():
    r = requests.get(TENKI_URL_WEATHER, timeout=10)
    soup = BeautifulSoup(r.text, "html.parser")
    # 長期的に安定している「雨の確率」の1日ぶんを取る
    # ページ構造次第なので、なければ "?" を返す
    today_rain = "?"
    tomorrow_rain = "?"

    # 今日・明日の2ブロックを探す
    blocks = soup.find_all(["section", "article", "div"])
    for b in blocks:
        title_el = b.find(["h2", "h3", "p", "h4"])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if "今日" in title and today_rain == "?":
            # その中で%を探す
            for t in b.find_all(["td", "span", "p", "li", "div"]):
                txt = t.get_text(strip=True)
                if txt.endswith("%") and txt[:-1].isdigit():
                    today_rain = txt
                    break
        if "明日" in title and tomorrow_rain == "?":
            for t in b.find_all(["td", "span", "p", "li", "div"]):
                txt = t.get_text(strip=True)
                if txt.endswith("%") and txt[:-1].isdigit():
                    tomorrow_rain = txt
                    break
        if today_rain != "?" and tomorrow_rain != "?":
            break

    return today_rain, tomorrow_rain

# ==============================
# 3. Open-Meteo から 日没・翌日の日の出
# ==============================
def fetch_sun_times():
    r = requests.get(OPEN_METEO_URL, timeout=10)
    data = r.json()
    # daily ブロックが必ずあるとは限らないので安全に
    daily = data.get("daily", {})
    sunrise_list = daily.get("sunrise", [])
    sunset_list = daily.get("sunset", [])

    if not sunset_list:
        raise RuntimeError("Open-Meteo: 日没データが取得できませんでした")
    if len(sunrise_list) < 2:
        raise RuntimeError("Open-Meteo: 翌日の日の出データが取得できませんでした")

    sunset_today = datetime.fromisoformat(sunset_list[0])  # 今日の日没（JST指定してるのでそのまま）
    sunrise_next = datetime.fromisoformat(sunrise_list[1])  # 翌日の日の出
    return sunset_today, sunrise_next

# ==============================
# 4. Open-Meteo から 夜間の雲量(hourly)を取る
# ==============================
def fetch_night_cloudcover(sunset_jst: datetime, sunrise_next_jst: datetime):
    r = requests.get(OPEN_METEO_URL, timeout=10)
    data = r.json()

    times = data["hourly"]["time"]
    covers = data["hourly"]["cloudcover"]
    result = []
    for t_str, c in zip(times, covers):
        dt = datetime.fromisoformat(t_str)  # これもJST
        if sunset_jst <= dt <= sunrise_next_jst:
            result.append((dt, int(c)))
    return result  # [(datetime, cloud%), ...]

# ==============================
# 5. 雲量をきれいにテキスト化（全角揃え）
# ==============================
def build_cloud_graph(cloud_data):
    lines = []
    MAX_BAR = 20
    to_zen = str.maketrans("0123456789%() ", "０１２３４５６７８９％（）　")

    def pad_percent(val: int) -> str:
        # 0〜9 → 全角2つ、10〜99 → 全角1つ、100 → なし
        if val < 10:
            pad = "　　"
        elif val < 100:
            pad = "　"
        else:
            pad = ""
        return f"{pad}{val}".translate(to_zen) + "％"

    for dt, c in cloud_data:
        hour_zen = f"{dt.hour:02d}".translate(to_zen)
        pct = pad_percent(c)
        bar = "▮" * int(c / 100 * MAX_BAR)
        lines.append(f"{hour_zen}時（{pct}）: {bar}")

    return "\n".join(lines) if lines else "データなし"

# ==============================
# 6. 月齢（簡易）
# ==============================
def calc_moon_age(date=None):
    if date is None:
        date = datetime.now(JST)
    base = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    diff_days = (date.astimezone(timezone.utc) - base).total_seconds() / 86400.0
    synodic = 29.53058867
    return diff_days % synodic

# ==============================
# 7. メッセージ組み立て
# ==============================
def build_message(sunset_jst: datetime):
    today = datetime.now(JST)
    starry = fetch_starry_data()  # [(idx_today, cmt_today), (idx_tom, cmt_tom)]
    rain_today, rain_tom = fetch_rain_today_tomorrow()
    sunrise_next = fetch_sun_times()[1]  # もう一回呼ぶの少し無駄だけど分かりやすさ優先
    cloud_data = fetch_night_cloudcover(sunset_jst, sunrise_next)
    cloud_text = build_cloud_graph(cloud_data)
    moon_age = calc_moon_age(today).real

    lines = []
    lines.append("🌌 相模原の天体観測情報（自動）")
    lines.append(f"📅 {today.strftime('%Y-%m-%d (%a)')}")
    lines.append(f"【今日】 指数: {starry[0][0]} / 降水: {rain_today} / {starry[0][1]}")
    lines.append(f"【明日】 指数: {starry[1][0]} / 降水: {rain_tom} / {starry[1][1]}")
    lines.append(f"🌙 月齢: {moon_age:.1f}日")
    lines.append(f"🕗 今日の日没（相模原）: {sunset_jst.strftime('%H:%M')}")
    lines.append(f"🌅 明日の日の出（相模原）: {sunrise_next.strftime('%H:%M')}")
    lines.append("")
    lines.append(f"☁️ 夜間雲量予報（{sunset_jst.strftime('%H:%M')}〜{sunrise_next.strftime('%H:%M')}）")
    lines.append(cloud_text)
    lines.append("")
    lines.append(f"🔗 星空指数: {TENKI_URL_STAR}")
    lines.append(f"🔗 天気: {TENKI_URL_WEATHER}")
    lines.append(f"🔗 雲量(元データ): {OPEN_METEO_URL}")
    lines.append("")

    # ===== テスト表示（全角揃え） =====
    lines.append("🧪 雲量バー表示テスト")
    to_zen = str.maketrans("0123456789%() ", "０１２３４５６７８９％（）　")
    MAX_BAR = 20

    def pad_percent_test(val: int) -> str:
        if val < 10:
            pad = "　　"
        elif val < 100:
            pad = "　"
        else:
            pad = ""
        return f"{pad}{val}".translate(to_zen) + "％"

    for c in [0, 25, 50, 75, 100]:
        bar = "▮" * int(c / 100 * MAX_BAR)
        pct = pad_percent_test(c)
        lines.append(f"１７時（{pct}）: {bar}")

    return "\n".join(lines)

# ==============================
# 8. 通知送信
# ==============================
def send_ntfy(text: str):
    r = requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=text.encode("utf-8"), timeout=10)
    r.raise_for_status()

# ==============================
# 9. main
# ==============================
def main():
    now = datetime.now(JST)
    # まず日の出・日没だけ1回取る
    sunset_jst, sunrise_next_jst = fetch_sun_times()

    # 朝の通知ウィンドウ：06:30〜07:29くらいに1回
    is_morning = (now.hour == 6 and now.minute >= 30) or (now.hour == 7 and now.minute < 30)

    # 日没1時間前（切り下げて30分単位）
    sunset_minus_1h = sunset_jst - timedelta(hours=1)
    block_minute = 0 if sunset_minus_1h.minute < 30 else 30
    target_block = sunset_minus_1h.replace(minute=block_minute, second=0, microsecond=0)
    is_sunset_block = (now < sunset_jst) and (now.replace(second=0, microsecond=0) == target_block)

    # GitHub Actionsからの手動実行なら必ず送るようにしておく
    event_name = os.getenv("GITHUB_EVENT_NAME", "")
    is_manual = event_name == "workflow_dispatch"

    if is_manual or is_morning or is_sunset_block:
        msg = build_message(sunset_jst)
        send_ntfy(msg)
        print("[INFO] notification sent")
    else:
        print("[INFO] skip")

if __name__ == "__main__":
    main()
