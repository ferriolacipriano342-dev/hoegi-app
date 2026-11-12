import streamlit as st
import sqlite3
import pandas as pd
import datetime
import os
import sys
import shutil
import socket
import subprocess
import re
import time
import json
import urllib.parse
import urllib.request
import requests
import xml.etree.ElementTree as ET

# ==============================================================================
# 0. 페이지 기본 설정 (스마트폰 & 데스크탑 반응형 겸용)
# ==============================================================================
st.set_page_config(
    page_title="회기동 다가구 임대관리 시스템 (스마트폰용)",
    page_icon="🏡",
    layout="wide",
    initial_sidebar_state="collapsed"  # 스마트폰 화면에서 본문이 먼저 보이도록 기본 접힘 처리
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 원본 폴더 및 안전백업 폴더 경로 탐색 (원본 파일 절대 수정 안 함)
ORIGINAL_DIR = r"C:\Users\sdgpk\.gemini\antigravity\scratch\rental_management"
BACKUP_DIR = os.path.join(BASE_DIR, "임대관리_안전백업")

# 스마트폰 앱 전용 DB 경로 (원본 DB 보호를 위해 바탕화면 또는 앱 폴더에 독립 생성 후 초기 데이터 복제)
MOBILE_DB_PATH = os.path.join(BASE_DIR, "임대관리_스마트폰용.db")
CLOUD_FALLBACK_DB_PATH = os.path.join(BASE_DIR, "rental_management.db")


def ensure_mobile_database():
    """
    원본 DB를 절대 직접 수정하거나 덮어쓰지 않고,
    기존 39세대 계약·수납·보수공사·일정 데이터를 100% 그대로 복제한 스마트폰 전용 DB를 준비합니다.
    클라우드 배포 시 같은 폴더에 있는 rental_management.db 또는 임대관리_스마트폰용.db를 사용합니다.
    """
    if os.path.exists(MOBILE_DB_PATH):
        return MOBILE_DB_PATH
    if os.path.exists(CLOUD_FALLBACK_DB_PATH):
        return CLOUD_FALLBACK_DB_PATH

    # 백업 폴더 또는 원본 폴더에서 읽기 전용으로 복사하여 스마트폰 전용 DB 생성
    candidate_sources = [
        os.path.join(BACKUP_DIR, "rental_management.db"),
        os.path.join(ORIGINAL_DIR, "rental_management.db"),
    ]
    for src in candidate_sources:
        if os.path.exists(src):
            try:
                shutil.copy2(src, MOBILE_DB_PATH)
                return MOBILE_DB_PATH
            except Exception:
                pass
    return MOBILE_DB_PATH


ACTIVE_DB_PATH = ensure_mobile_database()

# 업로드 이미지(수리사진/영수증) 조회 및 저장 경로 설정
UPLOAD_DIR = os.path.join(BASE_DIR, "임대관리_스마트폰_업로드", "repairs")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 기존 백업/원본에 저장된 수리 사진도 스마트폰에서 그대로 볼 수 있도록 조회 경로 리스트 구성
EXISTING_UPLOAD_DIRS = [
    UPLOAD_DIR,
    os.path.join(BACKUP_DIR, "static", "uploads", "repairs"),
    os.path.join(ORIGINAL_DIR, "static", "uploads", "repairs"),
    os.path.join(BASE_DIR, "static", "uploads", "repairs"),
]


def resolve_upload_file_path(filename):
    if not filename:
        return None
    for d in EXISTING_UPLOAD_DIRS:
        candidate = os.path.join(d, filename)
        if os.path.exists(candidate):
            return candidate
    return None


# ==============================================================================
# 1. 프로(Pro) 요금제 구글 계정 전용 API 재인증 모듈 (기존 무료 계정 토큰 무시)
# ==============================================================================
# 기존 무료 계정에서 생성된 토큰 파일(token.json, token.pickle 등)은 일절 읽지 않고 무시합니다.
LEGACY_FREE_TOKEN_FILES = [
    os.path.join(BASE_DIR, "token.json"),
    os.path.join(BASE_DIR, "token.pickle"),
    os.path.join(ORIGINAL_DIR, "token.json"),
    os.path.join(ORIGINAL_DIR, "token.pickle"),
]

# 프로 요금제 구글 계정 전용 인증 저장 경로
PRO_AUTH_CONFIG_PATH = os.path.join(BASE_DIR, "pro_google_auth_config.json")
PRO_OAUTH_TOKEN_PATH = os.path.join(BASE_DIR, "pro_google_oauth_token.json")
PRO_CLIENT_SECRET_PATH = os.path.join(BASE_DIR, "pro_client_secret.json")

DEFAULT_PRO_GOOGLE_EMAIL = "ferriolacipriano342@gmail.com"
GOOGLE_API_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]


def load_pro_auth_config():
    """프로 요금제 구글 계정 설정 로드 (기존 무료 계정 캐시 무시)"""
    default_cfg = {
        "pro_email": DEFAULT_PRO_GOOGLE_EMAIL,
        "gemini_pro_api_key": "",
        "ignore_legacy_free_tokens": True,
        "authenticated_at": None,
        "auth_method": "pro_account_direct",
    }
    # Streamlit Cloud st.secrets 지원
    try:
        if "PRO_GOOGLE_EMAIL" in st.secrets:
            default_cfg["pro_email"] = st.secrets["PRO_GOOGLE_EMAIL"]
        if "GEMINI_PRO_API_KEY" in st.secrets:
            default_cfg["gemini_pro_api_key"] = st.secrets["GEMINI_PRO_API_KEY"]
    except Exception:
        pass

    if os.path.exists(PRO_AUTH_CONFIG_PATH):
        try:
            with open(PRO_AUTH_CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            default_cfg.update(saved)
        except Exception:
            pass
    return default_cfg


def save_pro_auth_config(cfg):
    try:
        with open(PRO_AUTH_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def purge_and_ignore_free_tokens():
    """
    기존 무료 계정 인증 토큰을 무시하고 프로 계정 전용 토큰만 새로 발급받도록 초기화합니다.
    (원본 폴더 파일은 건드리지 않고, 현재 세션/환경변수 및 프로 전용 토큰 캐시만 초기화)
    """
    for env_key in ["GOOGLE_APPLICATION_CREDENTIALS", "OAUTHLIB_INSECURE_TRANSPORT"]:
        if env_key in os.environ:
            del os.environ[env_key]
    if os.path.exists(PRO_OAUTH_TOKEN_PATH):
        try:
            os.remove(PRO_OAUTH_TOKEN_PATH)
        except Exception:
            pass
    for k in ["pro_oauth_creds", "pro_verified_email", "pro_auth_status"]:
        if k in st.session_state:
            del st.session_state[k]


def run_pro_google_oauth_flow(pro_email_hint=DEFAULT_PRO_GOOGLE_EMAIL):
    """
    기존 무료 계정 토큰을 무시하고, 새로 바꾼 프로(Pro) 요금제 구글 계정으로
    OAuth 2.0 API 인증을 새로 수행합니다 (prompt='select_account consent' 강제 적용).
    """
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        return False, (
            "Google OAuth 라이브러리가 설치되어 있지 않습니다. "
            "터미널에서 `pip install google-auth google-auth-oauthlib google-api-python-client` 실행 후 다시 시도해 주세요."
        )

    if not os.path.exists(PRO_CLIENT_SECRET_PATH):
        return False, "OAuth 클라이언트 시크릿 JSON 파일(credentials.json)을 먼저 업로드해 주세요."

    try:
        flow = InstalledAppFlow.from_client_secrets_file(PRO_CLIENT_SECRET_PATH, GOOGLE_API_SCOPES)
        # 기존 무료 계정 자동 로그인을 방지하기 위해 select_account 및 consent 강제 지정
        creds = flow.run_local_server(
            port=0,
            prompt="select_account consent",
            login_hint=pro_email_hint.strip() if pro_email_hint else None,
            access_type="offline",
        )
        with open(PRO_OAUTH_TOKEN_PATH, "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())

        cfg = load_pro_auth_config()
        cfg["pro_email"] = pro_email_hint.strip() or DEFAULT_PRO_GOOGLE_EMAIL
        cfg["authenticated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cfg["auth_method"] = "google_oauth2_pro"
        save_pro_auth_config(cfg)
        return True, f"프로 요금제 구글 계정({cfg['pro_email']})으로 새 OAuth 2.0 API 토큰 발급이 완료되었습니다!"
    except Exception as e:
        return False, f"OAuth 인증 중 오류 발생: {e}"


def verify_gemini_pro_api_key(api_key):
    """새 프로 요금제 구글 계정의 Gemini API Key 유효성 즉시 검증"""
    if not api_key or not api_key.strip():
        return False, "API 키를 입력해 주세요."
    clean_key = api_key.strip()
    test_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={clean_key}"
    try:
        r = requests.get(test_url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            models = [m.get("name", "") for m in data.get("models", [])]
            pro_models = [m for m in models if "pro" in m.lower() or "flash" in m.lower()]
            return True, f"프로 계정 Gemini API 인증 성공! (사용 가능 모델 {len(pro_models)}개 확인됨)"
        else:
            err_msg = r.json().get("error", {}).get("message", r.text)
            return False, f"API 키 인증 실패 ({r.status_code}): {err_msg}"
    except Exception as e:
        return False, f"API 통신 오류: {e}"


def add_event_to_google_calendar_api(summary, date_str, description, calendar_id="primary"):
    """
    프로 계정 OAuth 토큰(pro_google_oauth_token.json)이 존재할 경우,
    Google Calendar API를 직접 호출하여 일정을 프로 계정 캘린더에 즉시 자동 등록합니다.
    """
    if not os.path.exists(PRO_OAUTH_TOKEN_PATH):
        return False, None
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        creds = Credentials.from_authorized_user_file(PRO_OAUTH_TOKEN_PATH, GOOGLE_API_SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(PRO_OAUTH_TOKEN_PATH, "w", encoding="utf-8") as f:
                f.write(creds.to_json())

        service = build("calendar", "v3", credentials=creds)
        date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        next_day = date_obj + datetime.timedelta(days=1)
        event_body = {
            "summary": summary,
            "description": description,
            "start": {"date": date_obj.strftime("%Y-%m-%d")},
            "end": {"date": next_day.strftime("%Y-%m-%d")},
        }
        target_cal = calendar_id.strip() if calendar_id and calendar_id.strip() else "primary"
        created = service.events().insert(calendarId=target_cal, body=event_body).execute()
        return True, created.get("htmlLink")
    except Exception:
        return False, None


# ==============================================================================
# 2. 네트워크 / 스마트폰 접속 헬퍼 함수
# ==============================================================================
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "192.168.219.103"


def get_active_streamlit_port():
    try:
        port = st.get_option("server.port")
        return int(port) if port else 8503
    except Exception:
        return 8503


def get_streamlit_tunnel_url():
    log_candidates = [
        os.path.join(BASE_DIR, "streamlit_mobile_tunnel.log"),
        os.path.join(os.path.dirname(BASE_DIR), "streamlit_mobile_tunnel.log"),
    ]
    for log_path in log_candidates:
        if os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                matches = re.findall(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", content)
                if matches:
                    return matches[-1]
            except Exception:
                pass
    return None


# ==============================================================================
# 3. 데이터베이스 초기화 및 CRUD 함수 (원본 app.py와 100% 동일한 스키마·기능)
# ==============================================================================
def get_db_connection():
    conn = sqlite3.connect(ACTIVE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_all_tables():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contracts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dong TEXT NOT NULL,
        room TEXT NOT NULL,
        tenant_name TEXT NOT NULL,
        deposit INTEGER DEFAULT 0,
        rent INTEGER DEFAULT 0,
        maintenance_fee INTEGER DEFAULT 0,
        expiry_date TEXT,
        note TEXT,
        phone TEXT DEFAULT ''
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id INTEGER,
        year INTEGER,
        month INTEGER,
        status TEXT DEFAULT 'Unpaid',
        payment_date TEXT,
        alert_sent_date TEXT,
        FOREIGN KEY (contract_id) REFERENCES contracts(id) ON DELETE CASCADE
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dong TEXT,
        room TEXT,
        title TEXT,
        event_date TEXT,
        description TEXT
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS repairs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dong TEXT,
        room TEXT,
        repair_date TEXT,
        category TEXT,
        title TEXT,
        description TEXT,
        cost INTEGER DEFAULT 0,
        photo_path TEXT DEFAULT '',
        receipt_path TEXT DEFAULT '',
        created_at TEXT
    )
    """)
    cursor.execute("PRAGMA table_info(contracts)")
    cols = [row[1] for row in cursor.fetchall()]
    if "phone" not in cols:
        cursor.execute("ALTER TABLE contracts ADD COLUMN phone TEXT DEFAULT ''")
    conn.commit()
    conn.close()


init_all_tables()


def save_uploaded_file(uploaded_file, prefix="img"):
    if not uploaded_file:
        return ""
    ext = os.path.splitext(uploaded_file.name)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".pdf"]:
        ext = ".jpg"
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    fname = f"{prefix}_{stamp}{ext}"
    fpath = os.path.join(UPLOAD_DIR, fname)
    with open(fpath, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return fname


def get_repairs():
    conn = get_db_connection()
    df = pd.read_sql_query("SELECT * FROM repairs ORDER BY repair_date DESC, id DESC", conn)
    conn.close()
    return df


def add_repair(dong, room, repair_date, category, title, description, cost, photo_path="", receipt_path="", also_schedule=True):
    clean_room = str(room).replace("호", "").strip() if room != "공통" else "공통"
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO repairs (dong, room, repair_date, category, title, description, cost, photo_path, receipt_path, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (dong, clean_room, repair_date, category, title, description, int(cost or 0), photo_path, receipt_path, now_str))
    if also_schedule:
        room_disp = f"{clean_room}호" if clean_room != "공통" else "공통"
        sched_title = f"[보수공사] {title} ({int(cost or 0):,}원)"
        sched_desc = f"분류: {category} | 비용: {int(cost or 0):,}원 | 내역: {description}"
        cursor.execute("""
        INSERT INTO schedules (dong, room, title, event_date, description)
        VALUES (?, ?, ?, ?, ?)
        """, (dong, room_disp, sched_title, repair_date, sched_desc))
    conn.commit()
    conn.close()


def delete_repair(repair_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM repairs WHERE id = ?", (repair_id,))
    conn.commit()
    conn.close()


def make_gcal_repair_url(dong, room, title, date_str, desc, cost=0, category="보수공사", pro_email=""):
    try:
        date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        date_obj = datetime.datetime.now()
    next_day_obj = date_obj + datetime.timedelta(days=1)
    dates_param = f"{date_obj.strftime('%Y%m%d')}/{next_day_obj.strftime('%Y%m%d')}"
    room_disp = f"{str(room).replace('호', '')}호" if str(room) != "공통" else "공통"
    cost_str = f" ({int(cost):,}원)" if int(cost or 0) > 0 else ""
    gcal_title = f"[{dong} {room_disp} {category}] {title}{cost_str}"
    gcal_details = f"■ 동/호실: {dong} {room_disp}\n■ 공사/일정 구분: {category}\n■ 지출 비용: {int(cost or 0):,}원\n■ 상세 수리내역: {desc or '-'}"
    authuser_param = f"&authuser={urllib.parse.quote(pro_email.strip())}" if pro_email and pro_email.strip() else ""
    return (
        f"https://calendar.google.com/calendar/render?action=TEMPLATE"
        f"&text={urllib.parse.quote(gcal_title)}"
        f"&dates={dates_param}"
        f"&details={urllib.parse.quote(gcal_details)}"
        f"{authuser_param}"
    )


def get_schedules():
    conn = get_db_connection()
    df = pd.read_sql_query("SELECT * FROM schedules ORDER BY event_date ASC", conn)
    conn.close()
    return df


def add_schedule(dong, room, title, event_date, description):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO schedules (dong, room, title, event_date, description)
    VALUES (?, ?, ?, ?, ?)
    """, (dong, room, title, event_date, description))
    conn.commit()
    conn.close()


def delete_schedule(schedule_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
    conn.commit()
    conn.close()


def get_contracts():
    conn = get_db_connection()
    df = pd.read_sql_query("SELECT * FROM contracts ORDER BY dong, CAST(room AS INTEGER)", conn)
    conn.close()
    return df


def get_payments(year, month):
    conn = get_db_connection()
    query = """
    SELECT c.id as contract_id, c.dong, c.room, c.tenant_name, COALESCE(c.phone, '') as phone,
           c.deposit, c.rent, c.maintenance_fee, c.note,
           p.status, p.payment_date, p.alert_sent_date
    FROM contracts c
    LEFT JOIN payments p ON c.id = p.contract_id AND p.year = ? AND p.month = ?
    ORDER BY c.dong, CAST(c.room AS INTEGER)
    """
    df = pd.read_sql_query(query, conn, params=(year, month))
    conn.close()
    return df


def update_payment_status(contract_id, year, month, status, payment_date=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM payments WHERE contract_id = ? AND year = ? AND month = ?", (contract_id, year, month))
    record = cursor.fetchone()
    if record:
        cursor.execute("""
        UPDATE payments 
        SET status = ?, payment_date = ?
        WHERE contract_id = ? AND year = ? AND month = ?
        """, (status, payment_date, contract_id, year, month))
    else:
        cursor.execute("""
        INSERT INTO payments (contract_id, year, month, status, payment_date)
        VALUES (?, ?, ?, ?, ?)
        """, (contract_id, year, month, status, payment_date))
    conn.commit()
    conn.close()


def update_alert_date(contract_id, year, month, alert_date):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM payments WHERE contract_id = ? AND year = ? AND month = ?", (contract_id, year, month))
    rec = cursor.fetchone()
    if rec:
        cursor.execute("""
        UPDATE payments 
        SET alert_sent_date = ?
        WHERE contract_id = ? AND year = ? AND month = ?
        """, (alert_date, contract_id, year, month))
    else:
        cursor.execute("""
        INSERT INTO payments (contract_id, year, month, status, alert_sent_date)
        VALUES (?, ?, ?, 'Unpaid', ?)
        """, (contract_id, year, month, alert_date))
    conn.commit()
    conn.close()


def add_contract(dong, room, tenant_name, phone, deposit, rent, maintenance_fee, expiry_date, note):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM contracts WHERE dong = ? AND room = ?", (dong, room))
    existing = cursor.fetchone()
    if existing:
        contract_id = existing["id"]
        cursor.execute("""
        UPDATE contracts 
        SET tenant_name = ?, phone = ?, deposit = ?, rent = ?, maintenance_fee = ?, expiry_date = ?, note = ?
        WHERE id = ?
        """, (tenant_name, phone, deposit, rent, maintenance_fee, expiry_date, note, contract_id))
    else:
        cursor.execute("""
        INSERT INTO contracts (dong, room, tenant_name, phone, deposit, rent, maintenance_fee, expiry_date, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (dong, room, tenant_name, phone, deposit, rent, maintenance_fee, expiry_date, note))
    conn.commit()
    conn.close()


def delete_contract(contract_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM contracts WHERE id = ?", (contract_id,))
    cursor.execute("DELETE FROM payments WHERE contract_id = ?", (contract_id,))
    conn.commit()
    conn.close()


def update_contract(contract_id, dong, room, tenant_name, phone, deposit, rent, maintenance_fee, expiry_date, note):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE contracts 
    SET dong = ?, room = ?, tenant_name = ?, phone = ?, deposit = ?, rent = ?, maintenance_fee = ?, expiry_date = ?, note = ?
    WHERE id = ?
    """, (dong, room, tenant_name, phone, deposit, rent, maintenance_fee, expiry_date, note, contract_id))
    conn.commit()
    conn.close()


def update_contract_phone(contract_id, phone):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE contracts SET phone = ? WHERE id = ?", (phone, contract_id))
    conn.commit()
    conn.close()


# ==============================================================================
# 4. 스타일시트 (기존 데스크탑 CSS 100% 유지 + 스마트폰 브라우저 터치/반응형 최적화)
# ==============================================================================
st.markdown("""
<style>
    .kpi-card {
        background-color: #f8f9fa;
        border-radius: 10px;
        padding: 18px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.08);
        text-align: center;
        border-left: 5px solid #007bff;
        margin-bottom: 14px;
    }
    .kpi-title {
        font-size: 13px;
        color: #6c757d;
        margin-bottom: 4px;
        font-weight: bold;
    }
    .kpi-val {
        font-size: 22px;
        color: #212529;
        font-weight: 800;
    }
    .status-paid {
        color: #28a745;
        font-weight: bold;
    }
    .status-unpaid {
        color: #dc3545;
        font-weight: bold;
    }
    .pro-badge {
        display: inline-block;
        background: linear-gradient(90deg, #0d6efd, #6610f2);
        color: #ffffff !important;
        font-size: 12px;
        font-weight: 800;
        padding: 4px 10px;
        border-radius: 999px;
        margin-bottom: 6px;
    }

    /* Summary Table Styling */
    .summary-table-container {
        margin-bottom: 22px;
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
    }
    .summary-table {
        width: 100%;
        border-collapse: collapse;
        margin: 10px 0;
        font-size: 13px;
        text-align: center;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
        border-radius: 8px;
        overflow: hidden;
        background-color: #ffffff !important;
        border: 1px solid #dddddd;
    }
    .summary-table th {
        background-color: #007bff;
        color: white !important;
        padding: 8px 10px;
        font-weight: bold;
        border: 1px solid #dddddd;
        text-align: center;
        vertical-align: middle;
    }
    .summary-table td {
        padding: 8px 10px;
        border: 1px solid #dddddd;
        color: #212529 !important;
        vertical-align: middle;
    }
    .summary-table td.label-cell {
        text-align: left;
    }
    .summary-table tbody tr {
        background-color: #ffffff !important;
    }
    .summary-table tbody tr:nth-of-type(even) {
        background-color: #f8f9fa !important;
    }
    .summary-table tr.total-row {
        background-color: #e9ecef !important;
        font-weight: bold;
        border-top: 2px solid #6c757d;
    }

    /* Detail Table Styling */
    .detail-table {
        width: 100%;
        border-collapse: collapse;
        margin: 15px 0;
        font-size: 12px;
        text-align: center;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05);
        border-radius: 6px;
        overflow: hidden;
        background-color: #ffffff;
        border: 1px solid #dddddd;
    }
    .detail-table th {
        background-color: #f1f3f5;
        color: #212529 !important;
        padding: 8px 6px;
        font-weight: bold;
        border: 1px solid #dddddd;
        text-align: center;
    }
    .detail-table td {
        padding: 8px 6px;
        border: 1px solid #dddddd;
        color: #212529 !important;
    }
    .detail-table tbody tr:nth-of-type(even) {
        background-color: #f8f9fa !important;
    }

    /* Print Optimization CSS */
    @media print {
        [data-testid="stSidebar"], 
        [data-testid="stHeader"],
        [data-testid="stDecoration"],
        .stTabs [role="tablist"], 
        button, 
        .stButton,
        div.stSelectbox,
        div.stRadio,
        hr,
        .stAlert,
        .mobile-quick-bar {
            display: none !important;
        }
        .main .block-container {
            max-width: 100% !important;
            padding: 0 !important;
            margin: 0 !important;
        }
        .summary-table, .detail-table {
            box-shadow: none !important;
            border: 1px solid #777 !important;
        }
        .summary-table th, .detail-table th {
            background-color: #eee !important;
            color: #000 !important;
            -webkit-print-color-adjust: exact;
            print-color-adjust: exact;
            border: 1px solid #777 !important;
        }
        .summary-table td, .detail-table td {
            border: 1px solid #777 !important;
        }
        .summary-table tr.total-row {
            background-color: #ddd !important;
            -webkit-print-color-adjust: exact;
            print-color-adjust: exact;
        }
        .summary-table-container {
            page-break-after: always !important;
            break-after: page !important;
            margin-bottom: 0 !important;
        }
        .a-dong-print-container {
            page-break-after: always !important;
            break-after: page !important;
            margin-bottom: 0 !important;
        }
        .b-dong-print-container {
            page-break-after: avoid !important;
            break-after: avoid !important;
        }
        @page {
            size: A4 portrait;
            margin: 15mm 15mm 15mm 15mm;
        }
    }

    /* Smartphone Touch & Responsive Optimization */
    @media screen and (max-width: 768px) {
        .main .block-container {
            padding-left: 0.65rem !important;
            padding-right: 0.65rem !important;
            padding-top: 1.2rem !important;
        }
        .kpi-card {
            padding: 12px 10px;
            margin-bottom: 8px;
        }
        .kpi-val {
            font-size: 19px;
        }
        .summary-table-container,
        .a-dong-print-container,
        .b-dong-print-container {
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
        }
        .summary-table,
        .detail-table {
            min-width: 640px;
            white-space: nowrap;
        }
        div.stButton > button {
            min-height: 44px !important;
            font-weight: 700 !important;
            border-radius: 8px !important;
        }
    }
</style>
""", unsafe_allow_html=True)


# ==============================================================================
# 5. 사이드바 & 프로(Pro) 요금제 구글 계정 재인증 설정
# ==============================================================================
pro_cfg = load_pro_auth_config()

st.sidebar.markdown('<span class="pro-badge">✨ PRO GOOGLE ACCOUNT EDITION</span>', unsafe_allow_html=True)
st.sidebar.header("📅 조회 월 선택")

current_year = datetime.datetime.now().year
current_month = datetime.datetime.now().month

if "selected_year" not in st.session_state:
    st.session_state["selected_year"] = current_year if current_year in [2026, 2025, 2024] else 2026
if "selected_month" not in st.session_state:
    st.session_state["selected_month"] = current_month

year_options = [2026, 2025, 2024]
selected_year = st.sidebar.selectbox(
    "연도",
    year_options,
    index=year_options.index(st.session_state["selected_year"]) if st.session_state["selected_year"] in year_options else 0,
    key="sb_year_select"
)
selected_month = st.sidebar.selectbox(
    "월",
    list(range(1, 13)),
    index=st.session_state["selected_month"] - 1,
    key="sb_month_select"
)
st.session_state["selected_year"] = selected_year
st.session_state["selected_month"] = selected_month

st.sidebar.markdown("---")

# --- [핵심 요구사항 2] 프로 요금제 구글 계정 재인증 섹션 (무료 계정 토큰 무시) ---
st.sidebar.subheader("🔐 프로(Pro) 구글 계정 API 인증")
st.sidebar.caption("기존 무료 계정 인증 토큰(`token.json` 등)은 자동 무시되며, 새로 바꾼 프로 요금제 구글 계정으로만 API를 인증합니다.")

pro_email_input = st.sidebar.text_input(
    "프로 요금제 구글 계정 (이메일)",
    value=pro_cfg.get("pro_email", DEFAULT_PRO_GOOGLE_EMAIL),
    placeholder="예: ferriolacipriano342@gmail.com",
    help="새로 변경한 프로(Pro) 요금제 구글 계정 이메일입니다. 구글 캘린더 연동 및 OAuth 재인증 시 기본 계정으로 사용됩니다."
)

if pro_email_input.strip() != pro_cfg.get("pro_email", ""):
    pro_cfg["pro_email"] = pro_email_input.strip()
    save_pro_auth_config(pro_cfg)

with st.sidebar.expander("🔑 프로 계정 API 재인증 및 토큰 관리", expanded=False):
    st.markdown(f"**현재 적용 계정:** `{pro_cfg.get('pro_email', DEFAULT_PRO_GOOGLE_EMAIL)}`")
    st.markdown("**무료 계정 토큰 상태:** `🚫 무시됨 (차단 완료)`")

    if os.path.exists(PRO_OAUTH_TOKEN_PATH):
        st.success(f"✅ 프로 계정 OAuth 토큰 활성 ({pro_cfg.get('authenticated_at') or '인증 완료'})")
    else:
        st.info("ℹ️ 프로 계정 이메일 기반 연동 중 (OAuth 또는 API 키 추가 인증 가능)")

    if st.button("🔄 기존 무료 토큰 무시 · 프로 계정 초기화", use_container_width=True):
        purge_and_ignore_free_tokens()
        pro_cfg["authenticated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_pro_auth_config(pro_cfg)
        st.success("기존 무료 계정 인증 캐시를 완전히 무시 처리하고 프로 계정 세션으로 초기화했습니다!")
        st.rerun()

    st.markdown("---")
    st.markdown("**1) Gemini Pro API 키 인증 (프로 계정)**")
    gemini_key_input = st.text_input(
        "프로 계정 Gemini API Key",
        value=pro_cfg.get("gemini_pro_api_key", ""),
        type="password",
        placeholder="AIzaSy... (Google AI Studio 프로 키)"
    )
    if st.button("✅ 프로 API 키 인증 및 저장", use_container_width=True):
        ok, msg = verify_gemini_pro_api_key(gemini_key_input)
        if ok:
            pro_cfg["gemini_pro_api_key"] = gemini_key_input.strip()
            pro_cfg["authenticated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            pro_cfg["auth_method"] = "gemini_pro_api_key"
            save_pro_auth_config(pro_cfg)
            st.success(msg)
        else:
            st.error(msg)

    st.markdown("---")
    st.markdown("**2) Google Calendar OAuth 2.0 재인증**")
    uploaded_cred = st.file_uploader("구글 클라우드 credentials.json 업로드", type=["json"], key="pro_cred_upload")
    if uploaded_cred is not None:
        with open(PRO_CLIENT_SECRET_PATH, "wb") as f:
            f.write(uploaded_cred.getbuffer())
        st.success("credentials.json 저장 완료!")

    if st.button("🌐 프로 구글 계정 로그인 창 열기 (OAuth)", use_container_width=True):
        purge_and_ignore_free_tokens()
        ok, msg = run_pro_google_oauth_flow(pro_email_input)
        if ok:
            st.success(msg)
            st.rerun()
        else:
            st.warning(msg)

st.sidebar.markdown("---")

# --- 스마트폰 접속 QR 코드 및 주소 안내 섹션 ---
st.sidebar.subheader("📱 스마트폰 브라우저 접속")
local_ip = get_local_ip()
active_port = get_active_streamlit_port()
local_streamlit_url = f"http://{local_ip}:{active_port}"
https_tunnel_url = get_streamlit_tunnel_url()
primary_mobile_url = https_tunnel_url if https_tunnel_url else local_streamlit_url
qr_target = urllib.parse.quote(primary_mobile_url)

st.sidebar.markdown(
    f'<div style="background-color: #ffffff; border: 2px solid #0d6efd; border-radius: 8px; padding: 12px; text-align: center; color: #212529;">'
    f'<div style="font-size: 13px; font-weight: bold; color: #0d6efd; margin-bottom: 4px;">📲 스마트폰 즉시 접속 QR ({("외부 HTTPS" if https_tunnel_url else "와이파이")})</div>'
    f'<div style="font-size: 11px; color: #555; margin-bottom: 6px;">스마트폰 카메라로 스캔하면 이 화면이 그대로 열립니다</div>'
    f'<img src="https://api.qrserver.com/v1/create-qr-code/?size=160x160&data={qr_target}" width="145" height="145" style="border-radius: 6px; border: 1px solid #eee; margin-bottom: 6px;" />'
    f'<div style="font-size: 12px; word-break: break-all; margin-bottom: 4px;">'
    f'<a href="{primary_mobile_url}" target="_blank" style="color: #0d6efd; font-weight: bold; text-decoration: underline;">{primary_mobile_url}</a>'
    f'</div>'
    f'<div style="font-size: 11px; color: #198754; border-top: 1px dashed #ddd; padding-top: 4px;">'
    f'🏠 와이파이: <a href="{local_streamlit_url}" target="_blank" style="color: #198754; font-weight: bold;">{local_streamlit_url}</a>'
    f'</div></div>',
    unsafe_allow_html=True
)

st.sidebar.markdown("---")

# --- 인쇄 모드 및 API 옵션 ---
st.sidebar.subheader("⚙️ 옵션")
print_mode = st.sidebar.checkbox(
    "🖨️ 인쇄 출력 모드 (Print Mode)",
    value=False,
    help="인쇄용 3페이지 레이아웃(총괄, A동, B동)을 화면에 출력합니다. 활성화 후 Ctrl+P를 눌러 출력하세요."
)
oc_key = st.sidebar.text_input(
    "법제처 API 인증키 (OC)",
    value="sdgpks21",
    type="password",
    help="법제처 국가법령정보 공동활용 사이트에서 발급받은 인증키(OC)입니다. 기본값으로 등록된 'sdgpks21'이 설정되어 있습니다."
)
calendar_id = st.sidebar.text_input(
    "구글 캘린더 ID (프로 계정 기본 연동)",
    value=pro_cfg.get("pro_email", DEFAULT_PRO_GOOGLE_EMAIL),
    placeholder="예: ferriolacipriano342@gmail.com",
    help="새로 바꾼 프로 요금제 구글 계정 이메일이 기본 연동되어 실시간으로 캘린더가 화면 내에 표시됩니다."
)

st.sidebar.markdown("---")

# --- 사이드바 신규 계약 등록 폼 (원본과 100% 동일) ---
st.sidebar.subheader("➕ 신규 계약 등록")
with st.sidebar.form("new_contract_form", clear_on_submit=True):
    new_dong = st.selectbox("동 구분", ["A동", "B동"])
    new_room = st.text_input("호수 (예: 201)")
    new_name = st.text_input("임차인 성명")
    new_phone = st.text_input("전화번호 (예: 010-1234-5678)")
    new_dep = st.number_input("보증금 (원)", min_value=0, step=1000000, value=10000000)
    new_rent = st.number_input("월세 (원) (전세일 경우 0)", min_value=0, step=10000, value=300000)
    new_m_fee = st.number_input("관리비 (원)", min_value=0, step=10000, value=80000)
    new_expiry = st.date_input("계약 만기일", datetime.date.today() + datetime.timedelta(days=730))
    new_note = st.text_input("메모")

    submit_new = st.form_submit_button("등록", use_container_width=True)
    if submit_new:
        if new_room and new_name:
            add_contract(new_dong, new_room.strip(), new_name.strip(), new_phone.strip(), new_dep, new_rent, new_m_fee, new_expiry.strftime("%Y-%m-%d"), new_note.strip())
            st.sidebar.success(f"{new_dong} {new_room}호 {new_name}님 계약 등록/변경 완료!")
            st.rerun()
        else:
            st.sidebar.error("호수와 임차인 성명은 필수 항목입니다.")


# ==============================================================================
# 6. 상단 타이틀, 스마트폰 퀵 컨트롤 바, 4대 메인 네비게이션 메뉴
# ==============================================================================
st.title("🏡 관리시스템")
st.markdown(
    f"A동 20세대 + B동 19세대 (총 39세대) 계약 정보 및 월세/관리비 수납 현황 관리 "
    f"&nbsp;<span class='pro-badge'>구글 프로 계정 연동: {pro_cfg.get('pro_email', DEFAULT_PRO_GOOGLE_EMAIL)}</span>",
    unsafe_allow_html=True
)

# 스마트폰에서는 사이드바를 열지 않고도 바로 연도/월 변경·신규계약·프로 계정 재인증을 할 수 있는 상단 접이식 바 제공
if not print_mode:
    with st.expander(f"📱 스마트폰 빠른 설정 바 ({selected_year}년 {selected_month}월 조회 중 · 터치하여 열기)", expanded=False):
        q_c1, q_c2, q_c3 = st.columns([1, 1, 1.5])
        with q_c1:
            q_yr = st.selectbox("조회 연도", year_options, index=year_options.index(selected_year), key="quick_yr")
            if q_yr != selected_year:
                st.session_state["selected_year"] = q_yr
                st.rerun()
        with q_c2:
            q_mo = st.selectbox("조회 월", list(range(1, 13)), index=selected_month - 1, key="quick_mo")
            if q_mo != selected_month:
                st.session_state["selected_month"] = q_mo
                st.rerun()
        with q_c3:
            st.markdown(f"**🔐 프로 구글 계정:** `{pro_cfg.get('pro_email', DEFAULT_PRO_GOOGLE_EMAIL)}`")
            if st.button("🔄 무료 토큰 무시 · 프로 계정 재인증", key="quick_reauth_btn", use_container_width=True):
                purge_and_ignore_free_tokens()
                pro_cfg["authenticated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                save_pro_auth_config(pro_cfg)
                st.success("기존 무료 계정 인증 토큰을 무시하고 프로 계정으로 재설정했습니다!")
                st.rerun()

if "active_menu" not in st.session_state:
    st.session_state["active_menu"] = "dashboard"

if not print_mode:
    col_nav1, col_nav2, col_nav3, col_nav4 = st.columns([1, 1, 1, 1])
    with col_nav1:
        if st.button("📊 수납 및 계약 종합", use_container_width=True, type="primary" if st.session_state["active_menu"] == "dashboard" else "secondary"):
            st.session_state["active_menu"] = "dashboard"
            st.rerun()
    with col_nav2:
        if st.button("🛠️ 보수공사 · 지출내역", use_container_width=True, type="primary" if st.session_state["active_menu"] == "repairs" else "secondary"):
            st.session_state["active_menu"] = "repairs"
            st.rerun()
    with col_nav3:
        if st.button("📅 실시간 구글 일정관리", use_container_width=True, type="primary" if st.session_state["active_menu"] == "calendar" else "secondary"):
            st.session_state["active_menu"] = "calendar"
            st.rerun()
    with col_nav4:
        if st.button("⚖️ 실시간 법령/판례 검색", use_container_width=True, type="primary" if st.session_state["active_menu"] == "law" else "secondary"):
            st.session_state["active_menu"] = "law"
            st.rerun()
    st.markdown("<hr style='margin: 0.5rem 0 1rem 0; border-top: 2px solid #0d6efd;' />", unsafe_allow_html=True)


# ==============================================================================
# 7. 수납 통계 계산 (원본 app.py와 100% 동일)
# ==============================================================================
df_payments = get_payments(selected_year, selected_month)


def get_sub_metrics(df_sub):
    sub_dep = df_sub["deposit"].sum()

    sub_r_tgt = df_sub["rent"].sum()
    sub_m_tgt = df_sub["maintenance_fee"].sum()
    sub_tot_tgt = sub_r_tgt + sub_m_tgt

    sub_r_coll = df_sub[df_sub["status"] == "Paid"]["rent"].sum()
    sub_m_coll = df_sub[df_sub["status"] == "Paid"]["maintenance_fee"].sum()
    sub_tot_coll = sub_r_coll + sub_m_coll

    sub_r_unp = sub_r_tgt - sub_r_coll
    sub_m_unp = sub_m_tgt - sub_m_coll
    sub_tot_unp = sub_tot_tgt - sub_tot_coll

    sub_unp_cnt = len(df_sub[df_sub["status"] != "Paid"])
    sub_tot_cnt = len(df_sub)
    sub_rate = (sub_tot_coll / sub_tot_tgt * 100) if sub_tot_tgt > 0 else 100.0

    return (
        sub_dep,
        sub_r_tgt, sub_m_tgt, sub_tot_tgt,
        sub_r_coll, sub_m_coll, sub_tot_coll,
        sub_r_unp, sub_m_unp, sub_tot_unp,
        sub_unp_cnt, sub_tot_cnt, sub_rate
    )


df_a = df_payments[df_payments["dong"] == "A동"]
df_b = df_payments[df_payments["dong"] == "B동"]

(dep_a, r_tgt_a, m_tgt_a, tot_tgt_a,
 r_coll_a, m_coll_a, tot_coll_a,
 r_unp_a, m_unp_a, tot_unp_a,
 unp_cnt_a, tot_cnt_a, rate_a) = get_sub_metrics(df_a)

(dep_b, r_tgt_b, m_tgt_b, tot_tgt_b,
 r_coll_b, m_coll_b, tot_coll_b,
 r_unp_b, m_unp_b, tot_unp_b,
 unp_cnt_b, tot_cnt_b, rate_b) = get_sub_metrics(df_b)

(dep_tot, r_tgt_tot, m_tgt_tot, tot_tgt_tot,
 r_coll_tot, m_coll_tot, tot_coll_tot,
 r_unp_tot, m_unp_tot, tot_unp_tot,
 unp_cnt_tot, tot_cnt_tot, rate_tot) = get_sub_metrics(df_payments)


# ==============================================================================
# 8. 메뉴 [4] 실시간 법령/판례 검색 뷰 (원본 100% 동일 + 스마트폰 자체 조문 뷰어)
# ==============================================================================
if st.session_state.get("active_menu", "dashboard") == "law" and not print_mode:
    st.subheader("⚖️ 국가법령정보 실시간 법령 및 판례 검색")
    st.markdown("법제처 국가법령정보 공동활용 Open API를 활용하여 임대차 관련 법령 및 판례를 스마트폰과 PC에서 실시간으로 검색할 수 있습니다.")

    search_oc = oc_key.strip() if (oc_key and oc_key.strip() != "test") else "sdgpks21"

    search_type = st.radio("검색 분류", ["현행법령 (Law)", "법원판례 (Precedent)"], horizontal=True, key="search_type_selector")

    # 스마트폰 터치 편의를 위한 추천 검색어 버튼 제공
    st.caption("👇 자주 찾는 임대차 키워드 (터치 시 바로 입력)")
    kw_cols = st.columns(5)
    preset_keywords = ["주택임대차보호법", "상가건물 임대차보호법", "계약갱신요구권", "차임연체 해지", "원상회복 보증금"]
    for idx_kw, kw in enumerate(preset_keywords):
        with kw_cols[idx_kw]:
            if st.button(kw, key=f"kw_btn_{idx_kw}", use_container_width=True):
                st.session_state["legal_search_query_input"] = kw
                st.rerun()

    search_query = st.text_input("검색어 입력 (예: 주택임대차, 상가임대차, 농지법, 권리금)", value="주택임대차", key="legal_search_query_input")

    if st.button("🔍 실시간 검색 실행", key="run_legal_search_btn", type="primary", use_container_width=True) or search_query:
        if not search_query:
            st.error("검색어를 입력해 주세요.")
        else:
            target = "law" if "현행법령" in search_type else "prec"
            encoded_q = urllib.parse.quote(search_query.strip())
            api_url = f"http://www.law.go.kr/DRF/lawSearch.do?OC={search_oc}&target={target}&type=JSON&query={encoded_q}"

            with st.spinner("법제처 API로부터 데이터를 조회하는 중..."):
                try:
                    response = requests.get(api_url, timeout=15)
                    if response.status_code == 200:
                        res_json = response.json()
                        if "result" in res_json and "msg" in res_json and "LawSearch" not in res_json and "PrecSearch" not in res_json:
                            st.error(f"법제처 API 응답 안내: {res_json.get('result')} - {res_json.get('msg')}")
                        elif target == "law":
                            search_root = res_json.get("LawSearch", {})
                            total_cnt_str = search_root.get("totalCnt", "0")
                            try:
                                total_cnt = int(total_cnt_str)
                            except ValueError:
                                total_cnt = 0

                            laws_data = search_root.get("law", [])
                            if isinstance(laws_data, dict):
                                laws = [laws_data]
                            elif isinstance(laws_data, list):
                                laws = laws_data
                            else:
                                laws = []

                            st.success(f"총 {total_cnt}건의 법령이 검색되었습니다.")
                            if len(laws) > 0:
                                for idx, l in enumerate(laws):
                                    mst = l.get("법령일련번호", "")
                                    law_name_kr = l.get("법령명한글", "")
                                    ext_law_url = f"https://www.law.go.kr/법령/{urllib.parse.quote(law_name_kr)}"
                                    st.markdown(f"""
                                    <div style="padding: 12px; border: 1px solid #e9ecef; border-radius: 6px; margin-bottom: 6px; background-color: #ffffff; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                                        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                                            <span style="font-size: 15px; font-weight: bold; color: #0d6efd;">{idx+1}. {law_name_kr} ({l.get('법령구분명', '')})</span>
                                            <a href="{ext_law_url}" target="_blank" style="padding: 4px 10px; background-color: #0d6efd; color: white !important; border-radius: 4px; text-decoration: none; font-size: 12px; font-weight: bold; border: none; display: inline-block;">국가법령정보센터 원문 ↗</a>
                                        </div>
                                        <div style="font-size: 12px; color: #6c757d; margin-top: 6px; border-top: 1px solid #f1f3f5; padding-top: 6px;">
                                            <b>소관부처:</b> {l.get('소관부처명', '')} | <b>공포번호:</b> {l.get('공포번호', '')} | <b>공포일:</b> {l.get('공포일자', '')} | <b>시행일:</b> {l.get('시행일자', '')}
                                        </div>
                                    </div>
                                    """, unsafe_allow_html=True)
                                    with st.expander(f"📖 {law_name_kr} - 조문 본문 바로보기"):
                                        try:
                                            xml_url = f"http://www.law.go.kr/DRF/lawService.do?OC={search_oc}&target=law&MST={mst}&type=XML"
                                            r_xml = requests.get(xml_url, timeout=15)
                                            r_xml.encoding = "utf-8"
                                            xroot = ET.fromstring(r_xml.text)
                                            for u in xroot.findall(".//조문단위"):
                                                j_txt = (u.findtext("조문내용") or "").strip()
                                                if j_txt:
                                                    st.markdown(f"**{j_txt}**")
                                                for h in u.findall("항"):
                                                    h_txt = (h.findtext("항내용") or "").strip()
                                                    if h_txt:
                                                        st.markdown(f"&nbsp;&nbsp;{h_txt}")
                                                    for ho in h.findall("호"):
                                                        ho_txt = (ho.findtext("호내용") or "").strip()
                                                        if ho_txt:
                                                            st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;{ho_txt}")
                                        except Exception as ex:
                                            st.error(f"본문 조회 오류: {ex}")
                            else:
                                st.warning("검색 결과가 없습니다.")
                        else:
                            search_root = res_json.get("PrecSearch", {})
                            total_cnt_str = search_root.get("totalCnt", "0")
                            try:
                                total_cnt = int(total_cnt_str)
                            except ValueError:
                                total_cnt = 0

                            prec_data = search_root.get("prec", [])
                            if isinstance(prec_data, dict):
                                precedents = [prec_data]
                            elif isinstance(prec_data, list):
                                precedents = prec_data
                            else:
                                precedents = []

                            st.success(f"총 {total_cnt}건의 판례가 검색되었습니다.")
                            if len(precedents) > 0:
                                for idx, p in enumerate(precedents):
                                    prec_id = p.get("판례일련번호", "")
                                    st.markdown(f"""
                                    <div style="padding: 12px; border: 1px solid #e9ecef; border-radius: 6px; margin-bottom: 6px; background-color: #ffffff; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                                        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                                            <span style="font-size: 15px; font-weight: bold; color: #198754;">{idx+1}. {p.get('사건명', '')}</span>
                                        </div>
                                        <div style="font-size: 13px; color: #212529; margin-top: 6px; font-weight: 500;">
                                            사건번호: {p.get('사건번호', '')} | 법원: {p.get('법원명', '')} ({p.get('판결유형', '') or '결정'})
                                        </div>
                                        <div style="font-size: 12px; color: #6c757d; margin-top: 4px; border-top: 1px solid #f1f3f5; padding-top: 6px;">
                                            <b>선고일자:</b> {p.get('선고일자', '')} | <b>출처:</b> {p.get('데이터출처명', '')}
                                        </div>
                                    </div>
                                    """, unsafe_allow_html=True)
                                    with st.expander(f"📖 {p.get('사건명', '')} ({p.get('사건번호', '')}) - 판례 본문 바로보기"):
                                        try:
                                            xml_url = f"http://www.law.go.kr/DRF/lawService.do?OC={search_oc}&target=prec&ID={prec_id}&type=XML"
                                            r_xml = requests.get(xml_url, timeout=15)
                                            r_xml.encoding = "utf-8"
                                            proot = ET.fromstring(r_xml.text)
                                            for sec_tag, sec_title in [("판시사항", "📌 판시사항"), ("판결요지", "⚖️ 판결요지"), ("참조조문", "📘 참조조문"), ("판례내용", "📄 판례전문")]:
                                                txt = (proot.findtext(sec_tag) or "").replace("<br/>", "\n").replace("<br>", "\n").strip()
                                                if txt:
                                                    st.markdown(f"#### {sec_title}")
                                                    st.markdown(txt)
                                        except Exception as ex:
                                            st.error(f"판례 본문 조회 오류: {ex}")
                            else:
                                st.warning("검색 결과가 없습니다.")
                    else:
                        st.error(f"법제처 API 호출 실패 (상태 코드: {response.status_code})")
                except Exception as e:
                    st.error(f"조회 중 오류 발생: {e}")
    st.stop()


# ==============================================================================
# 9. 보수공사 · 지출내역 공통 렌더링 함수 (원본 100% 동일 + 프로 계정 캘린더 연동)
# ==============================================================================
def render_repairs_and_expenses_section(selected_year, selected_month, key_prefix="rep"):
    st.subheader(f"🛠️ 세대별(A·B동) 보수공사 관리 · 사진/영수증 업로드 및 {selected_year}년 월별·연간 지출 결산")
    st.markdown("각 세대별(호실별) 일자별 보수공사 내역·비용·수리사진·영수증을 등록하면 **해당 호실 이력에 자동 업로드**되며 **프로 계정 구글 캘린더 일정** 및 **전체 세대 월별·연간 지출 통계**에 실시간 반영됩니다.")

    df_rep = get_repairs()
    df_contracts_all = get_contracts()

    yr_prefix = f"{selected_year}-"
    ym_prefix = f"{selected_year}-{selected_month:02d}-"
    if len(df_rep) > 0:
        df_yr = df_rep[df_rep["repair_date"].astype(str).str.startswith(yr_prefix)].copy()
        df_mo = df_rep[df_rep["repair_date"].astype(str).str.startswith(ym_prefix)].copy()
    else:
        df_yr = pd.DataFrame(columns=["id", "dong", "room", "repair_date", "category", "title", "description", "cost", "photo_path", "receipt_path"])
        df_mo = df_yr.copy()

    yr_tot = int(df_yr["cost"].sum()) if len(df_yr) > 0 else 0
    yr_a = int(df_yr[df_yr["dong"] == "A동"]["cost"].sum()) if len(df_yr) > 0 else 0
    yr_b = int(df_yr[df_yr["dong"] == "B동"]["cost"].sum()) if len(df_yr) > 0 else 0
    yr_com = int(df_yr[df_yr["dong"] == "공통"]["cost"].sum()) if len(df_yr) > 0 else 0
    mo_tot = int(df_mo["cost"].sum()) if len(df_mo) > 0 else 0

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.markdown(
            f'<div class="kpi-card" style="border-left-color: #dc3545;">'
            f'<div class="kpi-title">{selected_year}년 연간 총 지출 합계</div>'
            f'<div class="kpi-val" style="color: #dc3545;">{yr_tot:,}원</div>'
            f'<div style="font-size:12px; color:#6c757d;">연간 총 {len(df_yr)}건 보수공사</div>'
            f'</div>',
            unsafe_allow_html=True
        )
    with k2:
        st.markdown(
            f'<div class="kpi-card" style="border-left-color: #fd7e14;">'
            f'<div class="kpi-title">{selected_year}년 {selected_month}월 당월 지출액</div>'
            f'<div class="kpi-val" style="color: #fd7e14;">{mo_tot:,}원</div>'
            f'<div style="font-size:12px; color:#6c757d;">{selected_month}월 {len(df_mo)}건 보수공사</div>'
            f'</div>',
            unsafe_allow_html=True
        )
    with k3:
        st.markdown(
            f'<div class="kpi-card" style="border-left-color: #0d6efd;">'
            f'<div class="kpi-title">{selected_year}년 A동 누적 보수지출</div>'
            f'<div class="kpi-val" style="color: #0d6efd;">{yr_a:,}원</div>'
            f'<div style="font-size:12px; color:#6c757d;">A동 {len(df_yr[df_yr["dong"]=="A동"]) if len(df_yr)>0 else 0}건</div>'
            f'</div>',
            unsafe_allow_html=True
        )
    with k4:
        st.markdown(
            f'<div class="kpi-card" style="border-left-color: #198754;">'
            f'<div class="kpi-title">{selected_year}년 B동 · 공통 누적 지출</div>'
            f'<div class="kpi-val" style="color: #198754;">{yr_b:,}원</div>'
            f'<div style="font-size:12px; color:#6c757d;">B동 {yr_b:,}원 / 공통 {yr_com:,}원</div>'
            f'</div>',
            unsafe_allow_html=True
        )

    st.markdown(f"#### 📊 {selected_year}년 전체 세대 월별 · 연간 지출내역 총괄표")
    monthly_rows_html = ""
    for m in range(1, 13):
        m_prefix = f"{selected_year}-{m:02d}-"
        if len(df_yr) > 0:
            sub_m = df_yr[df_yr["repair_date"].astype(str).str.startswith(m_prefix)]
        else:
            sub_m = pd.DataFrame()
        m_a = int(sub_m[sub_m["dong"] == "A동"]["cost"].sum()) if len(sub_m) > 0 else 0
        m_b = int(sub_m[sub_m["dong"] == "B동"]["cost"].sum()) if len(sub_m) > 0 else 0
        m_c = int(sub_m[sub_m["dong"] == "공통"]["cost"].sum()) if len(sub_m) > 0 else 0
        m_sum = m_a + m_b + m_c
        m_cnt = len(sub_m)
        if m_cnt > 0:
            summary_items = ", ".join([f"{r['dong']} {r['room']}{'호' if r['room']!='공통' else ''}({r['title']})" for _, r in sub_m.head(3).iterrows()])
            if m_cnt > 3:
                summary_items += f" 외 {m_cnt - 3}건"
        else:
            summary_items = "-"
        row_bg = "background-color: #fff8f6 !important;" if m == selected_month else ""
        monthly_rows_html += (
            f'<tr style="{row_bg}">'
            f'<td><b>{m}월</b></td>'
            f'<td>{m_a:,}원</td>'
            f'<td>{m_b:,}원</td>'
            f'<td>{m_c:,}원</td>'
            f'<td style="color: {"#dc3545" if m_sum > 0 else "#212529"}; font-weight: bold;">{m_sum:,}원</td>'
            f'<td>{m_cnt}건</td>'
            f'<td style="text-align: left; font-size: 12px; color: #495057;">{summary_items}</td>'
            f'</tr>'
        )

    expense_table_html = (
        '<div class="summary-table-container">'
        '<table class="summary-table">'
        '<thead><tr>'
        '<th>월 구분</th>'
        '<th>🏠 A동 보수지출</th>'
        '<th>🏢 B동 보수지출</th>'
        '<th>🏛️ 공통시설 지출</th>'
        '<th>💰 월별 총 지출액</th>'
        '<th>공사 건수</th>'
        '<th>주요 보수/수리 내역 요약</th>'
        '</tr></thead>'
        f'<tbody>{monthly_rows_html}'
        '<tr class="total-row">'
        f'<td><b>⭐ {selected_year}년 연간 합계</b></td>'
        f'<td><b>{yr_a:,}원</b></td>'
        f'<td><b>{yr_b:,}원</b></td>'
        f'<td><b>{yr_com:,}원</b></td>'
        f'<td style="color: #dc3545; font-size: 15px;"><b>{yr_tot:,}원</b></td>'
        f'<td><b>총 {len(df_yr)}건</b></td>'
        f'<td style="text-align: left;"><b>A동 {yr_a:,}원 + B동 {yr_b:,}원 + 공통 {yr_com:,}원 = 총 {yr_tot:,}원</b></td>'
        '</tr></tbody></table></div>'
    )
    st.markdown(expense_table_html, unsafe_allow_html=True)

    st.markdown("---")

    col_r_form, col_r_list = st.columns([4.2, 5.8])

    with col_r_form:
        st.markdown("#### ➕ 세대별 일자별 보수공사 · 사진/영수증 등록 (구글캘린더 연동)")
        r_dong = st.selectbox("동 선택", ["A동", "B동", "공통"], key=f"{key_prefix}_dong")
        if r_dong in ["A동", "B동"]:
            r_rooms = sorted(list(df_contracts_all[df_contracts_all["dong"] == r_dong]["room"].unique()), key=lambda x: int(x) if str(x).isdigit() else str(x))
            r_room_opts = [f"{rm}호" for rm in r_rooms] + ["공통"]
        else:
            r_room_opts = ["공통"]
        r_room = st.selectbox("호실 선택", r_room_opts, key=f"{key_prefix}_room")

        with st.form(f"{key_prefix}_add_repair_form", clear_on_submit=True):
            r_date = st.date_input("보수공사 일자", datetime.date.today(), key=f"{key_prefix}_date")
            r_cat = st.selectbox("공사 분류", ["보수공사/수리", "누수/배관", "보일러/난방", "전기/조명", "도배/장판", "창호/도어락", "가전/에어컨", "공용시설 점검", "기타 지출"], key=f"{key_prefix}_cat")
            r_title = st.text_input("수리 요약 제목 (필수)", placeholder="예: 싱크대 수전 교체, 화장실 방수 보수", key=f"{key_prefix}_title")
            r_cost = st.number_input("지출 비용 (원)", min_value=0, step=10000, value=50000, key=f"{key_prefix}_cost")
            r_desc = st.text_area("상세 수리 내역 / 시공업체 메모", placeholder="수리 상세 내용, 시공업체명, 연락처 등을 기록하세요", key=f"{key_prefix}_desc")
            r_photo = st.file_uploader("📷 수리 현장 사진 업로드 (스마트폰 카메라 촬영/선택)", type=["jpg", "jpeg", "png", "webp"], key=f"{key_prefix}_photo")
            r_receipt = st.file_uploader("🧾 영수증 / 견적서 사진 업로드 (선택)", type=["jpg", "jpeg", "png", "webp", "pdf"], key=f"{key_prefix}_receipt")
            r_sync_cal = st.checkbox("📅 일정 달력 및 프로 계정 구글 캘린더에 동시 등록", value=True, key=f"{key_prefix}_sync")

            sub_rep_btn = st.form_submit_button("💾 호실별 보수내역 · 사진/영수증 저장 및 구글캘린더 연동", type="primary", use_container_width=True)
            if sub_rep_btn:
                final_r_title = r_title.strip() if r_title.strip() else r_cat
                date_str = r_date.strftime("%Y-%m-%d")
                p_fname = save_uploaded_file(r_photo, prefix="repair") if r_photo else ""
                rec_fname = save_uploaded_file(r_receipt, prefix="receipt") if r_receipt else ""
                add_repair(r_dong, r_room, date_str, r_cat, final_r_title, r_desc.strip(), r_cost, p_fname, rec_fname, also_schedule=r_sync_cal)

                # 프로 계정 OAuth 토큰이 있으면 API로 자동 등록 시도 + 템플릿 링크 동시 생성
                api_ok, api_link = add_event_to_google_calendar_api(
                    f"[{r_dong} {r_room} {r_cat}] {final_r_title} ({r_cost:,}원)",
                    date_str,
                    r_desc.strip(),
                    calendar_id=calendar_id
                )
                gcal_url = api_link or make_gcal_repair_url(
                    r_dong, r_room, final_r_title, date_str, r_desc.strip(), r_cost, r_cat,
                    pro_email=pro_cfg.get("pro_email", DEFAULT_PRO_GOOGLE_EMAIL)
                )
                st.session_state[f"{key_prefix}_last_gcal_url"] = gcal_url
                st.success(f"✅ {r_dong} {r_room} [{final_r_title}] 보수공사 내역({r_cost:,}원) 및 사진/영수증이 저장되었습니다!")
                st.rerun()

        if f"{key_prefix}_last_gcal_url" in st.session_state:
            st.markdown(
                f'<div style="padding: 12px; background-color: #e8f5e9; border: 1px solid #a5d6a7; border-radius: 8px; margin-top: 8px; text-align: center;">'
                f'<div style="color: #1b5e20; font-weight: bold; margin-bottom: 6px;">📅 프로 구글 계정({pro_cfg.get("pro_email")}) 캘린더에 보수공사 일정을 바로 추가하세요!</div>'
                f'<a href="{st.session_state[f"{key_prefix}_last_gcal_url"]}" target="_blank" style="display: inline-block; padding: 8px 16px; background-color: #2e7d32; color: white !important; border-radius: 6px; text-decoration: none; font-size: 13px; font-weight: bold;">구글 캘린더에 보수공사 일정 최종 저장 ↗</a>'
                f'</div>',
                unsafe_allow_html=True
            )

    with col_r_list:
        st.markdown("#### 🔍 세대별 · 일자별 보수공사 상세 내역 및 사진/영수증 확인")
        f_col1, f_col2, f_col3 = st.columns(3)
        with f_col1:
            flt_dong = st.selectbox("동 필터", ["전체", "A동", "B동", "공통"], key=f"{key_prefix}_flt_dong")
        with f_col2:
            if flt_dong in ["A동", "B동"]:
                flt_rooms = ["전체"] + [f"{rm}호" for rm in sorted(list(df_contracts_all[df_contracts_all["dong"] == flt_dong]["room"].unique()), key=lambda x: int(x) if str(x).isdigit() else str(x))]
            else:
                flt_rooms = ["전체"]
            flt_room = st.selectbox("호실 필터", flt_rooms, key=f"{key_prefix}_flt_room")
        with f_col3:
            flt_month = st.selectbox("조회 기간", [f"{selected_year}년 전체", f"{selected_year}년 {selected_month}월만", "전체 기간"], key=f"{key_prefix}_flt_month")

        df_view = df_rep.copy()
        if len(df_view) > 0:
            if flt_month == f"{selected_year}년 전체":
                df_view = df_view[df_view["repair_date"].astype(str).str.startswith(f"{selected_year}-")]
            elif flt_month == f"{selected_year}년 {selected_month}월만":
                df_view = df_view[df_view["repair_date"].astype(str).str.startswith(f"{selected_year}-{selected_month:02d}-")]
            if flt_dong != "전체":
                df_view = df_view[df_view["dong"] == flt_dong]
            if flt_room != "전체":
                clean_flt_room = flt_room.replace("호", "")
                df_view = df_view[df_view["room"].astype(str) == clean_flt_room]

        if len(df_view) == 0:
            st.info("조회 조건에 해당하는 보수공사 · 지출 내역이 없습니다. 왼쪽 폼에서 보수 내역과 사진/영수증을 등록해 보세요.")
        else:
            sub_sum = int(df_view["cost"].sum())
            st.markdown(f"**조회된 내역: 총 {len(df_view)}건 | 지출 합계: :red[{sub_sum:,}원]**")
            for _, row in df_view.iterrows():
                rep_id = int(row["id"])
                rm_disp = f"{row['room']}호" if str(row["room"]) != "공통" else "공통"
                gcal_link = make_gcal_repair_url(
                    row["dong"], row["room"], row["title"], row["repair_date"],
                    row["description"], row["cost"], row["category"],
                    pro_email=pro_cfg.get("pro_email", DEFAULT_PRO_GOOGLE_EMAIL)
                )
                st.markdown(
                    f'<div style="padding: 12px; border: 1px solid #dee2e6; border-left: 4px solid #0d6efd; border-radius: 8px; background-color: #ffffff; margin-bottom: 8px;">'
                    f'<div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 6px;">'
                    f'<span style="font-size: 15px; font-weight: 800; color: #0f172a;">🛠️ [{row["repair_date"]}] {row["dong"]} {rm_disp} — {row["title"]} <span style="font-size: 12px; background: #e9ecef; padding: 2px 7px; border-radius: 4px; font-weight: bold;">{row["category"]}</span></span>'
                    f'<span style="font-size: 15px; font-weight: 800; color: #dc3545;">비용: {int(row["cost"] or 0):,}원</span>'
                    f'</div>'
                    f'<div style="font-size: 13px; color: #495057; margin-top: 6px;">'
                    f'<b>수리 내역:</b> {row["description"] or "-"}'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True
                )

                b_c1, b_c2, b_c3 = st.columns([4, 4, 2])
                with b_c1:
                    st.markdown(f'<a href="{gcal_link}" target="_blank" style="display:inline-block; padding:5px 10px; background:#e8f5e9; color:#1b5e20 !important; border:1px solid #a5d6a7; border-radius:5px; font-size:12px; font-weight:bold; text-decoration:none;">📅 구글 캘린더에 등록 ↗</a>', unsafe_allow_html=True)
                with b_c3:
                    if st.button("🗑️ 삭제", key=f"{key_prefix}_del_rep_{rep_id}"):
                        delete_repair(rep_id)
                        st.success("보수 내역이 삭제되었습니다.")
                        st.rerun()

                p_path = (row.get("photo_path") or "").strip()
                r_path = (row.get("receipt_path") or "").strip()
                if p_path or r_path:
                    with st.expander(f"📷 {row['dong']} {rm_disp} ({row['repair_date']}) 업로드된 수리사진 · 영수증 보기"):
                        img_c1, img_c2 = st.columns(2)
                        with img_c1:
                            resolved_p = resolve_upload_file_path(p_path)
                            if resolved_p:
                                st.markdown("**📷 수리 현장 사진**")
                                st.image(resolved_p, use_container_width=True)
                            else:
                                st.caption("수리 현장 사진 없음")
                        with img_c2:
                            resolved_r = resolve_upload_file_path(r_path)
                            if resolved_r:
                                st.markdown("**🧾 영수증 / 증빙 사진**")
                                if r_path.lower().endswith(".pdf"):
                                    with open(resolved_r, "rb") as pdf_f:
                                        st.download_button("📄 영수증 PDF 다운로드", pdf_f, file_name=r_path, key=f"{key_prefix}_dl_{rep_id}")
                                else:
                                    st.image(resolved_r, use_container_width=True)
                            else:
                                st.caption("영수증 사진 없음")


# 메뉴 [2] 보수공사 · 지출내역 단독 실행 시
if st.session_state.get("active_menu", "dashboard") == "repairs" and not print_mode:
    render_repairs_and_expenses_section(selected_year, selected_month, key_prefix="top_rep")
    st.stop()


# ==============================================================================
# 10. 메뉴 [3] 실시간 구글 일정관리 뷰 (프로 계정 구글 캘린더 기본 연동)
# ==============================================================================
if st.session_state.get("active_menu", "dashboard") == "calendar" and not print_mode:
    st.subheader("📅 실시간 일정 및 구글 캘린더 관리 (프로 요금제 계정 연동)")
    st.markdown(f"각 세대별(A·B동) 일자별 보수공사 내역이나 일정을 등록하면 **비용, 수리내역, 현장사진, 영수증 사진이 각 호실별로 자동 업로드**되며 프로 계정(`{pro_cfg.get('pro_email', DEFAULT_PRO_GOOGLE_EMAIL)}`) 구글 캘린더와 연동됩니다.")

    col_c1, col_c2 = st.columns([4.5, 5.5])

    with col_c1:
        st.markdown("#### ➕ 새 일정 및 호실별 보수공사 등록")
        s_dong = st.selectbox("동 구분", ["A동", "B동", "공통"], index=0, key="sched_dong")

        df_contracts = get_contracts()
        if s_dong in ["A동", "B동"]:
            rooms_list = sorted(list(df_contracts[df_contracts["dong"] == s_dong]["room"].unique()), key=lambda x: int(x) if str(x).isdigit() else str(x))
        else:
            rooms_list = sorted(list(df_contracts["room"].unique()), key=lambda x: int(x) if str(x).isdigit() else str(x))
        rooms_list = ["공통"] + rooms_list
        s_room = st.selectbox("호수 선택", rooms_list, key="sched_room")

        with st.form("new_schedule_form", clear_on_submit=True):
            s_title = st.selectbox("일정 / 공사 유형", ["보수공사/수리", "누수/배관 보수", "보일러/난방 수리", "전기/조명 수리", "도배/장판 공사", "시설 점검", "계약 만기", "월세 납부일", "퇴실 예정", "기타"], index=0, key="sched_title_type")
            s_custom_title = st.text_input("상세 일정/수리 제목", placeholder="예: 싱크대 수전 교체, 화장실 타일 보수", key="sched_custom_title")
            s_date = st.date_input("일자 선택", datetime.date.today(), key="sched_date")
            s_cost = st.number_input("💰 보수/수리 지출 비용 (원, 일반 일정은 0)", min_value=0, step=10000, value=0, key="sched_cost")
            s_desc = st.text_area("수리 내역 상세 / 메모", placeholder="수리 내역, 시공업체, 특이사항 등을 입력하세요", key="sched_desc")
            s_photo = st.file_uploader("📷 호실 수리 현장 사진 업로드 (선택)", type=["jpg", "jpeg", "png", "webp"], key="sched_photo")
            s_receipt = st.file_uploader("🧾 호실 수리 영수증 사진 업로드 (선택)", type=["jpg", "jpeg", "png", "webp", "pdf"], key="sched_receipt")

            submit_sched = st.form_submit_button("📅 일정 등록 및 호실별 보수내역·사진 업로드", type="primary", use_container_width=True)

            if submit_sched:
                final_title = s_custom_title.strip() if s_custom_title.strip() else s_title
                date_str = s_date.strftime("%Y-%m-%d")
                is_repair_entry = ("보수" in s_title or "수리" in s_title or "공사" in s_title or "점검" in s_title or s_cost > 0 or s_photo is not None or s_receipt is not None)

                if is_repair_entry:
                    p_fname = save_uploaded_file(s_photo, prefix="repair") if s_photo else ""
                    rec_fname = save_uploaded_file(s_receipt, prefix="receipt") if s_receipt else ""
                    add_repair(s_dong, s_room, date_str, s_title, final_title, s_desc.strip(), s_cost, p_fname, rec_fname, also_schedule=True)
                    gcal_add_url = make_gcal_repair_url(
                        s_dong, s_room, final_title, date_str, s_desc.strip(), s_cost, s_title,
                        pro_email=pro_cfg.get("pro_email", DEFAULT_PRO_GOOGLE_EMAIL)
                    )
                else:
                    add_schedule(s_dong, s_room, final_title, date_str, s_desc)
                    gcal_add_url = make_gcal_repair_url(
                        s_dong, s_room, final_title, date_str, s_desc.strip(), 0, s_title,
                        pro_email=pro_cfg.get("pro_email", DEFAULT_PRO_GOOGLE_EMAIL)
                    )

                add_event_to_google_calendar_api(
                    f"[{s_dong} {s_room}] {final_title}",
                    date_str,
                    s_desc.strip(),
                    calendar_id=calendar_id
                )
                st.session_state["last_gcal_link"] = gcal_add_url
                st.success(f"✅ {s_dong} {s_room} 일정 및 호실별 보수내역/사진이 시스템에 등록되었습니다!")
                st.rerun()

        if "last_gcal_link" in st.session_state:
            st.markdown(f"""
            <div style="padding: 10px; background-color: #e8f5e9; border: 1px solid #c8e6c9; border-radius: 5px; margin-top: 10px; text-align: center;">
                <span style="color: #2e7d32; font-weight: bold;">📢 프로 계정({pro_cfg.get('pro_email')}) 구글 캘린더 등록 대기 중</span><br/>
                <a href="{st.session_state['last_gcal_link']}" target="_blank" style="display: inline-block; padding: 6px 12px; background-color: #2e7d32; color: white !important; border-radius: 4px; text-decoration: none; font-size: 13px; font-weight: bold; margin-top: 5px;">구글 캘린더에 일정 최종 저장 ↗</a>
            </div>
            """, unsafe_allow_html=True)
            if st.button("안내 창 닫기", key="close_gcal_link_btn"):
                del st.session_state["last_gcal_link"]
                st.rerun()

    with col_c2:
        st.markdown("#### 📅 일정 목록")
        df_s = get_schedules()

        if len(df_s) == 0:
            st.info("등록된 일정이 없습니다. 왼쪽 폼에서 일정을 등록해 보세요.")
        else:
            f_dong = st.selectbox("동 필터", ["전체", "A동", "B동", "공통"], key="filter_sched_dong")
            filtered_df_s = df_s if f_dong == "전체" else df_s[df_s["dong"] == f_dong]

            for idx, r in filtered_df_s.iterrows():
                date_str = r["event_date"]
                try:
                    date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                except Exception:
                    date_obj = datetime.datetime.now()
                next_day_obj = date_obj + datetime.timedelta(days=1)
                dates_param = f"{date_obj.strftime('%Y%m%d')}/{next_day_obj.strftime('%Y%m%d')}"

                gcal_title = f"[{r['dong']} {r['room']}] {r['title']}"
                gcal_details = f"동호실: {r['dong']} {r['room']}\n구분: {r['title']}\n설명: {r['description']}"
                authuser_q = urllib.parse.quote(pro_cfg.get("pro_email", DEFAULT_PRO_GOOGLE_EMAIL))
                gcal_add_url = (
                    f"https://calendar.google.com/calendar/render?action=TEMPLATE"
                    f"&text={urllib.parse.quote(gcal_title)}"
                    f"&dates={dates_param}"
                    f"&details={urllib.parse.quote(gcal_details)}"
                    f"&authuser={authuser_q}"
                )

                col_row1, col_row2 = st.columns([8, 2])
                with col_row1:
                    st.markdown(f"🗓️ **[{r['event_date']}] {r['dong']} {r['room']}** - **{r['title']}**")
                    if r["description"]:
                        st.markdown(f"<span style='font-size: 12px; color: #555;'>메모: {r['description']}</span>", unsafe_allow_html=True)
                with col_row2:
                    st.markdown(f'<a href="{gcal_add_url}" target="_blank" style="font-size: 11px; color: #2e7d32; font-weight: bold; text-decoration: none;">캘린더 추가 ↗</a>', unsafe_allow_html=True)
                    if st.button("삭제", key=f"del_sched_{r['id']}", type="secondary"):
                        delete_schedule(r["id"])
                        st.success("일정이 삭제되었습니다.")
                        st.rerun()
                st.markdown("<hr style='margin: 0.3rem 0; border-top: 1px dashed #eee;' />", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("### 🖨️ 구글 캘린더 실시간 뷰어 (프로 요금제 계정)")

    embed_cal_id = calendar_id.strip() if calendar_id and calendar_id.strip() else pro_cfg.get("pro_email", DEFAULT_PRO_GOOGLE_EMAIL)
    encoded_cal_id = urllib.parse.quote(embed_cal_id)
    encoded_holiday = urllib.parse.quote("ko.south_korea#holiday@group.v.calendar.google.com")
    embed_url = f"https://calendar.google.com/calendar/embed?src={encoded_cal_id}&src={encoded_holiday}&ctz=Asia%2FSeoul"

    st.markdown(f"""
    <div style="border: 1px solid #ddd; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 10px rgba(0,0,0,0.05); background-color: #ffffff;">
        <iframe src="{embed_url}" style="border: 0;" width="100%" height="600" frameborder="0" scrolling="no"></iframe>
    </div>
    """, unsafe_allow_html=True)
    st.stop()


# ==============================================================================
# 11. 메뉴 [1] 수납 및 계약 종합 대시보드 (원본 100% 동일)
# ==============================================================================
col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.markdown(f"""
    <div class="kpi-card" style="border-left-color: #0d6efd;">
        <div class="kpi-title">총 보증금 합계</div>
        <div class="kpi-val">{dep_tot/100000000:.2f} 억원</div>
        <div style="font-size:12px; color:#6c757d;">실제 보증금: {dep_tot:,}원</div>
    </div>
    """, unsafe_allow_html=True)
with col2:
    st.markdown(f"""
    <div class="kpi-card" style="border-left-color: #198754;">
        <div class="kpi-title">{selected_month}월 수납 대상액</div>
        <div class="kpi-val">{tot_tgt_tot/10000:.0f} 만원</div>
        <div style="font-size:12px; color:#6c757d;">수납 대상: {tot_tgt_tot:,}원</div>
    </div>
    """, unsafe_allow_html=True)
with col3:
    st.markdown(f"""
    <div class="kpi-card" style="border-left-color: #20c997;">
        <div class="kpi-title">{selected_month}월 수납 완료액</div>
        <div class="kpi-val" style="color: #198754;">{tot_coll_tot/10000:.0f} 만원</div>
        <div style="font-size:12px; color:#6c757d;">수납율: {rate_tot:.1f}%</div>
    </div>
    """, unsafe_allow_html=True)
with col4:
    st.markdown(f"""
    <div class="kpi-card" style="border-left-color: #dc3545;">
        <div class="kpi-title">{selected_month}월 미납 금액</div>
        <div class="kpi-val" style="color: #dc3545;">{tot_unp_tot/10000:.0f} 만원</div>
        <div style="font-size:12px; color:#6c757d;">미수금 {tot_unp_tot:,}원</div>
    </div>
    """, unsafe_allow_html=True)
with col5:
    st.markdown(f"""
    <div class="kpi-card" style="border-left-color: #ffc107;">
        <div class="kpi-title">미납 세대 수</div>
        <div class="kpi-val" style="color: #ff9f0a;">{unp_cnt_tot} / {tot_cnt_tot} 세대</div>
        <div style="font-size:12px; color:#6c757d;">전체 39세대 기준</div>
    </div>
    """, unsafe_allow_html=True)

# 동별 요약 및 전체 합계 테이블
st.subheader("📊 동별 요약 및 전체 합계")
st.markdown(f"""
<div class="summary-table-container">
    <table class="summary-table">
        <thead>
            <tr>
                <th rowspan="2">구분</th>
                <th rowspan="2">총 보증금</th>
                <th colspan="3">수납 대상액</th>
                <th colspan="3">수납 완료액</th>
                <th colspan="3">미납 금액</th>
                <th rowspan="2">미납 세대</th>
                <th rowspan="2">수납율</th>
            </tr>
            <tr>
                <th>월세</th>
                <th>관리비</th>
                <th>합계</th>
                <th>월세</th>
                <th>관리비</th>
                <th>합계</th>
                <th>월세</th>
                <th>관리비</th>
                <th>합계</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td class="label-cell"><b>🏠 A동 소계</b> (20세대)</td>
                <td>{dep_a:,}원</td>
                <td>{r_tgt_a:,}원</td>
                <td>{m_tgt_a:,}원</td>
                <td><b>{tot_tgt_a:,}원</b></td>
                <td>{r_coll_a:,}원</td>
                <td>{m_coll_a:,}원</td>
                <td style="color: #198754;"><b>{tot_coll_a:,}원</b></td>
                <td style="color: {'#dc3545' if r_unp_a > 0 else '#212529'};">{r_unp_a:,}원</td>
                <td style="color: {'#dc3545' if m_unp_a > 0 else '#212529'};">{m_unp_a:,}원</td>
                <td style="color: {'#dc3545' if tot_unp_a > 0 else '#212529'}; font-weight: bold;">{tot_unp_a:,}원</td>
                <td>{unp_cnt_a} / {tot_cnt_a} 세대</td>
                <td><b>{rate_a:.1f}%</b></td>
            </tr>
            <tr>
                <td class="label-cell"><b>🏢 B동 소계</b> (19세대)</td>
                <td>{dep_b:,}원</td>
                <td>{r_tgt_b:,}원</td>
                <td>{m_tgt_b:,}원</td>
                <td><b>{tot_tgt_b:,}원</b></td>
                <td>{r_coll_b:,}원</td>
                <td>{m_coll_b:,}원</td>
                <td style="color: #198754;"><b>{tot_coll_b:,}원</b></td>
                <td style="color: {'#dc3545' if r_unp_b > 0 else '#212529'};">{r_unp_b:,}원</td>
                <td style="color: {'#dc3545' if m_unp_b > 0 else '#212529'};">{m_unp_b:,}원</td>
                <td style="color: {'#dc3545' if tot_unp_b > 0 else '#212529'}; font-weight: bold;">{tot_unp_b:,}원</td>
                <td>{unp_cnt_b} / {tot_cnt_b} 세대</td>
                <td><b>{rate_b:.1f}%</b></td>
            </tr>
            <tr class="total-row">
                <td class="label-cell"><b>⭐ 전체 합계</b> (39세대)</td>
                <td>{dep_tot:,}원</td>
                <td>{r_tgt_tot:,}원</td>
                <td>{m_tgt_tot:,}원</td>
                <td><b>{tot_tgt_tot:,}원</b></td>
                <td>{r_coll_tot:,}원</td>
                <td>{m_coll_tot:,}원</td>
                <td style="color: #198754;"><b>{tot_coll_tot:,}원</b></td>
                <td style="color: {'#dc3545' if r_unp_tot > 0 else '#212529'};">{r_unp_tot:,}원</td>
                <td style="color: {'#dc3545' if m_unp_tot > 0 else '#212529'};">{m_unp_tot:,}원</td>
                <td style="color: {'#dc3545' if tot_unp_tot > 0 else '#212529'};"><b>{tot_unp_tot:,}원</b></td>
                <td>{unp_cnt_tot} / {tot_cnt_tot} 세대</td>
                <td style="background-color: #e8f5e9; color: #1b5e20;"><b>{rate_tot:.1f}%</b></td>
            </tr>
        </tbody>
    </table>
</div>
""", unsafe_allow_html=True)
st.caption("💡 스마트폰에서는 표를 좌우로 밀어서(스와이프) 전체 항목을 볼 수 있으며, PC에서는 Ctrl + P로 A4 3장 최적화 인쇄가 가능합니다.")

# 인쇄 모드일 때 3페이지 레이아웃 렌더링
if print_mode:
    st.info("💡 **인쇄 모드가 실행 중입니다.** 브라우저 인쇄 단추(Ctrl + P)를 누르시면 총괄 요약 1장, A동 세부 1장, B동 세부 1장으로 총 3장의 인쇄용 최적화 페이지가 정갈하게 출력됩니다.")

    df_a_detail = df_payments[df_payments["dong"] == "A동"]
    rows_a_html = ""
    for idx, r in df_a_detail.iterrows():
        total_pay = r["rent"] + r["maintenance_fee"]
        status_str = "완납" if r["status"] == "Paid" else "미납"
        status_style = "color: #198754; font-weight: bold;" if r["status"] == "Paid" else "color: #dc3545; font-weight: bold;"
        pay_date = r["payment_date"] or "-"
        note = r["note"] or "-"
        rows_a_html += f"""
        <tr>
            <td><b>{r['room']}호</b></td>
            <td>{r['tenant_name']}</td>
            <td>{r['deposit']:,}원</td>
            <td>{r['rent']:,}원</td>
            <td>{r['maintenance_fee']:,}원</td>
            <td><b>{total_pay:,}원</b></td>
            <td style="{status_style}">{status_str}</td>
            <td>{pay_date}</td>
            <td style="text-align: left;">{note}</td>
        </tr>
        """

    st.markdown(f"""
    <div class="a-dong-print-container">
        <h3 style="text-align: center; margin-bottom: 15px; color: #212529;">🏠 A동 세부 납부 및 계약 대장 ({selected_year}년 {selected_month}월)</h3>
        <table class="detail-table">
            <thead>
                <tr>
                    <th>호실</th>
                    <th>성명</th>
                    <th>보증금</th>
                    <th>월세</th>
                    <th>관리비</th>
                    <th>합계</th>
                    <th>수납 상태</th>
                    <th>납부일</th>
                    <th>비고</th>
                </tr>
            </thead>
            <tbody>
                {rows_a_html}
            </tbody>
        </table>
    </div>
    """, unsafe_allow_html=True)

    df_b_detail = df_payments[df_payments["dong"] == "B동"]
    rows_b_html = ""
    for idx, r in df_b_detail.iterrows():
        total_pay = r["rent"] + r["maintenance_fee"]
        status_str = "완납" if r["status"] == "Paid" else "미납"
        status_style = "color: #198754; font-weight: bold;" if r["status"] == "Paid" else "color: #dc3545; font-weight: bold;"
        pay_date = r["payment_date"] or "-"
        note = r["note"] or "-"
        rows_b_html += f"""
        <tr>
            <td><b>{r['room']}호</b></td>
            <td>{r['tenant_name']}</td>
            <td>{r['deposit']:,}원</td>
            <td>{r['rent']:,}원</td>
            <td>{r['maintenance_fee']:,}원</td>
            <td><b>{total_pay:,}원</b></td>
            <td style="{status_style}">{status_str}</td>
            <td>{pay_date}</td>
            <td style="text-align: left;">{note}</td>
        </tr>
        """

    st.markdown(f"""
    <div class="b-dong-print-container">
        <h3 style="text-align: center; margin-bottom: 15px; color: #212529;">🏢 B동 세부 납부 및 계약 대장 ({selected_year}년 {selected_month}월)</h3>
        <table class="detail-table">
            <thead>
                <tr>
                    <th>호실</th>
                    <th>성명</th>
                    <th>보증금</th>
                    <th>월세</th>
                    <th>관리비</th>
                    <th>합계</th>
                    <th>수납 상태</th>
                    <th>납부일</th>
                    <th>비고</th>
                </tr>
            </thead>
            <tbody>
                {rows_b_html}
            </tbody>
        </table>
    </div>
    """, unsafe_allow_html=True)
    st.stop()


# ==============================================================================
# 12. 대시보드 하단 4개 서브 탭 (월세 납부 관리 / 계약 명단 / 보수공사 / 미납 경보)
# ==============================================================================
tab_payment, tab_contracts, tab_repairs_sub, tab_alerts = st.tabs([
    "💰 월세 납부 관리",
    "📋 세대별 계약 명단",
    "🛠️ 세대별 보수공사 · 지출내역",
    "🚨 미납자 경고 알림"
])

# TAB 1: 월세 납부 관리
with tab_payment:
    st.subheader(f"📅 {selected_year}년 {selected_month}월 임대료 수납 현황")

    f_p1, f_p2 = st.columns([2, 2])
    with f_p1:
        dong_filter = st.radio("동 필터", ["전체", "A동", "B동"], horizontal=True, key="dong_pay_filter")
    with f_p2:
        status_filter = st.radio("수납 상태 필터", ["전체", "미납만", "완납만"], horizontal=True, key="status_pay_filter")

    filtered_pay = df_payments.copy()
    if dong_filter != "전체":
        filtered_pay = filtered_pay[filtered_pay["dong"] == dong_filter]
    if status_filter == "미납만":
        filtered_pay = filtered_pay[filtered_pay["status"] != "Paid"]
    elif status_filter == "완납만":
        filtered_pay = filtered_pay[filtered_pay["status"] == "Paid"]

    st.markdown("---")

    for idx, r in filtered_pay.iterrows():
        c_id = r["contract_id"]
        phone_val = (r.get("phone") or "").strip()
        room_desc = f"**{r['dong']} {r['room']}호** ({r['tenant_name']})"
        if r["rent"] == 0:
            contract_type = "전세"
            badge_style = "font-size: 12px; font-weight: bold; background-color: #0d6efd; color: #ffffff !important; padding: 3px 8px; border-radius: 5px; margin-left: 6px; display: inline-block;"
        else:
            contract_type = f"월세 {r['rent']/10000:.0f}만"
            badge_style = "font-size: 12px; font-weight: bold; background-color: #fd7e14; color: #ffffff !important; padding: 3px 8px; border-radius: 5px; margin-left: 6px; display: inline-block;"
        total_pay = r["rent"] + r["maintenance_fee"]

        row_col1, row_col2, row_col3, row_col4, row_col5 = st.columns([2.3, 2.2, 1.8, 1.5, 2.2])

        with row_col1:
            st.markdown(f"{room_desc} <span style='{badge_style}'>{contract_type}</span>", unsafe_allow_html=True)
            if phone_val:
                sms_body = urllib.parse.quote(f"[회기동 주택 안내] {r['dong']} {r['room']}호 {r['tenant_name']}님, {selected_month}월 청구액은 {total_pay:,}원입니다.")
                st.markdown(
                    f"<span style='font-size:12px; color:#0d6efd; font-weight:bold;'>📞 <a href='tel:{phone_val}' style='color:#0d6efd; text-decoration:none;'>{phone_val}</a></span> "
                    f"&nbsp;<a href='sms:{phone_val}?body={sms_body}' style='font-size:11px; background:#0d6efd; color:#fff !important; padding:2px 7px; border-radius:4px; text-decoration:none; font-weight:bold;'>💬 문자보내기</a>",
                    unsafe_allow_html=True
                )
            else:
                st.caption("📞 전화번호 미등록 (계약 명단 탭에서 입력)")
        with row_col2:
            st.text(f"청구액: {total_pay:,}원 (관리비 {r['maintenance_fee']:,}원)")
        with row_col3:
            if r["status"] == "Paid":
                st.markdown(f"<span class='status-paid'>🟢 완납 완료</span> ({r['payment_date'] or ''})", unsafe_allow_html=True)
            else:
                st.markdown("<span class='status-unpaid'>🔴 미납 상태</span>", unsafe_allow_html=True)
        with row_col4:
            if r["status"] == "Paid":
                if st.button("미납 처리", key=f"unpaid_{c_id}_{selected_year}_{selected_month}", use_container_width=True):
                    update_payment_status(c_id, selected_year, selected_month, "Unpaid", None)
                    st.success(f"{r['room']}호 미납 처리되었습니다.")
                    st.rerun()
            else:
                if st.button("완납 확인", key=f"paid_{c_id}_{selected_year}_{selected_month}", type="primary", use_container_width=True):
                    today_str = datetime.date.today().strftime("%Y-%m-%d")
                    update_payment_status(c_id, selected_year, selected_month, "Paid", today_str)
                    st.success(f"{r['room']}호 수납 처리 완료!")
                    st.rerun()
        with row_col5:
            st.text(f"메모: {r['note'] or '-'}")

        st.markdown("<hr style='margin: 0.5rem 0; border-top: 1px solid #eee;' />", unsafe_allow_html=True)

# TAB 2: 세대별 계약 명단
with tab_contracts:
    st.subheader("📋 다가구 39세대 계약 관리 대장")

    dong_filter_c = st.radio("동 필터", ["전체", "A동", "B동"], horizontal=True, key="dong_contract_filter")

    df_c = get_contracts()
    if "phone" not in df_c.columns:
        df_c["phone"] = ""
    df_c["phone"] = df_c["phone"].fillna("")
    if dong_filter_c != "전체":
        df_c = df_c[df_c["dong"] == dong_filter_c]

    df_display = df_c.copy()
    df_display["보증금"] = df_display["deposit"].map(lambda x: f"{x:,}원")
    df_display["월세"] = df_display["rent"].map(lambda x: f"{x:,}원" if x > 0 else "전세")
    df_display["관리비"] = df_display["maintenance_fee"].map(lambda x: f"{x:,}원")

    def get_remaining_days(exp_str):
        if not exp_str:
            return "만기 정보 없음"
        try:
            exp_date = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
            today = datetime.date.today()
            delta = exp_date - today
            if delta.days < 0:
                return f"만료 ({abs(delta.days)}일 경과)"
            elif delta.days <= 90:
                return f"⚠️ 만기 임박 ({delta.days}일 남음)"
            else:
                months = delta.days // 30
                return f"{months}개월 남음 ({delta.days}일)"
        except Exception:
            return exp_str

    df_display["계약 기간 상태"] = df_display["expiry_date"].apply(get_remaining_days)

    df_show = df_display[["id", "dong", "room", "tenant_name", "phone", "보증금", "월세", "관리비", "expiry_date", "계약 기간 상태", "note"]]
    df_show.columns = ["ID", "동", "호수", "세입자명", "전화번호", "보증금", "월세", "관리비", "만기일", "남은 기간", "비고"]

    st.dataframe(df_show, use_container_width=True, hide_index=True)

    st.markdown("---")

    # 호별 계약자 전화번호 빠른 입력 및 메시지 발송
    st.subheader("📞 호별 계약자 전화번호 빠른 입력 및 메시지 발송")
    if len(df_c) > 0:
        col_qp1, col_qp2, col_qp3 = st.columns([3, 3, 2])
        with col_qp1:
            qp_select = st.selectbox("호실 선택", [f"{r['dong']} {r['room']}호 ({r['tenant_name']})" for idx, r in df_c.iterrows()], key="qp_room_sel")
        if qp_select:
            qp_dong = qp_select.split()[0]
            qp_room = qp_select.split()[1].replace("호", "")
            qp_row = df_c[(df_c["dong"] == qp_dong) & (df_c["room"] == qp_room)].iloc[0]
            qp_id = int(qp_row["id"])
            cur_phone = str(qp_row.get("phone") or "")
            with col_qp2:
                new_qp_phone = st.text_input("계약자 전화번호 입력 (예: 010-1234-5678)", value=cur_phone, key=f"qp_input_{qp_id}")
            with col_qp3:
                st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                if st.button("💾 전화번호 저장", key=f"qp_save_{qp_id}", type="primary", use_container_width=True):
                    update_contract_phone(qp_id, new_qp_phone.strip())
                    st.success(f"{qp_dong} {qp_room}호 ({qp_row['tenant_name']}) 전화번호({new_qp_phone.strip()})가 저장되었습니다!")
                    st.rerun()
            if cur_phone:
                default_msg = f"안녕하세요, {qp_row['tenant_name']}님. 회기동 주택 {qp_dong} {qp_room}호 임대인입니다."
                custom_msg = st.text_input("발송할 문자 메시지 내용", value=default_msg, key=f"qp_msg_{qp_id}")
                sms_url = f"sms:{cur_phone}?body={urllib.parse.quote(custom_msg)}"
                st.markdown(
                    f"<a href='{sms_url}' style='display:inline-block; padding:8px 16px; background:#0d6efd; color:#fff !important; border-radius:6px; text-decoration:none; font-weight:bold; font-size:14px; margin-right:8px;'>💬 {cur_phone} ({qp_row['tenant_name']}님)에게 바로 문자 보내기 ↗</a>"
                    f"<a href='tel:{cur_phone}' style='display:inline-block; padding:8px 16px; background:#198754; color:#fff !important; border-radius:6px; text-decoration:none; font-weight:bold; font-size:14px;'>📞 바로 전화 걸기</a>",
                    unsafe_allow_html=True
                )

    st.markdown("---")

    # 계약 정보 전체 수정 및 퇴거 처리
    st.subheader("🛠️ 계약 정보 전체 수정 및 퇴거 처리")
    c_select = st.selectbox(
        "수정/삭제할 호실 선택",
        [f"{r['dong']} {r['room']}호 ({r['tenant_name']})" for idx, r in df_c.iterrows()],
        index=None,
        placeholder="관리할 호실을 선택하세요..."
    )

    if c_select:
        sel_dong = c_select.split()[0]
        sel_room = c_select.split()[1].replace("호", "")
        sel_row = df_c[(df_c["dong"] == sel_dong) & (df_c["room"] == sel_room)].iloc[0]
        c_id_edit = int(sel_row["id"])

        col_ed1, col_ed2 = st.columns(2)
        with col_ed1:
            st.markdown(f"#### 📝 {sel_dong} {sel_room}호 계약 수정")
            with st.form("edit_contract_form"):
                edit_name = st.text_input("임차인 성명", value=sel_row["tenant_name"])
                edit_phone = st.text_input("임차인 전화번호 (예: 010-1234-5678)", value=str(sel_row.get("phone") or ""))
                edit_dep = st.number_input("보증금 (원)", value=int(sel_row["deposit"]), step=1000000)
                edit_rent = st.number_input("월세 (원)", value=int(sel_row["rent"]), step=10000)
                edit_m_fee = st.number_input("관리비 (원)", value=int(sel_row["maintenance_fee"]), step=10000)

                try:
                    exp_parsed = datetime.datetime.strptime(sel_row["expiry_date"], "%Y-%m-%d").date()
                except Exception:
                    exp_parsed = datetime.date.today()

                edit_expiry = st.date_input("만기일", exp_parsed)
                edit_note = st.text_input("비고", value=sel_row["note"] or "")

                save_edit = st.form_submit_button("변경사항 저장", type="primary", use_container_width=True)
                if save_edit:
                    update_contract(c_id_edit, sel_dong, sel_room, edit_name, edit_phone.strip(), edit_dep, edit_rent, edit_m_fee, edit_expiry.strftime("%Y-%m-%d"), edit_note)
                    st.success(f"{sel_dong} {sel_room}호 계약 정보가 변경되었습니다.")
                    st.rerun()

        with col_ed2:
            st.markdown(f"#### 🗑️ {sel_dong} {sel_room}호 계약 해지 / 퇴거")
            st.warning(f"경고: 계약을 해지하면 해당 호실의 모든 납부 이력({selected_year}년 전체 등)이 영구 삭제됩니다.")
            confirm_del = st.checkbox(f"위 내용을 확인했으며, {sel_dong} {sel_room}호 계약을 해지하겠습니다.")

            if st.button("계약 영구 삭제", type="primary", disabled=not confirm_del, use_container_width=True):
                delete_contract(c_id_edit)
                st.error(f"{sel_dong} {sel_room}호 계약 해지 처리가 완료되었습니다.")
                st.rerun()

            st.markdown("---")
            st.markdown(f"#### 🛠️ {sel_dong} {sel_room}호 보수공사 내역 · 사진/영수증")
            df_room_rep = get_repairs()
            if len(df_room_rep) > 0:
                df_room_rep = df_room_rep[(df_room_rep["dong"] == sel_dong) & (df_room_rep["room"].astype(str) == str(sel_room))]
            if len(df_room_rep) == 0:
                st.caption("등록된 보수공사 내역이 없습니다.")
            else:
                st.markdown(f"**총 {len(df_room_rep)}건 | 누적 지출: :red[{int(df_room_rep['cost'].sum()):,}원]**")
                for _, rr in df_room_rep.iterrows():
                    st.markdown(f"- **[{rr['repair_date']}] {rr['title']}** ({int(rr['cost'] or 0):,}원) : {rr['description'] or '-'}")
                    p_file = resolve_upload_file_path((rr.get("photo_path") or "").strip())
                    r_file = resolve_upload_file_path((rr.get("receipt_path") or "").strip())
                    if p_file:
                        st.image(p_file, caption=f"📷 수리사진 ({rr['repair_date']})", width=220)
                    if r_file and not r_file.lower().endswith(".pdf"):
                        st.image(r_file, caption=f"🧾 영수증 ({rr['repair_date']})", width=220)

# TAB 3: 세대별 보수공사 · 지출내역
with tab_repairs_sub:
    render_repairs_and_expenses_section(selected_year, selected_month, key_prefix="sub_rep")

# TAB 4: 미납자 경고 알림
with tab_alerts:
    st.subheader(f"🚨 {selected_year}년 {selected_month}월 미납 세대 경보")

    unpaid_df = df_payments[df_payments["status"] != "Paid"]

    if len(unpaid_df) == 0:
        st.success("🎉 이번 달 미납 세대가 없습니다! 수납율 100% 달성!")
    else:
        st.markdown(f"현재 미납 상태인 세대는 총 **{len(unpaid_df)}세대**입니다.")

        for idx, r in unpaid_df.iterrows():
            c_id = r["contract_id"]
            phone_val = (r.get("phone") or "").strip()
            total_bill = r["rent"] + r["maintenance_fee"]
            room_desc = f"{r['dong']} {r['room']}호 ({r['tenant_name']}님)"
            alert_text = f"""[임대료 납부 안내]
안녕하세요, {r['tenant_name']}님.
회기동 주택 {r['dong']} {r['room']}호 {selected_month}월 임대료(월세/관리비) 미납을 안내드립니다.

■ 납부 금액: {total_bill:,}원 (월세 {r['rent']:,}원 + 관리비 {r['maintenance_fee']:,}원)
■ 계좌 번호: 신한은행 110-123-456789 (예금주: 임대인)

안전하고 원활한 주택 관리를 위해 납부를 부탁드립니다. 감사합니다."""

            col_a1, col_a2, col_a3 = st.columns([3.2, 3.8, 3])
            with col_a1:
                st.markdown(f"🚪 **{room_desc}**")
                st.markdown(f"미납 금액: **{total_bill:,}원** (월세 {r['rent']:,}원 + 관리비 {r['maintenance_fee']:,}원)")
                if phone_val:
                    st.markdown(f"📞 연락처: **{phone_val}**")
                else:
                    st.caption("📞 연락처 미등록")
            with col_a2:
                if r["alert_sent_date"]:
                    st.info(f"최근 경고 알림 발송일: **{r['alert_sent_date']}**")
                else:
                    st.warning("⚠️ 이번 달 발송 이력 없음")
            with col_a3:
                sms_target = phone_val if phone_val else ""
                sms_href = f"sms:{sms_target}?body={urllib.parse.quote(alert_text)}"
                st.markdown(
                    f"<a href='{sms_href}' style='display:inline-block; width:100%; text-align:center; padding:7px 12px; background:#0d6efd; color:#fff !important; border-radius:6px; text-decoration:none; font-weight:bold; font-size:13px; margin-bottom:6px;'>💬 {'문자 바로 보내기 (' + phone_val + ')' if phone_val else '문자앱으로 안내문 열기'}</a>",
                    unsafe_allow_html=True
                )
                if st.button("안내문 미리보기 / 발송 기록", key=f"send_alert_{c_id}_{selected_year}_{selected_month}", use_container_width=True):
                    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    update_alert_date(c_id, selected_year, selected_month, now_str)
                    st.session_state[f"alert_preview_{c_id}"] = alert_text
                    st.success("발송 이력이 기록되었습니다!")
                    st.rerun()

            if f"alert_preview_{c_id}" in st.session_state:
                st.code(st.session_state[f"alert_preview_{c_id}"], language="markdown")
                if st.button("닫기", key=f"close_preview_{c_id}"):
                    del st.session_state[f"alert_preview_{c_id}"]
                    st.rerun()

            st.markdown("<hr style='margin: 0.5rem 0; border-top: 1px dashed #ccc;' />", unsafe_allow_html=True)
