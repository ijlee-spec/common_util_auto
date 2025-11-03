# -*- coding: utf-8 -*-
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from PIL import Image
import pytesseract
import cv2
import datetime
import time
import re
import config
import runpy, pathlib
import sys, traceback, unicodedata, os

# (선택) Tesseract 경로 고정 (윈도우 설치 경로 사용 시 주석 해제)
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


# ----- 인코딩 안전 로그 유틸 -----
def _sanitize_unicode(s: str) -> str:
    """콘솔(cp949)에서 못 찍는 문자를 안전하게 치환"""
    # 정규화
    s = unicodedata.normalize('NFKC', s)
    # 대시/마이너스 계열을 ASCII로 통일
    s = (s.replace('\u2013', '-')     # en dash
           .replace('\u2014', '-')     # em dash
           .replace('\u2212', '-')     # minus sign
           .replace('\u00A0', ' '))    # non-breaking space
    return s

def log_message(message: str):
    msg = f"[LOG] {_sanitize_unicode(str(message))}"
    try:
        print(msg)
    except Exception:
        # cp949 콘솔에서도 절대 죽지 않게 강제 치환 출력
        safe = msg.encode('cp949', 'replace').decode('cp949')
        print(safe)

# ----- 브라우저 캡처/이미지 처리 -----
def capture_browser_only(driver, file_path="chart_full.png"):
    driver.save_screenshot(file_path)
    return file_path

def extract_report_area(image_path, cropped_path="cropped_report.png"):
    image = cv2.imread(image_path)
    if image is None:
        raise RuntimeError(f"이미지 로드 실패: {image_path}")
    h, w, _ = image.shape
    cropped = image[int(h * 0.2):, :]  # 상단 20% 제외
    cv2.imwrite(cropped_path, cropped)
    return cropped_path

def preprocess_image_for_ocr(image_path, output_path="preprocessed.png"):
    img = cv2.imread(image_path)
    if img is None:
        raise RuntimeError(f"이미지 로드 실패: {image_path}")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # 가벼운 이진화 (필요 시 adaptiveThreshold로 변경 가능)
    _, thresh = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY)
    cv2.imwrite(output_path, thresh)
    return output_path

SHOW_OCR_TEXT = False  # True면 전체 OCR 텍스트 출력, False면 헤더만 출력

def contains_ip_labels(image_path, ip_threshold=1):
    img = Image.open(image_path)
    text = pytesseract.image_to_string(img, config="--psm 6")

    # 🔈 OCR 텍스트 출력 제어
    if SHOW_OCR_TEXT:
        log_message(f"[오즈뷰어 인식 텍스트~!]\n{_sanitize_unicode(text)}")
    else:
        log_message("[오즈뷰어 인식 텍스트~!]")

    ip_matches = re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', text)
    log_message(f"📌 감지된 IP 주소 수: {len(ip_matches)} - {ip_matches}")
    return len(ip_matches) >= ip_threshold

def wait_document_ready(driver, timeout=20):
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )

# ----- ChromeDriver 설정 -----
service = Service(r'C:/Users/ijlee/AppData/Local/Programs/Python/chromedriver-win64/chromedriver.exe')
chrome_options = Options()
chrome_options.add_argument('--ignore-certificate-errors')
chrome_options.add_argument('--allow-insecure-localhost')
# chrome_options.add_argument('--headless=new')  # 필요 시 헤드리스

driver = None
wait = None

# 실패 스크린샷 폴더
os.makedirs("screens", exist_ok=True)

try:
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.set_page_load_timeout(60)
    wait = WebDriverWait(driver, 20)

    # 접속
    url = f"https://{config.hostname}:3443/webreport/index.ds"
    log_message(f"접속: {url}")
    driver.get(url)
    wait_document_ready(driver)
    time.sleep(0.8)

    # 로그인
    wait.until(EC.presence_of_element_located((By.ID, 'userId'))).send_keys(config.e_id)
    driver.find_element(By.ID, 'userPw').send_keys(config.e_pw)
    driver.find_element(By.ID, 'loginSubmitBtn').click()
    log_message("로그인 완료")
    wait_document_ready(driver)
    time.sleep(1.0)

    # 메뉴 클릭
    wait.until(EC.element_to_be_clickable((By.XPATH, "//span[@class='oneDepFont' and text()='DBMS 보고서']"))).click()
    log_message("DBMS 보고서 클릭")
    time.sleep(0.8)

    wait.until(EC.element_to_be_clickable((By.XPATH, "//span[@class='secondDepFont' and text()='접속제어 요약']"))).click()
    log_message("접속제어 요약 클릭")
    time.sleep(0.8)

    try:
        service_criteria = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "#menu_B101 a")))
        driver.execute_script("arguments[0].click();", service_criteria)
        log_message("서비스명 기준 클릭 성공 (menu_B101)")
    except Exception as e:
        fail_png = os.path.join("screens", "service_click_fail.png")
        driver.save_screenshot(fail_png)
        log_message(f"[오류] 서비스명 기준 클릭 실패: {e} (스크린샷: {fail_png})")
        raise

    # 날짜 설정
    today = datetime.date.today().strftime('%Y-%m-%d')
    for el_id, val in (('strDate', '2025-10-23'), ('endDate', today)):
        el = wait.until(EC.presence_of_element_located((By.ID, el_id)))
        el.clear()
        el.send_keys(val)
    log_message(f"날짜 설정 완료: (오늘: {today})")
    time.sleep(0.5)

    # 조회 버튼 클릭
    inquiry_btn = wait.until(EC.element_to_be_clickable((By.ID, 'inquiryButton')))
    driver.execute_script("arguments[0].click();", inquiry_btn)
    log_message("조회 버튼 클릭 완료")

    # 데이터 로딩 대기 (필요 시 스피너/그리드 로딩 완료 조건으로 교체)
    time.sleep(13)

    # 스크롤 하단 이동
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    log_message("스크롤 하단으로 이동 완료")
    time.sleep(2)

    # 캡처 및 분석
    capture_path = capture_browser_only(driver, file_path=os.path.join("screens", "chart_full.png"))
    cropped_path = extract_report_area(capture_path, cropped_path=os.path.join("screens", "cropped_report.png"))
    processed_path = preprocess_image_for_ocr(cropped_path, output_path=os.path.join("screens", "preprocessed.png"))

    if contains_ip_labels(processed_path, ip_threshold=1):
        log_message("✅ 입체 막대 그래프가 포함되어 있습니다 (IP 감지 기반).")
    else:
        log_message("❌ 데이터가 없거나 pnp_statistics 실행 필요")

    log_message("자동화 정상 종료")

except Exception as e:
    # 예외 상세 + 스크린샷
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    png = os.path.join("screens", f"fail_{ts}.png")
    try:
        if driver:
            driver.save_screenshot(png)
    except Exception:
        pass
    err_text = "".join(traceback.format_exception(*sys.exc_info())).strip()
    log_message(f"[스크립트 실패] {err_text}\n(스크린샷: {png})")

finally:
    # 항상 브라우저/서비스 종료
    if driver:
        try:
            driver.quit()
            log_message("브라우저 정상 종료")
        except Exception as e:
            log_message(f"브라우저 종료 중 오류: {e}")
    try:
        service.stop()
    except Exception:
        pass
   
    try:
        runpy.run_path(str(pathlib.Path(__file__).with_name("err_log.py")), run_name="__main__")
    except Exception as e:
        log_message(f"err_log_safer.py 실행 실패: {e}")



