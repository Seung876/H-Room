# notice_detail.py
import time
import re
import sqlite3
import requests
from bs4 import BeautifulSoup
import os
from datetime import datetime

# ---------------- 기본 설정 ---------------- #

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "hufs_notices.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# detail_text 이 아직 없는 공지들만 가져오기
def fetch_notice_rows(limit=50):
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
    res = requests.get(url, headers=BASE_HEADERS, timeout=10)
    res.raise_for_status()
    return res.text


def extract_main_text(html: str) -> str:
    """
    공지 본문 영역에서 텍스트만 추출.
    후보 컨테이너들 중 가장 긴 텍스트를 본문으로 사용.
    """
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


# ---------------- 사용 불가 여부 판단 규칙 ---------------- #

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

    # 1) 직접적인 사용 불가 / 제한 표현
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

    # 2) 안내/신청 류는 제외
    soft_patterns = [
        r"사용 안내",
        r"이용 안내",
        r"사용 신청",
        r"대관 신청",
    ]
    for pat in soft_patterns:
        if re.search(pat, full):
            return 0, ""

    # 3) 건물 + 강의실 + 날짜 + 시간 + 행사 키워드 → 그 시간 점유
    has_building = any(b in full for b in BUILDING_KEYWORDS)
    has_room = re.search(ROOM_PATTERN, full)
    has_event = any(e in full for e in EVENT_KEYWORDS)
    has_date = re.search(DATE_PATTERN, full)
    has_time = re.search(TIME_PATTERN, full)

    if has_building and has_room and has_event and has_date and has_time:
        return 1, "ROOM_EVENT 매칭"

    return 0, ""


# ---------------- '이번 달 일정'인지 확인 ---------------- #

def is_current_month_event(text: str) -> bool:
    """
    텍스트 안에 '현재 연도 + 현재 월' (예: 2025.12, 2025-12) 패턴이 있는지 확인.
    """
    now = datetime.now()
    year = now.year
    month = now.month  # 1~12

    # 2025.12 / 2025. 12 / 2025-12 등 대충 다 잡기
    pattern = rf"{year}\s*[.\-/]\s*0?{month}\b"
    return re.search(pattern, text) is not None


# ---------------- 장소 / 일시 추출 ---------------- #

def parse_place_and_time(text: str):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    place = ""
    time_str = ""

    # 장소
    for line in lines:
        if any(k in line for k in ["장소", "강의실", "시험장", "고사장"]):
            parts = re.split(r"[:：\-]", line, maxsplit=1)
            place = parts[1].strip() if len(parts) == 2 else line
            break

    # 일시
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


# ---------------- 메인 크롤러 루프 ---------------- #

def run_detail_crawler(batch_size=20, delay_sec=1.0):
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

                # 제목 + 일시 + 본문을 합쳐서 '이번 달 일정'인지 먼저 체크
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


if __name__ == "__main__":
    run_detail_crawler()
