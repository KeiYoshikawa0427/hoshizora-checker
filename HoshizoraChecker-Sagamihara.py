import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

# =========================================================
# 設定
# =========================================================
NTFY_TOPIC = "HoshizoraChecker-Sagamihara"
STARRY_URL = "https://tenki.jp/indexes/starry_sky/3/17/4620/14150/"
FORECAST_URL = "https://tenki.jp/forecast/3/17/4620/14150/"
JST = timezone(timedelta(hours=9))
# =========================================================


def _make_soup(html: str) -> BeautifulSoup:
    """lxmlがあればlxmlで、なければ標準parserでパースする"""
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def calc_moon_age(date: datetime.date) -> float:
    """簡易月齢計算（天文用途ではなく通知用途）"""
    base = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    dt_utc = datetime(date.year, date.month, date.day, tzinfo=timezone.utc)
    days = (dt_utc - base).total_seconds() / 86400.0
    synodic = 29.53058867
    return days % synodic


def fetch_starry_today_tomorrow():
    """
    星空指数ページから「指数:XX」を持つimgを上から2つ取って
    1つ目を【今日】、2つ目を【明日】にする。
    ページ構造が日によって変わっても耐えるようにする。
    """
    r = requests.get(STARRY_URL, timeout=10)
    r.raise_for_status()
    soup = _make_soup(r.text)

    imgs = soup.find_all("img", alt=lambda x: x and "指数:" in x)

    entries = []
    today_date = datetime.now(JST).date()

    for i, img in enumerate(imgs[:2]):
        # 「指数:80」みたいなaltから数字部分だけ取る
        alt = img.get("alt", "")
        index_val = alt.split("指数:")[-1].strip() if "指数:" in alt else "?"

        # コメントっぽい<p>を周辺から探す
        comment = ""
        parent = img.parent
        for _ in range(5):  # 親を最大5段階までさかのぼる
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
    """指定ブロックの中から最初に現れる「xx%」を返す"""
    for tag in block.find_all(["td", "span", "p", "div", "li"]):
        txt = tag.get_text(strip=True)
        if txt.endswith("%") and txt[:-1].isdigit():
            return txt
    return "?"


def fetch_rain_today_tomorrow():
    """
    /forecast/.../ のページから
    「今日」「明日」と書いてあるブロックだけを対象にして降水確率を取る。
    下のほうの「横浜市」などの他地域は無視する。
    """
    r = requests.get(FORECAST_URL, timeout=10)
    r.raise_for_status()
    soup = _make_soup(r.text)

    today_prob = "?"
    tomorrow_prob = "?"

    # ページの大きなかたまりを上から見ていく
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


def build_message() -> str:
    """最終的に送るテキストを組み立てる"""
    today = datetime.now(JST).date()
    moon_age = calc_moon_age(today)

    # 星空指数
    try:
        star_rows = fetch_starry_today_tomorrow()
    except Exception as e:
        star_rows = []
        star_err = str(e)
    else:
        star_err = ""

    # 降水確率
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
    lines.append(f"🌙 月齢: {moon_age:.1f}日")  # ← 改行削除
    # ここで空行を入れない

    # 【今日】【明日】だけ出す（多くても2つ）
    if star_rows:
        for r in star_rows:
            if r["label"] == "今日":
                lines.append(
                    f"【今日】 指数: {r['index']} / 降水: {today_rain} / {r['comment']}"
                )
            elif r["label"] == "明日":
                lines.append(
                    f"【明日】 指数: {r['index']} / 降水: {tomorrow_rain} / {r['comment']}"
                )
    else:
        lines.append("【今日】 データ取得失敗（星空指数）")
        lines.append("【明日】 データ取得失敗（星空指数）")

    lines.append("")
    lines.append(f"🔗 星空指数: {STARRY_URL}")
    lines.append(f"🔗 天気: {FORECAST_URL}")

    # 失敗した場合は最後にエラー概要を付ける
    if star_err or rain_err:
        lines.append("")
        lines.append("⚠ 取得時のメモ:")
        if star_err:
            lines.append(f"- 星空指数: {star_err}")
        if rain_err:
            lines.append(f"- 降水: {rain_err}")

    return "\n".join(lines)


def send_ntfy(text: str):
    """ntfy.sh に通知を送る"""
    url = f"https://ntfy.sh/{NTFY_TOPIC}"
    r = requests.post(url, data=text.encode("utf-8"), timeout=10)
    r.raise_for_status()


def main():
    msg = build_message()
    print(msg)
    send_ntfy(msg)


if __name__ == "__main__":
    main()
