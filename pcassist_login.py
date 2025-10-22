# -*- coding: utf-8 -*-
# DBSAFER AGENT 1차/2차(OTP) 자동 로그인 + 화면 OCR로 OTP 추출

from pywinauto import application
import time
import logging
import subprocess
import os
from PIL import Image
import pyautogui
import pytesseract
from datetime import datetime
import runpy, pathlib

# ========================= 사용자 설정 =========================
# 1) Tesseract 설치 경로
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# 2) OTP 캡처 영역 (좌표는 환경에 맞게 조정)
#    x, y: 좌상단 좌표 / width, height: 영역 크기
CAP_X, CAP_Y, CAP_W, CAP_H = 1600, 160, 150, 60

# 3) 디버깅용 이미지 저장 위치
SAVE_RAW = r"C:\Users\ijlee\Downloads\otp_area_debug.png"
SAVE_BW  = r"C:\Users\ijlee\Downloads\otp_processed_bw.png"

# 4) DBSAFER AGENT 실행 파일 경로
APP_PATH = r"C:\Program Files (x86)\PNPSECURE\DBSAFER AGENT\DBSaferAgt.exe"

# 5) 로그인용 계정 정보 모듈(config.py)에 s_id, s_pw가 있다고 가정
import config  # config.s_id / config.s_pw 사용
# =============================================================


def log_message(msg: str):
    print(f"[LOG] {msg}")


def get_otp_from_screen(
    x: int, y: int, w: int, h: int,
    save_raw: str, save_bw: str,
    threshold: int = 160,
    ocr_timeout_sec: int = 6,
) -> str:
    """
    화면 특정 영역을 캡처 → 전처리(흑백/이진화) → Tesseract OCR → 6자리 숫자 반환
    - 여러 번 재시도하여 안정성 확보
    """
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

    deadline = time.time() + ocr_timeout_sec
    last_text = ""

    while time.time() < deadline:
        # 캡처
        screenshot = pyautogui.screenshot(region=(x, y, w, h))
        # 원본 저장
        try:
            screenshot.save(save_raw)
        except Exception as e:
            log_message(f"원본 저장 실패(무시 가능): {e}")

        # 전처리: 흑백 → 이진화
        gray = screenshot.convert("L")
        bw = gray.point(lambda px: 0 if px < threshold else 255, "1")
        try:
            bw.save(save_bw)
        except Exception as e:
            log_message(f"전처리 저장 실패(무시 가능): {e}")

        # OCR
        ocr_cfg = "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789"
        text = pytesseract.image_to_string(bw, config=ocr_cfg)
        last_text = text.strip()
        otp_digits = "".join(ch for ch in last_text if ch.isdigit())

        log_message(f"[OCR raw] '{last_text}' -> [digits] '{otp_digits}'")

        # 6자리만 유효로 판단
        if len(otp_digits) == 6:
            return otp_digits

        # 0.5초 후 재시도
        time.sleep(0.5)

    # 실패 시 마지막 결과를 로그로 남기고 빈 문자열 반환
    log_message(f"[WARN] OCR에서 6자리 OTP 인식 실패(마지막 raw='{last_text}')")
    return ""


def main():
    log_message("2차인증 MOTP 테스트~!")
    time.sleep(2)

    # 관리자 권한으로 DBSAFER AGENT 실행
    logging.info("DBSAFER AGENT 실행 중...")
    log_message("DBSAFER AGENT 실행 중...")
    subprocess.run(["powershell", "Start-Process", f'"{APP_PATH}"', "-Verb", "runAs"])

    # 애플리케이션 로딩 대기
    time.sleep(10)

    # 애플리케이션 연결
    try:
        app = application.Application().connect(title="DB보안 사용자 인증", timeout=10)
        logging.info("DBSAFER AGENT 1차 연결 성공")
        log_message("DBSAFER AGENT 1차 연결 성공")
    except Exception as e:
        log_message(f"DBSAFER AGENT 1차 연결 실패: {e}")
        logging.info(f"DBSAFER AGENT 1차 연결 실패: {e}")
        return

    # 로그인 창 핸들
    dlg = app.window(title="DB보안 사용자 인증")

    # 사용자 ID / PW 입력
    try:
        dlg.Edit1.type_keys(config.s_id)
        dlg.Edit2.type_keys(config.s_pw)
        dlg.Button1.click()
    except Exception as e:
        log_message(f"[ERR] 1차 인증 입력 중 오류: {e}")
        return

    # OTP 창 대기 (약간 여유)
    time.sleep(2)

    # OTP 창 확인
    try:
        dlg_otp = app.window(title="OTP 인증")
        dlg_otp.wait("exists ready", timeout=10)
        log_message("OTP 인증창이 확인되었습니다.")
        logging.info("OTP 인증창이 확인되었습니다.")
    except Exception as e:
        log_message(f"OTP 인증창을 찾는 도중 오류 발생: {e}")
        logging.info(f"OTP 인증창을 찾는 도중 오류 발생: {e}")
        return

    # ======== OCR로 OTP 인식 후 입력 ========
    log_message("화면에서 OTP 캡처 후 OCR 인식 시작...")
    otp_code = get_otp_from_screen(
        CAP_X, CAP_Y, CAP_W, CAP_H,
        SAVE_RAW, SAVE_BW,
        threshold=160,
        ocr_timeout_sec=8,
    )

    if not otp_code:
        log_message("[FAIL] OTP 인식 실패. 스크린샷을 확인하세요.")
        return

    try:
        dlg_otp.Edit.type_keys(otp_code)
        dlg_otp.Button1.click()
        log_message(f"OTP 코드 입력 완료: {otp_code}")
        logging.info("OTP 코드 입력 완료.")
    except Exception as e:
        log_message(f"[ERR] OTP 입력/확인 클릭 중 오류: {e}")
        return

    time.sleep(3)



if __name__ == "__main__":
    main()


runpy.run_path(str(pathlib.Path(__file__).with_name("report.py")), run_name="__main__")