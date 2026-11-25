"""
1단계 확인용 프로그램.

당근 검색 결과 페이지를 '딱 한 번만' 열어 보고,
- 접속이 되는지 (막히거나 로그인이 필요한지)
- 매물 정보가 페이지 안에 어떤 모양으로 들어 있는지
를 '확인결과.txt' 파일에 적는다. 그 내용을 Claude에게 보내 주면 된다.
"""

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from settings import HERE, load_settings

SEARCH_URL = "https://www.daangn.com/kr/buy-sell/?search={q}"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
REPORT_FILE = HERE / "확인결과.txt"

TITLE_KEYS = {"title", "name", "subject"}
PRICE_KEYS = {"price", "priceText", "offers"}


def fetch(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.geturl(), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, url, e.read().decode("utf-8", "replace")


def json_blobs(html):
    """페이지 안에 숨어 있는 데이터(JSON) 덩어리들을 찾는다."""
    found = []
    decoder = json.JSONDecoder()
    for m in re.finditer(r"<script([^>]*)>(.*?)</script>", html, re.S):
        attrs, body = m.group(1), m.group(2).strip()
        if not body:
            continue
        if "application/ld+json" in attrs or "application/json" in attrs:
            start = 0
            label = "ld+json" if "ld+json" in attrs else "json-script"
        else:
            name = re.search(r"(window\.[\w$]+|[\w$]+)\s*=\s*[\{\[]", body)
            if not name:
                continue
            start = body.index(name.group(0)) + len(name.group(0)) - 1
            label = name.group(1)
        try:
            data, _ = decoder.raw_decode(body[start:])
            found.append((label, data))
        except ValueError:
            continue
    return found


def find_item_lists(data, path="", out=None):
    """제목과 가격이 함께 들어 있는 목록을 찾는다."""
    if out is None:
        out = []
    if isinstance(data, list):
        dicts = [x for x in data if isinstance(x, dict)]
        if dicts:
            keys = set(dicts[0].keys())
            if keys & TITLE_KEYS and keys & PRICE_KEYS:
                out.append((path, dicts))
        for i, x in enumerate(data[:50]):
            find_item_lists(x, f"{path}[{i}]", out)
    elif isinstance(data, dict):
        for k, v in data.items():
            find_item_lists(v, f"{path}.{k}" if path else k, out)
    return out


def short(value, limit=120):
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


def main():
    settings = load_settings()
    keyword = settings["searches"][0]["keyword"]
    url = SEARCH_URL.format(q=urllib.parse.quote(keyword))
    print("당근 주소를 붙여 넣고 엔터를 누르세요. (그냥 엔터만 누르면 기본 검색으로 확인해요)")
    pasted = input("주소: ").strip().strip('"')
    if pasted.startswith("https://www.daangn.com/"):
        url = pasted
        keyword = "(붙여 넣은 주소)"

    lines = [
        "=== 당근 확인 결과 ===",
        f"확인 시각: {datetime.now():%Y-%m-%d %H:%M}",
        f"설정에서 읽은 검색어: {[s['keyword'] for s in settings['searches']]}",
        f"설정에서 읽은 최대 가격: {[s['max_price'] for s in settings['searches']]}",
        f"설정에서 읽은 제외 단어 수: {len(settings['exclude_words'])}",
        f"확인한 검색어: {keyword}",
        f"요청 주소: {url}",
    ]

    print(f"'{keyword}' 검색 결과를 한 번만 열어 볼게요...")
    try:
        status, final_url, html = fetch(url)
    except Exception as e:  # 인터넷 문제 등
        lines.append(f"접속 실패: {type(e).__name__}: {e}")
        finish(lines)
        return

    lines += [
        f"응답 번호: {status}",
        f"최종 주소: {final_url}",
        f"페이지 크기: {len(html):,} 글자",
    ]
    title = re.search(r"<title>(.*?)</title>", html, re.S)
    lines.append(f"페이지 제목: {title.group(1).strip()[:100] if title else '(없음)'}")
    lines.append(f"'로그인' 글자 수: {html.count('로그인')}")
    lines.append(f"'/kr/buy-sell/' 링크 수: {html.count('/kr/buy-sell/')}")

    if status in (401, 403, 429) or "login" in final_url:
        lines.append("판단: 접속이 막혔거나 로그인이 필요해 보여요. (우회하지 않고 여기서 멈춰요)")
        finish(lines)
        return

    blobs = json_blobs(html)
    lines.append(f"찾은 데이터 덩어리: {[label for label, _ in blobs]}")
    total_lists = 0
    for label, data in blobs:
        for path, items in find_item_lists(data):
            total_lists += 1
            if total_lists > 4:
                break
            lines.append("")
            lines.append(f"[매물 목록 후보] {label} → {path[:200]}")
            lines.append(f"  개수: {len(items)}")
            instant = sum(1 for x in items if x.get("isInstantBuyAvailable") is True)
            lines.append(f"  바로구매 가능: {instant}개 / 아님: {len(items) - instant}개")
            regions = {}
            for x in items:
                name = (x.get("region") or {}).get("name", "?")
                regions[name] = regions.get(name, 0) + 1
            top = sorted(regions.items(), key=lambda kv: -kv[1])[:10]
            lines.append(f"  동네(많은 순 10개): {top}")
            lines.append(f"  항목 이름들: {sorted(items[0].keys())}")
            for item in items[:2]:
                lines.append("  예시:")
                for k, v in item.items():
                    lines.append(f"    {k}: {short(v)}")
    if total_lists == 0:
        lines.append("매물 목록 후보를 찾지 못했어요.")
        cards = re.findall(r'href="(/kr/buy-sell/[^"]+)"', html)
        lines.append(f"카드 링크 예시: {cards[:5]}")
    if keyword != "(붙여 넣은 주소)":
        region_probe(html, blobs, keyword, lines)
    finish(lines)


def summarize(items):
    instant = sum(1 for x in items if x.get("isInstantBuyAvailable") is True)
    regions = {}
    for x in items:
        name = (x.get("region") or {}).get("name", "?")
        regions[name] = regions.get(name, 0) + 1
    top = sorted(regions.items(), key=lambda kv: -kv[1])[:10]
    return [
        f"  개수: {len(items)}",
        f"  바로구매 가능: {instant}개 / 아님: {len(items) - instant}개",
        f"  동네(많은 순 10개): {top}",
    ]


def region_probe(html, blobs, keyword, lines):
    """동네를 정한 검색 주소 모양을 찾아보고, 한 번만 시험해 본다."""
    lines.append("")
    lines.append("=== 동네 검색 확인 ===")
    for label, data in blobs:
        if label != "window.__remixContext":
            continue
        loader = data.get("state", {}).get("loaderData", {})
        for route, value in loader.items():
            if isinstance(value, dict):
                lines.append(f"[{route[:60]}] 항목: {sorted(value.keys())[:30]}")
                for k, v in value.items():
                    if k in ("currentFilters", "listSearch", "searchQueryAdjustment", "searchRegion"):
                        lines.append(f"  {k}: {short(v, 600)}")
    found = []
    for raw in re.findall(r'[?&](?:amp;)?in=([^&"\'\\\s<>]+)', html):
        value = urllib.parse.unquote(raw)
        if value not in found:
            found.append(value)
    lines.append(f"주소 속 동네 값 ({len(found)}개): {found[:30]}")
    if not found:
        # 페이지 속 기본 동네(searchRegion)로 '이름-번호' 모양을 만들어 본다.
        for label, data in blobs:
            if label != "window.__remixContext":
                continue
            for value in data.get("state", {}).get("loaderData", {}).values():
                region = value.get("searchRegion") if isinstance(value, dict) else None
                if isinstance(region, dict) and region.get("id") and region.get("name"):
                    found.append(f"{region['name']}-{region['id']}")
        lines.append(f"기본 동네로 만든 값: {found}")
    if not found:
        lines.append("동네 값을 찾지 못했어요.")
        return
    pick = next((v for v in found if v.startswith("역삼")), None) or next(
        (v for v in found if any(s in v for s in ("서울", "강남", "서초", "동-"))), found[0]
    )
    url = "https://www.daangn.com/kr/buy-sell/?in={}&search={}".format(
        urllib.parse.quote(pick), urllib.parse.quote(keyword)
    )
    print("10초 쉬었다가 동네 검색을 한 번 더 확인할게요...")
    time.sleep(10)
    try:
        status, final_url, page = fetch(url)
    except Exception as e:
        lines.append(f"동네 검색 접속 실패: {e}")
        return
    lines.append(f"시험한 동네 값: {pick}")
    lines.append(f"요청 주소: {url}")
    lines.append(f"응답 번호: {status}")
    lines.append(f"최종 주소: {final_url}")
    if status != 200:
        return
    for label, data in json_blobs(page):
        for path, items in find_item_lists(data)[:2]:
            lines.append(f"[동네 검색 목록] {path[:150]}")
            lines += summarize(items)


def finish(lines):
    text = "\n".join(lines) + "\n"
    REPORT_FILE.write_text(text, encoding="utf-8")
    print()
    print(text)
    print(f"위 내용이 '{REPORT_FILE.name}' 파일에도 저장됐어요.")
    print("이 파일 내용을 전부 복사해서 Claude에게 보내 주세요.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        print(e)
        sys.exit(1)
