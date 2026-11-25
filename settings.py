"""설정.txt 파일을 읽어서 프로그램이 쓰기 좋은 모양으로 바꿔 주는 부분."""

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETTINGS_FILE = HERE / "설정.txt"
MIN_INTERVAL_MINUTES = 15


def _read_text(path):
    # 윈도우 메모장은 저장 방식이 두 가지일 수 있어서 둘 다 시도한다.
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def parse_price(text):
    """'30만원', '300,000', '300000원', '1만 5천원' 같은 글을 숫자로 바꾼다."""
    s = text.replace(",", "").replace(" ", "").replace("원", "")
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total = 0
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)만)?(?:(\d+)천)?(\d+)?", s)
    if not m or not any(m.groups()):
        return None
    if m.group(1):
        total += int(float(m.group(1)) * 10000)
    if m.group(2):
        total += int(m.group(2)) * 1000
    if m.group(3):
        total += int(m.group(3))
    return total


def load_settings(path=SETTINGS_FILE):
    """설정을 읽어서 돌려준다. 문제가 있으면 쉬운 말로 된 오류를 낸다."""
    if not path.exists():
        raise SystemExit(f"설정 파일을 찾을 수 없어요: {path}")

    searches, exclude_words, interval = [], [], MIN_INTERVAL_MINUTES
    title_must_match, max_age_days = True, 0
    section = None
    for line_no, line in enumerate(_read_text(path).splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue

        if section == "찾을 물건":
            if "/" not in line:
                raise SystemExit(
                    f"설정.txt {line_no}번째 줄을 이해하지 못했어요: '{line}'\n"
                    "  '검색어 / 최대 가격' 모양으로 써 주세요. 예) 아이패드 에어5 / 30만원"
                )
            keyword, price_text = line.rsplit("/", 1)
            keyword = keyword.strip()
            # '30만원' 또는 '15만원 ~ 30만원' (최소 ~ 최대)
            if "~" in price_text:
                low_text, high_text = price_text.split("~", 1)
                min_price = parse_price(low_text.strip())
            else:
                high_text, min_price = price_text, 0
            price = parse_price(high_text.strip())
            if not keyword or price is None or min_price is None:
                raise SystemExit(
                    f"설정.txt {line_no}번째 줄의 검색어나 가격이 이상해요: '{line}'"
                )
            searches.append({"keyword": keyword, "max_price": price, "min_price": min_price})
        elif section == "제외 단어":
            exclude_words += [w.strip() for w in line.split(",") if w.strip()]
        elif section == "기타":
            if "=" in line:
                name, value = (p.strip() for p in line.split("=", 1))
                if name.startswith("확인 간격"):
                    digits = re.sub(r"\D", "", value)
                    if digits:
                        interval = max(MIN_INTERVAL_MINUTES, int(digits))
                elif name.startswith("제목에 검색어"):
                    title_must_match = not value.startswith(("아니", "no", "No", "끄"))
                elif name.startswith("최근"):
                    digits = re.sub(r"\D", "", value)
                    max_age_days = int(digits) if digits else 0

    if not searches:
        raise SystemExit("설정.txt의 [찾을 물건] 아래에 검색어가 하나도 없어요.")

    return {
        "searches": searches,
        "exclude_words": exclude_words,
        "interval_minutes": interval,
        "title_must_match": title_must_match,
        "max_age_days": max_age_days,
    }
