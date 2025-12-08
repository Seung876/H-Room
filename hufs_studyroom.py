# hufs_studyroom.py
"""
HUFS 스터디룸 크롤링 + sqlite 저장 모듈

기능 요약:
1) 캠퍼스(H1/H2) 기준으로 스터디룸 목록 가져오기
2) sNum + 날짜(rDate) 기준으로 예약 정보 가져오기
3) sqlite(hufs_lectures.db)에
   - studyroom_master
   - studyroom_reservation
   - studyroom_period_status
   테이블로 저장
4) update_studyroom_all() 한 번 호출하면
   오늘 날짜 기준으로 H1/H2 전체 스터디룸 예약 상태가 DB에 반영됨
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Dict, List, Any

import requests


# ==============================
# 0. 공통 설정 / 유틸
# ==============================

DB_PATH = "hufs_lectures.db"

SPACE_LIST_URL = (
    "https://rs.hufs.ac.kr/client/studyroom/ajax/get_space_list_by_campus_category_notonoff_range.jsp"
)
RESERVATION_URL = (
    "https://rs.hufs.ac.kr/client/studyroom/ajax/get_reservations_by_sNum_rDate.jsp"
)

# 크롬 Network 탭에서 복사한 내용을 기반으로 설정
COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 11.0; Surface Duo) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/142.0.0.0 Mobile Safari/537.36"
    ),
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://rs.hufs.ac.kr",
    "Referer": "https://rs.hufs.ac.kr/client/main.jsp",
    # 여기 Cookie 값은 브라우저에서 Network → Request Headers → Cookie 내용을 그대로 복사한 것
    "Cookie": (
        "_ga_RTQGD6SR18=GS2.1.s1757432002$o10$g0$t1757432002$j60$l0$h0; "
        "LIB3PRX_SESSID=88301828111121973214905632; "
        "LP1121SID=88301828111121973214905632; "
        "_ga_0Z0FZV5ZF4=GS2.1.s1757575752$o7$g1$t1757575871$j15$l0$h0; "
        "perf_dv6Tr4n=1; "
        "JSESSIONID=FDAE97993E80C4F8DB13586A601321C4; "
        "_fbp=fb.2.1763702885688.629660238344326313; "
        "_ga_GP60419TCP=GS2.1.s1763731452$o2$g0$t1763731452$j60$l0$h0; "
        "_ga=GA1.1.1855293459.1711212555; "
        "_ga_FSPRHP5QMZ=GS2.1.s1764645517$o265$g1$t1764645531$j46$l0$h0"
    ),
}

# 교시 → 시간대 매핑 (H:Room 기준)
PERIOD_RANGE: Dict[str, tuple[str, str]] = {
    "1": ("09:00", "09:50"),
    "2": ("10:00", "10:50"),
    "3": ("11:00", "11:50"),
    "4": ("12:00", "12:50"),
    "5": ("13:00", "13:50"),
    "6": ("14:00", "14:50"),
    "7": ("15:00", "15:50"),
    "8": ("16:00", "16:50"),
    "9": ("17:00", "17:50"),
}


def safe_json(text: str) -> Any:
    """
    response.text를 json으로 파싱하되,
    앞뒤에 HTML 개행 같은 잡다한 게 붙어 있어도 처리해보는 함수
    """
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        # 혹시 앞뒤에 이상한 태그가 있을 경우를 대비해서
        # 첫 '{' 부터 마지막 '}' 까지만 잘라서 다시 시도
        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            return json.loads(text[start:end])
        except Exception as e2:
            print("[safe_json] JSON 파싱 실패:", e2)
            print("[safe_json] 원본 일부:", text[:200])
            return None


def get_db() -> sqlite3.Connection:
    print(f"[DB] connecting to {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_studyroom_tables() -> None:
    """
    스터디룸 관련 테이블 3개를 생성합니다.
    (이미 존재하면 그대로 둡니다.)
    """
    print("[DB] init_studyroom_tables() 호출됨")
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS studyroom_master (
            sNum          INTEGER PRIMARY KEY,
            campus_code   TEXT,
            building_name TEXT,
            room_name     TEXT,
            capacity      INTEGER
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS studyroom_reservation (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            sNum       INTEGER,
            date       TEXT,
            start_time TEXT,
            end_time   TEXT,
            UNIQUE(sNum, date, start_time, end_time)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS studyroom_period_status (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            sNum     INTEGER,
            date     TEXT,
            period   TEXT,
            is_free  INTEGER,   -- 1=공실, 0=예약
            UNIQUE(sNum, date, period)
        )
        """
    )

    conn.commit()
    conn.close()
    print("[DB] studyroom_* 테이블 생성/확인 완료")


def campus_code_to_name(campus_code: str) -> str:
    if campus_code == "H1":
        return "서울캠퍼스"
    if campus_code == "H2":
        return "글로벌캠퍼스"
    return ""


def to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def is_time_overlap(start1: str, end1: str, start2: str, end2: str) -> bool:
    """
    [start1, end1), [start2, end2) 구간이 겹치는지 여부
    """
    s1 = to_minutes(start1)
    e1 = to_minutes(end1)
    s2 = to_minutes(start2)
    e2 = to_minutes(end2)
    return (s1 < e2) and (s2 < e1)


# ==============================
# 1. 마스터 데이터 저장 함수
# ==============================

def save_studyroom_master(rows: List[dict], campus_code: str) -> List[int]:
    """
    SPACE_LIST_URL에서 받은 rows를 studyroom_master에 저장하고,
    sNum 리스트를 리턴.
    """
    conn = get_db()
    cur = conn.cursor()

    s_nums: List[int] = []

    for item in rows:
        if not isinstance(item, dict):
            continue

        try:
            s_num = int(item.get("sNum"))
        except Exception:
            continue

        building_name = item.get("bName", "")
        room_name = item.get("sRNum", "")
        capacity = item.get("sCapa")

        cur.execute(
            """
            INSERT OR REPLACE INTO studyroom_master
                (sNum, campus_code, building_name, room_name, capacity)
            VALUES (?, ?, ?, ?, ?)
            """,
            (s_num, campus_code, building_name, room_name, capacity),
        )
        s_nums.append(s_num)

    conn.commit()
    conn.close()

    print(f"[MASTER] {campus_code} → studyroom_master 저장 완료 (sNum {len(s_nums)}개)")
    return s_nums


# ==============================
# 2. 스터디룸 마스터 가져오기
# ==============================

def fetch_studyroom_master(campus_code: str) -> List[int]:
    """
    주어진 캠퍼스(H1/H2)의 스터디룸 목록을 HUFS API에서 가져와
    studyroom_master 테이블에 저장하고, 해당 sNum 리스트를 반환합니다.
    """
    campus_name = campus_code_to_name(campus_code)
    if not campus_name:
        print(f"[MASTER] campus_code {campus_code} → 캠퍼스 이름 없음")
        return []


    if campus_code == "H1":
        payload = {
            "pNum": "202102104",
            "campus": campus_name,     
            "sCate": "스터디룸",
            "onOff": "CLOSE",
            "range": "글로벌학부생",   
        }
    else:
        payload = {
            "pNum": "202102104",
            "campus": campus_name,      
            "sCate": "스터디룸",
            "onOff": "CLOSE",
            "range": "글로벌학부생",
        }

    print(f"[MASTER] {campus_code} Payload = {payload}")

    res = requests.post(
        SPACE_LIST_URL,
        data=payload,
        headers=COMMON_HEADERS,
        timeout=10,
    )

    print(f"[MASTER] HTTP {res.status_code} from SPACE_LIST_URL ({campus_code})")
    print("[MASTER] raw text:", res.text[:300])

    data = safe_json(res.text)
    if not data:
        print(f"[MASTER] {campus_code} JSON 파싱 실패")
        return []

    if data.get("status") == "FAIL":
        print(f"[MASTER] {campus_code} 서버 응답 FAIL → 이 캠퍼스는 스킵합니다.")
        print(f"[MASTER] FAIL 응답 전체: {data}")
        return []

    rows = data.get("data", [])
    print(f"[MASTER] {campus_code} 스터디룸 개수: {len(rows)}")

    if rows:
        print(f"[MASTER] {campus_code} 예시 row:", rows[0])

    return save_studyroom_master(rows, campus_code)


# ==============================
# 3. 예약 정보 가져오기 + 저장
# ==============================

def fetch_studyroom_reservations(date_str: str, s_num_list: List[int]) -> List[dict]:
    """
    주어진 날짜(date_str)에 대해, sNum 리스트 전체의 예약 정보를
    get_reservations_by_sNum_rDate.jsp 에서 가져와
    studyroom_reservation 테이블에 저장하고, 전체 예약 리스트를 반환합니다.
    """
    if not s_num_list:
        print("[RESV] sNum_list 가 비어 있어서 예약 조회 안 함")
        return []

    payload = {
        "pNum": "202102104",                         
        "sNumArray": ",".join(map(str, s_num_list)),  
        "rDate": date_str,
    }

    print(f"[RESV] 날짜={date_str}, sNum 개수={len(s_num_list)} 요청 payload = {payload}")

    res = requests.post(
        RESERVATION_URL,
        data=payload,
        headers=COMMON_HEADERS,
        timeout=10,
    )
    print(f"[RESV] HTTP {res.status_code} from RESERVATION_URL")
    print("[RESV] raw response text:", res.text[:300])

    data = safe_json(res.text)
    if not data:
        print("[RESV] JSON 파싱 실패")
        return []

    if data.get("status") == "FAIL":
        print("[RESV] 서버에서 FAIL 응답을 반환했습니다.")
        print("[RESV] 전체 응답:", data)
        return []

    raw = data.get("data", data)
    if isinstance(raw, dict):
        reservations = list(raw.values())
    else:
        reservations = raw

    print(f"[RESV] 예약 레코드 개수:", len(reservations))

    if reservations:
        print("[RESV] 예시 예약 row:", reservations[0])

    conn = get_db()
    cur = conn.cursor()

    for r in reservations:
        if not isinstance(r, dict):
            continue

        try:
            s_num = int(r.get("sNum"))
        except Exception:
            continue

        start_time = r.get("fHour")
        end_time = r.get("tHour")
        if not start_time or not end_time:
            continue

        cur.execute(
            """
            INSERT OR REPLACE INTO studyroom_reservation
                (sNum, date, start_time, end_time)
            VALUES (?, ?, ?, ?)
            """,
            (s_num, date_str, start_time, end_time),
        )

    conn.commit()
    conn.close()

    print("[RESV] studyroom_reservation 저장 완료")
    return reservations


# ==============================
# 4. 교시 단위 상태 계산 + 저장
# ==============================

def build_period_status(date_str: str, reservations: List[dict]) -> None:
    """
    예약 리스트를 바탕으로,
    각 sNum / 교시(period)별 공실 여부를 계산해
    studyroom_period_status 테이블에 저장합니다.
    """
    print(f"[PERIOD] {date_str} 기준 교시별 상태 계산 시작 (예약 {len(reservations)}건)")

    grouped: Dict[int, List[dict]] = {}
    for r in reservations:
        if not isinstance(r, dict):
            continue
        try:
            s_num = int(r.get("sNum"))
        except Exception:
            continue
        grouped.setdefault(s_num, []).append(r)

    conn = get_db()
    cur = conn.cursor()

    for s_num, res_list in grouped.items():
        for period, (p_start, p_end) in PERIOD_RANGE.items():
            is_reserved = False
            for r in res_list:
                f_hour = r.get("fHour")
                t_hour = r.get("tHour")
                if not f_hour or not t_hour:
                    continue
                if is_time_overlap(f_hour, t_hour, p_start, p_end):
                    is_reserved = True
                    break

            is_free = 0 if is_reserved else 1

            cur.execute(
                """
                INSERT OR REPLACE INTO studyroom_period_status
                    (sNum, date, period, is_free)
                VALUES (?, ?, ?, ?)
                """,
                (s_num, date_str, period, is_free),
            )

    conn.commit()
    conn.close()
    print("[PERIOD] studyroom_period_status 저장 완료")


# ==============================
# 5. 오늘 기준 전체 동기화
# ==============================

def update_studyroom_all() -> None:
    """
    오늘 날짜 기준으로
    - H1 / H2 스터디룸 마스터
    - 예약 원본
    - 교시 단위 공실 여부
    를 모두 sqlite에 반영합니다.
    """
    print("===== update_studyroom_all() 시작 =====")
    init_studyroom_tables()

    today = datetime.now().strftime("%Y-%m-%d")
    print(f"[MAIN] 오늘 날짜: {today}")

    all_s_nums: List[int] = []
    all_s_nums += fetch_studyroom_master("H1")
    all_s_nums += fetch_studyroom_master("H2")

    print(f"[MAIN] 전체 sNum 개수: {len(all_s_nums)}")

    reservations = fetch_studyroom_reservations(today, all_s_nums)

    if reservations:
        build_period_status(today, reservations)
    else:
        print("[MAIN] 예약이 없어 교시별 상태 계산은 생략")

    print(f"[MAIN] [{today}] 스터디룸 DB 업데이트 완료")
    print("===== update_studyroom_all() 끝 =====")


if __name__ == "__main__":
    update_studyroom_all()
