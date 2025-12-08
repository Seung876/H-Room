# studyroom_auto_sync.py
"""
1분마다 HUFS 스터디룸 예약 정보를 자동으로 동기화하는 스크립트.

이 파일은 Flask(app.py)와는 별도로
터미널에서 따로 실행해 두면 됩니다.

예)
  터미널 1: python app.py         # Flask 서버
  터미널 2: python studyroom_auto_sync.py  # 1분마다 자동 동기화
"""

import time
from datetime import datetime

from hufs_studyroom import update_studyroom_all


INTERVAL_SECONDS = 60  # 1분


def main():
    while True:
        print("=" * 60)
        print(f"[{datetime.now()}] 스터디룸 자동 동기화 시작")

        try:
            update_studyroom_all()
            print(f"[{datetime.now()}] 스터디룸 자동 동기화 성공")
        except Exception as e:
            print(f"[{datetime.now()}] 스터디룸 자동 동기화 실패: {e}")

        print(f"{INTERVAL_SECONDS}초 후 다시 실행...\n")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
