import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

# ===== 設定 =====
NTFY_TOPIC = "HoshizoraChecker-Sagamihara"
STARRY_URL = "https://tenki.jp/indexes/starry_sky/3/17/4620/14150/"
FORECAST_URL = "https://tenki.jp/forecast/3/17/4620/14150/"
JST = timezone(timedelta(hours=9))
# ================


def calc_moon_age(date):
    """簡易月齢計算"""
    base = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    dt_utc = datetime(date.year, date.month, date.day, tzinfo=timezone.utc)
    days = (dt_utc - base).total_seconds() / 86400.0
    synodic = 29.53058867
    return days % synodic


def fetch_starry_today_tomorrow():
    """
    星空指数ページから「指数:XX」が付いているものを上から2つ取る。
    ページによって<h3>の表現が違っても拾えるようにする。
    戻り値は [{'label': '今日', ...}, {'label': '明日', ...}] の最大2件。
    """
    r = requests.get(STARRY_URL, timeout=10)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # 1) 「指数:」を含むimgを全部とる
    imgs = soup.find_all("img", alt=lambda x: x and "指数:" in x)
    entries = []
    for i, img in enumerate(imgs[:2]):  # 上から2つだけ
        index_val = img["alt"].split("指数:")[-1]
        # コメントは画像の親→親あたりを探す
        comment = ""
        parent = img.parent
        found = False
        # 親を少しずつ遡って<p>を探す
        for _ in range(4):
            if parent is None:
                break
            ps = parent.find_all("p")
            for p in ps:
                txt = p.get_text(strip=True)
                if txt:
                    comment = txt
                    found = True
                    break
            if found:
                break
            parent = parent.parent

        # ラベルを決める（1つ目を今日、2つ目を明日扱い）
        label = "今日" if i == 0 else "明日"

        today = datetime.now(JST).date()
        date_str = (
            today.strftime("%Y-%m-%d (%a)")
            if label == "今日"
            else (today + timedelta(days=1)).strftime("%Y-%m-%d (%a)")
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


def _extract_first_percent(block):
    """そのblock内から最初の「xx%」を探す"""
    # いろんなタグに分かれてることがあるので幅広く見る
    for tag in block.find_all(["td", "span", "p", "div", "li"]):
        txt = tag.get_text(strip=True)
        if txt.endswith("%") and txt[:-1].isdigit():
            return txt
    return "?"


def fetch_rain_today_tomorrow():
    """
    /forecast/.../ のページから
    見出しに「今日」「明日」と書いてあるブロックだけを見る。
    下にある「横浜市」などは無視する。
    """
    r = requests.get(FORECAST_URL, timeout=10)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    today_prob = "?"
    tomorrow_prob = "?"

    # section / article / div のどれかに「今日」「明日」が書いてある
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


def build_message():
    today = datetime.now(JST).date()
    moon_age = calc_moon_age(today)

    star_rows = fetch_starry_today_tomorrow()
    today_rain, tomorrow_rain = fetch_rain_today_tomorrow()

    lines = []
    lines.append("🌌 相模原の天体観測情報（自動）")
    lines.append(f"📅 {today.strftime('%Y-%m-%d (%a)')}")
    lines.append(f"🌙 月齢: {moon_age:.1f}日")
    lines.append("")

    # ここで「今日」「明日」だけ並べる（念のため）
    for r in star_rows:
        if r["label"] == "今日":
            lines.append(
                f"【今日】 指数: {r['index']} / 降水: {today_rain} / {r['comment']}"
            )
        elif r["label"] == "明日":
            lines.append(
                f"【明日】 指数: {r['index']} / 降水: {tomorrow_rain} / {r['comment']}"
            )

    lines.append("")
    lines.append(f"🔗 ソース: {STARRY_URL}")
    return "\n".join(lines)


def send_ntfy(text: str):
    url = f"https://ntfy.sh/{NTFY_TOPIC}"
    r = requests.post(url, data=text.encode("utf-8"), timeout=10)
    r.raise_for_status()


def main():
    msg = build_message()
    send_ntfy(msg)


if __name__ == "__main__":
    main()