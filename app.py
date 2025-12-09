# app.py
from flask import Flask, render_template, request, jsonify, abort, session
import sqlite3
import threading
import time
import os
from datetime import datetime
from hufs_studyroom import update_studyroom_all 
import json

app = Flask(__name__)
app.secret_key = "hroom-dev-secret"  

BUILDING_ALIAS = {
    "어문관": "어문학관",
    "본관(백년관)": "백년관",
}

def norm_building(name: str) -> str:
    """건물 이름을 표준 이름으로 정규화"""
    if not name:
        return ""
    name = name.strip()
    return BUILDING_ALIAS.get(name, name)


# 요일 매핑
DAY_MAP = {
    "mon": "월",
    "tue": "화",
    "wed": "수",
    "thu": "목",
    "fri": "금",
}

# 교시 → 시간대
PERIOD_TIME = {
    1: ("09:00", "09:50"),
    2: ("10:00", "10:50"),
    3: ("11:00", "11:50"),
    4: ("12:00", "12:50"),
    5: ("13:00", "13:50"),
    6: ("14:00", "14:50"),
    7: ("15:00", "15:50"),
    8: ("16:00", "16:50"),
    9: ("17:00", "17:50"),
    10: ("18:00", "18:50"),
    11: ("19:00", "19:50"),
    12: ("20:00", "20:50"),
}

def build_free_time_ranges(free_periods):
    """
    free_periods: [1,2,7,8,9] 처럼 공실 교시 번호 리스트
    리턴: "09:00 ~ 10:50, 15:00 ~ 17:50" 이런 문자열
    """
    if not free_periods:
        return ""

    free_periods = sorted(set(int(p) for p in free_periods))

    # 연속 구간 묶기
    ranges = []
    start = free_periods[0]
    prev = start

    for p in free_periods[1:]:
        if p == prev + 1:
            prev = p
        else:
            ranges.append((start, prev))
            start = p
            prev = p
    ranges.append((start, prev))

    # PERIOD_TIME = {1: ("09:00","09:50"), ...} 를 사용해서 시간으로 변환
    parts = []
    for s, e in ranges:
        st = PERIOD_TIME.get(s, ("", ""))[0]
        ed = PERIOD_TIME.get(e, ("", ""))[1]
        if st and ed:
            parts.append(f"{st} ~ {ed}")

    return ", ".join(parts)


def get_room_schedule(cur, campus, building_std, room_number):
    """
    lectures.schedules 컬럼을 바탕으로
    해당 강의실의 요일별 period 리스트를 만드는 함수.
    building_std : 표준 건물명 (예: '백년관')
    return: dict[day_kr] = [period1, period2, ...]  예) {"월": [1,2,3], "수":[4,5]}
    """

    # 1) 이 표준 건물명에 매칭되는 실제 building_name 후보들
    raw_names = {building_std}
    for original, std in BUILDING_ALIAS.items():
        if std == building_std:
            raw_names.add(original)

    placeholders = ",".join("?" for _ in raw_names)

    # 2) 해당 건물(alias 포함) + 강의실 번호에 해당하는 모든 schedules 가져오기
    rows = cur.execute(
        f"""
        SELECT schedules
        FROM lectures
        WHERE campus = ?
          AND building_name IN ({placeholders})
          AND room_number   = ?
        """,
        [campus, *raw_names, room_number],
    ).fetchall()

    # 3) 요일별 period 집합
    schedule_by_day = {}

    for row in rows:
        s_json = row["schedules"]
        if not s_json:
            continue

        try:
            # 예: [{"day":"월","periods":[1,2,3]}, ...]
            blocks = json.loads(s_json)
        except json.JSONDecodeError:
            continue

        for block in blocks:
            day = block.get("day")       
            periods = block.get("periods", [])
            if not day:
                continue

            # periods 안을 전부 int 로 정규화
            norm_periods = []
            for p in periods:
                try:
                    norm_periods.append(int(p))
                except (TypeError, ValueError):
                    continue

            if day not in schedule_by_day:
                schedule_by_day[day] = set()
            schedule_by_day[day].update(norm_periods)

    # 4) set → 정렬된 list 로 변환
    for d in schedule_by_day:
        schedule_by_day[d] = sorted(schedule_by_day[d])

    return schedule_by_day


def compute_free_info(schedule_by_day, day_kr, period):
    """
    day_kr (예: '월'), period (예: 4) 기준으로
    - 지금 공실인지 여부
    - 비는 시간 텍스트 ("09:00 ~ 12:00")
    - 다음 수업 시간 텍스트 ("12:00", "오늘 이후 수업 없음" 등)
    를 계산한다.
    """
    # 필터가 안 들어온 경우: 그냥 공실 처리만 대략 해주고 끝
    if not day_kr or not period:
        return True, "", ""

    busy_periods = schedule_by_day.get(day_kr, [])
    is_free = period not in busy_periods

    # 다음 수업 찾기
    next_p = None    # next_period
    for p in busy_periods:
        if p > period:
            next_p = p
            break

    if next_p:
        next_start, _ = PERIOD_TIME.get(next_p, ("", ""))
        next_class_text = next_start
    elif busy_periods:
        next_class_text = "오늘 이후 수업 없음"
    else:
        next_class_text = "오늘 수업 없음"

    free_range_text = ""
    if is_free:
        start, _ = PERIOD_TIME.get(period, ("", ""))
        if next_p:
            end = PERIOD_TIME.get(next_p, ("", ""))[0]
        else:
            end = "18:00"  # 이후 수업 없으면 대략 18시까지 빈 것으로
        if start and end:
            free_range_text = f"{start} ~ {end}"

    return is_free, free_range_text, next_class_text


# ------------------------
#  DB 연결 유틸
# ------------------------
def get_db():
    conn = sqlite3.connect("hufs_lectures.db")
    conn.row_factory = sqlite3.Row
    return conn

def get_rental_db():
    conn = sqlite3.connect("hufs_rental.db")
    conn.row_factory = sqlite3.Row
    return conn


# 공지사항 DB
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def get_notice_db():
    db_path = os.path.join(BASE_DIR, "hufs_notices.db")
    print("[get_notice_db] 사용 중인 DB 파일:", db_path)  # ← 로그 찍기

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn



def fetch_notices(limit=5):
    conn = get_notice_db()
    cur = conn.cursor()

    # 실제 테이블/컬럼 이름에 맞게 수정해서 사용하세요
    cur.execute(
        """
        SELECT id, title, url, place_text, date
        FROM notices
        ORDER BY date DESC
        LIMIT ?
        """,
        (limit,)
    )
    rows = cur.fetchall()
    conn.close()

    # Jinja에서 쓰기 편하게 dict로 변환
    notices = []
    for r in rows:
        notices.append({
            "id":         r["id"],
            "title":      r["title"],
            "url":        r["url"],
            "place_text": r["place_text"],
            "date":       r["date"],
        })
    return notices


# ------------------------
#  스터디룸 자동 갱신 스레드 (30초 기준)
# ------------------------
def start_studyroom_auto_refresh(interval_sec: int = 30):
    """
    interval_sec(초)마다 update_studyroom_all()을 호출하는 백그라운드 스레드 시작.
    기본: 30초
    """

    def worker():
        while True:
            try:
                print("[AUTO-REFRESH] 스터디룸 최신화 시작")
                update_studyroom_all()
                print("[AUTO-REFRESH] 스터디룸 최신화 완료")
            except Exception as e:
                print("[AUTO-REFRESH] 에러:", e)
            time.sleep(interval_sec)

    t = threading.Thread(target=worker, daemon=True)
    t.start()


# ------------------------
#  홈(index)
# ------------------------
@app.route("/")
def index():
    return render_template("index.html")


# ------------------------
#  메인 화면
# ------------------------
@app.route("/main")
def main():
    conn = get_notice_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, title, url, date, place_text, keyword
            FROM notices
            WHERE id = 1
            LIMIT 1
            """
        )
        rows = cur.fetchall()
        print("[/main] notices 행 개수:", len(rows))
        notices = [
            {
                "id":         r["id"],
                "title":      r["title"],
                "url":        r["url"],
                "date":       r["date"],
                "place_text": r["place_text"],
            }
            for r in rows
        ]
        print("[/main] notices count =", len(notices))
    except sqlite3.Error as e:
        print("[/main] notice 조회 에러:", e)
        notices = []
    finally:
        conn.close()

    return render_template("main.html", notices=notices)


@app.route("/canvas")
def canvas_page():
    return render_template("canvas.html")


# ------------------------
#  API: 캠퍼스별 건물 목록 (강의실 + 스터디룸 통합)
# ------------------------
@app.route("/api/buildings")
def api_buildings():
    campus = request.args.get("campus", "").strip()  # H1 / H2

    conn = get_db()
    cur = conn.cursor()

    # lectures.building_name + studyroom_master.building_name 통합
    sql = """
        SELECT DISTINCT building_name
        FROM (
            -- 강의실 건물
            SELECT building_name, campus AS campus_code
            FROM lectures
            WHERE building_name IS NOT NULL
              AND building_name != ''

            UNION

            -- 스터디룸 건물 (백년관, 공학관 등 bName 저장해둔 것)
            SELECT building_name, campus_code AS campus_code
            FROM studyroom_master
            WHERE building_name IS NOT NULL
              AND building_name != ''
        ) AS all_buildings
        WHERE 1 = 1
    """

    params = []
    if campus:
        sql += " AND campus_code = ?"
        params.append(campus)

    sql += " ORDER BY building_name"

    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
        buildings = [r["building_name"] for r in rows]
    except Exception as e:
        # 혹시 studyroom_master가 아직 없거나 에러 나면 lectures만 사용
        print("[/api/buildings] 통합 조회 에러, lectures만 사용:", e)
        if campus:
            cur.execute(
                """
                SELECT DISTINCT building_name
                FROM lectures
                WHERE campus = ?
                  AND building_name IS NOT NULL
                  AND building_name != ''
                ORDER BY building_name
                """,
                (campus,),
            )
        else:
            cur.execute(
                """
                SELECT DISTINCT building_name
                FROM lectures
                WHERE building_name IS NOT NULL
                  AND building_name != ''
                ORDER BY building_name
                """
            )
        rows = cur.fetchall()
        buildings = [r["building_name"] for r in rows]

    conn.close()

    # alias + 학생회관 제거
    normalized = {}
    for name in buildings:
        name = (name or "").strip()
        if not name:
            continue
        if name == "학생회관":
            continue  # 학생회관은 리스트에서 제외
        std_name = norm_building(name)
        normalized[std_name] = True

    # key만 꺼내서 가나다순 정렬
    final_list = sorted(normalized.keys())

    return jsonify(final_list)


# ------------------------
#  API: 건물별 공실 현황 (강의실 + 스터디룸, 타입 필터 지원)
# ------------------------
@app.route("/api/vacancy-summary")
def api_vacancy_summary():
    campus = request.args.get("campus", "").strip() or "H2"
    day    = request.args.get("day", "").strip()      # mon, tue ...
    period = request.args.get("period", "").strip()   # "1" ~ "12"
    space_type = request.args.get("space_type", "").strip()  # "", "lecture", "studyroom"

    # 오늘 날짜 (스터디룸 예약은 당일만 의미 있음)
    today = datetime.now().strftime("%Y-%m-%d")

    # 요일 한글 (월,화,...) 변환
    day_kr = DAY_MAP.get(day)
    period_int = int(period) if period.isdigit() else None

    # period 가 없거나 day 가 없으면 → 교시 외 시간대라고 보고
    # "총 강의실/스터디룸 수"만 보여주고 공실은 0 으로 처리
    is_closed_slot = (not day_kr) or (period_int is None)

    conn = get_db()
    cur = conn.cursor()

    # ---------------------------
    # 1) 강의실 데이터 (실제 period 기준)
    # ---------------------------
    lecture_results = {}
    lecture_total_rooms = 0
    lecture_empty_rooms = 0

    if space_type in ("", "lecture"):
        # 우선 이 캠퍼스의 모든 강의실 목록을 가져온다
        cur.execute(
            """
            SELECT DISTINCT building_name, room_number
            FROM lectures
            WHERE campus = ?
              AND building_name IS NOT NULL
              AND building_name != ''
              AND room_number IS NOT NULL
            """,
            (campus,),
        )
        room_rows = cur.fetchall()

        # building_name / room_number 단위로 실제 공실 여부 계산
        building_map = {}  # { 표준건물명: {"total": n, "free": m} }

        for r in room_rows:
            raw_bname = r["building_name"]
            std_bname = norm_building(raw_bname)
            if not std_bname or std_bname == "학생회관":
                continue

            rnum = r["room_number"]

            # 총 강의실 수 카운트
            info = building_map.setdefault(std_bname, {"total": 0, "free": 0})
            info["total"] += 1

            # 교시 외 시간대이면 free 계산은 하지 않고 넘어감
            if is_closed_slot:
                continue

            # 이 강의실의 주간 시간표 가져오기
            schedule_by_day = get_room_schedule(cur, campus, raw_bname, rnum)
            busy_periods = schedule_by_day.get(day_kr, [])
            is_free_now = period_int not in busy_periods

            if is_free_now:
                info["free"] += 1

        # building_map → lecture_results 로 변환
        for bname, info in building_map.items():
            total = info["total"]
            free = info["free"] if not is_closed_slot else 0
            empty_rate = int(round(free / total * 100)) if total else 0

            lecture_results[bname] = {
                "building_name": bname,
                "total_rooms": total,
                "empty_rooms": free,
                "empty_rate": empty_rate,
            }

            lecture_total_rooms += total
            lecture_empty_rooms += free

    # ---------------------------
    # 2) 스터디룸 데이터 (기존처럼 period_status 기반)
    # ---------------------------
    study_results = {}
    study_total_rooms = 0
    study_empty_rooms = 0

    if space_type in ("", "studyroom"):
        if is_closed_slot:
            # 교시 외 시간대: 스터디룸 수만 세고 공실 0
            cur.execute(
                """
                SELECT
                    building_name,
                    COUNT(*) AS room_count
                FROM studyroom_master
                WHERE campus_code = ?
                GROUP BY building_name
                """,
                (campus,),
            )
            rows = cur.fetchall()
            for r in rows:
                raw_bname = r["building_name"]
                std_bname = norm_building(raw_bname)
                if not std_bname or std_bname == "학생회관":
                    continue

                room_count = r["room_count"]

                study_results[std_bname] = {
                    "building_name": std_bname,
                    "total_rooms": room_count,
                    "empty_rooms": 0,
                    "empty_rate": 0,
                }
                study_total_rooms += room_count
                # empty_rooms 는 그대로 0
        else:
            # 실제 period 기준 free 여부 계산
            params = [today]
            period_filter_sql = ""
            if period_int is not None:
                period_filter_sql = " AND sps.period = ?"
                params.append(period_int)
            params.append(campus)

            cur.execute(
                f"""
                SELECT
                    sm.building_name,
                    sm.sNum,
                    MAX(CASE WHEN sps.is_free = 1 THEN 1 ELSE 0 END) AS is_free_any
                FROM studyroom_master sm
                LEFT JOIN studyroom_period_status sps
                  ON sm.sNum = sps.sNum
                 AND sps.date = ?
                 {period_filter_sql}
                WHERE sm.campus_code = ?
                GROUP BY sm.building_name, sm.sNum
                """,
                params,
            )
            rows = cur.fetchall()

            building_map = {}
            for r in rows:
                raw_bname = r["building_name"]
                std_bname = norm_building(raw_bname)
                if not std_bname or std_bname == "학생회관":
                    continue

                is_free_any = r["is_free_any"] or 0
                info = building_map.setdefault(std_bname, {"total": 0, "free": 0})
                info["total"] += 1
                if is_free_any:
                    info["free"] += 1

            for bname, info in building_map.items():
                total_rooms = info["total"]
                free_rooms = info["free"]
                empty_rate = int(round(free_rooms / total_rooms * 100)) if total_rooms else 0

                study_results[bname] = {
                    "building_name": bname,
                    "total_rooms": total_rooms,
                    "empty_rooms": free_rooms,
                    "empty_rate": empty_rate,
                }

                study_total_rooms += total_rooms
                study_empty_rooms += free_rooms

    conn.close()

    # ---------------------------
    # 3) space_type 별 결과 조합
    # ---------------------------
    if space_type == "lecture":
        buildings_list = list(lecture_results.values())
        total_rooms = lecture_total_rooms
        empty_rooms = lecture_empty_rooms

    elif space_type == "studyroom":
        buildings_list = list(study_results.values())
        total_rooms = study_total_rooms
        empty_rooms = study_empty_rooms

    else:
        # 전체 (강의실 + 스터디룸)
        combined = {}
        for bname, info in lecture_results.items():
            combined[bname] = {
                "building_name": bname,
                "total_rooms": info["total_rooms"],
                "empty_rooms": info["empty_rooms"],
            }
        for bname, info in study_results.items():
            if bname in combined:
                combined[bname]["total_rooms"] += info["total_rooms"]
                combined[bname]["empty_rooms"] += info["empty_rooms"]
            else:
                combined[bname] = {
                    "building_name": bname,
                    "total_rooms": info["total_rooms"],
                    "empty_rooms": info["empty_rooms"],
                }

        buildings_list = []
        total_rooms = 0
        empty_rooms = 0
        for bname, info in combined.items():
            tr = info["total_rooms"]
            er = info["empty_rooms"]
            empty_rate = int(round(er / tr * 100)) if tr else 0
            buildings_list.append(
                {
                    "building_name": bname,
                    "total_rooms": tr,
                    "empty_rooms": er,
                    "empty_rate": empty_rate,
                }
            )
            total_rooms += tr
            empty_rooms += er

    # 건물명 정렬
    buildings_list.sort(key=lambda x: x["building_name"])

    overall_rate = int(round(empty_rooms / total_rooms * 100)) if total_rooms else 0

    return jsonify(
        {
            "campus": campus,
            "total_rooms": total_rooms,
            "empty_rooms": empty_rooms,
            "empty_rate": overall_rate,
            "buildings": buildings_list,
        }
    )


# ------------------------
#  검색 결과 페이지
# ------------------------
@app.route("/result")
def result():
    # ----------------------------------
    # 1) 쿼리스트링 파라미터
    # ----------------------------------
    day_raw      = (request.args.get("day", "") or "").strip()      # "mon" ~ "fri"
    time_        = (request.args.get("time", "") or "").strip()     # "1" ~ "12"
    campus       = (request.args.get("campus", "") or "").strip()   # "H1"/"H2"
    building_raw = (request.args.get("building", "") or "").strip() # 건물명(표준 이름 or '스터디룸')

    if not campus:
        campus = "H2"  # 기본은 글로벌

    # 건물 필터가 "스터디룸" 이면 → 스터디룸만 보기 플래그
    studyroom_only = (building_raw == "스터디룸")

    # 건물명은 먼저 표준 이름으로 통일 (단, 스터디룸 전용일 때는 건물 필터 X)
    building_std = "" if studyroom_only else norm_building(building_raw)

    # ----------------------------------
    # 2) day / time 이 비어 있으면 현재 시각 기준으로 자동 세팅
    # ----------------------------------
    now = datetime.now()

    day = day_raw
    if not day:
        weekday_idx = now.weekday()  # 월=0 ... 일=6
        weekday_map = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri"}
        day = weekday_map.get(weekday_idx, "")

    if not time_:
        h = now.hour
        m = now.minute
        minute_of_day = h * 60 + m

        # 1~12교시까지 시간대 정의
        ranges = [
            (9 * 60,  9 * 60 + 50),   # 1교시
            (10 * 60, 10 * 60 + 50),  # 2교시
            (11 * 60, 11 * 60 + 50),  # 3교시
            (12 * 60, 12 * 60 + 50),  # 4교시
            (13 * 60, 13 * 60 + 50),  # 5교시
            (14 * 60, 14 * 60 + 50),  # 6교시
            (15 * 60, 15 * 60 + 50),  # 7교시
            (16 * 60, 16 * 60 + 50),  # 8교시
            (17 * 60, 17 * 60 + 50),  # 9교시
            (18 * 60, 18 * 60 + 50),  # 10교시
            (19 * 60, 19 * 60 + 50),  # 11교시
            (20 * 60, 20 * 60 + 50),  # 12교시
        ]

        for i, (start, end) in enumerate(ranges, start=1):
            if start <= minute_of_day < end:
                time_ = str(i)
                break
        # 어떤 교시에도 속하지 않으면 time_은 그대로 '' (야간, 새벽 등)

    # ----------------------------------
    # 3) 요일/교시 해석
    # ----------------------------------
    day_kr = DAY_MAP.get(day) if day else None   # "월", "화" ...
    period = int(time_) if time_.isdigit() else None

    # 21:00~09:00 같이 교시 외 시간대에는
    # period 가 None 이라서 "운영시간 아님"으로 간주
    if not period:
        return render_template(
            "result.html",
            rooms=[],
            count=0,
            day=day,
            time=time_,
            campus=campus,
            building=building_std,
        )

    # ----------------------------------
    # 4) DB 연결 (강의실 DB + 대여 DB 둘 다)
    # ----------------------------------
    conn = get_db()              # hufs_lectures.db
    cur = conn.cursor()

    rental_conn = get_rental_db()    # hufs_rental.db
    rental_cur  = rental_conn.cursor()

    # ----------------------------------
    # 4-0) rental_rooms 기준 "대여 필요 강의실" 집합 만들기
    #     key: (표준건물이름, 강의실번호) -> 대여 대상 여부
    # ----------------------------------
    campus_rental_name = "글로벌캠퍼스" if campus == "H2" else "서울캠퍼스"

    params_rooms = [campus_rental_name]
    sql_rooms = """
        SELECT building_name, classroom_id
        FROM rental_rooms
        WHERE campus = ?
    """
    # 건물 필터가 있으면 쿼리 줄이기(옵션)
    if building_std:
        raw_names = {building_std}
        for original, std in BUILDING_ALIAS.items():
            if std == building_std:
                raw_names.add(original)
        placeholders = ",".join("?" for _ in raw_names)
        sql_rooms += f" AND building_name IN ({placeholders})"
        params_rooms.extend(raw_names)

    rows_rooms = rental_cur.execute(sql_rooms, params_rooms).fetchall()

    rental_target_set = {
        (norm_building(r["building_name"]), str(r["classroom_id"]))
        for r in rows_rooms
    }

    # 결과를 강의실 / 스터디룸 따로 모았다가 마지막에 합침
    rooms_lectures = []
    rooms_study = []

    # ----------------------------------
    # 4-1) 강의실 데이터
    # ----------------------------------
    if not studyroom_only:
        params = [campus]
        sql = """
            SELECT building_name, room_number, floor
            FROM lectures
            WHERE campus = ?
              AND building_name IS NOT NULL
              AND building_name != ''
              AND room_number IS NOT NULL
              AND TRIM(room_number) != ''
        """

        # building_std(표준 이름)에 대응하는 모든 실제 이름(IN 쿼리)으로 필터링
        if building_std:
            raw_names = {building_std}
            # alias 딕셔너리에서 value == building_std 인 key 들도 허용
            for original, std in BUILDING_ALIAS.items():
                if std == building_std:
                    raw_names.add(original)

            placeholders = ",".join("?" for _ in raw_names)
            sql += f" AND building_name IN ({placeholders})"
            params.extend(raw_names)

        sql += """
            GROUP BY building_name, room_number, floor
            ORDER BY building_name, room_number
        """

        room_rows = cur.execute(sql, params).fetchall()

        for r in room_rows:
            raw_bname = r["building_name"]
            bname = norm_building(raw_bname)  # 화면에는 표준 이름으로 표시
            if not bname or bname == "학생회관":
                continue

            rnum  = str(r["room_number"])
            floor = r["floor"]

            # 이 강의실의 요일별 전체 시간표 가져오기
            schedule_by_day = get_room_schedule(cur, campus, raw_bname, rnum)

            # 현재 요일/교시 기준 공실 여부 및 시간대 계산
            is_free, free_range_text, next_class_text = compute_free_info(
                schedule_by_day,
                day_kr,   # 예: "화"
                period    # 예: 3
            )

            # -----------------------------
            #  대여 대상 여부 (rental_rooms 기준)
            # -----------------------------
            key = (bname, rnum)
            is_rental_target = key in rental_target_set

            # 기본 값
            badge_text   = None
            note_text    = None
            status_text  = "현재 공실입니다." if is_free else "현재 선택한 시간에는 수업이 진행 중입니다."

            # 대여 대상 강의실이면 배지 + 안내 문구만 표시
            if is_rental_target:
                badge_text = "대여 필요 강의실" if is_free else "대여 대상 강의실"
                note_text  = "해당 강의실은 수업 종료 이후 대여를 통해 이용할 수 있습니다."

            room_obj = {
                "room_name":       f"{bname} {rnum}호",
                "badge_text":      badge_text,
                "is_free":         bool(is_free),
                "status_text":     status_text,
                "free_range_text": free_range_text,   # 예: "09:00 ~ 12:00"
                "next_class_text": next_class_text,   # 예: "다음 수업 5교시 (13:00)"
                "building_name":   bname,
                "room_number":     rnum,
                "floor":           floor,
                "is_studyroom":    False,
                "capacity":        None,
                "reserve_url":     None,             
                "note_text":       note_text,
                "requires_rental": is_rental_target,
            }

            # 요일+교시가 지정된 경우 → "현재 시점에 사용 가능한 방"만 보여주기
            if day_kr and period:
                if room_obj["is_free"]:
                    rooms_lectures.append(room_obj)
            else:
                rooms_lectures.append(room_obj)

    # ----------------------------------
    # 4-2) 스터디룸 데이터 (기존 로직 그대로)
    # ----------------------------------
    today = datetime.now().strftime("%Y-%m-%d")

    params_sr = [today, period, campus]
    sql_sr = """
        SELECT
            sm.building_name,
            sm.room_name,
            sm.capacity,
            sm.sNum,
            MAX(CASE WHEN sps.is_free = 1 THEN 1 ELSE 0 END) AS is_free_any
        FROM studyroom_master sm
        LEFT JOIN studyroom_period_status sps
          ON sm.sNum = sps.sNum
         AND sps.date = ?
         AND sps.period = ?
        WHERE sm.campus_code = ?
    """

    # 강의실과 마찬가지로, 건물 필터가 특정 건물일 때는 해당 건물 스터디룸만
    if building_std and not studyroom_only:
        raw_names = {building_std}
        for original, std in BUILDING_ALIAS.items():
            if std == building_std:
                raw_names.add(original)
        placeholders = ",".join("?" for _ in raw_names)
        sql_sr += f" AND sm.building_name IN ({placeholders})"
        params_sr.extend(raw_names)

    sql_sr += """
        GROUP BY sm.building_name, sm.room_name, sm.capacity, sm.sNum
    """

    sr_rows = cur.execute(sql_sr, params_sr).fetchall()

    for r in sr_rows:
        raw_bname = r["building_name"]
        bname = norm_building(raw_bname)
        if not bname or bname == "학생회관":
            continue

        room_name = r["room_name"] or ""     # 예: "5호실(GPS라운지 내)"
        capacity  = r["capacity"] or 0
        s_num     = r["sNum"]
        is_free   = bool(r["is_free_any"] or 0)

        # "5호실(GPS라운지 내)" → "5호실"
        short_room = room_name.split("(")[0].strip() or f"{s_num}호실"

        display_name = f"{bname} {short_room} ({capacity}인용)"

        room_obj = {
            "room_name":       display_name,
            "badge_text":      "스터디룸",
            "is_free":         is_free,
            "status_text":     "현재 이용 가능한 스터디룸입니다." if is_free else "현재 예약이 잡혀 있습니다.",
            "free_range_text": "",
            "next_class_text": "",
            "building_name":   bname,
            "room_number":     short_room,
            "floor":           None,
            "is_studyroom":    True,
            "capacity":        capacity,
            "reserve_url":     "https://rs.hufs.ac.kr/",
            "note_text":       None,
            "requires_rental": False,
        }

        # 현재 공실인 스터디룸만 노출
        if is_free:
            rooms_study.append(room_obj)

    # 두 DB 모두 닫기
    conn.close()
    rental_conn.close()

    # ----------------------------------
    # 5) 강의실 + 스터디룸 합치기
    # ----------------------------------
    if studyroom_only:
        rooms = rooms_study
    else:
        rooms = []
        rooms.extend(rooms_lectures)
        rooms.extend(rooms_study)

    # ----------------------------------
    # 6) 템플릿 렌더링
    # ----------------------------------
    return render_template(
        "result.html",
        rooms=rooms,
        count=len(rooms),
        day=day,
        time=time_,
        campus=campus,
        building="스터디룸" if studyroom_only else building_std,
    )


# ------------------------
#  강의실 상세 페이지 (더미)
# ------------------------
@app.route("/detail")
def detail():
    # 1) 쿼리 파라미터: 어떤 강의실인지
    #    /detail?campus=H2&building=백년관&room=301&day=mon
    campus       = (request.args.get("campus", "") or "").strip() or "H2"
    building_raw = (request.args.get("building", "") or "").strip()
    room_number  = (request.args.get("room", "") or "").strip()

    building_std = norm_building(building_raw)

    if not building_std or not room_number:
        abort(400, "building / room 파라미터가 필요합니다.")

    # ---------------------------
    # 2) 요일 결정 (쿼리스트링 우선)
    # ---------------------------
    now = datetime.now()
    day_param = (request.args.get("day", "") or "").strip().lower()  # mon~fri

    weekday_idx = now.weekday()          # 월(0) ~ 일(6)
    weekday_map = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri"}

    if day_param in ("mon", "tue", "wed", "thu", "fri"):
        day = day_param
    else:
        day = weekday_map.get(weekday_idx, "")

    day_kr = DAY_MAP.get(day)  # "월", "화" ...

    # ---------------------------
    # 3) DB 연결 (강의실 + 대여)
    # ---------------------------
    conn        = get_db()
    cur         = conn.cursor()
    rental_conn = get_rental_db()
    rental_cur  = rental_conn.cursor()

    # 이 표준 건물명에 매칭되는 실제 building_name 후보들
    raw_names = {building_std}
    for original, std in BUILDING_ALIAS.items():
        if std == building_std:
            raw_names.add(original)
    placeholders = ",".join("?" for _ in raw_names)

    # 3-1) 강의실 기본 정보
    sql_room = f"""
        SELECT building_name, room_number, floor
        FROM lectures
        WHERE campus = ?
          AND room_number = ?
          AND building_name IN ({placeholders})
        LIMIT 1
    """
    params_room = [campus, room_number, *raw_names]
    row = cur.execute(sql_room, params_room).fetchone()
    if not row:
        conn.close()
        rental_conn.close()
        abort(404, "해당 강의실을 찾을 수 없습니다.")

    raw_bname = row["building_name"]
    floor     = row["floor"]
    bname     = norm_building(raw_bname)

    # 3-2) 요일별 전체 시간표(교시 목록) – 공실 여부 계산용
    schedule_by_day = get_room_schedule(cur, campus, raw_bname, room_number)
    busy_today = schedule_by_day.get(day_kr, [])     # 예: [2,3,4,5,6]
    has_classes_today = bool(busy_today)

    # 오늘(선택된 요일)에 수업이 하나도 없으면 09~18 공실로 표시
    if not has_classes_today:
        free_start = "09:00"
        free_end   = "18:00"
        is_free_now = True
    else:
        free_start = ""
        free_end   = ""
        is_free_now = False

    # 3-3) lectures 테이블에서 과목명 + schedules 파싱
    sql_lec = f"""
        SELECT subject, schedules
        FROM lectures
        WHERE campus = ?
          AND room_number = ?
          AND building_name IN ({placeholders})
    """
    rows_lec = cur.execute(sql_lec, [campus, room_number, *raw_names]).fetchall()

    # lectures_today : "선택한 요일(day_kr)" 기준으로만 과목 매핑
    lectures_today = {}  # {period(int): subject(str)}

    for r in rows_lec:
        subj = r["subject"] or ""
        s_json = r["schedules"]
        if not s_json:
            continue
        try:
            blocks = json.loads(s_json)
        except json.JSONDecodeError:
            continue

        for block in blocks:
            blk_day = block.get("day")
            periods = block.get("periods", [])

            # 선택한 요일만 반영
            if blk_day != day_kr:
                continue

            for p in periods:
                try:
                    p_int = int(p)
                except (TypeError, ValueError):
                    continue

                # 같은 교시 여러 과목 겹치는 일은 거의 없다고 가정하고
                if p_int not in lectures_today:
                    lectures_today[p_int] = subj

    # "다음 수업" = 오늘(day_kr) 기준 가장 이른 교시
    if not lectures_today:
        next_class_time = "오늘 수업 없음"
        next_class_name = ""
    else:
        first_p = min(lectures_today.keys())
        next_class_time = PERIOD_TIME.get(first_p, ("", ""))[0]
        next_class_name = lectures_today[first_p]

    # ---------------------------
    # 4) 대여 정보 (rental_slots)
    # ---------------------------
    today_str          = now.strftime("%Y-%m-%d")
    campus_rental_name = "글로벌캠퍼스" if campus == "H2" else "서울캠퍼스"

    sql_rental = """
        SELECT rental_time, is_reserved
        FROM rental_slots
        WHERE date = ?
          AND campus = ?
          AND building_name = ?
          AND classroom_id = ?
    """
    rental_rows = rental_cur.execute(
        sql_rental,
        [today_str, campus_rental_name, building_std, room_number]
    ).fetchall()

    is_rental_target = len(rental_rows) > 0
    any_reserved     = any(r["is_reserved"] for r in rental_rows)

    rental_limited = is_rental_target
    notice_text = "모든 수업이 종료된 이후에는 대여 신청이 필요한 강의실입니다." \
        if is_rental_target else ""

    # 상태 문구 (요일 기준)
    if not has_classes_today:
        if is_rental_target:
            if any_reserved:
                status_text = f"{day_kr}요일 수업은 없으며,<br>대여 대상 강의실로 예약이 잡혀 있습니다."
            else:
                status_text = f"{day_kr}요일 수업은 없으며,<br>대여 대상 강의실입니다."
        else:
            status_text = f"{day_kr}요일 수업이 없는 강의실입니다."
    else:
        if is_rental_target:
            status_text = f"{day_kr}요일에 수업이 있는 대여 대상 강의실입니다.<br>아래 시간표를 참고하세요."
        else:
            status_text = f"{day_kr}요일에 수업이 있는 강의실입니다.<br>아래 시간표를 참고하세요."

    # ---------------------------
    # 5) 시간표 리스트 (1~12교시, "선택 요일" 기준)
    # ---------------------------
    timetable = []
    for p in range(1, 13):
        start, end = PERIOD_TIME.get(p, ("", ""))
        # 오늘(day_kr) 기준 과목명
        subj = lectures_today.get(p, "")
        is_free = (p not in busy_today)

        row_data = {
            "period":  p,
            "start":   start,
            "end":     end,
            "title":   subj if not is_free else "",
            "is_free": is_free,
        }
        timetable.append(row_data)

        # 5-1) 오늘 기준 "비는 시간" 요약 텍스트 만들기
        if not has_classes_today:
            # 오늘 수업이 아예 없으면 고정으로 09~18
            free_time_text = "09:00 ~ 18:00"
        else:
            free_periods = [row["period"] for row in timetable if row["is_free"]]
            free_time_text = build_free_time_ranges(free_periods)
            if not free_time_text:
                free_time_text = "--"

    # ---------------------------
    # 6) 같은 층 추천 공실 (오늘 요일 기준 "하루 종일 공실"인 강의실)
    # ---------------------------
    recommend_list = []
    sql_same_floor = f"""
        SELECT building_name, room_number, floor
        FROM lectures
        WHERE campus = ?
          AND floor = ?
          AND building_name IN ({placeholders})
        GROUP BY building_name, room_number, floor
        ORDER BY room_number
    """
    rows_same = cur.execute(sql_same_floor, [campus, floor, *raw_names]).fetchall()

    for rr in rows_same:
        rnum2 = str(rr["room_number"])
        if rnum2 == room_number:
            continue

        sched2 = get_room_schedule(cur, campus, rr["building_name"], rnum2)
        busy2 = sched2.get(day_kr, [])

        # 오늘(day_kr) 수업이 "아예 없는" 강의실만 추천
        if busy2:
            continue

        recommend_list.append({
            "name":       f"{norm_building(rr['building_name'])} {rnum2}호",
            "free_start": "09:00",
            "free_end":   "18:00",
            "campus":     campus,
            "building":   norm_building(rr["building_name"]),
            "room":       rnum2,
        })
        if len(recommend_list) >= 4:
            break

    conn.close()
    rental_conn.close()

    # ---------------------------
    # 7) 템플릿에 넘길 room 객체
    # ---------------------------
    room = {
        "id":              f"{bname}_{room_number}",
        "name":            f"{bname} {room_number}호",
        "floor":           f"{floor}층" if floor is not None else "",
        "rental_limited":  rental_limited,
        "status":          status_text,
        "is_free_now":     bool(is_free_now),
        "free_start":      free_start,
        "free_end":        free_end,
        "next_class_time": next_class_time,
        "next_class_name": next_class_name,
        "favorite":        False,
    }

    return render_template(
        "detail.html",
        room=room,
        timetable=timetable,
        notice_text=notice_text,
        recommendations=recommend_list,
        day=day,
        free_time_text=free_time_text,
    )


@app.route("/favorite")
def favorite():
    return render_template("favorite.html")


@app.route("/api/favorite", methods=["POST"])
def api_favorite():
    data = request.get_json() or {}
    room = data.get("room")

    # 최소한 이름은 있어야 저장
    if not room or "name" not in room:
        return jsonify({"ok": False, "error": "no room"}), 400

    favorites = session.get("favorites", [])

    # 같은 이름이 이미 있으면 중복 저장 안 함
    names = [f.get("name") for f in favorites]
    if room["name"] not in names:
        favorites.append(room)
        session["favorites"] = favorites

    return jsonify({"ok": True})


# ------------------------
#  실행
# ------------------------
if __name__ == "__main__":
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        start_studyroom_auto_refresh(30)  

    app.run(debug=True)
