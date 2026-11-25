"""
당근 매물 모아보기 — 본 프로그램.

하는 일
 1. 설정.txt 의 검색어마다 당근 검색 결과를 '한 번씩만' 가져온다. (검색어 사이에 쉬는 시간)
 2. 가격·제외 단어·판매 상태로 걸러 낸다.
 3. web/index.html 결과 화면을 만든다. (가격 낮은 순, 새 글엔 NEW)
 4. 내 컴퓨터에 작은 웹서버를 켜서 컴퓨터/휴대폰에서 볼 수 있게 한다.
 5. 설정한 간격(최소 15분)마다 1~3을 반복한다.
"""

import html
import json
import math
import random
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from settings import HERE, load_settings

SEARCH_URL = "https://www.daangn.com/kr/search/buy-sell/?q={q}"
SITE = "https://www.daangn.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
PORT = 8000
WEB_DIR = HERE / "web"
PAGE_FILE = WEB_DIR / "index.html"
SEEN_FILE = HERE / "기록.json"
PAUSE_BETWEEN_SEARCHES = (10, 20)  # 검색어 사이 쉬는 시간(초)
KST = timezone(timedelta(hours=9))


class Blocked(Exception):
    """당근이 접속을 막았거나 로그인을 요구할 때."""


# ───────────────────────── 1. 가져오기 ─────────────────────────
def fetch_page(keyword):
    url = SEARCH_URL.format(q=urllib.parse.quote_plus(keyword))
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            final_url = resp.geturl()
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429):
            raise Blocked(f"당근이 접속을 거절했어요 (응답 번호 {e.code})")
        raise
    if "login" in final_url:
        raise Blocked("당근이 로그인을 요구해요")
    return body


def extract_articles(page):
    """페이지 속 데이터(__remixContext)에서 매물 목록을 꺼낸다."""
    marker = "window.__remixContext"
    pos = page.find(marker)
    if pos < 0:
        raise ValueError("페이지에서 매물 데이터를 찾지 못했어요 (당근 화면 구조가 바뀐 것 같아요)")
    start = page.index("{", pos)
    data, _ = json.JSONDecoder().raw_decode(page[start:])
    loader = data.get("state", {}).get("loaderData", {})

    # 확인 결과에서 본 위치를 먼저 보고, 없으면 비슷한 목록을 찾는다.
    for value in loader.values():
        if isinstance(value, dict) and isinstance(value.get("buySellArticles"), list):
            return value["buySellArticles"]
    found = []

    def walk(x):
        if isinstance(x, list) and x and isinstance(x[0], dict) and {"title", "price", "href"} <= x[0].keys():
            found.extend(x)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(loader)
    if not found:
        raise ValueError("매물 목록을 찾지 못했어요 (당근 화면 구조가 바뀐 것 같아요)")
    return found


# ───────────────────────── 2. 거르기 ─────────────────────────
def to_price(value):
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def squash(text):
    """띄어쓰기를 없애고 영어는 소문자로. ('에어 5' 와 '에어5' 를 같게 보려고)"""
    return "".join(text.split()).lower()


def parse_time(text):
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def distance_km(a, b):
    """두 위치(위도, 경도) 사이의 거리(km)."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(h))


def article_location(article):
    for c in article.get("contentCoordinates") or []:
        try:
            return float(c["latitude"]), float(c["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
    return None


def clean(article, search, settings):
    """조건에 맞으면 화면에 쓸 정보를 돌려주고, 아니면 None."""
    keyword = search["keyword"]
    title = (article.get("title") or "").strip()
    price = to_price(article.get("price"))
    status = article.get("status") or "Ongoing"
    if status != "Ongoing":  # 예약중·판매완료 등은 뺀다
        return None
    if price is None or not (search["min_price"] <= price <= search["max_price"]):
        return None
    if any(word in title for word in settings["exclude_words"] + search["exclude_words"]):
        return None
    if settings["title_must_match"]:
        # 검색어의 낱말이 제목에 모두 들어 있어야 함 (띄어쓰기는 무시)
        squashed = squash(title)
        if not all(squash(word) in squashed for word in keyword.split()):
            return None
    posted = article.get("boostedAt") or article.get("createdAt") or ""
    if settings["max_age_days"]:
        t = parse_time(posted)
        if t and datetime.now(timezone.utc) - t > timedelta(days=settings["max_age_days"]):
            return None
    if settings["radius_km"]:
        loc = article_location(article)
        if loc and distance_km(settings["center"], loc) > settings["radius_km"]:
            return None
    href = article.get("href") or article.get("id") or ""
    return {
        "keyword": keyword,
        "title": title,
        "price": price,
        "region": (article.get("region") or {}).get("name", ""),
        "time": posted,
        "image": article.get("thumbnail") or "",
        "url": urllib.parse.urljoin(SITE, href),
        "key": href,
    }


# ───────────────────────── 3. 화면 만들기 ─────────────────────────
def load_seen():
    try:
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_seen(seen):
    # 30일 넘게 안 보인 기록은 지운다.
    limit = (datetime.now(KST) - timedelta(days=30)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= limit}
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False), encoding="utf-8")


def won(n):
    return f"{n:,}원"


def build_page(items, keywords, errors, checked_at, next_at, interval):
    e = html.escape
    tabs = ['<button class="tab on" data-kw="">전체</button>'] + [
        f'<button class="tab" data-kw="{e(k)}">{e(k)}</button>' for k in keywords
    ]
    cards = []
    for it in items:
        new = '<span class="new">NEW</span>' if it["new"] else ""
        img = (
            f'<img src="{e(it["image"])}" alt="" loading="lazy" referrerpolicy="no-referrer">'
            if it["image"]
            else '<div class="noimg">사진 없음</div>'
        )
        cards.append(
            f'''<a class="card" href="{e(it["url"])}" target="_blank" rel="noopener" data-kw="{e(it["keyword"])}">
  <div class="thumb">{img}</div>
  <div class="info">
    <div class="title">{new}{e(it["title"])}</div>
    <div class="price">{won(it["price"])}</div>
    <div class="meta">{e(it["region"])} · <time datetime="{e(it["time"])}">{e(it["time"][:10])}</time></div>
    <div class="kw">{e(it["keyword"])}</div>
  </div>
</a>'''
        )
    if not cards:
        cards.append('<p class="empty">조건에 맞는 매물이 아직 없어요.</p>')
    error_html = "".join(f'<div class="error">⚠️ {e(msg)}</div>' for msg in errors)
    new_count = sum(1 for it in items if it["new"])

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>당근 매물 모아보기</title>
<style>
  :root {{ --bg:#f6f6f6; --card:#fff; --text:#212124; --sub:#868b94; --line:#e9ecef; --accent:#ff6f0f; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#121212; --card:#1e1e1e; --text:#eee; --sub:#9a9a9a; --line:#2c2c2c; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
         font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif; }}
  header {{ position:sticky; top:0; z-index:1; background:var(--card); border-bottom:1px solid var(--line);
           padding:12px 16px 8px; }}
  h1 {{ margin:0; font-size:18px; }}
  .status {{ color:var(--sub); font-size:13px; margin-top:4px; }}
  .tabs {{ display:flex; gap:6px; overflow-x:auto; padding:10px 0 4px; }}
  .tab {{ flex:none; border:1px solid var(--line); background:var(--card); color:var(--text);
         border-radius:999px; padding:6px 12px; font-size:14px; }}
  .tab.on {{ background:var(--accent); border-color:var(--accent); color:#fff; }}
  main {{ max-width:720px; margin:0 auto; padding:8px 16px 40px; }}
  .card {{ display:flex; gap:12px; padding:12px; margin:8px 0; background:var(--card);
          border-radius:12px; color:inherit; text-decoration:none; border:1px solid var(--line); }}
  .thumb {{ flex:none; width:96px; height:96px; border-radius:8px; overflow:hidden; background:var(--line); }}
  .thumb img {{ width:100%; height:100%; object-fit:cover; display:block; }}
  .noimg {{ display:flex; align-items:center; justify-content:center; height:100%; color:var(--sub); font-size:12px; }}
  .info {{ min-width:0; flex:1; }}
  .title {{ font-size:15px; line-height:1.35; overflow:hidden; display:-webkit-box;
           -webkit-line-clamp:2; -webkit-box-orient:vertical; }}
  .new {{ background:#e53935; color:#fff; font-size:11px; font-weight:700; border-radius:4px;
         padding:1px 5px; margin-right:5px; vertical-align:2px; }}
  .price {{ font-weight:700; font-size:16px; margin-top:4px; }}
  .meta {{ color:var(--sub); font-size:13px; margin-top:4px; }}
  .kw {{ color:var(--sub); font-size:12px; margin-top:2px; }}
  .error {{ background:#fdecea; color:#b3261e; border-radius:8px; padding:10px 12px; margin:8px 0; font-size:14px; }}
  .empty {{ color:var(--sub); text-align:center; padding:40px 0; }}
</style>
</head>
<body>
<header>
  <h1>당근 매물 모아보기</h1>
  <div class="status">마지막 확인 {checked_at:%m월 %d일 %H:%M} · 다음 확인 {next_at:%H:%M} ({interval}분마다)
    · 새 글 {new_count}개</div>
  <div class="tabs">{''.join(tabs)}</div>
</header>
<main>
{error_html}
{''.join(cards)}
</main>
<script>
  // 'n분 전' 처럼 보여 주기
  function ago(iso) {{
    const diff = (Date.now() - new Date(iso)) / 60000;
    if (isNaN(diff)) return '';
    if (diff < 1) return '방금 전';
    if (diff < 60) return Math.floor(diff) + '분 전';
    if (diff < 1440) return Math.floor(diff / 60) + '시간 전';
    return Math.floor(diff / 1440) + '일 전';
  }}
  function updateTimes() {{
    document.querySelectorAll('time').forEach(t => {{ t.textContent = ago(t.getAttribute('datetime')); }});
  }}
  // 검색어별로 골라 보기 (고른 것은 주소 뒤에 기억)
  function show(kw) {{
    document.querySelectorAll('.tab').forEach(b => b.classList.toggle('on', b.dataset.kw === kw));
    document.querySelectorAll('.card').forEach(c => {{ c.style.display = (!kw || c.dataset.kw === kw) ? '' : 'none'; }});
  }}
  document.querySelectorAll('.tab').forEach(b => b.addEventListener('click', () => {{
    show(b.dataset.kw); history.replaceState(null, '', '#' + encodeURIComponent(b.dataset.kw));
  }}));
  show(decodeURIComponent(location.hash.slice(1)));
  updateTimes();
  setInterval(updateTimes, 60000);
  // 2분마다 화면 새로 고침 (새 결과 반영)
  setTimeout(() => location.reload(), 120000);
</script>
</body>
</html>
"""


def write_page(text):
    WEB_DIR.mkdir(exist_ok=True)
    tmp = PAGE_FILE.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(PAGE_FILE)


# ───────────────────────── 한 번 확인하기 ─────────────────────────
def check_once(settings, seen):
    now = datetime.now(KST)
    first_run = not seen
    items, errors = {}, []

    for i, search in enumerate(settings["searches"]):
        if i > 0:
            time.sleep(random.uniform(*PAUSE_BETWEEN_SEARCHES))  # 당근에 부담 주지 않게 쉬기
        kw = search["keyword"]
        print(f"  - '{kw}' 확인 중...", end=" ", flush=True)
        try:
            articles = extract_articles(fetch_page(kw))
        except Blocked:
            print("막힘")
            raise
        except Exception as e:
            print("실패")
            errors.append(f"'{kw}' 확인 실패: {e}")
            continue
        kept = 0
        for a in articles:
            it = clean(a, search, settings)
            if it and it["key"] not in items:
                items[it["key"]] = it
                kept += 1
        print(f"{len(articles)}개 중 {kept}개 남김")

    result = []
    for key, it in items.items():
        it["new"] = (key not in seen) and not first_run
        seen[key] = now.isoformat()
        result.append(it)
    result.sort(key=lambda x: (x["price"], x["title"]))
    return result, errors


# ───────────────────────── 웹서버 ─────────────────────────
class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def start_server():
    WEB_DIR.mkdir(exist_ok=True)
    try:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), partial(QuietHandler, directory=str(WEB_DIR)))
    except OSError:
        print(f"\n[!] {PORT}번 문이 이미 쓰이고 있어요. 프로그램이 이미 켜져 있는지 확인해 주세요.")
        input("엔터를 누르면 창이 닫혀요.")
        sys.exit(1)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def main():
    print("=" * 50)
    print(" 당근 매물 모아보기를 시작해요")
    print(" 끄려면 이 검은 창을 닫으면 돼요.")
    print("=" * 50)
    start_server()
    print(f"\n결과 화면 주소 (이 컴퓨터):  http://localhost:{PORT}")
    ip = local_ip()
    if ip:
        print(f"같은 와이파이의 휴대폰에서:  http://{ip}:{PORT}")
    print()

    # 켤 때마다 예전 화면을 지우고 '확인 중' 화면으로 바꾼다
    write_page("<meta charset='utf-8'><meta http-equiv='refresh' content='30'>"
               "<p style='font-family:sans-serif;padding:20px'>"
               "확인 중이에요. 잠시 뒤 자동으로 새로 고침 돼요.</p>")
    webbrowser.open(f"http://localhost:{PORT}")

    seen = load_seen()
    while True:
        settings = load_settings()  # 매번 다시 읽어서, 설정을 고치면 다음 확인부터 반영
        interval = settings["interval_minutes"]
        now = datetime.now(KST)
        print(f"[{now:%H:%M}] 확인 시작")
        try:
            items, errors = check_once(settings, seen)
        except Blocked as b:
            msg = f"{b}. 억지로 다시 시도하지 않고 확인을 멈췄어요. Claude에게 알려 주세요."
            print("\n[!] " + msg)
            write_page(build_page([], [], [msg], now, now, interval))
            input("엔터를 누르면 창이 닫혀요.")
            return
        save_seen(seen)
        next_at = datetime.now(KST) + timedelta(minutes=interval)
        keywords = [s["keyword"] for s in settings["searches"]]
        write_page(build_page(items, keywords, errors, now, next_at, interval))
        new_count = sum(1 for it in items if it["new"])
        print(f"[{datetime.now(KST):%H:%M}] 완료: {len(items)}개 (새 글 {new_count}개). "
              f"다음 확인은 {next_at:%H:%M}\n")
        time.sleep(interval * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n프로그램을 껐어요.")
    except SystemExit as e:
        if e.code not in (None, 0, 1):
            print(e)
            input("엔터를 누르면 창이 닫혀요.")
        raise
