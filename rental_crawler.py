# rental_crawler.py
# - rs_cookies.json 에서 쿠키를 읽어와 requests.Session 에 주입
# - 글로벌캠퍼스 건물 리스트 + 오늘 날짜 기준 대관 슬롯/예약 현황 크롤링
# - get_step3_init_data.jsp 를 사용해서 sData(oData, rData) 파싱
# - sqlite 에 rental_slots, rental_rooms 테이블로 저장
# - 30초마다 전체 최신화

import json
import time
import datetime
import re
from pathlib import Path

import requests
import sqlite_utils


# ----------------- 기본 설정 ----------------- #

COOKIE_FILE = Path("rs_cookies.json")
DB_FILE = "hufs_rental.db"

BASE_URL = "https://rs.hufs.ac.kr"

# 건물 리스트
URL_BUILDINGS = (
    f"{BASE_URL}/client/classroom/ajax/get_building_list_by_campus_sCate.jsp"
)

# 3단계 강의실 선택 화면 초기화 (슬롯 + 예약)
# 실제로 sData 가 채워져 있는 엔드포인트
URL_STEP3_INIT = (
    f"{BASE_URL}/client/classroom/ajax/get_step3_init_data.jsp"
)

# 포털에서 보이는 캠퍼스 이름 그대로 사용
CAMPUS_LIST = ["글로벌캠퍼스"]

# 수강생 구분 (status), 학번 pNum 은 본인 계정에 맞게 넣어야 함
PNUM = "202102104"        # ← 본인 학번으로 바꿔 쓰기
STATUS_GLOBAL = "글로벌학부생"


# ----------------- 공통 유틸 ----------------- #

def load_cookies_to_session(cookie_file: Path) -> requests.Session:
    """login_session.py 에서 저장한 쿠키를 불러와 Session 에 세팅"""
    if not cookie_file.exists():
        raise RuntimeError(
            f"{cookie_file} 가 없습니다. 먼저 login_session.py 를 실행해서 쿠키를 저장하세요."
        )

    with cookie_file.open("r", encoding="utf-8") as f:
        cookies = json.load(f)

    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    })

    for c in cookies:
        name = c.get("name")
        value = c.get("value")
        domain = c.get("domain") or ".hufs.ac.kr"
        path = c.get("path") or "/"
        if name and value:
            s.cookies.set(name, value, domain=domain, path=path)

    return s


def today_date() -> datetime.date:
    return datetime.date.today()


def today_str() -> str:
    return today_date().strftime("%Y-%m-%d")


def now_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def weekday_kor(d: datetime.date) -> str:
    """요일을 한국어 한 글자로 반환 (월, 화, 수, 목, 금, 토, 일)"""
    mapping = ["월", "화", "수", "목", "금", "토", "일"]  # Monday = 0
    return mapping[d.weekday()]


def parse_room_number(t_num: str) -> str:
    """
    tNum 이 "|4044|" 같이 들어오는 경우가 많아서
    그 안에서 3~4자리 숫자를 '강의실 번호'로 추출하는 유틸.
    """
    if not t_num:
        return ""
    m = re.search(r"(\d{3,4})", str(t_num))
    return m.group(1) if m else ""


# ----------------- DB 초기화 ----------------- #

def init_db(db_path: str) -> sqlite_utils.Database:
    db = sqlite_utils.Database(db_path)

    # 건물 테이블 (campus + building_id 기준)
    if "buildings" not in db.table_names():
        db["buildings"].create(
            {
                "campus": str,        # "글로벌캠퍼스"
                "building_id": str,   # bNum
                "building_name": str, # 어문학관, 자연과학관 ...
            },
            pk=("campus", "building_id"),
        )

    # 대관 슬롯/예약 현황 테이블
    if "rental_slots" not in db.table_names():
        db["rental_slots"].create(
            {
                "date": str,           # "2025-12-05"
                "campus": str,         # "글로벌캠퍼스"
                "building_id": str,    # bNum
                "building_name": str,  # 백년관, 자연과학관 ...
                "classroom_id": str,   # 강의실 번호 (예: "4024")
                "classroom_name": str, # 화면 표기용
                "capacity": int,       # 수용인원
                "rental_time": str,    # "17:00 ~ 21:00"
                "start_time": str,     # "17:00"
                "end_time": str,       # "21:00"
                "dept": str,           # 담당부서
                "tel": str,            # 연락처
                "is_reserved": int,    # 0/1
                "raw_sNum": str,       # 슬롯 ID
                "raw_rNum": str,       # 예약 ID (필요 시 확장)
                "raw_json": str,       # 원본 JSON
                "updated_at": str,
            },
            pk=("date", "campus", "building_id", "classroom_id", "rental_time"),
        )
    else:
        # 기존 테이블이 있을 경우 start_time, end_time 없으면 추가
        cols = db["rental_slots"].columns_dict
        if "start_time" not in cols:
            db["rental_slots"].add_column("start_time", str)
        if "end_time" not in cols:
            db["rental_slots"].add_column("end_time", str)

    # 대여 대상 강의실 마스터 테이블
    if "rental_rooms" not in db.table_names():
        db["rental_rooms"].create(
            {
                "campus": str,
                "building_id": str,
                "building_name": str,
                "classroom_id": str,
                "classroom_name": str,
                "capacity": int,
                "dept": str,
                "tel": str,
                "first_seen": str,   # 최초로 등장한 날짜
                "last_seen": str,    # 마지막으로 등장한 날짜
            },
            pk=("campus", "building_id", "classroom_id"),
        )

    return db


# ----------------- 1) 캠퍼스별 건물 리스트 ----------------- #

def fetch_buildings(session: requests.Session, campus: str):
    """
    캠퍼스별 건물 리스트 조회
    """
    payload = {
        "campus": campus,
        "sCate": "강의실",
    }

    resp = session.post(URL_BUILDINGS, data=payload)
    resp.raise_for_status()

    res_json = resp.json()  # {status: "...", data: [...]}

    if res_json.get("status") != "SUCCESS":
        print(f"[{campus}] 건물 리스트 응답 오류:", res_json)
        return []

    rows = res_json.get("data", [])
    buildings = []

    for item in rows:
        b_id = str(item.get("bNum"))
        b_name = item.get("bName")

        if not b_id or not b_name:
            continue

        buildings.append({
            "campus": campus,
            "building_id": b_id,
            "building_name": b_name,
        })

    return buildings


def sync_buildings(db: sqlite_utils.Database, session: requests.Session):
    """buildings 테이블 동기화"""
    for campus in CAMPUS_LIST:
        buildings = fetch_buildings(session, campus)
        if not buildings:
            continue

        print(f"[{campus}] 건물 {len(buildings)}개 동기화 중...")

        for b in buildings:
            db["buildings"].upsert(b, pk=("campus", "building_id"))

        print(f"[{campus}] 건물 리스트 동기화 완료.")


# ----------------- 2) get_step3_init_data 로 오늘 대관 슬롯/예약 가져오기 ----------------- #

def status_for_campus(campus_name: str) -> str:
    # 지금은 글로벌캠퍼스만 사용
    if campus_name == "글로벌캠퍼스":
        return STATUS_GLOBAL
    return ""


def fetch_building_rentals(
    session: requests.Session,
    campus_name: str,
    building_id: str,
    date_obj: datetime.date,
):
    """
    특정 캠퍼스 + 특정 건물(bNum)에 대해,
    특정 날짜(date_obj) 기준 슬롯(sData) + 예약(rData) 정보를 조회.
    get_step3_init_data.jsp 의 Form Data 구조를 그대로 사용.
    """

    r_date_str = date_obj.strftime("%Y-%m-%d")  # 예: 2025-12-12
    yoil = weekday_kor(date_obj)                # 예: 금, 토 등
    status = status_for_campus(campus_name)

    payload = {
        "pNum": PNUM,               # 본인 학번
        "sCate": "강의실",
        "campus": campus_name,
        "bNum": str(building_id),
        "rDate": r_date_str,
        "yoil": yoil,
        "status": status,
    }

    resp = session.post(URL_STEP3_INIT, data=payload)
    resp.raise_for_status()

    data = resp.json()  # { oData: [...], rData: [...], sData: [...] }

    o_data = data.get("oData") or []
    r_data = data.get("rData") or []
    s_data = data.get("sData") or []

    if not s_data:
        print(f"[{campus_name} bNum={building_id}] sData가 비어 있습니다.")
        return []

    # 실제로 예약이 걸려있는 슬롯 ID(sNum) 집합
    reserved_snums = {
        str(item.get("sNum"))
        for item in (o_data + r_data)
        if item.get("sNum") is not None
    }

    slots = []

    for item in s_data:
        s_num = str(item.get("sNum") or "")
        is_reserved = 1 if s_num in reserved_snums else 0

        b_num = str(item.get("bNum") or building_id)

        # 호실 번호: sRNum (예: "0214") 을 우선 사용
        room_no = (item.get("sRNum") or "").strip().strip('"').strip("'")
        if room_no:
            room_no = room_no.lstrip("0") or room_no
        else:
            room_no = parse_room_number(item.get("tNum") or "")

        # "17:00 ~ 21:00" 형식
        time_range = (
            item.get("description")
            or item.get("tRange")
            or ""
        )

        start_time = ""
        end_time = ""
        if " ~ " in time_range:
            start_str, end_str = time_range.split("~", 1)
            start_time = start_str.strip()
            end_time = end_str.strip()

        capacity = item.get("sCapa")
        dept = item.get("mDept") or ""
        tel = item.get("mTel") or ""

        slots.append(
            {
                "campus": campus_name,
                "building_id": b_num,
                "building_name": "",  # 나중에 sync_today_rentals 에서 채움
                "room_number": room_no,
                "capacity": capacity,
                "rental_time": time_range,
                "start_time": start_time,
                "end_time": end_time,
                "dept": dept,
                "tel": tel,
                "is_reserved": is_reserved,
                "raw_sNum": s_num,
                "raw_json": item,
            }
        )

    return slots


def sync_today_rentals(db: sqlite_utils.Database, session: requests.Session):
    """오늘(date 기준) 전체 대관 현황 동기화"""
    date_obj = today_date()
    date_str = date_obj.strftime("%Y-%m-%d")
    print(f"[{date_str}] 당일 대관 현황 동기화 시작...")

    # 오늘 데이터 전부 삭제 후 다시 채우기 (중복 방지)
    db["rental_slots"].delete_where("date = ?", [date_str])

    # 건물 목록이 비어 있으면 먼저 동기화
    if db["buildings"].count == 0:
        print("buildings 테이블이 비어 있습니다. 먼저 건물 리스트를 동기화합니다.")
        sync_buildings(db, session)

    for campus in CAMPUS_LIST:
        buildings = list(db["buildings"].rows_where("campus = ?", [campus]))
        if not buildings:
            print(f"[{campus}] buildings 테이블에 건물 정보가 없습니다.")
            continue

        for b in buildings:
            b_id = b["building_id"]
            b_name = b["building_name"]

            slots = fetch_building_rentals(
                session=session,
                campus_name=campus,
                building_id=b_id,
                date_obj=date_obj,
            )

            if not slots:
                continue

            for s in slots:
                # 1) 시간대별 대관 슬롯 저장
                db["rental_slots"].upsert(
                    {
                        "date": date_str,
                        "campus": s["campus"],
                        "building_id": s["building_id"],
                        "building_name": s["building_name"] or b_name,
                        "classroom_id": s["room_number"],
                        "classroom_name": s["room_number"],
                        "capacity": s["capacity"],
                        "rental_time": s["rental_time"],
                        "start_time": s["start_time"],
                        "end_time": s["end_time"],
                        "dept": s["dept"],
                        "tel": s["tel"],
                        "is_reserved": s["is_reserved"],
                        "raw_sNum": s["raw_sNum"],
                        "raw_rNum": "",
                        "raw_json": json.dumps(s["raw_json"], ensure_ascii=False),
                        "updated_at": now_str(),
                    },
                    pk=(
                        "date",
                        "campus",
                        "building_id",
                        "classroom_id",
                        "rental_time",
                    ),
                )

                # 2) 대여 대상 강의실 마스터 테이블 갱신
                if s["room_number"]:
                    db["rental_rooms"].upsert(
                        {
                            "campus": s["campus"],
                            "building_id": s["building_id"],
                            "building_name": s["building_name"] or b_name,
                            "classroom_id": s["room_number"],
                            "classroom_name": s["room_number"],
                            "capacity": s["capacity"],
                            "dept": s["dept"],
                            "tel": s["tel"],
                            "first_seen": date_str,
                            "last_seen": date_str,
                        },
                        pk=("campus", "building_id", "classroom_id"),
                    )

            print(f"[{campus} 건물 {b_id} {b_name}] 동기화 완료.")

    print(f"[{date_str}] 당일 대관 현황 동기화 완료.")


# ----------------- 메인 루프 ----------------- #

def main_loop():
    session = load_cookies_to_session(COOKIE_FILE)
    db = init_db(DB_FILE)

    while True:
        try:
            sync_buildings(db, session)
            sync_today_rentals(db, session)
        except Exception as e:
            print(f"[ERROR] 동기화 중 예외 발생: {e}")
            print("세션이 만료되었을 수 있습니다. login_session.py 를 다시 실행해서 쿠키를 갱신하세요.")

        print("[INFO] 30초 후 다시 동기화합니다...\n")
        time.sleep(30)


if __name__ == "__main__":
    # 테스트용으로 한 번만 실행하고 싶으면 아래처럼 바꿔도 됨
    # session = load_cookies_to_session(COOKIE_FILE)
    # db = init_db(DB_FILE)
    # sync_buildings(db, session)
    # sync_today_rentals(db, session)
    main_loop()
