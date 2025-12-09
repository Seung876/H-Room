# hufs_notice.py
import time
import re
import sqlite3
import urllib.parse
import requests
from bs4 import BeautifulSoup
import os
from datetime import datetime

# ---------------- 공통 설정 ---------------- #

BASE_URL = "https://search.hufs.ac.kr/RSA/front/Search.jsp"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "hufs_notices.db")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

KEYWORDS = [
    "강의실", "대관", "사용 불가", "사용불가", "사용 중지", "출입 제한",
    "출입제한", "점검", "폐쇄", "이용 제한",
    "공학관", "교양관", "백년관", "어문학관",
    "인문경상관", "자연과학관", "사회과학관", "학생회관",
]

PAGE_SIZE = 10


# ---------------- DB 유틸 ---------------- #

def ensure_db():
    """
    notices 테이블을 생성한다. (없으면 만들고, 있으면 그대로 둠)
    url 을 UNIQUE 로 해서 중복 공지를 막는다.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS notices (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword             TEXT NOT NULL,
            title               TEXT NOT NULL,
            url                 TEXT NOT NULL UNIQUE,
            source              TEXT,
            date                TEXT,
            detail_text         TEXT,
            place_text          TEXT,
            time_text           TEXT,
            unavailable_flag    INTEGER DEFAULT 0,
            unavailable_reason  TEXT,
            created_at          TEXT DEFAULT (datetime('now','localtime'))
        )
        """
    )
    conn.commit()
    conn.close()


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def save_notices(conn, rows):
    """
    rows: (keyword, title, url, source, date)
    이미 있는 url 은 INSERT OR IGNORE 로 무시.
    """
    cur = conn.cursor()
    cur.executemany(
        """
        INSERT OR IGNORE INTO notices (keyword, title, url, source, date)
        VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


# ---------------- 검색 폼 / 페이지 파싱 ---------------- #

def get_form_base():
    res = requests.get(BASE_URL, headers=HEADERS, timeout=10)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    form = soup.find("form", attrs={"name": "RsaSearchForm1"})
    if not form:
        raise RuntimeError("RsaSearchForm1 폼을 찾지 못했습니다.")

    base = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        base[name] = inp.get("value", "")

    if "menu" in base and not base["menu"]:
        base["menu"] = "통합검색"

    return base


def fetch_page(form_base, keyword: str, page: int) -> str:
    start = (page - 1) * PAGE_SIZE

    data = form_base.copy()
    data["qt"] = keyword
    if "q" in data:
        data["q"] = keyword
    if "realQuery" in data:
        data["realQuery"] = keyword
    if "startCount" in data:
        data["startCount"] = str(start)

    print(f"  - {page}페이지 요청 (keyword={keyword}, startCount={start})")

    res = requests.post(BASE_URL, headers=HEADERS, data=data, timeout=10)
    res.raise_for_status()
    return res.text


def parse_page(html: str, keyword: str):
    soup = BeautifulSoup(html, "html.parser")
    rows = []

    notice_header = soup.find("ul", id="Result_공지사항")
    if not notice_header:
        return rows

    container = notice_header.find_parent("div", id="meu_sc")
    if not container:
        container = notice_header.parent

    for dl in container.select("dl.C_Cts"):
        dt = dl.find("dt", class_="txt")
        if not dt:
            continue
        a = dt.find("a")
        if not a:
            continue

        title = a.get_text(strip=True)
        href = a.get("href", "").strip()
        url = href if href.startswith("http") else urllib.parse.urljoin(BASE_URL, href)

        date_el = a.find("span", class_="wGun")
        date = date_el.get_text(strip=True) if date_el else ""

        rows.append((keyword, title, url, "공지사항", date))

    return rows


def crawl_keyword(conn, form_base, keyword: str):
    print(f"\n[크롤링 시작] {keyword}")

    all_rows = []
    seen_urls = set()

    for page in range(1, 30):
        html = fetch_page(form_base, keyword, page)
        rows = parse_page(html, keyword)

        if not rows:
            print("  → 더 이상 결과 없음, 중단")
            break

        # 이 페이지에서 '처음 보는 URL'만 모으기
        new_rows = []
        for r in rows:
            url = r[2]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            new_rows.append(r)

        if not new_rows:
            print("  → 새로 나온 공지가 없어 중단 (페이징이 안 먹는 듯)")
            break

        print(f"  → 파싱 {len(rows)}건, 신규 {len(new_rows)}건 저장 예정")
        all_rows.extend(new_rows)
        time.sleep(0.3)

    if all_rows:
        save_notices(conn, all_rows)
        print(f"  ===>> 총 {len(all_rows)}건 DB 저장 완료\n")
    else:
        print("  → 저장할 공지 없음\n")


# ---------------- 상세 페이지 파싱 ---------------- #

def fetch_notice_rows(limit=50):
    """
    detail_text가 아직 없는 공지들 일부만 가져오기
    """
    # 혹시라도 테이블이 아직 없다면 한 번 더 보장
    ensure_db()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, keyword, title, url
        FROM notices
        WHERE detail_text IS NULL OR detail_text = ''
        ORDER BY id
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def fetch_detail_html(url: str) -> str:
    res = requests.get(url, headers=HEADERS, timeout=10)
    res.raise_for_status()
    return res.text


def extract_main_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    candidates = []

    for sel in [
        "div.bbs_view",
        "div.bbs_contents",
        "div.board_view",
        "div#content",
        "body",
    ]:
        el = soup.select_one(sel)
        if not el:
            continue
        text = el.get_text("\n", strip=True)
        if len(text) >= 50:
            candidates.append(text)

    if not candidates:
        return soup.get_text("\n", strip=True)

    candidates.sort(key=len, reverse=True)
    return candidates[0]


# ---- 사용불가 판단 규칙 ---- #

BUILDING_KEYWORDS = [
    "공학관",
    "교양관",
    "백년관",
    "어문학관",
    "인문경상관",
    "자연과학관",
    "사회과학관",
    "학생회관",
]

EVENT_KEYWORDS = [
    "시험",
    "고사",
    "교육",
    "특강",
    "설명회",
    "세미나",
    "워크숍",
    "워크샵",
    "면접",
    "행사",
    "대회",
]

ROOM_PATTERN = r"\d{3}\s*호"
DATE_PATTERN = r"20\d{2}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2}"
TIME_PATTERN = r"\d{1,2}\s*시"


def infer_unavailable(title: str, text: str):
    """
    제목 + 본문을 보고 '사용 불가/점유' 여부 판단.
    (현재 달인지 여부는 별도 함수에서 체크)
    """
    full = f"{title}\n{text}"

    hard_patterns = [
        r"사용\s*불가",
        r"사용\s*중지",
        r"출입\s*제한",
        r"이용\s*제한",
        r"폐쇄",
        r"폐관",
        r"대관\s*불가",
        r"대관\s*중지",
        r"공사.*사용\s*불가",
        r"점검.*사용\s*불가",
    ]

    for pat in hard_patterns:
        if re.search(pat, full):
            return 1, f"패턴매칭:{pat}"

    soft_patterns = [
        r"사용 안내",
        r"이용 안내",
        r"사용 신청",
        r"대관 신청",
    ]
    for pat in soft_patterns:
        if re.search(pat, full):
            return 0, ""

    has_building = any(b in full for b in BUILDING_KEYWORDS)
    has_room = re.search(ROOM_PATTERN, full)
    has_event = any(e in full for e in EVENT_KEYWORDS)
    has_date = re.search(DATE_PATTERN, full)
    has_time = re.search(TIME_PATTERN, full)

    if has_building and has_room and has_event and has_date and has_time:
        return 1, "ROOM_EVENT 매칭"

    return 0, ""


def is_current_month_event(text: str) -> bool:
    """
    텍스트 안에 '현재 연도 + 현재 월' (예: 2025.12, 2025-12) 패턴이 있는지 확인.
    """
    now = datetime.now()
    year = now.year
    month = now.month

    pattern = rf"{year}\s*[.\-/]\s*0?{month}\b"
    return re.search(pattern, text) is not None


def parse_place_and_time(text: str):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    place = ""
    time_str = ""

    for line in lines:
        if any(k in line for k in ["장소", "강의실", "시험장", "고사장"]):
            parts = re.split(r"[:：\-]", line, maxsplit=1)
            place = parts[1].strip() if len(parts) == 2 else line
            break

    for line in lines:
        if any(k in line for k in ["일시", "시험일", "고사일", "기간", "시행일"]):
            parts = re.split(r"[:：\-]", line, maxsplit=1)
            time_str = parts[1].strip() if len(parts) == 2 else line
            break

    return place, time_str


def update_notice_detail(
    rid: int,
    detail_text: str,
    place: str,
    time_str: str,
    flag: int,
    reason: str,
):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE notices
        SET detail_text        = ?,
            place_text         = ?,
            time_text          = ?,
            unavailable_flag   = ?,
            unavailable_reason = ?
        WHERE id = ?
        """,
        (detail_text, place, time_str, flag, reason, rid),
    )
    conn.commit()
    conn.close()


def run_detail_crawler(batch_size=20, delay_sec=0.3):
    while True:
        rows = fetch_notice_rows(limit=batch_size)
        if not rows:
            print("모든 공지 상세 파싱 완료")
            break

        print(f"{len(rows)}건 상세 파싱 중...")

        for r in rows:
            rid = r["id"]
            title = r["title"]
            url = r["url"]

            print(f"\n[{rid}] {title[:60]}")
            print("URL:", url)

            try:
                html = fetch_detail_html(url)
                detail_text = extract_main_text(html)
                place, time_str = parse_place_and_time(detail_text)

                combined = f"{title}\n{time_str}\n{detail_text}"
                if is_current_month_event(combined):
                    flag, reason = infer_unavailable(title, detail_text)
                else:
                    flag, reason = 0, "이번 달 일정 아님"

                update_notice_detail(rid, detail_text, place, time_str, flag, reason)

                print(f" → 장소: {place or '-'}")
                print(f" → 일시: {time_str or '-'}")
                print(f" → 사용불가: {flag} ({reason})")
            except Exception as e:
                print("  [ERROR]", e)

            time.sleep(delay_sec)


# ---------------- main ---------------- #

if __name__ == "__main__":
    # 0) 테이블 보장
    ensure_db()

    # 1) 검색 크롤링 (키워드별 목록)
    conn = get_conn()
    try:
        form_base = get_form_base()
        for kw in KEYWORDS:
            crawl_keyword(conn, form_base, kw)
    finally:
        conn.close()

    # 2) 상세 페이지 크롤링 + 사용불가 판정
    run_detail_crawler()
