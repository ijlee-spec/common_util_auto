# saferuas_engineer_page.py
import os
import time
import yaml
import logging

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.alert import Alert
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementNotInteractableException
import runpy, pathlib

# ======= 절대 경로로 config.yaml 지정 =======
CONFIG_PATH = r"C:\Users\ijlee\AppData\Local\Programs\Python\2025\auto\config.yaml"

# =========================
# 1) CONFIG 로드/해석
# =========================
def load_config(path=CONFIG_PATH):
    if not os.path.exists(path):
        raise FileNotFoundError(f"config.yaml 파일을 찾을 수 없습니다: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def resolve_hostname(cfg: dict) -> str:
    if cfg.get("hostname"):
        return cfg["hostname"]
    if cfg.get("server", {}).get("hostname"):
        return cfg["server"]["hostname"]
    if cfg.get("ssh", {}).get("host"):
        return cfg["ssh"]["host"]
    raise KeyError("호스트명을 config.yaml에서 찾지 못했습니다.")

def resolve_engineer_creds(cfg: dict):
    eng = cfg.get("eng_page", {})
    e_id = eng.get("engin_id")
    e_pw = eng.get("engin_pass")
    if not e_id or not e_pw:
        raise KeyError("config.yaml의 eng_page.engin_id / eng_page.engin_pass 값이 필요합니다.")
    return e_id, e_pw

def resolve_pnp_otp(cfg: dict):
    otp = cfg.get("pnp_otp", {})
    send_method = str(otp.get("send_method", "1"))
    rule_time   = str(otp.get("rule_time", "120"))
    site_key    = str(otp.get("site_key", "WEDQN97H"))
    return send_method, rule_time, site_key

# =========================
# 2) 로깅
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s"
)
def log_message(msg):
    print(f"[LOG] {msg}")
  

# =========================
# 3) 유틸/헬퍼
# =========================
def accept_alert_if_present(wait: WebDriverWait, label: str = "alert", timeout=1):
    try:
        short_wait = WebDriverWait(wait._driver, timeout)
        short_wait.until(EC.alert_is_present())
        Alert(wait._driver).accept()
        log_message(f"{label} 수락")
        return True
    except Exception:
        return False

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
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
        """, elem, value)
    return elem

def click_with_mouse(driver, element):
    actions = ActionChains(driver)
    actions.move_to_element(element).pause(0.2).click().perform()

def ensure_detail_open(driver, wait: WebDriverWait):
    """
    상세 설정 버튼(#sauthDetail) 강제 오픈
    """
    try:
        detail = wait.until(EC.presence_of_element_located((By.ID, "sauthDetail")))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", detail)
        click_with_mouse(driver, detail)
        accept_alert_if_present(wait, "상세 설정 전환(alert)", timeout=1)
        wait.until(EC.presence_of_element_located((By.ID, "sauthOtpType")))
        log_message("상세 설정: 패널 열림 완료")
    except Exception:
        log_message("경고: 상세 설정 열기 실패")

def fast_switch_to_pnp(driver, wait, timeout=1):
    """
    OTP 유형을 PNP(5)로 빠르게 전환 (JS 직접 세팅 + 짧은 alert 확인)
    """
    try:
        # JS로 select 값 변경
        driver.execute_script("""
            var sel = document.getElementById("sauthOtpType");
            if (sel) {
                sel.value = "5";
                sel.dispatchEvent(new Event('change', {bubbles:true}));
            }
        """)
        # 알럿 확인 (짧게)
        accept_alert_if_present(wait, "OTP 유형 전환(alert)", timeout=timeout)
        # PNP 영역 보이는지 확인
        WebDriverWait(driver, 2).until(
            EC.visibility_of_element_located((By.ID, "pnpOtpArea"))
        )
        log_message("PNP OTP 탭 활성화 완료 (fast)")
    except Exception as e:
        log_message(f"경고: PNP OTP 전환 실패 → {e}")

# =========================
# 4) 메인 로직
# =========================
def main():
    cfg = load_config(CONFIG_PATH)
    hostname = resolve_hostname(cfg)
    e_id, e_pw = resolve_engineer_creds(cfg)
    pnp_send_method, pnp_rule_time, pnp_site_key = resolve_pnp_otp(cfg)

    service = Service(r"C:/Users/ijlee/AppData/Local/Programs/Python/chromedriver-win64/chromedriver.exe")
    chrome_options = Options()
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--allow-insecure-localhost")

    driver = webdriver.Chrome(service=service, options=chrome_options)
    wait = WebDriverWait(driver, 15)

    try:
        url = f"https://{hostname}:3443/saferuas/engineer"
        log_message(f"페이지 접속: {url}")
        driver.get(url)

        # 로그인
        username_input = wait.until(EC.presence_of_element_located((By.ID, "userId")))
        password_input = wait.until(EC.presence_of_element_located((By.ID, "userPw")))
        login_button   = wait.until(EC.element_to_be_clickable((By.ID, "loginBt")))

        username_input.clear(); username_input.send_keys(e_id)
        password_input.clear(); password_input.send_keys(e_pw)
        login_button.click()

        wait.until(EC.presence_of_element_located((By.ID, "sauthType_user")))
        log_message("엔지니어 페이지 로그인 성공")

        # '보안계정 별 2차인증' 체크
        try:
            sauth_checkbox = driver.find_element(By.ID, "sauthType_user")
            if not sauth_checkbox.is_selected():
                sauth_checkbox.click()
                log_message("'보안계정 별 2차인증' 체크 ON")
            else:
                log_message("'보안계정 별 2차인증' 이미 체크됨")
        except NoSuchElementException:
            log_message("경고: 'sauthType_user' 없음")

        # 기본 OTP 유효 시간 120
        try:
            safe_set_input(driver, By.ID, "otpRuleTime", "120", wait)
            log_message("OTP 유효 시간(기본) 120으로 설정")
        except Exception:
            log_message("참고: otpRuleTime 없음")

        # 상세 설정 열기
        ensure_detail_open(driver, wait)

        # OTP 유형을 PNP(5)로 전환 (빠른 버전)
        fast_switch_to_pnp(driver, wait)

        # PNP OTP 설정
        try:
            pnp_area = wait.until(EC.visibility_of_element_located((By.ID, "pnpOtpArea")))
            safe_set_input(driver, By.ID, "pnpOtpRuleTime", pnp_rule_time, wait)
            log_message(f"PNP OTP 유효 시간 {pnp_rule_time}으로 설정")
            safe_set_input(driver, By.ID, "pnpOtpSiteKey", pnp_site_key, wait)
            log_message(f"PNP OTP Site Key 설정 완료 ({pnp_site_key})")
            sel = driver.find_element(By.ID, "pnpOtpSendMethod")
            Select(sel).select_by_value(pnp_send_method)
            log_message(f"PNP OTP 키 발급 방식 설정 완료 (value={pnp_send_method})")
        except Exception as e:
            log_message(f"경고: PNP OTP 설정 실패 → {e}")

        # 저장
        try:
            save_btn = wait.until(EC.element_to_be_clickable((By.ID, "saveBt")))
            save_btn.click()
            accept_alert_if_present(wait, "저장 확인(alert)", timeout=1)
            log_message("설정 저장 완료")
        except Exception:
            log_message("경고: 저장 실패")

    finally:
        time.sleep(2)
        log_message("엔지니어 페이지 종료")
        driver.quit()

if __name__ == "__main__":
    main()

runpy.run_path(str(pathlib.Path(__file__).with_name("pcassist_login.py")), run_name="__main__")

