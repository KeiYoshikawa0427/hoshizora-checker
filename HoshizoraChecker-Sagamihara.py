import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import math

# =========================================================
# 設定
# =========================================================
NTFY_TOPIC = "HoshizoraChecker-Sagamihara"
STARRY_URL = "https://tenki.jp/indexes/starry_sky/3/17/4620/14150/"
FORECAST_URL = "https://tenki.jp/forecast/3/17/4620/14150/"
LAT = 35.5714   # 相模原の緯度
LON = 139.3733  # 相模原の経度
JST = timezone(timedelta(hours=9))
WINDOW_MIN = 5  # 朝7:00判定幅だけに使う
SLOT_MIN = 15   # GitHub Actions を 15分おきに回す前提
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


def round_up_to_slot(dt: datetime, slot_min: int = 15) -> datetime:
    """
    dt を slot_min 分刻みの「次の」時刻に切り上げる。
    例: 15:20, slot=15 → 15:30
        15:48, slot=15 → 16:00
    """
    minute = dt.minute
    mod = minute % slot_min
    if mod == 0 and dt.second == 0 and dt.microsecond == 0:
        # ぴったりならそのまま
        return dt.replace(second=0, microsecond=0)
    add_min = slot_min - mod
    dt2 = dt + timedelta(minutes=add_min)
    return dt2.replace(second=0, microsecond=0)


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

        entries.append(
            {
                "date": date_str,
                "label": label,
                "index": index_val,
                "comment": comment,
            }
        )

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
    """
    送信するかどうかを判定する。

    1) 朝7:00 ± WINDOW_MIN 分なら送信
    2) 日没1時間前を「15分刻みの次の時間」に切り上げた時刻と
       今が一致したときだけ送信（重複しない）
       例: 日没16:20 → 1h前=15:20 → 切り上げて15:30 → 15:30に1回だけ送る
    """
    # 1) 朝7:00判定
    target_morning = now_jst.replace(hour=7, minute=0, second=0, microsecond=0)
    if abs((now_jst - target_morning).total_seconds()) <= WINDOW_MIN * 60:
        return True

    # 2) 日没1時間前の「送るべきスロット」を計算
    window_start = sunset_jst - timedelta(hours=1)         # 生の「1時間前」
    target_slot = round_up_to_slot(window_start, SLOT_MIN) # 15分に切り上げた時刻

    # print用に残しておくとデバッグしやすい
    # print("now:", now_jst, "slot:", target_slot)

    # 今がそのスロットなら送る（分・秒まで一致）
    if now_jst.replace(second=0, microsecond=0) == target_slot:
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
        today_rain = "?"
        tomorrow_rain = "?"
        rain_err = str(e)
    else:
        rain_err = ""

    lines = []
    lines.append("🌌 相模原の天体観測情報（自動）")
    lines.append(f"📅 {today.strftime('%Y-%m-%d (%a)')}")
    lines.append(f"🌙 月齢: {moon_age:.1f}日")

    if star_rows:
        for r in star_rows:
            if r["label"] == "今日":
                lines.append(f"【今日】 指数: {r['index']} / 降水: {today_rain} / {r['comment']}")
            elif r["label"] == "明日":
                lines.append(f"【明日】 指数: {r['index']} / 降水: {tomorrow_rain} / {r['comment']}")
    else:
        lines.append("【今日】 データ取得失敗（星空指数）")
        lines.append("【明日】 データ取得失敗（星空指数）")

    lines.append(f"🕗 今日の日没（相模原）: {sunset_jst.strftime('%H:%M')}")
    lines.append("")
    lines.append(f"🔗 星空指数: {STARRY_URL}")
    lines.append(f"🔗 天気: {FORECAST_URL}")

    if star_err or rain_err:
        lines.append("")
        lines.append("⚠ 取得時のメモ:")
        if star_err:
            lines.append(f"- 星空指数: {star_err}")
        if rain_err:
            lines.append(f"- 降水: {rain_err}")

    return "\n".join(lines)


def send_ntfy(text: str):
    url = f"https://ntfy.sh/{NTFY_TOPIC}"
    r = requests.post(url, data=text.encode("utf-8"), timeout=10)
    r.raise_for_status()


def main():
    now_jst = datetime.now(JST)
    sunset_jst = fetch_sunset_jst()

    # 手動実行は必ず送る（テストしやすくする）
    event_name = os.getenv("GITHUB_EVENT_NAME", "")
    force_send = event_name == "workflow_dispatch"

    if not force_send:
        if not should_send(now_jst, sunset_jst):
            print(f"[{now_jst.strftime('%Y-%m-%d %H:%M:%S')}] skip: not in send window")
            return

    msg = build_message(sunset_jst)
    print(msg)
    send_ntfy(msg)


if __name__ == "__main__":
    main()
