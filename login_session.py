# login_session.py
# 1) Selenium으로 rs.hufs.ac.kr 접속 및 로그인
# 2) 로그인 완료 상태의 쿠키를 rs_cookies.json 으로 저장

import json
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options


LOGIN_URL = "https://rs.hufs.ac.kr/client/classroom/classroom_main.jsp"
COOKIE_FILE = Path("rs_cookies.json")


def save_cookies(driver, cookie_file: Path):
    cookies = driver.get_cookies()
    with cookie_file.open("w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)
    print(f"[OK] 쿠키 {cookie_file} 에 저장 완료 (쿠키 개수: {len(cookies)})")


def main():
    chrome_options = Options()
    # headless 로 하면 로그인 UI가 안 보이니 로그인할 때는 보통 모드 추천
    # chrome_options.add_argument("--headless=new")

    driver = webdriver.Chrome(options=chrome_options)

    try:
        print(f"[INFO] {LOGIN_URL} 접속 중...")
        driver.get(LOGIN_URL)

        print(
            "\n[안내] 브라우저 창에서 학교 계정으로 로그인한 뒤, "
            "강의실 대관 메인 화면이 뜨는 것까지 확인해주세요."
        )
        print("로그인이 완료되면 여기 터미널로 돌아와서 Enter 를 눌러주세요.")
        input("계속하려면 Enter ▶ ")

        # 혹시 로그인 직후 쿠키 세팅이 늦게 될 수도 있으니 약간 대기
        time.sleep(2)

        save_cookies(driver, COOKIE_FILE)

    finally:
        driver.quit()
        print("[INFO] 브라우저 종료 완료.")


if __name__ == "__main__":
    main()
