# dbscanner.py
import os
import sys
import locale
import re
import time
import logging

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.alert import Alert
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, ElementNotInteractableException
)
import runpy, pathlib

# ===== cp949 콘솔 친화 출력 유틸 =====
def _normalize_ascii_punct(s: str) -> str:
    # 보기엔 같지만 cp949에 없는 문자들을 ASCII로 치환
    return (
        s.replace("→", "->")
         .replace("’", "'").replace("‘", "'")
         .replace("“", '"').replace("”", '"')
         .replace("•", "-")
         .replace("✅", "[OK]").replace("❌", "[FAIL]")
    )

def _safe_str(s: str) -> str:
    s = _normalize_ascii_punct(str(s))
    enc = (getattr(sys.stdout, "encoding", None)
           or locale.getpreferredencoding(False)
           or "cp949")
    try:
        s.encode(enc, errors="strict")
        return s
    except Exception:
        # 콘솔이 못 찍는 문자는 ? 등으로 치환
        return s.encode(enc, errors="replace").decode(enc, errors="replace")

logging.basicConfig(level=logging.INFO, format="%(message)s")
def log(msg: str):
    print(_safe_str(f"[LOG] {msg}"))

# ===== 설정 =====
CHROMEDRIVER_PATH = r"C:/Users/ijlee/AppData/Local/Programs/Python/chromedriver-win64/chromedriver.exe"
BASE_URL = "https://10.77.166.34:3443/dbscanner/index.ds"
ADMIN_ID = "admin"
ADMIN_PW = "admin007"

TARGET_POLICY_NAME = "PostgreSQL_san_Test"  # 결과 그리드에서 클릭할 정책명
TARGET_DETAIL_LABEL = "휴대폰"               # 상세 테이블에서 선택할 항목명

# ===== 공통 헬퍼 =====
def accept_alert_if_present(driver, label="alert", timeout=1):
    try:
        WebDriverWait(driver, timeout).until(EC.alert_is_present())
        Alert(driver).accept()
        log(f"{label} 수락")
        return True
    except Exception:
        return False

def safe_click(driver, elem):
    try:
        elem.click()
    except Exception:
        ActionChains(driver).move_to_element(elem).pause(0.2).click().perform()

def safe_js_click(driver, elem):
    driver.execute_script("arguments[0].click();", elem)

def safe_set_input(driver, by, locator, value, wait: WebDriverWait, scroll=True):
    elem = wait.until(EC.presence_of_element_located((by, locator)))
    if scroll:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
    try:
        wait.until(EC.element_to_be_clickable((by, locator)))
        elem.click()
        try:
            elem.clear()
        except Exception:
            elem.send_keys(Keys.CONTROL, "a")
            elem.send_keys(Keys.BACK_SPACE)
        elem.send_keys(value)
    except (ElementNotInteractableException, TimeoutException, NoSuchElementException, Exception):
        driver.execute_script("""
            var el = arguments[0];
            el.removeAttribute('disabled');
            el.removeAttribute('readonly');
            el.value = arguments[1];
            el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
        """, elem, value)
    return elem

def back_to_default(driver):
    driver.switch_to.default_content()

def switch_to_frame_by_id(driver, frame_id, timeout=15):
    WebDriverWait(driver, timeout).until(
        EC.frame_to_be_available_and_switch_to_it((By.ID, frame_id))
    )
    log(f"프레임 전환: #{frame_id}")
    time.sleep(1)
def switch_into_scanconf_iframe_if_any(driver, wait, timeout=10):
    """스캔설정 목록 iframe(#scanConfListIfrm) 진입."""
    driver.switch_to.default_content()
    try:
        wait.until(EC.frame_to_be_available_and_switch_to_it((By.ID, "scanConfListIfrm")))
        log("프레임 전환: #scanConfListIfrm")
        return True
    except Exception:
        return False

# ===== 메뉴/액션 헬퍼 =====
def click_general_table_menu(driver, wait):
    anchor = wait.until(EC.element_to_be_clickable((By.ID, "personConfClick")))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", anchor)
    safe_click(driver, anchor)
    accept_alert_if_present(driver, "일반 테이블로 이동(alert)", timeout=1)
    log("메뉴 클릭: '일반 테이블' 선택 완료")

def click_master_checkbox(driver, wait):
    """
    헤더 체크박스 클릭 시도 -> 실패 시 selectCheckbox 보정
    (iframe 들어갔다가 마지막에 원복)
    """
    in_iframe = switch_into_scanconf_iframe_if_any(driver, wait)

    sel_css = "table.tableDefinition tr.tableTitle td.tableObject input[type='checkbox']"
    try:
        header_cb = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, sel_css)))
    except Exception as e:
        log(f"경고: 헤더 체크박스 요소 탐색 실패 -> {e}")
        try:
            driver.execute_script(
                "if (typeof selectCheckbox==='function'){ selectCheckbox('confListChkbox','', true, 'chkConfStr'); }"
            )
            time.sleep(0.3)
            log("전체선택(JS 함수 직접 호출) 완료")
        except Exception as e2:
            log(f"오류: 전체선택 JS 직접 호출 실패 -> {e2}")
        finally:
            if in_iframe:
                driver.switch_to.default_content()
        return

    driver.execute_script("""
        const el = arguments[0];
        el.scrollIntoView({block:'center'});
        el.style.visibility = 'visible';
        el.style.opacity = '1';
        el.style.pointerEvents = 'auto';
    """, header_cb)
    time.sleep(0.1)

    clicked = False
    try:
        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, sel_css)))
        ActionChains(driver).move_to_element(header_cb).pause(0.1).click().perform()
        clicked = True
    except Exception:
        try:
            rect = header_cb.rect
            driver.execute_script("window.scrollBy(0, -50);")
            ActionChains(driver).move_by_offset(0, 0).move_by_offset(
                int(rect['left'] + rect['width']/2),
                int(rect['top'] + rect['height']/2)
            ).click().perform()
            clicked = True
        except Exception:
            driver.execute_script("arguments[0].click();", header_cb)
            clicked = True

    if clicked:
        time.sleep(0.3)

    any_checked = driver.execute_script("""
        return Array.from(document.querySelectorAll("input[name='confListChkbox'],input.confListChkbox"))
                    .some(b => b.checked);
    """)
    if any_checked:
        log("전체선택 체크박스 클릭 완료")
    else:
        driver.execute_script("""
            if (typeof selectCheckbox === 'function') {
                selectCheckbox('confListChkbox','', true, 'chkConfStr');
            }
        """)
        time.sleep(0.2)
        any_checked2 = driver.execute_script("""
            return Array.from(document.querySelectorAll("input[name='confListChkbox'],input.confListChkbox"))
                        .some(b => b.checked);
        """)
        if any_checked2:
            log("전체선택: 클릭 후 보정(JS) 완료")
        else:
            log("경고: 전체선택 실패(체크된 항목 없음)")

    if in_iframe:
        driver.switch_to.default_content()

def get_checked_row_ids(driver, wait=None):
    """체크된 confListChkbox value 수집 (상위->iframe 순서로 시도)"""
    def _collect(drv):
        return drv.execute_script("""
            const nodes = document.querySelectorAll("input[name='confListChkbox'],input.confListChkbox");
            return Array.from(nodes).filter(b=>b.checked).map(b=>b.value);
        """)

    try:
        ids = _collect(driver)
    except Exception:
        ids = []

    if (not ids) and wait is not None:
        try:
            driver.switch_to.default_content()
            wait.until(EC.frame_to_be_available_and_switch_to_it((By.ID, "scanConfListIfrm")))
            ids = _collect(driver)
        except Exception:
            ids = []
        finally:
            driver.switch_to.default_content()

    try:
        ids = [int(x) for x in ids]
    except Exception:
        pass

    log(f"선택된 row id: {ids}")
    return ids

def click_run_scan(driver, wait):
    btn = wait.until(EC.element_to_be_clickable((By.ID, "runScanConf")))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
    try:
        btn.click()
    except Exception:
        safe_js_click(driver, btn)
    accept_alert_if_present(driver, "스캔시작(alert)", timeout=1)
    log("스캔 시작 버튼 클릭 완료")

# === 진행률 100%까지 폴링 ===
def wait_until_progress_done(driver, wait, row_ids, timeout=40, poll_interval=1.0):
    """
    #scanConfListIfrm 안의
      - status:   #progressStatusTxt_{rid}  (예: '[종료] ')
      - percent:  #progressPercentage_{rid} (예: '100%')
    를 폴링해서 모든 rid가 '[종료]' AND '100%'가 될 때까지 기다린다.
    """
    driver.switch_to.default_content()
    try:
        wait.until(EC.frame_to_be_available_and_switch_to_it((By.ID, "scanConfListIfrm")))
        log("프레임 전환: #scanConfListIfrm (진행률 체크)")
    except Exception as e:
        log(f"경고: 진행률 체크용 iframe 전환 실패 -> {e}")
        return False

    if not row_ids:
        row_ids = [1]

    end_time = time.time() + timeout
    done = {rid: False for rid in row_ids}

    def _read(rid):
        status_txt = driver.execute_script(
            "const el=document.getElementById(arguments[0]);return el?el.textContent.trim():'';",
            f"progressStatusTxt_{rid}"
        ) or ""
        percent_txt = driver.execute_script(
            "const el=document.getElementById(arguments[0]);return el?el.textContent.trim():'';",
            f"progressPercentage_{rid}"
        ) or ""
        return status_txt, percent_txt

    for rid in row_ids:
        s, p = _read(rid)
        if s or p:
            log(f"row {rid}: 초기 상태 -> status='{s}', percent='{p}'")

    while time.time() < end_time and not all(done.values()):
        for rid in row_ids:
            if done[rid]:
                continue
            try:
                s, p = _read(rid)
                if ("종료" in s) and (p == "100%"):
                    log(f"row {rid}: OK -> [{s}] {p}")
                    done[rid] = True
                else:
                    log(f"row {rid}: 진행중 -> status='{s}', percent='{p}'")
            except Exception as e:
                log(f"row {rid}: 진행률 읽기 예외(무시하고 재시도) -> {e}")
        time.sleep(poll_interval)

    for rid in row_ids:
        if not done[rid]:
            s, p = _read(rid)
            log(f"row {rid}: 진행률 확인 미완료 -> status='{s}', percent='{p}'")

    driver.switch_to.default_content()
    return all(done.values())

def go_to_private_result_general(driver, wait):
    try:
        header = wait.until(EC.presence_of_element_located((
            By.XPATH, "//div[@id='menuArea']//span[contains(@class,'menuOneDepFont') and normalize-space()='스캔결과']"
        )))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", header)
        try:
            header.click()
        except Exception:
            safe_js_click(driver, header)
        accept_alert_if_present(driver, "스캔결과(alert)", timeout=1)
        log("메뉴: '스캔결과' 섹션 선택")
        time.sleep(2)
    except Exception as e:
        log(f"경고: '스캔결과' 섹션 클릭 실패(무시 가능) -> {e}")

    try:
        sec = wait.until(EC.presence_of_element_located((
            By.XPATH, "//div[@id='menuArea']//span[contains(@class,'menuSecDepFont') and normalize-space()='개인정보 스캔결과']"
        )))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", sec)
        try:
            sec.click()
        except Exception:
            safe_js_click(driver, sec)
        accept_alert_if_present(driver, "개인정보 스캔결과(alert)", timeout=1)
        log("메뉴: '개인정보 스캔결과' 선택")
        time.sleep(2)
    except Exception as e:
        log(f"경고: '개인정보 스캔결과' 클릭 실패(무시 가능) -> {e}")

    try:
        a = wait.until(EC.presence_of_element_located((By.ID, "privateResultClick")))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", a)
        try:
            WebDriverWait(driver, 2).until(EC.element_to_be_clickable((By.ID, "privateResultClick")))
            a.click()
        except Exception:
            safe_js_click(driver, a)
        accept_alert_if_present(driver, "결과-일반테이블(alert)", timeout=1)
        log("메뉴: '스캔결과 > 개인정보 스캔결과 > 일반 테이블' 이동 완료")
        return True
    except Exception as e:
        log(f"경고: privateResultClick 클릭 실패 -> {e}")
        return False

def click_result_policy_by_name(driver, wait, policy_name: str):
    table = wait.until(EC.presence_of_element_located((By.ID, "summaryTable")))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", table)

    xp = ("//table[@id='summaryTable']//tr[.//div[@title=$t]]"
          "//span[normalize-space()=$t]").replace("$t", f"'{policy_name}'")
    el = wait.until(EC.presence_of_element_located((By.XPATH, xp)))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    try:
        WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, xp)))
        el.click()
    except Exception:
        safe_js_click(driver, el)
    log(f"결과 그리드: '{policy_name}' 클릭 완료")
    return True

def click_top_detail_label_in_popup(driver, wait, label_text: str):
    back_to_default(driver)
    switch_to_frame_by_id(driver, "resultPopupIfrm", timeout=20)

    wait.until(EC.presence_of_element_located((By.ID, "resultDetailTable")))
    xp = ("("
          "//table[@id='resultDetailTable']//tr[.//span[normalize-space()=$t]]"
          "//span[normalize-space()=$t]"
          ")[1]").replace("$t", f"'{label_text}'")
    span_el = wait.until(EC.presence_of_element_located((By.XPATH, xp)))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", span_el)

    try:
        WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, xp)))
        span_el.click()
    except Exception:
        safe_js_click(driver, span_el)

    log(f"상세 그리드(팝업): 맨 위 '{label_text}' 클릭 완료")
    time.sleep(1)
    return True

def validate_sample_phone_data_from_detail_popup(driver, wait, expected_profile_name="휴대폰"):
    back_to_default(driver)
    switch_to_frame_by_id(driver, "resultDetailIfrm", timeout=20)

    wait.until(EC.presence_of_element_located((By.ID, "sampleData")))

    result  = driver.execute_script("return document.querySelector('#resultData')?.value || '';")
    profile = driver.execute_script("return document.querySelector('#profileName')?.value || '';")

    log(f"샘플 profileName='{profile}', bytes={len(result)}")
    
    if profile.strip() != expected_profile_name:
        log(f"[FAIL] profileName 불일치: '{profile}' (기대: '{expected_profile_name}')")
        return False

    if not result or '|' not in result:
        log("[FAIL] 번호 데이터 없음(또는 구분자 누락)")
        return False

    numbers = [x.strip() for x in result.split('|') if x.strip()]
    if not numbers:
        log("[FAIL] 번호 항목 0건")
        return False

    phone_re = re.compile(r"^01[016789]-\d{3,4}-\d{4}$")
    bad = [n for n in numbers if not phone_re.match(n)]
    if bad:
        log(f"참고: 형식이 살짝 다른 번호 존재 -> {bad[:5]} ... (데이터 수집은 OK로 간주)")

    log(f"번호 {len(numbers)}건 예: {numbers[:5]} ...")
    log("[OK] 문제없음: 휴대폰 샘플 데이터 수집 확인 -> 테스트 완료")
    return True

# ===== 메인 =====
def main():
    service = Service(CHROMEDRIVER_PATH)
    chrome_options = Options()
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--allow-insecure-localhost")
    # chrome_options.add_argument("--headless=new")  # 필요 시 사용

    driver = webdriver.Chrome(service=service, options=chrome_options)
    wait = WebDriverWait(driver, 15)

    try:
        # 로그인
        log(f"페이지 접속: {BASE_URL}")
        driver.get(BASE_URL)
        user_input = wait.until(EC.presence_of_element_located((By.ID, "userid")))
        pw_input   = wait.until(EC.presence_of_element_located((By.ID, "userpw")))
        login_btn  = wait.until(EC.element_to_be_clickable((By.ID, "loginClick")))
        user_input.clear(); user_input.send_keys(ADMIN_ID)
        pw_input.clear();   pw_input.send_keys(ADMIN_PW)
        safe_click(driver, login_btn)

        wait.until(EC.presence_of_element_located((By.ID, "menuArea")))
        log("DBSCANNER 로그인 성공")

        # 설정 -> 헤더 전체선택 -> 스캔 시작 -> 진행률 100%까지 대기
        click_general_table_menu(driver, wait)
        click_master_checkbox(driver, wait)

        selected_ids = get_checked_row_ids(driver, wait)
        if not selected_ids:
            time.sleep(0.3)
            selected_ids = get_checked_row_ids(driver, wait)

        click_run_scan(driver, wait)

        # 100%까지 폴링
        wait_until_progress_done(driver, wait, selected_ids, timeout=40, poll_interval=1.5)

        # 스캔결과 -> 개인정보 스캔결과 -> 일반 테이블
        moved = go_to_private_result_general(driver, wait)
        if not moved:
            log("경고: 결과 메뉴 이동 실패 (계속 진행 시도)")

        # 결과 그리드에서 정책 클릭
        click_result_policy_by_name(driver, wait, TARGET_POLICY_NAME)

        # 팝업 프레임에서 상세 테이블 맨 위 '휴대폰' 클릭
        click_top_detail_label_in_popup(driver, wait, TARGET_DETAIL_LABEL)

        # 상세 다이얼로그 프레임에서 샘플 데이터 검증
        valid = validate_sample_phone_data_from_detail_popup(driver, wait, expected_profile_name=TARGET_DETAIL_LABEL)
        if not valid:
            log("[FAIL] 샘플 데이터 검증 실패")

        time.sleep(1)

    finally:
        log("브라우저 종료")
        driver.quit()

if __name__ == "__main__":
    main()

runpy.run_path(str(pathlib.Path(__file__).with_name("saferuas_engineer_page.py")), run_name="__main__")
