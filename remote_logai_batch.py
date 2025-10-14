#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
원격 서버 로그를 '최근 N분(기본 4분)' 범위만 단발성 분석 (룰 + LLM).
- 서버 시간/타임존 기준 [now - recent_minutes, now]만 수집
- 레벨 게이팅(require_level)로 DEBUG 노이즈 차단
- 룰 정확 매칭 우선, 없으면 LLM 판단, 그래도 없으면 키워드 백업
- 핵심 로그 그룹(RULE/LEVEL/KEYWORD)별 최대 4줄 출력
- 에러 없으면 최근 3줄 샘플 출력

필수: paramiko, pyyaml
외부: rules_engine.RulesEngine, llm_judge.LLMJudge
"""

from __future__ import annotations

import os
import re
import yaml
import paramiko
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Tuple, Dict, Any

# 외부 모듈(사용자 제공)
from rules_engine import RulesEngine
from llm_judge import LLMJudge

# ---------------- Timestamp parsing ----------------
MONTHS = {m: i for i, m in enumerate(
    ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"], start=1)}

TS_PATTERNS = [
    # 2025-08-27 09:53:21,123 / .123
    (re.compile(r"(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})[ T](?P<H>\d{2}):(?P<M>\d{2}):(?P<S>\d{2})(?:[.,](?P<f>\d{1,6}))?"), "ymd_hmsf"),
    # 2025-08-27T00:53:53.340Z / +09:00
    (re.compile(r"(?P<iso>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))"), "iso"),
    # 27-Aug-2025 09:53:21.123
    (re.compile(r"(?P<d>\d{2})-(?P<mon>[A-Za-z]{3})-(?P<y>\d{4})[ T](?P<H>\d{2}):(?P<M>\d{2}):(?P<S>\d{2})(?:\.(?P<f>\d{1,6}))?"), "dmy_mon"),
    # Aug 27, 2025 9:53:21 AM
    (re.compile(r"(?P<mon>[A-Za-z]{3})\s+(?P<d>\d{1,2}),\s*(?P<y>\d{4})\s+(?P<h>\d{1,2}):(?P<M>\d{2}):(?P<S>\d{2})\s*(?P<ampm>AM|PM)"), "mdy_ampm"),
    # Aug 27 09:53:21 (year 없음 → 현재 연도 가정)
    (re.compile(r"(?P<mon>[A-Za-z]{3})\s+(?P<d>\d{1,2})\s+(?P<H>\d{2}):(?P<M>\d{2}):(?P<S>\d{2})"), "mon_d_hms"),
    # 10:00:00.007  (날짜 없음 → 서버 '오늘' 가정)
    (re.compile(r"(?P<H>\d{2}):(?P<M>\d{2}):(?P<S>\d{2})(?:[.,](?P<f>\d{1,6}))?"), "hmsf_only"),
]

def parse_line_epoch(line: str, server_tz: timezone, assumed_year: int, today: Optional[datetime] = None) -> Optional[int]:
    """한 라인에서 타임스탬프를 찾아 epoch(초)로 변환. 실패 시 None."""
    line = line.strip()
    for rx, kind in TS_PATTERNS:
        m = rx.search(line)
        if not m:
            continue
        try:
            if kind == "ymd_hmsf":
                y=int(m["y"]); mo=int(m["m"]); d=int(m["d"])
                H=int(m["H"]); Mi=int(m["M"]); S=int(m["S"])
                f=int((m["f"] or "0").ljust(6,"0"))
                dt = datetime(y,mo,d,H,Mi,S,f,tzinfo=server_tz)
                return int(dt.timestamp())

            if kind == "iso":
                s = m["iso"]
                if s.endswith("Z"): s = s[:-1] + "+00:00"
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=server_tz)
                return int(dt.timestamp())

            if kind == "dmy_mon":
                y=int(m["y"]); mon=MONTHS[m["mon"].title()]; d=int(m["d"])
                H=int(m["H"]); Mi=int(m["M"]); S=int(m["S"])
                f=int((m["f"] or "0").ljust(6,"0"))
                dt = datetime(y,mon,d,H,Mi,S,f,tzinfo=server_tz)
                return int(dt.timestamp())

            if kind == "mdy_ampm":
                y=int(m["y"]); mon=MONTHS[m["mon"].title()]; d=int(m["d"])
                h=int(m["h"]); Mi=int(m["M"]); S=int(m["S"])
                ampm = m["ampm"]
                if ampm=="PM" and h!=12: h+=12
                if ampm=="AM" and h==12: h=0
                dt = datetime(y,mon,d,h,Mi,S,tzinfo=server_tz)
                return int(dt.timestamp())

            if kind == "mon_d_hms":
                mon=MONTHS[m["mon"].title()]; d=int(m["d"])
                H=int(m["H"]); Mi=int(m["M"]); S=int(m["S"])
                dt = datetime(assumed_year,mon,d,H,Mi,S,tzinfo=server_tz)
                return int(dt.timestamp())

            if kind == "hmsf_only":
                H=int(m["H"]); Mi=int(m["M"]); S=int(m["S"])
                f=int((m["f"] or "0").ljust(6,"0"))
                base = today or datetime.now(server_tz)
                dt = datetime(base.year, base.month, base.day, H, Mi, S, f, tzinfo=server_tz)
                return int(dt.timestamp())
        except Exception:
            continue
    return None

# ---------------- SSH ----------------
def open_ssh(ssh_cfg: dict) -> paramiko.SSHClient:
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(
        ssh_cfg["host"],
        port=int(ssh_cfg["port"]),
        username=ssh_cfg["username"],
        password=ssh_cfg["password"],
        look_for_keys=False,
        allow_agent=False,
        timeout=15,
        banner_timeout=15,
        auth_timeout=15,
    )
    # KeepAlive
    try:
        cli.get_transport().set_keepalive(30)
    except Exception:
        pass
    return cli

def get_server_epoch_and_tz(cli: paramiko.SSHClient) -> Tuple[int, timezone, int]:
    stdin, stdout, stderr = cli.exec_command("date +%s"); epoch = int(stdout.read().decode().strip())
    stdin, stdout, stderr = cli.exec_command("date +%z"); z = stdout.read().decode().strip()
    sign = 1 if z.startswith("+") else -1
    hh=int(z[1:3]); mm=int(z[3:5]); offset = timedelta(hours=sign*hh, minutes=sign*mm)
    tz = timezone(offset)
    stdin, stdout, stderr = cli.exec_command("date +%Y"); year = int(stdout.read().decode().strip())
    return epoch, tz, year

# ---------------- 경로 디스커버리 ----------------
def resolve_log_path(sftp: paramiko.SFTPClient, primary_path: str, candidates: Optional[List[str]] = None) -> Tuple[str, Optional[str]]:
    """
    지정 경로가 없으면 후보 경로들에서 존재하는 첫 번째 경로를 사용.
    추가로, 흔한 실수(webreport/ozreport/ozreport.log → webreport/webreport.log) 자동 보정.
    반환: (resolved_path, reason)
    """
    def exists(p: str) -> bool:
        try:
            sftp.stat(p)
            return True
        except IOError:
            return False

    # 1) 우선 primary
    if exists(primary_path):
        return primary_path, None

    # 2) 자동 후보
    auto_candidates: List[str] = []
    if "/logs/webreport/ozreport/ozreport.log" in primary_path:
        auto_candidates.append("/usr/local/apache/logs/webreport/webreport.log")
        auto_candidates.append("/usr/local/apache/logs/webreport/ozreport.log")

    # 3) 사용자가 넣은 candidates + 자동 후보
    try_list: List[str] = (candidates or []) + auto_candidates

    for cand in try_list:
        if exists(cand):
            reason = f"path_not_found:{primary_path} -> fallback:{cand}"
            return cand, reason

    # 모두 실패
    return primary_path, "not_found_no_fallback"

# ---------------- 핵심 로그 추출 ----------------
def extract_core_lines(
    lines: List[str],
    rules_engine: RulesEngine,
    require_levels: List[str],
    keywords: List[str],
    max_per_group: int = 4,
    max_groups: int = 10,
) -> List[Tuple[str, List[str]]]:
    groups: Dict[str, List[str]] = {}

    # 1) RULE 매칭
    for line in lines:
        for r in getattr(rules_engine, "rules", []):
            try:
                if r.regex.search(line):
                    label = f"RULE:{r.category}/{r.note}"
                    groups.setdefault(label, [])
                    if len(groups[label]) < max_per_group:
                        groups[label].append(line)
                    break
            except Exception:
                continue

    # 2) LEVEL 매칭
    for line in lines:
        for lvl in require_levels or []:
            if lvl in line:
                label = f"LEVEL:{lvl}"
                groups.setdefault(label, [])
                if len(groups[label]) < max_per_group:
                    groups[label].append(line)
                break

    # 3) KEYWORD 매칭
    for line in lines:
        for kw in keywords or []:
            if kw in line:
                label = f"KEYWORD:{kw}"
                groups.setdefault(label, [])
                if len(groups[label]) < max_per_group:
                    groups[label].append(line)
                break

    # 입력 순서 유지: RULE → LEVEL → KEYWORD (dict 삽입 순서 보장)
    ordered = []
    for label in groups:
        ordered.append((label, groups[label]))
        if len(ordered) >= max_groups:
            break
    return ordered

# ---------------- Analyze ----------------
def summarize_blob(blob: str, rules: RulesEngine, keywords: List[str], require_levels: List[str], llm: LLMJudge):
    if not blob.strip():
        return (False, None, "선택된(최근 N분) 범위에 분석할 로그가 없습니다.")

    strong_keywords = ["OutOfMemoryError", "NullPointerException", "SSLHandshakeException", "ORA-"]
    has_strong = any(sk in blob for sk in strong_keywords)

    # 무시할 패턴 목록 (화이트리스트)
    ignore_patterns = [
        "u.SessionUtil:197- Session object is not exist."
    ]

    # 무시 대상이 포함되면 무조건 '에러 없음' 처리
    for pat in ignore_patterns:
        if pat in blob:
            return (False, None, f"무시 대상 패턴 감지됨: {pat}")

    # 1) 룰 매칭 최우선
    vr = rules.evaluate(blob)
    if vr:
        cause = f"{vr['category']} {vr['severity']}: {vr['note']}"
        return (True, vr, cause)

    # 2) 강력 키워드
    if has_strong:
        return (True,
                {"category":"UNKNOWN","severity":"LOW","action":"IGNORE","note":"strong_keyword"},
                "강력 키워드 감지(레벨 단어 없지만 OutOfMemoryError 등)")

    # 3) 레벨 게이팅
    if require_levels and not any(level in blob for level in require_levels):
        return (False, None, "ERROR/WARN/FATAL 레벨 로그 미존재 — 대부분 DEBUG로 판단(안정 상태로 추정).")

    # 4) (옵션) LLM 판단
    v = llm.call(blob) if llm and llm.enabled else None
    if v:
        sev = str(v.get("severity","LOW")).upper()
        if sev not in ("LOW","MEDIUM","HIGH"): sev = "LOW"
        cat = v.get("category","UNKNOWN")
        act = str(v.get("action","IGNORE")).upper()
        if act not in ("IGNORE","RETRY","ABORT"): act = "IGNORE"
        conf = float(v.get("confidence", 0.5))
        summary = v.get("summary","")
        details = v.get("details", {})
        verdict = {"category":cat,"severity":sev,"action":act,"note":summary,"confidence":conf,"details":details}
        cause = f"{cat} {sev}: {summary or 'LLM 판단 결과'}"
        return (True, verdict, cause)

    # 5) 일반 키워드(백업)
    hits = [k for k in keywords if k in blob]
    if hits:
        verdict = {"category":"UNKNOWN","severity":"LOW","action":"IGNORE","note":"keyword_hits"}
        return (True, verdict, f"명시 규칙/LLM 미매칭이지만 오류 키워드 감지({', '.join(hits[:5])})")

    return (False, None, "규칙/LLM/키워드 모두 미탐지(안정 상태로 추정).")

def analyze_file(
    sftp: paramiko.SFTPClient,
    path: str,
    rules: RulesEngine,
    start_epoch: int,
    end_epoch: int,
    server_tz: timezone,
    assumed_year: int,
    today: datetime,
    keywords: List[str],
    require_levels: List[str],
    max_lines: int,
    llm: LLMJudge,
    candidates: Optional[List[str]] = None,
    tag: Optional[str] = None,
) -> Dict[str, Any]:
    """
    주의: SFTP는 main에서 열고 여기로 주입(재사용).
    """
    try:
        resolved_path, reason = resolve_log_path(sftp, path, candidates)
        f = sftp.open(resolved_path, "r")
    except IOError as e:
        return {
            "file": path, "resolved_path": resolved_path if 'resolved_path' in locals() else path,
            "result": "처리 불가", "원인 분석": f"파일 열기 실패: {e}", "path_note": reason, "tag": tag,
        }

    lines_considered = 0
    eligible: List[str] = []
    last_ts: Optional[int] = None
    try:
        for raw in f:
            if max_lines and lines_considered >= max_lines:
                break
            try:
                line = raw.decode("utf-8", errors="ignore")
            except AttributeError:
                line = raw

            parsed = parse_line_epoch(line, server_tz, assumed_year, today)
            if parsed is not None:
                last_ts = parsed

            ts_to_use = parsed if parsed is not None else last_ts
            if ts_to_use is not None and (start_epoch <= ts_to_use <= end_epoch):
                eligible.append(line.rstrip("\n"))
                lines_considered += 1
    finally:
        try:
            f.close()
        except Exception:
            pass

    blob = "\n".join(eligible)
    has_err, verdict, msg = summarize_blob(blob, rules, keywords, require_levels, llm)

    base = {"file": path, "resolved_path": resolved_path, "tag": tag, "path_note": reason}
    if has_err:
        core_groups = extract_core_lines(
            eligible, rules_engine=rules, require_levels=require_levels, keywords=keywords,
            max_per_group=4, max_groups=10
        )
        base.update({"result": "에러 있음", "원인 분석": msg, "decision": verdict or {}, "core_groups": core_groups})
        return base
    else:
        recent3 = eligible[-3:] if eligible else []
        base.update({"result": "에러 없음", "이유 한줄": msg, "recent_samples": recent3})
        return base

def main():
    # 설정 로드 (스크립트와 동일 디렉토리의 config.yaml)
    cfg_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    ssh_cfg = cfg["ssh"]
    logs = cfg["logs"]
    rules = RulesEngine(cfg["rules"])
    keywords = cfg.get("keywords", [])

    analysis = cfg.get("analysis", {})
    recent_min = int(analysis.get("recent_minutes", 4))
    max_lines = int(analysis.get("max_lines_per_file", 100000))

    nf = cfg.get("noise_filter", {})
    require_levels = nf.get("require_level", []) if nf.get("enabled", False) else []

    llm = LLMJudge(cfg.get("llm", {}))

    print(f"[BATCH] SSH 연결 시도 → {ssh_cfg['host']}:{ssh_cfg['port']} ...")
    cli = open_ssh(ssh_cfg)
    print("[BATCH] SSH connected.")

    # 서버 시간 & TZ
    server_epoch, server_tz, assumed_year = get_server_epoch_and_tz(cli)
    today = datetime.fromtimestamp(server_epoch, tz=server_tz)

    # 최근 N분 구간
    start_epoch = server_epoch - (recent_min * 60)
    end_epoch   = server_epoch
    print(f"[BATCH] 분석 구간: {datetime.fromtimestamp(start_epoch, tz=server_tz).isoformat()} "
          f"~ {datetime.fromtimestamp(end_epoch,   tz=server_tz).isoformat()}")

    print("\n===== 분석 결과 (최근 {}분, 룰 + LLM) =====".format(recent_min))

    # SFTP 한 번만 열고 재사용
    sftp = cli.open_sftp()
    try:
        for it in logs:
            path = it["path"]
            tag = it.get("tag", os.path.basename(path))
            candidates = it.get("candidates", [])

            try:
                res = analyze_file(
                    sftp=sftp, path=path, rules=rules,
                    start_epoch=start_epoch, end_epoch=end_epoch,
                    server_tz=server_tz, assumed_year=assumed_year, today=today,
                    keywords=keywords, require_levels=require_levels,
                    max_lines=max_lines, llm=llm,
                    candidates=candidates, tag=tag
                )
            except Exception as e:
                res = {"file": path, "resolved_path": path, "result": "처리 불가",
                       "원인 분석": f"분석 실패: {e}", "tag": tag}

            # 공통 출력
            note = ""
            if res.get("resolved_path") and res.get("resolved_path") != path:
                note = f" (→ {res.get('resolved_path')})"
            path_note = f" | path_note={res.get('path_note')}" if res.get("path_note") else ""

            if res.get("result") == "에러 없음":
                print(f"\n[{tag}] 에러 없음{note}{path_note}\n> {res.get('이유 한줄')}")
                samples = res.get("recent_samples", [])
                if samples:
                    print("  --- 최근 로그 샘플 (3줄) ---")
                    for line in samples:
                        print(f"  {line}")

            elif res.get("result") == "에러 있음":
                print(f"\n[{tag}] 에러 있음{note}{path_note}\n> {res.get('원인 분석')}")
                dec = res.get("decision")
                if dec:
                    print(f"  - decision: {dec}")
                core_groups = res.get("core_groups", [])
                for label, lines in core_groups:
                    print(f"  --- 핵심 로그 (원인: {label}) ---")
                    for ln in lines:
                        print(f"  {ln}")

            else:
                print(f"\n[{tag}] 처리 불가{note}{path_note}\n> {res}")
    finally:
        try:
            sftp.close()
        except Exception:
            pass
        try:
            cli.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()
