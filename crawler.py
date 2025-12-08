# crawler.py
# HUFS 강의시간표 크롤러 + sqlite 저장 (서울 / 글로벌 전부)
# pip install requests sqlite-utils 필요

import requests
import urllib.parse
import json
import time
from datetime import datetime
import sqlite_utils


# ---------------------- 기본 설정 ---------------------- #

BASE_PAGE_URL = "https://wis.hufs.ac.kr/src08/jsp/lecture/LECTURE2020L.jsp"
POST_URL = "https://wis.hufs.ac.kr/hufs"

DEFAULT_ORG_SECT = "A"   

# 글로벌 캠퍼스 건물명 
BUILDING_NAME_H2 = {
    "0": "백년관",
    "1": "어문학관",
    "2": "교양관",
    "3": "자연과학관",
    "4": "인문경상관",
    "5": "공학관",
    "6": "학생회관",
}

# 서울 캠퍼스 건물명
BUILDING_NAME_H1 = {
    "0": "본관",
    "1": "인문과학관",
    "2": "교수학습개발원",
    "3": "사회과학관",
    "5": "법학관",
    "6": "대학원",
    "B": "역사관",   
    "C": "사이버관",
}

DAY_KO = {"월", "화", "수", "목", "금", "토"}

# 전공 외에 “교양 / 기초(대학기초, 단과대학별 기초과목)” 그룹 코드
# (DevTools 에서 본 select box value 그대로 사용)
EXTRA_GROUPS = {
    "H2": [
        # ----- 글로벌 교양 (gubun=2) -----
        {"code": "305", "gubun": "2", "name": "대학외국어(글로벌)"},
        {"code": "306", "gubun": "2", "name": "미네르바인문(글로벌)"},
        {"code": "307", "gubun": "2", "name": "RC영어(글로벌)"},
        {"code": "308", "gubun": "2", "name": "소프트웨어기초(글로벌)"},
        {"code": "309", "gubun": "2", "name": "COLLEGE ENGLISH(글로벌)"},
        {"code": "322", "gubun": "2", "name": "공통교양(글로벌)"},
        {"code": "330", "gubun": "2", "name": "언어와문학(글로벌)"},
        {"code": "331", "gubun": "2", "name": "문화와예술(글로벌)"},
        {"code": "332", "gubun": "2", "name": "역사와철학(글로벌)"},
        {"code": "334", "gubun": "2", "name": "과학과기술(글로벌)"},
        {"code": "336", "gubun": "2", "name": "인간과사회(글로벌)"},
        {"code": "342", "gubun": "2", "name": "인성교육(글로벌)"},
        {"code": "351", "gubun": "2", "name": "HUFS CAREER(글로벌)"},
        {"code": "353", "gubun": "2", "name": "미래시뮬레이션(글로벌)"},
        {"code": "355", "gubun": "2", "name": "실용외국어(선택)(글로벌)"},
        {"code": "356", "gubun": "2", "name": "외국인을 위한 한국학(글로벌)"},
        {"code": "358", "gubun": "2", "name": "생활과스포츠(글로벌)"},
        {"code": "61",  "gubun": "2", "name": "군사학(글로벌)"},

        # ----- 글로벌 기초 / 단과대학 기초 (gubun=3) -----
        {"code": "71A", "gubun": "3", "name": "인문대학(글로벌)"},
        {"code": "71B", "gubun": "3", "name": "국가전략어대학(글로벌)"},
        {"code": "71C", "gubun": "3", "name": "경상대학(글로벌)"},
        {"code": "71E", "gubun": "3", "name": "공과대학(공과계열)(글로벌)"},
        {"code": "71F", "gubun": "3", "name": "CULTURE&TECHNOLOGY융합대학(글로벌)"},
        {"code": "71G", "gubun": "3", "name": "AI융합대학(글로벌)"},
        {"code": "720", "gubun": "3", "name": "폴란드학과(글로벌)"},
        {"code": "72P", "gubun": "3", "name": "루마니아학과(글로벌)"},
        {"code": "72Q", "gubun": "3", "name": "체코·슬로바키아학과(글로벌)"},
        {"code": "72R", "gubun": "3", "name": "헝가리학과(글로벌)"},
        {"code": "72S", "gubun": "3", "name": "세르비아·크로아티아학과(글로벌)"},
        {"code": "72U", "gubun": "3", "name": "중앙아시아학과(글로벌)"},
        {"code": "72W", "gubun": "3", "name": "우크라이나어과(글로벌)"},
        {"code": "72X", "gubun": "3", "name": "한국학과(글로벌)"},
        {"code": "734", "gubun": "3", "name": "이공계열(글로벌)"},
    ],

    "H1": [
        # ----- 서울 교양 (gubun=2) -----
        {"code": "305", "gubun": "2", "name": "대학외국어(서울)"},
        {"code": "306", "gubun": "2", "name": "미네르바인문(서울)"},
        {"code": "308", "gubun": "2", "name": "소프트웨어기초(서울)"},
        {"code": "309", "gubun": "2", "name": "COLLEGE ENGLISH(서울)"},
        {"code": "322", "gubun": "2", "name": "공통교양(한예종)(서울)"},
        {"code": "330", "gubun": "2", "name": "언어와문학(서울)"},
        {"code": "331", "gubun": "2", "name": "문화와예술(서울)"},
        {"code": "332", "gubun": "2", "name": "역사와철학(서울)"},
        {"code": "334", "gubun": "2", "name": "과학과기술(서울)"},
        {"code": "336", "gubun": "2", "name": "인간과사회(서울)"},
        {"code": "342", "gubun": "2", "name": "인성교육(서울)"},
        {"code": "351", "gubun": "2", "name": "HUFS CAREER(서울)"},
        {"code": "353", "gubun": "2", "name": "미래시뮬레이션(서울)"},
        {"code": "355", "gubun": "2", "name": "실용외국어(선택)(서울)"},
        {"code": "356", "gubun": "2", "name": "외국인을 위한 한국학(서울)"},
        {"code": "358", "gubun": "2", "name": "생활과스포츠(서울)"},
        {"code": "61",  "gubun": "2", "name": "군사학(서울)"},

        # ----- 서울 기초 / 단과대학 기초 (gubun=3) -----
        {"code": "710", "gubun": "3", "name": "영어대학(서울)"},
        {"code": "713", "gubun": "3", "name": "아시아언어문화대학(특수외국어(인도·아세안지역)계열)(서울)"},
        {"code": "714", "gubun": "3", "name": "아시아언어문화대학(특수외국어(중동지역)계열)(서울)"},
        {"code": "715", "gubun": "3", "name": "중국학대학(서울)"},
        {"code": "716", "gubun": "3", "name": "일본학대학(서울)"},
        {"code": "717", "gubun": "3", "name": "사회과학대학(서울)"},
        {"code": "718", "gubun": "3", "name": "상경대학(서울)"},
        {"code": "719", "gubun": "3", "name": "경영대학(서울)"},
        {"code": "71G", "gubun": "3", "name": "AI융합대학(서울)"},
        {"code": "724", "gubun": "3", "name": "프랑스어학부(서울)"},
        {"code": "725", "gubun": "3", "name": "독일어과(서울)"},
        {"code": "726", "gubun": "3", "name": "노어과(서울)"},
        {"code": "727", "gubun": "3", "name": "스페인어과(서울)"},
        {"code": "728", "gubun": "3", "name": "이탈리아어과(서울)"},
        {"code": "729", "gubun": "3", "name": "포르투갈어과(서울)"},
        {"code": "72A", "gubun": "3", "name": "네덜란드어과(서울)"},
        {"code": "72B", "gubun": "3", "name": "스칸디나비아어과(서울)"},
        {"code": "72C", "gubun": "3", "name": "말레이·인도네시아어과(서울)"},
        {"code": "72D", "gubun": "3", "name": "태국학과(서울)"},
        {"code": "72E", "gubun": "3", "name": "베트남어과(서울)"},
        {"code": "72F", "gubun": "3", "name": "인도어과(서울)"},
        {"code": "72G", "gubun": "3", "name": "아랍어과(서울)"},
        {"code": "72H", "gubun": "3", "name": "튀르키예·아제르바이잔학과(서울)"},
        {"code": "72I", "gubun": "3", "name": "페르시아어·이란학과(서울)"},
        {"code": "72J", "gubun": "3", "name": "몽골어과(서울)"},
        {"code": "72K", "gubun": "3", "name": "중국언어문화학부(서울)"},
        {"code": "72L", "gubun": "3", "name": "중국외교통상학부(서울)"},
        {"code": "72M", "gubun": "3", "name": "일본언어문화학부(서울)"},
        {"code": "72N", "gubun": "3", "name": "융합일본지역학부(서울)"},
        {"code": "734", "gubun": "3", "name": "이공계열(서울)"},
        {"code": "74",  "gubun": "3", "name": "대학기초(서울)"},
    ],
}


# ---------------------- 공통 유틸 ---------------------- #

def get_current_year_semester():
    """
    현재 날짜 기준으로 HUFS 연도/학기 코드 자동 결정
    - 3~7월  : 1학기 (코드 '1')
    - 9~12월 : 2학기 (코드 '3')
    - 1~2월  : 직전 해 2학기 (코드 '3')
    - 8월    : 당해 1학기 (코드 '1')  → 여름학기 별도 구분 안 함
    """
    now = datetime.now()
    year = now.year
    month = now.month

    if 3 <= month <= 7:
        sessn = "1"
    elif 9 <= month <= 12:
        sessn = "3"
    elif month in (1, 2):
        year = year - 1
        sessn = "3"
    else:  # month == 8
        sessn = "1"

    return str(year), sessn


def create_session() -> requests.Session:
    """쿠키/세션 유지를 위한 requests.Session 생성"""
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    })
    # JSESSIONID 등 쿠키 받기 위해 메인 페이지 한 번 호출
    s.get(BASE_PAGE_URL)
    return s


def decode_json(resp: requests.Response) -> dict:
    """서버 응답이 URL 인코딩된 문자열이므로 unquote 후 json.loads"""
    resp.encoding = "utf-8"
    decoded = urllib.parse.unquote(resp.text)
    return json.loads(decoded)


def get_building_name(campus: str, building_no: str) -> str:
    if campus == "H2":
        return BUILDING_NAME_H2.get(building_no, "")
    elif campus == "H1":
        return BUILDING_NAME_H1.get(building_no, "")
    return ""


def parse_room_code(room_code: str, campus: str):
    """
    강의실 코드 파싱
      '0505'  (글로벌) → 건물 0, 층 5, 호실 505
      '1507'  (서울)   → 건물 1, 층 5, 호실 507
      '01202' → 건물 0, 층 1, 호실 1202 (1층 202호를 1202로 저장)
    """
    if not room_code:
        return None, None, None, None

    code = room_code.strip()
    if len(code) < 3:
        return None, None, None, None

    building_no = code[0]
    local = code[1:]  # 나머지 = 층 + 호실

    if not local[0].isdigit():
        return building_no, get_building_name(campus, building_no), None, None

    floor = int(local[0])

    try:
        room_3digit = int(local)  # 층 + 호실 전체를 정수로 저장
    except ValueError:
        room_3digit = None

    building_name = get_building_name(campus, building_no)

    return building_no, building_name, floor, room_3digit


def parse_day_time(display: str):
    """
    dayTimeDisplay 파싱
    예) '목 4 5 6 (0505)' → [{'day':'목','periods':[4,5,6]}]
        '월 1 2 3 수 4 5 6 (0505)' →
          [{'day':'월','periods':[1,2,3]}, {'day':'수','periods':[4,5,6]}]
    """
    if not display:
        return []

    left = display.split("(")[0].strip()
    tokens = left.split()

    result = []
    current = None

    for tok in tokens:
        if tok in DAY_KO:
            if current:
                result.append(current)
            current = {"day": tok, "periods": []}
        elif current is not None and tok.isdigit():
            current["periods"].append(int(tok))

    if current:
        result.append(current)

    return result


# ---------------------- 1. 전공(학과) 목록 ---------------------- #

def get_majors(
    session: requests.Session,
    year: str,
    sessn: str,
    campus: str,
    org_sect: str = DEFAULT_ORG_SECT,
):
    """
    process3_1a 호출해서 전공(학과) 리스트 가져오기
    (여기서는 gubun=1 계열 전공/부전공용 코드 목록이라고 보면 됨)
    """
    payload = {
        "mName": "process3_1a",
        "cName": "hufs.stu1.STU1_C008",
        "org_sect": org_sect,
        "ledg_year": year,
        "ledg_sessn": sessn,
        "campus": campus,
    }

    resp = session.post(POST_URL, data=payload)
    obj = decode_json(resp)

    data_count = int(obj.get("dataCount", 0))
    if data_count == 0:
        return []

    rows = [obj["data"]] if data_count == 1 else obj["data"]

    majors = []
    for row in rows:
        majors.append({
            "code": row["hakkwaCode1"],       # 예: ARDA1
            "name": row["hakkwaName1"],
            "name_en": row["hakkwaName1E"],
            "campus_name": row.get("campusName1"),
        })
    return majors


# ---------------------- 2. 그룹(전공/교양/기초)별 강의 목록 ---------------------- #

def get_lectures_for_group(
    session: requests.Session,
    group_code: str,  
    gubun: str,      
    year: str,
    sessn: str,
    campus: str,
    org_sect: str = DEFAULT_ORG_SECT,
):
    """
    getDataLssnLista 호출해서 특정 그룹(전공/교양/기초)의 강의 리스트 조회
    예:
      - 전공: group_code = 전공코드(ARDA1 등), gubun="1"
      - 교양: group_code = "305", gubun="2"
      - 기초: group_code = "71A", gubun="3" (또는 단과대학별 코드)
    """
    payload = {
        "mName": "getDataLssnLista",
        "cName": "hufs.stu1.STU1_C009",
        "org_sect": org_sect,
        "ledg_year": year,
        "ledg_sessn": sessn,
        "campus": campus,
        "crs_strct_cd": group_code,
        "gubun": gubun,
        "subjt_nm": "",
        "won": "",
        "cyber": "",
        "emp_nm": "",
        "d1": "N", "d2": "N", "d3": "N", "d4": "N", "d5": "N", "d6": "N",
        "t1": "N", "t2": "N", "t3": "N", "t4": "N", "t5": "N", "t6": "N",
        "t7": "N", "t8": "N", "t9": "N", "t10": "N", "t11": "N", "t12": "N",
    }

    resp = session.post(POST_URL, data=payload)
    obj = decode_json(resp)

    data_count = int(obj.get("dataCount", 0))
    if data_count == 0:
        return []

    rows = [obj["data"]] if data_count == 1 else obj["data"]

    lectures = []

    for row in rows:
        # 과목명 (한글 우선, 없으면 영문)
        subject = (
            row.get("subjtNaKr")
            or row.get("subjtNaEng")
            or row.get("subjtNaENG", "")
        )
        # 교수명 (한글 우선)
        prof = row.get("empNm") or row.get("empNmEng", "")

        time_str = row.get("dayTimeDisplay", "")
        room_code = ""
        if "(" in time_str and ")" in time_str:
            room_code = time_str.split("(")[-1].split(")")[0].strip()

        # 강의실/시간 상세 파싱
        building_no, building_name, floor, room_3digit = parse_room_code(
            room_code, campus
        )
        schedules = parse_day_time(time_str)

        lectures.append({
            "major_code": group_code,  
            "gubun": gubun,            

            "subject": subject,
            "professor": prof,

            "time_raw": time_str,
            "room_code": room_code,

            "building_no": building_no,
            "building_name": building_name,
            "floor": floor,
            "room_number": room_3digit, 

            "schedules": json.dumps(schedules, ensure_ascii=False),
        })

    return lectures


# ---------------------- 3. 전체 캠퍼스 크롤링 + DB 저장 ---------------------- #

def crawl_and_save(campus: str):
    """
    주어진 캠퍼스(H1/H2)에 대해:
      1) 현재 연도/학기 자동 감지
      2) 전공(1) 목록 조회
      3) 교양/기초(2,3) 그룹 추가
      4) 각 그룹별 강의 조회
      5) sqlite DB(hufs_lectures.db)의 lectures 테이블에 저장
    """
    year, sessn = get_current_year_semester()
    print(f"캠퍼스 {campus} → 자동 감지 연도/학기: {year}년 / {sessn}")

    session = create_session()

    # 1) 전공(학과) 목록 가져오기 (gubun=1용 코드)
    majors = get_majors(session, year, sessn, campus)
    print(f"[{campus}] 전공 수: {len(majors)}")

    # 2) “전공 + 교양 + 기초” 전체 그룹 리스트 만들기
    groups = []

    # 전공 (gubun=1)
    for m in majors:
        groups.append({
            "code": m["code"],         
            "name": m["name"],
            "name_en": m["name_en"],
            "gubun": "1",
        })

    # 교양/기초 (gubun=2,3) – 캠퍼스별 추가 코드
    extra = EXTRA_GROUPS.get(campus, [])
    for g in extra:
        groups.append({
            "code": g["code"],
            "name": g["name"],
            "name_en": g.get("name_en", ""),
            "gubun": g["gubun"],
        })

    db = sqlite_utils.Database("hufs_lectures.db")

    total = 0

    # 3) 각 그룹별로 강의 목록 조회 후 DB 저장
    for grp in groups:
        grp_code = grp["code"]
        grp_name = grp["name"]
        grp_name_en = grp["name_en"]
        gubun = grp["gubun"]

        print(f"[{campus}] [gubun={gubun}] [{grp_code}] {grp_name} 크롤링 중...")

        lec_list = get_lectures_for_group(
            session=session,
            group_code=grp_code,
            gubun=gubun,
            year=year,
            sessn=sessn,
            campus=campus,
        )

        for lec in lec_list:
            # 공통 메타데이터 추가
            lec["major_name"] = grp_name
            lec["major_name_en"] = grp_name_en
            lec["campus"] = campus      # H1 / H2
            lec["year"] = int(year)
            lec["semester"] = int(sessn)

            db["lectures"].insert(
                lec,
                ignore=True  # 중복인 경우 무시
            )
            total += 1

        time.sleep(0.15)  # 서버 부하 줄이기

    print(f"[{campus}] 저장 완료: {total}개 강의 → hufs_lectures.db")


# ---------------------- 4. 실행 ---------------------- #

if __name__ == "__main__":

    # 글로벌(H2), 서울(H1) 순서로 모두 저장
    crawl_and_save(campus="H2")
    crawl_and_save(campus="H1")

    # DB 내용 테스트로 몇 개만 확인
    db = sqlite_utils.Database("hufs_lectures.db")
    print("\n예시 출력 (앞 10개):")
    for row in db["lectures"].rows_where(limit=10):
        print(
            f"[{row['campus']}] gubun={row.get('gubun')} | "
            f"{row['major_name']} | "
            f"{row['subject']} | {row['professor']} | "
            f"{row['time_raw']} | {row['building_name']} {row['room_number']}"
        )
