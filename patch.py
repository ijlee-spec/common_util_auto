import os
import re
import time
import glob
import yaml
import shutil
import pathlib
import paramiko
import requests
from urllib.parse import urljoin
from scp import SCPClient

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# ======================= 사용자 설정 =======================
PAGE_URL = "https://bbs.pnpsecure.com/showthread.php?tid=12342"
BBS_BASE = "https://bbs.pnpsecure.com/"
BBS_LOGIN_URL = "https://bbs.pnpsecure.com/member.php?action=login"
BBS_USER = "ijlee"
BBS_PASS = "dbsafer00@"

DOWNLOAD_DIR = r"C:\Users\ijlee\Downloads"
CHROMEDRIVER_PATH = r"C:\Users\ijlee\AppData\Local\Programs\Python\chromedriver-win64\chromedriver.exe"
CONFIG_YAML = str((pathlib.Path(__file__).parent / "config.yaml").resolve())
REMOTE_DIR = "/root/patch_commonutil"
# ==========================================================

# 파일명 패턴 (버전 1~2자리 허용)
FNAME_RE = re.compile(r"COMMON_UTIL_V3\.\d{1,2}_(\d{8})\.tgz", re.IGNORECASE)
FNAME_FULL_RE = re.compile(r"(COMMON_UTIL_V3\.\d{1,2}_\d{8}\.tgz)", re.IGNORECASE)


def load_ssh_config(config_path: str):
    if not os.path.isabs(config_path):
        config_path = os.path.abspath(config_path)
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"config.yaml을 찾을 수 없습니다: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    ssh = data.get("ssh", {})
    for k in ("host", "port", "username", "password"):
        if k not in ssh:
            raise RuntimeError(f"config.yaml의 ssh.{k} 값이 없습니다.")
    return ssh


def make_chrome_driver(download_dir: str):
    chrome_opts = Options()
    chrome_opts.add_argument("--ignore-certificate-errors")
    chrome_opts.add_argument("--allow-insecure-localhost")
    chrome_opts.add_argument("--test-type")
    # chrome_opts.add_argument("--headless=new")
    chrome_opts.add_experimental_option("prefs", {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "profile.default_content_settings.popups": 0,
        "safebrowsing.enabled": True,
        "safebrowsing_for_trusted_sources_enabled": False,
    })
    chrome_opts.set_capability("acceptInsecureCerts", True)
    service = Service(CHROMEDRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=chrome_opts)
    driver.set_page_load_timeout(120)
    return driver


def bypass_ssl_interstitial(driver, timeout=10):
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_all_elements_located((By.TAG_NAME, "body"))
        )
    except TimeoutException:
        return
    try:
        details = WebDriverWait(driver, 2).until(
            EC.element_to_be_clickable((By.ID, "details-button"))
        )
        details.click()
        proceed = WebDriverWait(driver, 2).until(
            EC.element_to_be_clickable((By.ID, "proceed-link"))
        )
        proceed.click()
        return
    except Exception:
        pass
    try:
        driver.find_element(By.TAG_NAME, "body").send_keys("thisisunsafe")
    except Exception:
        pass


def login_to_bbs(driver):
    print("[로그인] BBS 로그인 시도")
    driver.get(BBS_LOGIN_URL)
    bypass_ssl_interstitial(driver)
    try:
        WebDriverWait(driver, 12).until(EC.presence_of_element_located((By.NAME, "username")))
        driver.find_element(By.NAME, "username").send_keys(BBS_USER)
        driver.find_element(By.NAME, "password").send_keys(BBS_PASS)
        driver.find_element(By.CSS_SELECTOR, "input.button").click()
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        print("[로그인] 로그인 성공")
    except Exception as e:
        raise RuntimeError(f"BBS 로그인 실패: {e}")


def find_latest_link(driver):
    driver.get(PAGE_URL)
    bypass_ssl_interstitial(driver)
    try:
        WebDriverWait(driver, 15).until(EC.presence_of_all_elements_located((By.TAG_NAME, "a")))
    except TimeoutException:
        pass

    links = driver.find_elements(By.TAG_NAME, "a")
    candidates = []
    for a in links:
        try:
            text = (a.text or "").strip()
            href = (a.get_attribute("href") or "").strip()
        except Exception:
            continue
        m_txt = FNAME_RE.search(text)
        m_href = FNAME_RE.search(href)
        m = m_txt or m_href
        if m:
            yyyymmdd = m.group(1)
            filename = m.group(0)
            candidates.append({
                "elem": a,
                "text": text,
                "href": href,
                "filename": filename,
                "yyyymmdd": yyyymmdd
            })

    if not candidates:
        html = driver.page_source or ""
        full_hits = list(set(FNAME_FULL_RE.findall(html)))
        if full_hits:
            parsed = []
            for fn in full_hits:
                m = FNAME_RE.search(fn)
                if m:
                    parsed.append((fn, m.group(1)))
            parsed.sort(key=lambda t: t[1], reverse=True)
            if parsed:
                best_name, best_date = parsed[0]
                for a in driver.find_elements(By.TAG_NAME, "a"):
                    t = (a.text or "")
                    h = (a.get_attribute("href") or "")
                    if best_name in t or best_name in h:
                        return {
                            "elem": a,
                            "text": t,
                            "href": h,
                            "filename": best_name,
                            "yyyymmdd": best_date
                        }
        raise RuntimeError("페이지에서 COMMON_UTIL_V3.*.tgz 링크를 찾지 못했습니다.")

    candidates.sort(key=lambda x: x["yyyymmdd"], reverse=True)
    return candidates[0]


def move_old_downloads(download_dir: str) -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    stash_dir = os.path.join(download_dir, f"_old_common_util_{ts}")
    os.makedirs(stash_dir, exist_ok=True)
    moved = False
    for p in glob.glob(os.path.join(download_dir, "COMMON_UTIL_V3.*.tgz")):
        try:
            shutil.move(p, os.path.join(stash_dir, os.path.basename(p)))
            moved = True
        except Exception:
            pass
    if moved:
        print(f"  - 기존 파일 이동: {stash_dir}")
    else:
        try:
            os.rmdir(stash_dir)
        except OSError:
            pass
        stash_dir = ""
    return stash_dir


def wait_for_actual_new_download(download_dir: str, start_time: float, timeout_sec: int = 900):
    end = time.time() + timeout_sec
    saw_cr = False
    final_path = None
    while time.time() < end:
        for p in glob.glob(os.path.join(download_dir, "*.crdownload")):
            try:
                if os.path.getmtime(p) >= start_time:
                    saw_cr = True
            except FileNotFoundError:
                pass
        tgzs = []
        for p in glob.glob(os.path.join(download_dir, "*.tgz")):
            try:
                if os.path.getmtime(p) >= start_time:
                    tgzs.append(p)
            except FileNotFoundError:
                pass
        if saw_cr and tgzs and not glob.glob(os.path.join(download_dir, "*.crdownload")):
            tgzs.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            final_path = tgzs[0]
            break
        time.sleep(0.5)
    if not final_path:
        raise TimeoutError("새 다운로드가 감지되지 않았거나 완료되지 않았습니다.")
    return final_path


def selenium_cookies_to_requests(driver) -> requests.Session:
    sess = requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0"})
    for c in driver.get_cookies():
        domain = c.get("domain", "").lstrip(".")
        if not domain:
            domain = "bbs.pnpsecure.com"
        sess.cookies.set(c["name"], c.get("value", ""), domain=domain, path=c.get("path", "/"))
    return sess

#1234556

def download_via_requests_with_cookies(session: requests.Session, attachment_href: str, referer: str, save_path: str):
    url = urljoin(BBS_BASE, attachment_href) if not attachment_href.lower().startswith("http") else attachment_href
    headers = {"Referer": referer, "User-Agent": "Mozilla/5.0"}
    with session.get(url, headers=headers, stream=True, timeout=120, verify=False) as r:
        r.raise_for_status()
        disp = r.headers.get("Content-Disposition", "")
        if "filename=" in disp and disp.count(".tgz") >= 1:
            fn = disp.split("filename=")[-1].strip('"; ')
            if fn:
                save_path = os.path.join(os.path.dirname(save_path), fn)
        with open(save_path, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    f.write(chunk)
    return save_path


def ssh_exec(ssh: paramiko.SSHClient, cmd: str, check=True):
    stdin, stdout, stderr = ssh.exec_command(cmd)
    out = stdout.read().decode("utf-8", errors="ignore")
    err = stderr.read().decode("utf-8", errors="ignore")
    rc = stdout.channel.recv_exit_status()
    if check and rc != 0:
        raise RuntimeError(f"Command failed (rc={rc}): {cmd}\nSTDOUT:\n{out}\nSTDERR:\n{err}")
    return rc, out, err


def ensure_scp(ssh: paramiko.SSHClient):
    return SCPClient(ssh.get_transport())


# ---------- 새로 추가: 버전 정보 조회 공용 함수 ----------
def read_version_info(ssh: paramiko.SSHClient):
    """
    /usr/local/apache/bin/version.sh 실행 결과에서
    'Server version:'과 'JVM Version:' 두 줄을 파싱해 반환
    """
    _, out, _ = ssh_exec(ssh, "bash -lc 'cd /usr/local/apache/bin && ./version.sh'", check=False)
    server_version = None
    jvm_version = None
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("Server version:"):
            server_version = s
        elif s.startswith("JVM Version:"):
            jvm_version = s
    return server_version or "Server version: (파싱 실패)", jvm_version or "JVM Version: (파싱 실패)"


def main():
    # 0) SSH 설정
    print(f"[0/10] SSH 설정 로드: {CONFIG_YAML}")
    ssh_cfg = load_ssh_config(CONFIG_YAML)

    # 1) SSH 접속 & (패치 전) 현재 버전 출력
    print(f"[1/10] SSH 접속: {ssh_cfg['host']}:{ssh_cfg['port']} (user={ssh_cfg['username']})")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        ssh_cfg["host"],
        port=int(ssh_cfg["port"]),
        username=ssh_cfg["username"],
        password=ssh_cfg["password"],
        look_for_keys=False,
        allow_agent=False
    )

    try:
        print("[2/10] (패치 전) 현재 버전 확인 (/usr/local/apache/bin/version.sh)")
        before_server, before_jvm = read_version_info(ssh)
        print("\n==== BEFORE ====")
        print(before_server)
        print(before_jvm)
        print("===============")

        # 2) 브라우저/로그인 및 다운로드
        print(f"[3/10] 브라우저로 페이지 접속 → {PAGE_URL}")
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        driver = make_chrome_driver(DOWNLOAD_DIR)

        try:
            login_to_bbs(driver)

            # 3-1) 기존 파일 스태시
            _ = move_old_downloads(DOWNLOAD_DIR)

            # 4) 최신 링크 식별
            latest = find_latest_link(driver)
            print(f"  - 최신판: {latest['filename']} (날짜: {latest['yyyymmdd']})")
            print(f"  - 링크: {latest['href'] or '(직접 다운로드 링크 아님)'}")

            # 5) 클릭 → 새 탭 전환 처리
            print(f"[4/10] 다운로드 시작 (Chrome 자동 저장 폴더: {DOWNLOAD_DIR})")
            start_time = time.time()
            before_handles = set(driver.window_handles)
            latest["elem"].click()

            time.sleep(0.8)
            after_handles = set(driver.window_handles)
            new_handles = list(after_handles - before_handles)
            if new_handles:
                driver.switch_to.window(new_handles[-1])

            # 6) 실제 새 다운로드 대기
            try:
                local_file = wait_for_actual_new_download(DOWNLOAD_DIR, start_time, timeout_sec=900)
                print(f"  - 다운로드 완료(첫 시도): {os.path.basename(local_file)} ({os.path.getsize(local_file)/(1024*1024):.2f} MB)")
            except TimeoutError:
                print("  - 클릭 다운로드 감지 실패 → 쿠키 이관 후 직접 GET 재시도")
                sess = selenium_cookies_to_requests(driver)
                tentative = os.path.join(DOWNLOAD_DIR, latest["filename"])
                local_file = download_via_requests_with_cookies(sess, latest["href"], PAGE_URL, tentative)
                print(f"  - 직접 다운로드 완료: {os.path.basename(local_file)} ({os.path.getsize(local_file)/(1024*1024):.2f} MB)")

            # 7) 파일명 검증(날짜 불일치 시 재다운로드)
            m_local = FNAME_RE.search(os.path.basename(local_file))
            if not m_local or m_local.group(1) != latest["yyyymmdd"]:
                print(f"  - 경고: 받은 파일명이 최신 날짜와 불일치 → 쿠키 이관 후 재다운로드 강제")
                sess = selenium_cookies_to_requests(driver)
                tentative = os.path.join(DOWNLOAD_DIR, latest["filename"])
                local_file = download_via_requests_with_cookies(sess, latest["href"], PAGE_URL, tentative)
                print(f"  - 재다운로드 완료: {os.path.basename(local_file)} ({os.path.getsize(local_file)/(1024*1024):.2f} MB)")

        finally:
            try:
                driver.quit()
            except Exception:
                pass

        # 8) SSH 업로드/설치
        print(f"[5/10] 원격 디렉터리 준비 → {REMOTE_DIR}")
        ssh_exec(ssh, f"mkdir -p {REMOTE_DIR}")

        print(f"  - 업로드: {local_file} → {REMOTE_DIR}/")
        with ensure_scp(ssh) as scp:
            scp.put(local_file, remote_path=REMOTE_DIR + "/")

        remote_fname = os.path.basename(local_file)
        stem = re.sub(r"\.tgz$", "", remote_fname, flags=re.IGNORECASE)
        extract_dir = f"{REMOTE_DIR}/{stem}"

        print(f"  - 압축 해제: {remote_fname} → {extract_dir}")
        ssh_exec(ssh, f"bash -lc 'cd {REMOTE_DIR} && tar -xzf {remote_fname}'")

        print("[6/10] 서비스 정지 (pnp_statistics, pnpweb)")
        ssh_exec(ssh, "bash -lc 'cd /dbsafer && ./pnp_statistics stop || true'")
        time.sleep(4)
        ssh_exec(ssh, "bash -lc 'service pnpweb stop || true'")
        time.sleep(5)

        print("[7/10] 설치 실행: source ./install.sh -upgrade")
        ssh_exec(ssh, f"bash -lc 'cd {extract_dir} && source ./install.sh -upgrade'")
        time.sleep(3)

        print("[8/10] 서비스 시작: pnpweb start")
        ssh_exec(ssh, "bash -lc 'service pnpweb start || true'")
        time.sleep(4)

        # 9) (패치 후) 버전 재확인
        print("[9/10] (패치 후) 버전 확인 (/usr/local/apache/bin/version.sh)")
        after_server, after_jvm = read_version_info(ssh)

        # 10) 결과 출력
        print("\n==== RESULT (AFTER) ====")
        print(after_server)
        print(after_jvm)
        print("========================")
        print("COMMON_UTIL 패치 완료")

    finally:
        ssh.close()


if __name__ == "__main__":
    main()
