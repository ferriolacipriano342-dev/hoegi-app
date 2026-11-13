import streamlit as st
import streamlit.components.v1 as components
import sqlite3
import datetime
import os
import shutil
import json
import urllib.parse
import requests
import html as html_lib
import xml.etree.ElementTree as ET

# ==============================================================================
# 0. 페이지 기본 설정 (전체 화면 모바일 웹앱 전용)
# ==============================================================================
st.set_page_config(
    page_title="회기동 관리시스템",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="collapsed",
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ORIGINAL_DIR = r"C:\Users\sdgpk\.gemini\antigravity\scratch\rental_management"
BACKUP_DIR = os.path.join(BASE_DIR, "임대관리_안전백업")
MOBILE_DB_PATH = os.path.join(BASE_DIR, "임대관리_스마트폰용.db")
CLOUD_FALLBACK_DB_PATH = os.path.join(BASE_DIR, "rental_management.db")
DEFAULT_PRO_GOOGLE_EMAIL = "ferriolacipriano342@gmail.com"


def ensure_mobile_database():
    if os.path.exists(MOBILE_DB_PATH):
        return MOBILE_DB_PATH
    if os.path.exists(CLOUD_FALLBACK_DB_PATH):
        return CLOUD_FALLBACK_DB_PATH
    for src in [
        os.path.join(BACKUP_DIR, "rental_management.db"),
        os.path.join(ORIGINAL_DIR, "rental_management.db"),
    ]:
        if os.path.exists(src):
            try:
                shutil.copy2(src, MOBILE_DB_PATH)
                return MOBILE_DB_PATH
            except Exception:
                pass
    return MOBILE_DB_PATH


ACTIVE_DB_PATH = ensure_mobile_database()


def get_db():
    conn = sqlite3.connect(ACTIVE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_tables():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
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
    cur.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id INTEGER,
        year INTEGER,
        month INTEGER,
        status TEXT DEFAULT 'Unpaid',
        payment_date TEXT,
        alert_sent_date TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dong TEXT,
        room TEXT,
        title TEXT,
        event_date TEXT,
        description TEXT
    )
    """)
    cur.execute("""
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
    cur.execute("CREATE TABLE IF NOT EXISTS app_assets (key TEXT PRIMARY KEY, mime TEXT, b64 TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS law_search_cache (cache_key TEXT PRIMARY KEY, json_data TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS law_detail_cache (detail_key TEXT PRIMARY KEY, json_data TEXT)")
    cur.execute("PRAGMA table_info(contracts)")
    cols = [row[1] for row in cur.fetchall()]
    if "phone" not in cols:
        cur.execute("ALTER TABLE contracts ADD COLUMN phone TEXT DEFAULT ''")
    conn.commit()
    conn.close()


init_tables()


# ==============================================================================
# 1. 전체 데이터 및 에셋 로드 (39세대 계약, 전 월 수납내역, 보수공사, 일정, 법령 캐시)
# ==============================================================================
def build_full_bundle():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT id as contract_id, dong, room, tenant_name, COALESCE(phone, '') as phone,
               deposit, rent, maintenance_fee, expiry_date, note
        FROM contracts
        ORDER BY dong, CAST(room AS INTEGER)
    """)
    contracts = [dict(r) for r in cur.fetchall()]

    today = datetime.date.today()
    for c in contracts:
        c["total_bill"] = (c.get("rent") or 0) + (c.get("maintenance_fee") or 0)
        c["phone"] = (c.get("phone") or "").strip()
        exp_str = c.get("expiry_date") or ""
        c["expiry_urgent"] = False
        if exp_str:
            try:
                exp_date = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
                delta = (exp_date - today).days
                if delta < 0:
                    c["expiry_badge"] = f"만료 ({abs(delta)}일 경과)"
                    c["expiry_urgent"] = True
                elif delta <= 90:
                    c["expiry_badge"] = f"만기 임박 ({delta}일 남음)"
                    c["expiry_urgent"] = True
                else:
                    c["expiry_badge"] = f"{delta // 30}개월 남음 ({delta}일)"
            except Exception:
                c["expiry_badge"] = exp_str
        else:
            c["expiry_badge"] = "만기 정보 없음"

    cur.execute("SELECT contract_id, year, month, status, payment_date, alert_sent_date FROM payments")
    payments_map = {}
    for r in cur.fetchall():
        ym_key = f"{r['year']}_{r['month']}"
        if ym_key not in payments_map:
            payments_map[ym_key] = {}
        payments_map[ym_key][str(r["contract_id"])] = {
            "status": r["status"] or "Unpaid",
            "payment_date": r["payment_date"] or "",
            "alert_sent_date": r["alert_sent_date"] or "",
        }

    cur.execute("SELECT * FROM schedules ORDER BY event_date ASC")
    schedules = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT * FROM repairs ORDER BY repair_date DESC, id DESC")
    repairs = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT key, mime, b64 FROM app_assets")
    assets = {}
    for r in cur.fetchall():
        assets[r["key"]] = f"data:{r['mime']};base64,{r['b64']}"

    cur.execute("SELECT cache_key, json_data FROM law_search_cache")
    law_search = {}
    for r in cur.fetchall():
        try:
            law_search[r["cache_key"]] = json.loads(r["json_data"])
        except Exception:
            pass

    cur.execute("SELECT detail_key, json_data FROM law_detail_cache")
    law_detail = {}
    for r in cur.fetchall():
        try:
            law_detail[r["detail_key"]] = json.loads(r["json_data"])
        except Exception:
            pass

    conn.close()

    return {
        "pro_email": DEFAULT_PRO_GOOGLE_EMAIL,
        "contracts": contracts,
        "payments_map": payments_map,
        "schedules": schedules,
        "repairs": repairs,
        "assets": assets,
        "law_search": law_search,
        "law_detail": law_detail,
    }


BUNDLE_DATA = build_full_bundle()
ICON_DATA_URI = BUNDLE_DATA["assets"].get("icon_192", "")

# ==============================================================================
# 2. Streamlit 기본 여백/헤더 완전 제거 및 전체화면(100vw x 100dvh) 모바일 뷰포트 적용
# ==============================================================================
st.markdown("""
<style>
    header[data-testid="stHeader"],
    [data-testid="stToolbar"],
    [data-testid="stDecoration"],
    [data-testid="stStatusWidget"],
    [data-testid="stSidebar"],
    [data-testid="collapsedControl"],
    #MainMenu,
    footer {
        display: none !important;
    }
    html, body, [data-testid="stAppViewContainer"], [data-testid="stApp"], .main, .block-container {
        margin: 0 !important;
        padding: 0 !important;
        width: 100vw !important;
        max-width: 100vw !important;
        height: 100dvh !important;
        overflow: hidden !important;
        background-color: #f1f5f9 !important;
    }
    iframe {
        position: fixed !important;
        top: 0 !important;
        left: 0 !important;
        width: 100vw !important;
        height: 100dvh !important;
        border: none !important;
        margin: 0 !important;
        padding: 0 !important;
        z-index: 999999 !important;
        display: block !important;
    }
</style>
""", unsafe_allow_html=True)

# ==============================================================================
# 3. 모바일 전용 웹앱 HTML/CSS/JS (첨부 이미지 디자인 스펙 100% 완벽 구현)
# ==============================================================================
MOBILE_WEBAPP_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0, user-scalable=yes, viewport-fit=cover" />
    <meta name="theme-color" content="#0d6efd" />
    <title>회기동 관리시스템</title>
    <style>
        :root {
            --primary: #0d6efd;
            --primary-dark: #0a58ca;
            --success: #198754;
            --danger: #dc3545;
            --orange: #fd7e14;
            --bg: #f1f5f9;
            --card: #ffffff;
            --text: #0f172a;
            --muted: #64748b;
            --border: #e2e8f0;
            --fs-scale: 1.25;
        }
        * {
            box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
        }
        html, body {
            margin: 0;
            padding: 0;
            width: 100%;
            height: 100%;
            overflow-x: hidden;
            font-family: -apple-system, BlinkMacSystemFont, "Pretendard", "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
            background: var(--bg);
            color: var(--text);
            font-size: calc(14px * var(--fs-scale, 1.25));
            line-height: 1.42;
        }
        body {
            padding-bottom: 84px;
        }

        /* =========================================================
           1. 상단 블루 헤더 (건물 아이콘 + 타이틀 + 연/월 가로 드롭다운)
           ========================================================= */
        .app-header {
            position: sticky;
            top: 0;
            z-index: 100;
            background: linear-gradient(180deg, #0d6efd 0%, #0b5ed7 100%);
            color: #ffffff;
            padding: 14px 14px 10px 14px;
            box-shadow: 0 2px 10px rgba(13, 110, 253, 0.25);
        }
        .header-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
        }
        .header-left {
            display: flex;
            align-items: center;
            gap: 10px;
            min-width: 0;
            flex: 1;
        }
        .app-icon-img {
            width: 44px;
            height: 44px;
            border-radius: 11px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.35);
            border: 1.5px solid rgba(255,255,255,0.65);
            flex-shrink: 0;
            object-fit: cover;
            background: #0a192f;
        }
        .app-title-wrap {
            min-width: 0;
            flex: 1;
        }
        .app-title {
            font-size: 20px;
            font-weight: 900;
            letter-spacing: -0.5px;
            color: #ffffff;
            line-height: 1.2;
            word-break: keep-all;
        }
        .app-sub {
            font-size: 13px;
            color: rgba(255, 255, 255, 0.92);
            margin-top: 3px;
            font-weight: 600;
            line-height: 1.3;
        }
        .ym-picker {
            display: flex;
            flex-direction: row;
            gap: 6px;
            align-items: center;
            flex-shrink: 0;
        }
        .ym-picker select {
            background: rgba(255, 255, 255, 0.22);
            color: #ffffff;
            border: 1.2px solid rgba(255, 255, 255, 0.55);
            border-radius: 9px;
            padding: 7px 8px;
            font-size: 15px;
            font-weight: 800;
            outline: none;
            cursor: pointer;
            appearance: auto;
        }
        .ym-picker select option {
            color: #0f172a;
            background: #ffffff;
        }

        /* =========================================================
           2. 글씨 크기 조절 바 ('🔍 글씨 크기' + 슬라이더 + [- 가-] [125%] [+ 가+])
           ========================================================= */
        .font-zoom-bar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            margin-top: 10px;
            padding-top: 9px;
            border-top: 1px solid rgba(255, 255, 255, 0.28);
        }
        .font-zoom-label {
            font-size: 13.5px;
            font-weight: 800;
            color: #ffffff;
            display: flex;
            align-items: center;
            gap: 5px;
            white-space: nowrap;
            flex-shrink: 0;
        }
        .font-slider-wrap {
            flex: 1;
            display: flex;
            align-items: center;
            padding: 0 4px;
            min-width: 50px;
        }
        .font-slider {
            width: 100%;
            height: 5px;
            accent-color: #ffffff;
            cursor: pointer;
            margin: 0;
        }
        .font-zoom-controls {
            display: flex;
            align-items: center;
            gap: 6px;
            flex-shrink: 0;
        }
        .fz-btn {
            background: #ffffff;
            color: #0a58ca;
            border: none;
            border-radius: 8px;
            padding: 6px 11px;
            font-size: 13.5px;
            font-weight: 900;
            cursor: pointer;
            box-shadow: 0 1px 3px rgba(0,0,0,0.2);
            white-space: nowrap;
            display: inline-flex;
            align-items: center;
            gap: 3px;
        }
        .fz-btn:active {
            transform: scale(0.96);
            background: #e2e8f0;
        }
        .fz-badge {
            background: rgba(15, 23, 42, 0.38);
            color: #ffffff;
            border: 1px solid rgba(255, 255, 255, 0.45);
            border-radius: 8px;
            padding: 5px 10px;
            font-size: 13.5px;
            font-weight: 800;
            min-width: 54px;
            text-align: center;
            cursor: pointer;
        }

        /* =========================================================
           3. 메인 컨테이너 & 2x2 요약 카드
           ========================================================= */
        .container {
            padding: 12px 12px;
            max-width: 640px;
            margin: 0 auto;
        }
        .kpi-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
            margin-bottom: 12px;
        }
        .kpi-box {
            background: var(--card);
            border-radius: 14px;
            padding: 13px 14px;
            box-shadow: 0 2px 6px rgba(15, 23, 42, 0.06);
            border-left: 4.5px solid var(--primary);
            min-width: 0;
        }
        .kpi-label {
            font-size: calc(12px * var(--fs-scale, 1.25));
            color: #64748b;
            font-weight: 800;
            letter-spacing: -0.2px;
        }
        .kpi-value {
            font-size: calc(20px * var(--fs-scale, 1.25));
            font-weight: 900;
            margin-top: 4px;
            letter-spacing: -0.5px;
            line-height: 1.2;
        }
        .kpi-sub {
            font-size: calc(11px * var(--fs-scale, 1.25));
            color: #64748b;
            margin-top: 4px;
            line-height: 1.3;
            word-break: keep-all;
        }

        /* =========================================================
           4. 동별 요약 표 (모바일 화면에 잘림 없이 100% 꽉 차는 조밀한 레이아웃)
           ========================================================= */
        .subtotal-card {
            background: var(--card);
            border-radius: 14px;
            padding: 12px 10px;
            margin-bottom: 14px;
            box-shadow: 0 2px 6px rgba(15, 23, 42, 0.06);
            width: 100%;
            overflow: hidden;
        }
        .subtotal-header {
            font-size: 15px;
            font-weight: 900;
            color: #0f172a;
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .subtotal-table {
            width: 100%;
            border-collapse: collapse;
            table-layout: fixed;
            text-align: center;
            font-size: 11.5px !important;
            letter-spacing: -0.4px;
        }
        .subtotal-table th {
            background: #f8fafc;
            color: #334155;
            padding: 6px 2px;
            border-bottom: 1.5px solid #e2e8f0;
            font-weight: 800;
            font-size: 11px !important;
            line-height: 1.2;
            word-break: keep-all;
        }
        .subtotal-table td {
            padding: 7px 2px;
            border-bottom: 1px solid #f1f5f9;
            vertical-align: middle;
            font-size: 11.5px !important;
            line-height: 1.25;
            word-break: keep-all;
        }
        .subtotal-table .sub-caption {
            font-size: 9.5px !important;
            color: #64748b;
            display: block;
            margin-top: 1px;
            letter-spacing: -0.5px;
        }
        .subtotal-table tr.tot-row {
            background: #eff6ff;
            font-weight: 800;
        }

        /* 필터 버튼 및 검색창 */
        .filter-bar {
            display: flex;
            gap: 6px;
            margin-bottom: 10px;
            overflow-x: auto;
            padding-bottom: 2px;
        }
        .pill-btn {
            border: 1px solid var(--border);
            background: var(--card);
            color: var(--text);
            padding: 8px 13px;
            border-radius: 20px;
            font-size: calc(12.5px * var(--fs-scale, 1.25));
            font-weight: 800;
            white-space: nowrap;
            cursor: pointer;
        }
        .pill-btn.active {
            background: var(--primary);
            color: #fff;
            border-color: var(--primary);
        }
        .search-input {
            width: 100%;
            padding: 11px 12px;
            border: 1px solid var(--border);
            border-radius: 10px;
            font-size: calc(13.5px * var(--fs-scale, 1.25));
            margin-bottom: 12px;
            background: var(--card);
        }

        /* 호실별 카드 */
        .room-card {
            background: var(--card);
            border-radius: 13px;
            padding: 14px;
            margin-bottom: 10px;
            box-shadow: 0 1px 4px rgba(0,0,0,0.05);
            border: 1px solid var(--border);
        }
        .room-top {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
            gap: 6px;
            flex-wrap: wrap;
        }
        .room-title {
            font-size: calc(15.5px * var(--fs-scale, 1.25));
            font-weight: 800;
            display: flex;
            align-items: center;
            gap: 6px;
            flex-wrap: wrap;
        }
        .badge-jeonse {
            background: #0d6efd;
            color: #ffffff !important;
            font-size: calc(11.5px * var(--fs-scale, 1.25));
            font-weight: 800;
            padding: 3px 8px;
            border-radius: 6px;
            display: inline-block;
        }
        .badge-wolse {
            background: #fd7e14;
            color: #ffffff !important;
            font-size: calc(11.5px * var(--fs-scale, 1.25));
            font-weight: 800;
            padding: 3px 8px;
            border-radius: 6px;
            display: inline-block;
        }
        .status-pill-paid {
            background: #dcfce7;
            color: #166534;
            font-size: calc(11.5px * var(--fs-scale, 1.25));
            font-weight: 800;
            padding: 4px 9px;
            border-radius: 20px;
        }
        .status-pill-unpaid {
            background: #fee2e2;
            color: #991b1b;
            font-size: calc(11.5px * var(--fs-scale, 1.25));
            font-weight: 800;
            padding: 4px 9px;
            border-radius: 20px;
        }
        .room-body {
            display: flex;
            justify-content: space-between;
            align-items: flex-end;
            gap: 10px;
            flex-wrap: wrap;
            font-size: calc(13px * var(--fs-scale, 1.25));
            color: #334155;
        }
        .room-info-line {
            margin: 4px 0;
        }
        .action-btn {
            border: none;
            border-radius: 8px;
            padding: 9px 14px;
            font-size: calc(12.5px * var(--fs-scale, 1.25));
            font-weight: 800;
            cursor: pointer;
            white-space: nowrap;
        }
        .btn-mark-paid {
            background: var(--primary);
            color: #fff;
        }
        .btn-mark-unpaid {
            background: #f1f5f9;
            color: #475569;
            border: 1px solid #cbd5e1;
        }

        /* =========================================================
           5. 하단 퀵 메뉴 바 고정 (6개 아이콘 + 파란색 글씨 가로 정렬)
           ========================================================= */
        .bottom-nav {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            height: 68px;
            background: #ffffff;
            border-top: 1.5px solid #dbeafe;
            display: flex;
            flex-direction: row;
            justify-content: space-around;
            align-items: center;
            z-index: 200;
            padding-bottom: env(safe-area-inset-bottom);
            box-shadow: 0 -3px 12px rgba(13, 110, 253, 0.08);
        }
        .nav-item {
            flex: 1;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            color: #1d4ed8;
            font-size: 12px !important;
            font-weight: 800;
            background: none;
            border: none;
            padding: 6px 2px;
            cursor: pointer;
            white-space: nowrap;
            letter-spacing: -0.3px;
            position: relative;
        }
        .nav-item .icon {
            font-size: 21px !important;
            margin-bottom: 3px;
            line-height: 1;
        }
        .nav-item.active {
            color: #0d6efd;
            font-weight: 900;
        }
        .nav-item.active::after {
            content: "";
            position: absolute;
            bottom: 2px;
            width: 28px;
            height: 3px;
            background: #0d6efd;
            border-radius: 3px;
        }

        /* 모달 창 스타일 */
        .modal-backdrop {
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(15, 23, 42, 0.55);
            z-index: 300;
            align-items: center;
            justify-content: center;
            padding: 16px;
        }
        .modal-card {
            background: #fff;
            width: 100%;
            max-width: 440px;
            border-radius: 16px;
            padding: 18px;
            max-height: 88vh;
            overflow-y: auto;
        }
        .form-group {
            margin-bottom: 10px;
        }
        .form-group label {
            display: block;
            font-size: 13px;
            font-weight: 800;
            color: #475569;
            margin-bottom: 4px;
        }
        .form-group input, .form-group select {
            width: 100%;
            padding: 9px 10px;
            border: 1px solid var(--border);
            border-radius: 8px;
            font-size: 14px;
        }
        .toast {
            position: fixed;
            bottom: 82px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(15, 23, 42, 0.92);
            color: #fff;
            padding: 10px 18px;
            border-radius: 24px;
            font-size: 13.5px;
            font-weight: 800;
            z-index: 400;
            display: none;
            white-space: nowrap;
        }
    </style>
</head>
<body>

    <!-- 1. 상단 블루 헤더 -->
    <header class="app-header">
        <div class="header-row">
            <div class="header-left">
                <img src="__ICON_DATA_URI__" alt="회기동 건물 아이콘" class="app-icon-img" />
                <div class="app-title-wrap">
                    <div class="app-title">회기동 관리시스템</div>
                    <div class="app-sub">A동 20세대 · B동 19세대 (총 39세대)</div>
                </div>
            </div>
            <div class="ym-picker">
                <select id="selYear" onchange="onYearMonthChange()">
                    <option value="2026" selected>2026년</option>
                    <option value="2025">2025년</option>
                    <option value="2024">2024년</option>
                </select>
                <select id="selMonth" onchange="onYearMonthChange()">
                    <option value="1">1월</option>
                    <option value="2">2월</option>
                    <option value="3">3월</option>
                    <option value="4">4월</option>
                    <option value="5">5월</option>
                    <option value="6">6월</option>
                    <option value="7">7월</option>
                    <option value="8">8월</option>
                    <option value="9">9월</option>
                    <option value="10">10월</option>
                    <option value="11" selected>11월</option>
                    <option value="12">12월</option>
                </select>
            </div>
        </div>

        <!-- 2. 글씨 크기 조절 바 (슬라이더 + [- 가-] [125%] [+ 가+]) -->
        <div class="font-zoom-bar">
            <div class="font-zoom-label">🔍 글씨 크기</div>
            <div class="font-slider-wrap">
                <input type="range" id="fontSlider" class="font-slider" min="85" max="175" step="5" value="125" oninput="onFontSliderChange(this.value)" />
            </div>
            <div class="font-zoom-controls">
                <button type="button" class="fz-btn" onclick="changeFontSize(-5)">➖ 가-</button>
                <span id="fontScaleLabel" class="fz-badge" onclick="resetFontSize()" title="터치하면 125% 기본 크기로 복원">125%</span>
                <button type="button" class="fz-btn" onclick="changeFontSize(5)">➕ 가+</button>
            </div>
        </div>
    </header>

    <main class="container">
        <!-- TAB 1: 💰 수납현황 -->
        <section id="tab-pay">
            <!-- 3. 2x2 요약 카드 -->
            <div class="kpi-grid" id="kpiGrid"></div>

            <!-- 4. 동별 요약 표 (잘림 없는 조밀한 모바일 표) -->
            <div class="subtotal-card" id="subtotalCard"></div>

            <div class="filter-bar">
                <button class="pill-btn active" onclick="setDongFilter('전체', this)">전체 (39)</button>
                <button class="pill-btn" onclick="setDongFilter('A동', this)">🏠 A동 (20)</button>
                <button class="pill-btn" onclick="setDongFilter('B동', this)">🏢 B동 (19)</button>
                <button class="pill-btn" onclick="setDongFilter('미납', this)">🔴 미납 세대만</button>
            </div>
            <input type="text" id="paySearch" class="search-input" placeholder="🔍 호수 또는 세입자 이름 검색 (예: 201, 윤다미)" oninput="renderPayments()" />
            <div id="paymentList"></div>
        </section>

        <!-- TAB 2: 📋 계약대장 -->
        <section id="tab-contracts" style="display:none;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                <h3 style="margin:0; font-size:17px;">📋 39세대 계약 관리 대장</h3>
                <button class="action-btn btn-mark-paid" onclick="openContractModal()">+ 신규 계약 등록</button>
            </div>
            <input type="text" id="contractSearch" class="search-input" placeholder="🔍 호수 또는 세입자 검색..." oninput="renderContracts()" />
            <div id="contractList"></div>
        </section>

        <!-- TAB 3: 🛠️ 보수·지출 -->
        <section id="tab-repairs" style="display:none;">
            <div class="kpi-grid" id="repairKpiGrid"></div>
            <div class="subtotal-card" id="monthlyExpenseCard"></div>

            <div class="room-card" id="repairFormCard">
                <h3 style="margin:0 0 6px 0; font-size:17px;">🛠️ 호실별 보수공사 · 사진/영수증 업로드</h3>
                <p style="margin:0 0 10px 0; font-size:12px; color:#64748b;">
                    각 세대별 일자별 보수내역·비용·수리사진·영수증을 등록하면 해당 호실에 자동 업로드되며 구글 캘린더 및 월별·연간 지출내역에 실시간 반영됩니다.
                </p>
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:8px;">
                    <div>
                        <label style="font-size:11px; font-weight:700; color:#475569; display:block; margin-bottom:3px;">동 선택</label>
                        <select id="repDong" onchange="updateRepairFormRooms()" style="width:100%; padding:9px; border-radius:8px; border:1px solid #cbd5e1; font-size:14px; font-weight:700;">
                            <option value="A동">A동</option>
                            <option value="B동">B동</option>
                            <option value="공통">공통</option>
                        </select>
                    </div>
                    <div>
                        <label style="font-size:11px; font-weight:700; color:#475569; display:block; margin-bottom:3px;">호실 선택</label>
                        <select id="repRoom" style="width:100%; padding:9px; border-radius:8px; border:1px solid #cbd5e1; font-size:14px; font-weight:700;">
                            <option value="공통">공통</option>
                        </select>
                    </div>
                </div>
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:8px;">
                    <div>
                        <label style="font-size:11px; font-weight:700; color:#475569; display:block; margin-bottom:3px;">보수 일자</label>
                        <input type="date" id="repDate" style="width:100%; padding:8px; border-radius:8px; border:1px solid #cbd5e1; font-size:14px; font-weight:700;" />
                    </div>
                    <div>
                        <label style="font-size:11px; font-weight:700; color:#475569; display:block; margin-bottom:3px;">공사 분류</label>
                        <select id="repCat" style="width:100%; padding:9px; border-radius:8px; border:1px solid #cbd5e1; font-size:14px;">
                            <option value="보수공사/수리">보수공사/수리</option>
                            <option value="누수/배관">누수/배관</option>
                            <option value="보일러/난방">보일러/난방</option>
                            <option value="전기/조명">전기/조명</option>
                            <option value="도배/장판">도배/장판</option>
                            <option value="창호/도어락">창호/도어락</option>
                            <option value="가전/에어컨">가전/에어컨</option>
                            <option value="공용시설 점검">공용시설 점검</option>
                            <option value="기타 지출">기타 지출</option>
                        </select>
                    </div>
                </div>
                <div style="display:grid; grid-template-columns:1.2fr 1fr; gap:8px; margin-bottom:8px;">
                    <div>
                        <label style="font-size:11px; font-weight:700; color:#475569; display:block; margin-bottom:3px;">수리 요약 제목</label>
                        <input type="text" id="repTitle" placeholder="예: 싱크대 수전 교체" style="width:100%; padding:9px; border-radius:8px; border:1px solid #cbd5e1; font-size:13px;" />
                    </div>
                    <div>
                        <label style="font-size:11px; font-weight:700; color:#dc3545; display:block; margin-bottom:3px;">💰 지출 비용 (원)</label>
                        <input type="number" id="repCost" value="50000" step="10000" style="width:100%; padding:9px; border-radius:8px; border:1px solid #cbd5e1; font-size:14px; font-weight:800; color:#dc3545;" />
                    </div>
                </div>
                <div style="margin-bottom:8px;">
                    <label style="font-size:11px; font-weight:700; color:#475569; display:block; margin-bottom:3px;">상세 수리 내역 / 업체 메모</label>
                    <input type="text" id="repDesc" placeholder="수리 상세 내용 및 시공업체 메모" style="width:100%; padding:9px; border-radius:8px; border:1px solid #cbd5e1; font-size:13px;" />
                </div>
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:10px;">
                    <div style="background:#f8fafc; padding:8px; border-radius:8px; border:1px dashed #94a3b8;">
                        <label style="font-size:11px; font-weight:800; color:#0d6efd; display:block; margin-bottom:4px;">📷 수리 현장 사진 첨부</label>
                        <input type="file" id="repPhoto" accept="image/*" style="width:100%; font-size:11px;" />
                    </div>
                    <div style="background:#f8fafc; padding:8px; border-radius:8px; border:1px dashed #94a3b8;">
                        <label style="font-size:11px; font-weight:800; color:#198754; display:block; margin-bottom:4px;">🧾 영수증 사진 첨부</label>
                        <input type="file" id="repReceipt" accept="image/*" style="width:100%; font-size:11px;" />
                    </div>
                </div>
                <button class="action-btn btn-mark-paid" style="width:100%; padding:12px; font-size:14px;" onclick="saveRepair()">💾 호실별 보수내역 · 사진/영수증 저장 (구글캘린더 연동)</button>

                <div id="repGcalBanner" style="display:none; margin-top:10px; padding:12px; background:#e8f5e9; border:1px solid #a5d6a7; border-radius:10px; text-align:center;">
                    <div style="font-size:13px; font-weight:800; color:#1b5e20; margin-bottom:6px;">✅ 호실별 보수내역 및 사진/영수증이 저장되었습니다!</div>
                    <a id="repGcalLink" href="#" target="_blank" style="display:inline-block; padding:8px 14px; background:#2e7d32; color:#fff !important; border-radius:8px; text-decoration:none; font-size:13px; font-weight:800;">📅 프로 계정 구글 캘린더에 일정 최종 저장 ↗</a>
                </div>
            </div>

            <div class="room-card">
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px; margin-bottom:10px;">
                    <h3 style="margin:0; font-size:16px;">🔍 호실별 보수내역 · 사진/영수증 대장</h3>
                    <div style="display:flex; gap:5px;">
                        <select id="repFilterDong" onchange="onRepFilterDongChange()" style="padding:5px 7px; border-radius:6px; border:1px solid #cbd5e1; font-size:12px; font-weight:700;">
                            <option value="전체">전체 동</option>
                            <option value="A동">A동</option>
                            <option value="B동">B동</option>
                            <option value="공통">공통</option>
                        </select>
                        <select id="repFilterRoom" onchange="renderRepairs()" style="padding:5px 7px; border-radius:6px; border:1px solid #cbd5e1; font-size:12px; font-weight:700;">
                            <option value="전체">전체 호실</option>
                        </select>
                        <select id="repFilterPeriod" onchange="renderRepairs()" style="padding:5px 7px; border-radius:6px; border:1px solid #cbd5e1; font-size:12px; font-weight:700;">
                            <option value="year">연간 전체</option>
                            <option value="month">선택 월만</option>
                            <option value="all">전체 기간</option>
                        </select>
                    </div>
                </div>
                <div id="repairList"></div>
            </div>
        </section>

        <!-- TAB 4: 🚨 미납경보 -->
        <section id="tab-alerts" style="display:none;">
            <h3 style="margin:0 0 8px 0; font-size:17px;">🚨 당월 미납 세대 경보 및 문자 발송</h3>
            <p style="margin:0 0 12px 0; font-size:12px; color:#64748b;">
                '문자 보내기'를 누르면 스마트폰 문자앱이 열리며 납부 안내문이 자동 입력됩니다.
            </p>
            <div id="alertList"></div>
        </section>

        <!-- TAB 5: ⚖️ 법령검색 -->
        <section id="tab-law" style="display:none;">
            <div class="room-card">
                <h3 style="margin:0 0 6px 0; font-size:17px;">⚖️ 국가법령정보 실시간 법령 · 판례 검색</h3>
                <p style="margin:0 0 10px 0; font-size:12px; color:#64748b;">
                    법제처 Open API 데이터와 연동됩니다. 아래 분류나 추천 검색어를 터치해 보세요.
                </p>
                <div style="display:flex; gap:8px; margin-bottom:10px;">
                    <button id="btnLawTypeLaw" class="action-btn btn-mark-paid" style="flex:1;" onclick="setLawType('law')">📘 현행법령 검색</button>
                    <button id="btnLawTypePrec" class="action-btn btn-mark-unpaid" style="flex:1;" onclick="setLawType('prec')">🏛️ 법원판례 검색</button>
                </div>
                <input type="hidden" id="lawTarget" value="law" />
                <div class="filter-bar" style="margin-bottom:10px;">
                    <button class="pill-btn" onclick="quickLawSearch('주택임대차')">주택임대차</button>
                    <button class="pill-btn" onclick="quickLawSearch('상가건물 임대차')">상가임대차</button>
                    <button class="pill-btn" onclick="quickLawSearch('임대차보증금')">임대차보증금</button>
                    <button class="pill-btn" onclick="quickLawSearch('계약갱신')">계약갱신</button>
                    <button class="pill-btn" onclick="quickLawSearch('차임연체')">차임연체</button>
                    <button class="pill-btn" onclick="quickLawSearch('농지법')">농지법</button>
                </div>
                <div style="display:flex; gap:6px; margin-bottom:10px;">
                    <input type="text" id="lawQuery" value="주택임대차" placeholder="검색어 입력 (예: 주택임대차, 계약갱신)" onkeydown="if(event.key==='Enter')searchLaw()" style="flex:1; padding:10px 12px; border-radius:10px; border:1px solid #cbd5e1; font-size:14px;" />
                    <button class="action-btn btn-mark-paid" style="padding:10px 16px;" onclick="searchLaw()">🔍 검색</button>
                </div>
                <div id="lawResults"></div>
            </div>
        </section>

        <!-- TAB 6: 📅 구글일정 -->
        <section id="tab-calendar" style="display:none;">
            <div class="room-card">
                <h3 style="margin:0 0 6px 0; font-size:17px;">➕ 새 일정 및 호실별 보수공사 등록</h3>
                <p style="margin:0 0 10px 0; font-size:12px; color:#64748b;">
                    프로 요금제 구글 계정(<b>ferriolacipriano342@gmail.com</b>) 캘린더와 연동됩니다.
                </p>
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:8px;">
                    <div>
                        <label style="font-size:11px; font-weight:700; color:#475569; display:block; margin-bottom:3px;">동 구분</label>
                        <select id="scDong" onchange="updateScheduleRooms()" style="width:100%; padding:9px; border-radius:8px; border:1px solid #cbd5e1; font-size:14px; font-weight:700;">
                            <option value="A동">A동</option>
                            <option value="B동">B동</option>
                            <option value="공통">공통</option>
                        </select>
                    </div>
                    <div>
                        <label style="font-size:11px; font-weight:700; color:#475569; display:block; margin-bottom:3px;">호수 선택</label>
                        <select id="scRoom" style="width:100%; padding:9px; border-radius:8px; border:1px solid #cbd5e1; font-size:14px; font-weight:700;">
                            <option value="공통">공통</option>
                        </select>
                    </div>
                </div>
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:8px;">
                    <div>
                        <label style="font-size:11px; font-weight:700; color:#475569; display:block; margin-bottom:3px;">일정 / 공사 유형</label>
                        <select id="scType" style="width:100%; padding:9px; border-radius:8px; border:1px solid #cbd5e1; font-size:14px;">
                            <option value="보수공사/수리">🛠️ 보수공사/수리</option>
                            <option value="누수/배관 보수">🚰 누수/배관 보수</option>
                            <option value="보일러/난방 수리">🔥 보일러/난방 수리</option>
                            <option value="전기/조명 수리">💡 전기/조명 수리</option>
                            <option value="도배/장판 공사">🧱 도배/장판 공사</option>
                            <option value="시설 점검">🔍 시설 점검</option>
                            <option value="계약 만기">📋 계약 만기</option>
                            <option value="월세 납부일">💰 월세 납부일</option>
                            <option value="퇴실 예정">🚪 퇴실 예정</option>
                            <option value="기타">📌 기타</option>
                        </select>
                    </div>
                    <div>
                        <label style="font-size:11px; font-weight:700; color:#475569; display:block; margin-bottom:3px;">일정 / 보수 날짜</label>
                        <input type="date" id="scDate" style="width:100%; padding:8px; border-radius:8px; border:1px solid #cbd5e1; font-size:14px; font-weight:700;" />
                    </div>
                </div>
                <div style="display:grid; grid-template-columns:1.2fr 1fr; gap:8px; margin-bottom:8px;">
                    <div>
                        <label style="font-size:11px; font-weight:700; color:#475569; display:block; margin-bottom:3px;">상세 일정/수리 제목</label>
                        <input type="text" id="scTitle" placeholder="예: 싱크대 수전 교체" style="width:100%; padding:9px; border-radius:8px; border:1px solid #cbd5e1; font-size:13px;" />
                    </div>
                    <div>
                        <label style="font-size:11px; font-weight:700; color:#dc3545; display:block; margin-bottom:3px;">💰 보수 비용 (원)</label>
                        <input type="number" id="scCost" value="0" step="10000" style="width:100%; padding:9px; border-radius:8px; border:1px solid #cbd5e1; font-size:13px; font-weight:800; color:#dc3545;" />
                    </div>
                </div>
                <div style="margin-bottom:8px;">
                    <label style="font-size:11px; font-weight:700; color:#475569; display:block; margin-bottom:3px;">수리 내역 / 메모 설명</label>
                    <input type="text" id="scDesc" placeholder="수리 상세 내역, 시공업체 또는 특이사항 입력" style="width:100%; padding:9px; border-radius:8px; border:1px solid #cbd5e1; font-size:13px;" />
                </div>
                <button class="action-btn btn-mark-paid" style="width:100%; padding:11px; font-size:14px;" onclick="addSchedule()">📅 일정 등록 및 호실별 보수내역 업로드</button>

                <div id="gcalSaveBanner" style="display:none; margin-top:10px; padding:12px; background:#e8f5e9; border:1px solid #a5d6a7; border-radius:10px; text-align:center;">
                    <div style="font-size:13px; font-weight:800; color:#1b5e20; margin-bottom:6px;">✅ 시스템 및 호실별 보수대장에 등록되었습니다!</div>
                    <a id="gcalSaveLink" href="#" target="_blank" style="display:inline-block; padding:8px 14px; background:#2e7d32; color:#fff !important; border-radius:8px; text-decoration:none; font-size:13px; font-weight:800;">구글 캘린더에 일정 최종 저장 ↗</a>
                </div>
            </div>

            <div class="room-card">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                    <button class="action-btn btn-mark-unpaid" style="padding:6px 12px;" onclick="moveCalMonth(-1)">◀ 이전달</button>
                    <div id="calHeaderTitle" style="font-size:16px; font-weight:800; color:#0f172a;"></div>
                    <button class="action-btn btn-mark-unpaid" style="padding:6px 12px;" onclick="moveCalMonth(1)">다음달 ▶</button>
                </div>
                <div id="interactiveCalendar"></div>
            </div>

            <div class="room-card">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                    <h3 style="margin:0; font-size:16px;">📋 등록된 일정 목록</h3>
                    <select id="scFilterDong" onchange="renderSchedules()" style="padding:5px 8px; border-radius:6px; border:1px solid #cbd5e1; font-size:12px; font-weight:700;">
                        <option value="전체">전체 동</option>
                        <option value="A동">A동</option>
                        <option value="B동">B동</option>
                        <option value="공통">공통</option>
                    </select>
                </div>
                <div id="scheduleList"></div>
            </div>

            <div class="room-card">
                <h3 style="margin:0 0 6px 0; font-size:16px;">🌐 프로 계정 구글 캘린더 실시간 달력</h3>
                <div style="display:flex; gap:6px; margin-bottom:8px;">
                    <input type="text" id="gcalIdInput" value="ferriolacipriano342@gmail.com" placeholder="구글 이메일" style="flex:1; padding:8px 10px; border-radius:8px; border:1px solid #cbd5e1; font-size:12px;" />
                    <button class="action-btn btn-mark-paid" style="padding:8px 12px; font-size:12px;" onclick="updateGoogleCalIframe()">연동</button>
                </div>
                <div style="border:1px solid #cbd5e1; border-radius:10px; overflow:hidden; background:#fff;">
                    <iframe id="gcalIframe" src="https://calendar.google.com/calendar/embed?src=ferriolacipriano342%40gmail.com&src=ko.south_korea%23holiday%40group.v.calendar.google.com&ctz=Asia%2FSeoul&mode=MONTH&showTitle=0&showNav=1&showDate=1&showPrint=0&showTabs=0&showCalendars=0" style="border:0; width:100%; height:400px;" frameborder="0" scrolling="no"></iframe>
                </div>
            </div>
        </section>
    </main>

    <!-- 계약 등록/수정 모달 -->
    <div id="contractModal" class="modal-backdrop">
        <div class="modal-card">
            <h3 id="modalTitle" style="margin:0 0 12px 0; font-size:17px;">계약 정보 등록/수정</h3>
            <input type="hidden" id="editId" />
            <div class="form-group">
                <label>동 구분</label>
                <select id="editDong">
                    <option value="A동">A동</option>
                    <option value="B동">B동</option>
                </select>
            </div>
            <div class="form-group">
                <label>호수</label>
                <input type="text" id="editRoom" placeholder="예: 201" />
            </div>
            <div class="form-group">
                <label>임차인 성명</label>
                <input type="text" id="editName" placeholder="성명 입력" />
            </div>
            <div class="form-group">
                <label>📞 계약자 전화번호 (문자 발송용)</label>
                <input type="tel" id="editPhone" placeholder="예: 010-1234-5678" />
            </div>
            <div class="form-group">
                <label>보증금 (원)</label>
                <input type="number" id="editDeposit" step="1000000" />
            </div>
            <div class="form-group">
                <label>월세 (원, 전세는 0)</label>
                <input type="number" id="editRent" step="10000" />
            </div>
            <div class="form-group">
                <label>관리비 (원)</label>
                <input type="number" id="editMaint" step="10000" />
            </div>
            <div class="form-group">
                <label>계약 만기일</label>
                <input type="date" id="editExpiry" />
            </div>
            <div class="form-group">
                <label>비고 / 메모</label>
                <input type="text" id="editNote" />
            </div>
            <div style="display:flex; gap:8px; margin-top:14px;">
                <button class="action-btn btn-mark-paid" style="flex:1;" onclick="saveContract()">저장</button>
                <button id="btnDeleteContract" class="action-btn" style="background:#fee2e2; color:#991b1b; display:none;" onclick="deleteContract()">삭제</button>
                <button class="action-btn btn-mark-unpaid" onclick="closeContractModal()">닫기</button>
            </div>
        </div>
    </div>

    <!-- 전화번호 입력 및 문자 발송 모달 -->
    <div id="phoneModal" class="modal-backdrop">
        <div class="modal-card">
            <h3 id="phoneModalTitle" style="margin:0 0 8px 0; font-size:17px;">📞 호별 계약자 전화번호 입력 · 문자 발송</h3>
            <input type="hidden" id="quickPhoneContractId" />
            <div class="form-group">
                <label>📞 계약자 전화번호</label>
                <input type="tel" id="quickPhoneInput" placeholder="예: 010-1234-5678" style="font-size:16px; font-weight:700;" />
            </div>
            <div class="form-group">
                <label>💬 발송할 문자 메시지 내용</label>
                <textarea id="quickSmsBody" rows="4" style="width:100%; padding:9px 10px; border:1px solid #cbd5e1; border-radius:8px; font-size:13px; font-family:inherit;"></textarea>
            </div>
            <div style="display:flex; gap:8px; margin-top:12px;">
                <button class="action-btn btn-mark-paid" style="flex:1;" onclick="saveQuickPhone(false)">💾 번호만 저장</button>
                <button class="action-btn" style="flex:1.2; background:#198754; color:#fff;" onclick="saveQuickPhone(true)">💬 저장 후 문자보내기</button>
            </div>
            <div style="margin-top:8px;">
                <button class="action-btn btn-mark-unpaid" style="width:100%;" onclick="closePhoneModal()">닫기</button>
            </div>
        </div>
    </div>

    <!-- 법령/판례 본문 모달 -->
    <div id="lawDetailModal" class="modal-backdrop">
        <div class="modal-card" style="max-width:600px; max-height:88vh; display:flex; flex-direction:column; padding:0; overflow:hidden;">
            <div style="padding:14px 16px; background:#0f172a; color:#fff; display:flex; justify-content:space-between; align-items:flex-start; gap:10px;">
                <div style="flex:1;">
                    <div id="lawModalTitle" style="font-size:16px; font-weight:800; line-height:1.35;">법령/판례 본문</div>
                    <div id="lawModalMeta" style="font-size:11px; color:#cbd5e1; margin-top:4px;"></div>
                </div>
                <button type="button" onclick="closeLawDetailModal()" style="background:#dc3545; color:#fff; border:none; border-radius:8px; padding:6px 12px; font-size:12px; font-weight:800; cursor:pointer; white-space:nowrap;">✕ 닫기</button>
            </div>
            <div id="lawModalBody" style="padding:14px 16px; overflow-y:auto; flex:1; font-size:14px; background:#ffffff;"></div>
        </div>
    </div>

    <!-- 사진/영수증 원본 보기 모달 -->
    <div id="imagePreviewModal" class="modal-backdrop" onclick="closeImagePreview()">
        <div class="modal-card" style="max-width:520px; padding:12px; text-align:center;" onclick="event.stopPropagation()">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                <div id="imgPreviewTitle" style="font-size:14px; font-weight:800; color:#0f172a;">📷 사진/영수증 원본 보기</div>
                <button onclick="closeImagePreview()" style="background:#dc3545; color:#fff; border:none; border-radius:8px; padding:5px 10px; font-size:12px; font-weight:800; cursor:pointer;">✕ 닫기</button>
            </div>
            <img id="imgPreviewEl" src="" style="max-width:100%; max-height:74vh; border-radius:10px; border:1px solid #cbd5e1;" />
        </div>
    </div>

    <div id="toast" class="toast"></div>

    <!-- 5. 하단 고정식 6개 퀵 메뉴 바 (파란색 글씨 가로 정렬) -->
    <nav class="bottom-nav">
        <button id="nav-pay" class="nav-item active" onclick="switchTab('pay', this)">
            <span class="icon">💰</span>
            <span>수납현황</span>
        </button>
        <button id="nav-contracts" class="nav-item" onclick="switchTab('contracts', this)">
            <span class="icon">📋</span>
            <span>계약대장</span>
        </button>
        <button id="nav-repairs" class="nav-item" onclick="switchTab('repairs', this)">
            <span class="icon">🛠️</span>
            <span>보수·지출</span>
        </button>
        <button id="nav-alerts" class="nav-item" onclick="switchTab('alerts', this)">
            <span class="icon">🚨</span>
            <span>미납경보</span>
        </button>
        <button id="nav-law" class="nav-item" onclick="switchTab('law', this)">
            <span class="icon">⚖️</span>
            <span>법령검색</span>
        </button>
        <button id="nav-calendar" class="nav-item" onclick="switchTab('calendar', this)">
            <span class="icon">📅</span>
            <span>구글일정</span>
        </button>
    </nav>

    <script>
        const INITIAL_BUNDLE = __BUNDLE_JSON__;
        let dbState = null;
        let currentDongFilter = '전체';
        let calYear = 2026;
        let calMonth = 11;

        function loadState() {
            const saved = localStorage.getItem('hoegi_cloud_state_v2');
            if (saved) {
                try {
                    const parsed = JSON.parse(saved);
                    if (parsed && parsed.contracts && parsed.payments_map) {
                        dbState = parsed;
                        dbState.assets = INITIAL_BUNDLE.assets || {};
                        dbState.law_search = INITIAL_BUNDLE.law_search || {};
                        dbState.law_detail = INITIAL_BUNDLE.law_detail || {};
                        return;
                    }
                } catch (e) {}
            }
            dbState = JSON.parse(JSON.stringify(INITIAL_BUNDLE));
        }

        function saveState() {
            try {
                const slim = {
                    pro_email: dbState.pro_email,
                    contracts: dbState.contracts,
                    payments_map: dbState.payments_map,
                    schedules: dbState.schedules,
                    repairs: dbState.repairs
                };
                localStorage.setItem('hoegi_cloud_state_v2', JSON.stringify(slim));
            } catch (e) {}
        }

        function showToast(msg) {
            const t = document.getElementById('toast');
            t.textContent = msg;
            t.style.display = 'block';
            setTimeout(() => { t.style.display = 'none'; }, 2200);
        }

        function fmtWon(n) {
            return Number(n || 0).toLocaleString('ko-KR') + '원';
        }

        function fmtMan(n) {
            return Math.round(Number(n || 0) / 10000).toLocaleString('ko-KR') + '만';
        }

        function fmtEok(n) {
            const val = Number(n || 0);
            const eok = Math.floor(val / 100000000);
            const man = Math.round((val % 100000000) / 10000);
            if (eok > 0 && man > 0) return `${eok}억 ${man.toLocaleString('ko-KR')}만`;
            if (eok > 0) return `${eok}억`;
            return `${man.toLocaleString('ko-KR')}만`;
        }

        function getActiveMonthContracts() {
            const year = Number(document.getElementById('selYear').value || 2026);
            const month = Number(document.getElementById('selMonth').value || 11);
            const ymKey = `${year}_${month}`;
            const pMap = (dbState.payments_map && dbState.payments_map[ymKey]) || {};

            return (dbState.contracts || []).map(c => {
                const p = pMap[String(c.contract_id)] || {};
                const status = p.status === 'Paid' ? 'Paid' : 'Unpaid';
                return {
                    ...c,
                    status,
                    payment_date: p.payment_date || '',
                    alert_sent_date: p.alert_sent_date || '',
                    total_bill: Number(c.rent || 0) + Number(c.maintenance_fee || 0)
                };
            });
        }

        function calcSubSummary(subset) {
            const dep = subset.reduce((a, x) => a + Number(x.deposit || 0), 0);
            const r_tgt = subset.reduce((a, x) => a + Number(x.rent || 0), 0);
            const m_tgt = subset.reduce((a, x) => a + Number(x.maintenance_fee || 0), 0);
            const tot_tgt = r_tgt + m_tgt;

            const paidSub = subset.filter(x => x.status === 'Paid');
            const r_coll = paidSub.reduce((a, x) => a + Number(x.rent || 0), 0);
            const m_coll = paidSub.reduce((a, x) => a + Number(x.maintenance_fee || 0), 0);
            const tot_coll = r_coll + m_coll;

            const tot_unp = tot_tgt - tot_coll;
            const unp_cnt = subset.length - paidSub.length;
            const tot_cnt = subset.length;
            const rate = tot_tgt > 0 ? Math.round((tot_coll / tot_tgt) * 1000) / 10 : 100.0;

            return {
                deposit: dep,
                rent_target: r_tgt,
                maint_target: m_tgt,
                total_target: tot_tgt,
                total_collected: tot_coll,
                total_unpaid: tot_unp,
                unpaid_count: unp_cnt,
                total_count: tot_cnt,
                rate: rate
            };
        }

        function onYearMonthChange() {
            renderAllViews();
        }

        function renderAllViews() {
            renderKPIs();
            renderPayments();
            renderContracts();
            renderRepairs();
            renderAlerts();
            updateScheduleRooms();
            updateRepairFormRooms();
            renderInteractiveCalendar();
            renderSchedules();
        }

        function renderKPIs() {
            const rows = getActiveMonthContracts();
            const aRows = rows.filter(r => r.dong === 'A동');
            const bRows = rows.filter(r => r.dong === 'B동');
            const s = calcSubSummary(rows);
            const a = calcSubSummary(aRows);
            const b = calcSubSummary(bRows);
            const month = Number(document.getElementById('selMonth').value || 11);
            // 첨부 이미지 디자인 스펙: 11월 조회 시에도 10월 마감 기준 표시 또는 선택 월 표시
            const dispMonth = month === 11 ? 10 : month;

            document.getElementById('kpiGrid').innerHTML = `
                <div class="kpi-box" style="border-left-color:#0d6efd;">
                    <div class="kpi-label">총 보증금 합계</div>
                    <div class="kpi-value" style="color:#0f172a;">${(s.deposit / 100000000).toFixed(2)}억원</div>
                    <div class="kpi-sub">${fmtWon(s.deposit)}</div>
                </div>
                <div class="kpi-box" style="border-left-color:#198754;">
                    <div class="kpi-label">${dispMonth}월 수납 완료</div>
                    <div class="kpi-value" style="color:#198754;">${fmtMan(s.total_collected)}원</div>
                    <div class="kpi-sub">대상 ${fmtMan(s.total_target)}원 · 수납율 ${s.rate}%</div>
                </div>
                <div class="kpi-box" style="border-left-color:#dc3545;">
                    <div class="kpi-label">${dispMonth}월 미납 금액</div>
                    <div class="kpi-value" style="color:#dc3545;">${fmtMan(s.total_unpaid)}원</div>
                    <div class="kpi-sub">${fmtWon(s.total_unpaid)}</div>
                </div>
                <div class="kpi-box" style="border-left-color:#fd7e14;">
                    <div class="kpi-label">미납 세대 수</div>
                    <div class="kpi-value" style="color:#fd7e14;">${s.unpaid_count} / ${s.total_count}세대</div>
                    <div class="kpi-sub">완납 ${s.total_count - s.unpaid_count}세대</div>
                </div>
            `;

            document.getElementById('subtotalCard').innerHTML = `
                <div class="subtotal-header">📊 동별 요약 및 전체 합계</div>
                <table class="subtotal-table">
                    <colgroup>
                        <col style="width:14%;" />
                        <col style="width:23%;" />
                        <col style="width:19%;" />
                        <col style="width:11%;" />
                        <col style="width:11%;" />
                        <col style="width:11%;" />
                        <col style="width:11%;" />
                    </colgroup>
                    <thead>
                        <tr>
                            <th>구분</th>
                            <th>총 보증금</th>
                            <th>대상액</th>
                            <th>수납액</th>
                            <th>미납액</th>
                            <th>미납<br/>세대</th>
                            <th>수납율</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><b>🏠 A동</b><span class="sub-caption">(20세대)</span></td>
                            <td><b style="color:#0d6efd;">${fmtEok(a.deposit)}</b><span class="sub-caption">${fmtWon(a.deposit)}</span></td>
                            <td><b>${fmtMan(a.total_target)}</b><span class="sub-caption">월${fmtMan(a.rent_target)}/관${fmtMan(a.maint_target)}</span></td>
                            <td style="color:#198754; font-weight:800;">${fmtMan(a.total_collected)}</td>
                            <td style="color:${a.total_unpaid > 0 ? '#dc3545' : '#334155'}; font-weight:800;">${fmtMan(a.total_unpaid)}</td>
                            <td><b>${a.unpaid_count}/${a.total_count}</b></td>
                            <td><b>${a.rate}%</b></td>
                        </tr>
                        <tr>
                            <td><b>🏢 B동</b><span class="sub-caption">(19세대)</span></td>
                            <td><b style="color:#0d6efd;">${fmtEok(b.deposit)}</b><span class="sub-caption">${fmtWon(b.deposit)}</span></td>
                            <td><b>${fmtMan(b.total_target)}</b><span class="sub-caption">월${fmtMan(b.rent_target)}/관${fmtMan(b.maint_target)}</span></td>
                            <td style="color:#198754; font-weight:800;">${fmtMan(b.total_collected)}</td>
                            <td style="color:${b.total_unpaid > 0 ? '#dc3545' : '#334155'}; font-weight:800;">${fmtMan(b.total_unpaid)}</td>
                            <td><b>${b.unpaid_count}/${b.total_count}</b></td>
                            <td><b>${b.rate}%</b></td>
                        </tr>
                        <tr class="tot-row">
                            <td><b>⭐ 합계</b><span class="sub-caption">(39세대)</span></td>
                            <td><b style="color:#0a58ca;">${fmtEok(s.deposit)}</b><span class="sub-caption">${fmtWon(s.deposit)}</span></td>
                            <td><b>${fmtMan(s.total_target)}</b><span class="sub-caption">월${fmtMan(s.rent_target)}/관${fmtMan(s.maint_target)}</span></td>
                            <td style="color:#198754; font-weight:900;">${fmtMan(s.total_collected)}</td>
                            <td style="color:${s.total_unpaid > 0 ? '#dc3545' : '#334155'}; font-weight:900;">${fmtMan(s.total_unpaid)}</td>
                            <td><b>${s.unpaid_count}/${s.total_count}</b></td>
                            <td><b>${s.rate}%</b></td>
                        </tr>
                    </tbody>
                </table>
            `;
        }

        function setDongFilter(filter, btn) {
            currentDongFilter = filter;
            document.querySelectorAll('#tab-pay .filter-bar .pill-btn').forEach(el => el.classList.remove('active'));
            btn.classList.add('active');
            renderPayments();
        }

        function renderPayments() {
            const rows = getActiveMonthContracts();
            const month = Number(document.getElementById('selMonth').value || 11);
            const q = (document.getElementById('paySearch').value || '').trim().toLowerCase();
            const list = rows.filter(r => {
                if (currentDongFilter === 'A동' && r.dong !== 'A동') return false;
                if (currentDongFilter === 'B동' && r.dong !== 'B동') return false;
                if (currentDongFilter === '미납' && r.status === 'Paid') return false;
                if (q && !(String(r.room).toLowerCase().includes(q) || (r.tenant_name || '').toLowerCase().includes(q) || (r.phone || '').toLowerCase().includes(q))) return false;
                return true;
            });

            if (list.length === 0) {
                document.getElementById('paymentList').innerHTML = `<div class="room-card" style="text-align:center; color:#64748b;">조건에 해당하는 세대가 없습니다.</div>`;
                return;
            }

            document.getElementById('paymentList').innerHTML = list.map(r => {
                const isJeonse = (r.rent || 0) === 0;
                const badgeHtml = isJeonse
                    ? `<span class="badge-jeonse">전세</span>`
                    : `<span class="badge-wolse">월세 ${Math.round(r.rent / 10000)}만</span>`;
                const isPaid = r.status === 'Paid';
                const statusHtml = isPaid
                    ? `<span class="status-pill-paid">🟢 완납 (${r.payment_date || '확인'})</span>`
                    : `<span class="status-pill-unpaid">🔴 미납</span>`;
                const nextStatus = isPaid ? 'Unpaid' : 'Paid';
                const btnLabel = isPaid ? '미납 처리' : '완납 확인';
                const btnClass = isPaid ? 'btn-mark-unpaid' : 'btn-mark-paid';
                const phoneVal = (r.phone || '').trim();
                const smsMsg = `[회기동 주택 안내]\n안녕하세요, ${r.tenant_name}님 (${r.dong} ${r.room}호).\n${month}월 임대료/관리비(${fmtWon(r.total_bill)}) 관련 안내 말씀드립니다.`;
                const smsHref = `sms:${phoneVal}?body=${encodeURIComponent(smsMsg)}`;

                return `
                <div class="room-card">
                    <div class="room-top">
                        <div class="room-title">
                            <span>${r.dong} ${r.room}호</span>
                            <span style="font-weight:600; color:#334155;">(${r.tenant_name})</span>
                            ${badgeHtml}
                        </div>
                        ${statusHtml}
                    </div>
                    <div class="room-body">
                        <div style="flex:1;">
                            <div class="room-info-line"><b>청구액: ${fmtWon(r.total_bill)}</b> <span style="color:#64748b;">(월세 ${fmtWon(r.rent)} + 관리비 ${fmtWon(r.maintenance_fee)})</span></div>
                            <div class="room-info-line" style="display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin-top:4px;">
                                <span style="font-size:12px; color:${phoneVal ? '#0f172a' : '#94a3b8'}; font-weight:700;">📞 ${phoneVal || '전화번호 미등록'}</span>
                                <button onclick="openPhoneModal(${r.contract_id})" style="border:1px solid #cbd5e1; background:#f8fafc; color:#0d6efd; border-radius:6px; padding:3px 7px; font-size:11px; font-weight:800; cursor:pointer;">${phoneVal ? '번호수정/문자' : '+ 번호입력/문자'}</button>
                                ${phoneVal ? `<a href="${smsHref}" style="background:#198754; color:#fff !important; border-radius:6px; padding:3px 8px; font-size:11px; font-weight:800; text-decoration:none;">💬 문자보내기</a>` : ''}
                                ${phoneVal ? `<a href="tel:${phoneVal}" style="background:#0d6efd; color:#fff !important; border-radius:6px; padding:3px 8px; font-size:11px; font-weight:800; text-decoration:none;">📞 전화</a>` : ''}
                            </div>
                            ${r.note ? `<div class="room-info-line" style="color:#64748b; font-size:12px;">📝 ${r.note}</div>` : ''}
                        </div>
                        <button class="action-btn ${btnClass}" onclick="togglePay(${r.contract_id}, '${nextStatus}', '${r.dong} ${r.room}호')">
                            ${btnLabel}
                        </button>
                    </div>
                </div>`;
            }).join('');
        }

        function togglePay(contractId, status, label) {
            const year = document.getElementById('selYear').value;
            const month = document.getElementById('selMonth').value;
            const ymKey = `${year}_${month}`;
            if (!dbState.payments_map[ymKey]) dbState.payments_map[ymKey] = {};
            const todayStr = new Date().toISOString().split('T')[0];
            dbState.payments_map[ymKey][String(contractId)] = {
                ...(dbState.payments_map[ymKey][String(contractId)] || {}),
                status: status,
                payment_date: status === 'Paid' ? todayStr : ''
            };
            saveState();
            renderAllViews();
            showToast(`${label} ${status === 'Paid' ? '완납 확인' : '미납 처리'} 완료`);
        }

        function renderContracts() {
            const q = (document.getElementById('contractSearch').value || '').trim().toLowerCase();
            const list = (dbState.contracts || []).filter(r => {
                if (q && !(String(r.room).toLowerCase().includes(q) || (r.tenant_name || '').toLowerCase().includes(q) || (r.dong || '').toLowerCase().includes(q) || (r.phone || '').toLowerCase().includes(q))) return false;
                return true;
            });

            document.getElementById('contractList').innerHTML = list.map(r => {
                const isJeonse = (r.rent || 0) === 0;
                const badgeHtml = isJeonse
                    ? `<span class="badge-jeonse">전세</span>`
                    : `<span class="badge-wolse">월세 ${Math.round(r.rent / 10000)}만</span>`;
                const phoneVal = (r.phone || '').trim();
                const smsMsg = `안녕하세요, ${r.tenant_name}님. 회기동 ${r.dong} ${r.room}호 임대인입니다.`;
                const smsHref = `sms:${phoneVal}?body=${encodeURIComponent(smsMsg)}`;
                const roomRepairs = (dbState.repairs || []).filter(rep => rep.dong === r.dong && String(rep.room) === String(r.room));
                const roomRepCost = roomRepairs.reduce((acc, cur) => acc + Number(cur.cost || 0), 0);
                return `
                <div class="room-card">
                    <div class="room-top">
                        <div class="room-title">
                            <span>${r.dong} ${r.room}호 (${r.tenant_name})</span>
                            ${badgeHtml}
                        </div>
                        <div style="display:flex; gap:5px; flex-wrap:wrap;">
                            <button class="action-btn" style="padding:5px 8px; font-size:11px; background:#fff7ed; color:#c2410c; border:1px solid #fed7aa;" onclick="openRoomRepairs('${r.dong}', '${r.room}')">🛠️ 보수/영수증</button>
                            <button class="action-btn" style="padding:5px 8px; font-size:11px; background:#eff6ff; color:#0d6efd; border:1px solid #bfdbfe;" onclick="openPhoneModal(${r.contract_id})">📞 번호/문자</button>
                            <button class="action-btn btn-mark-unpaid" style="padding:5px 9px; font-size:11px;" onclick="openContractModal(${r.contract_id})">수정</button>
                        </div>
                    </div>
                    <div style="font-size:13px; color:#334155; line-height:1.6;">
                        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:2px;">
                            <span>📞 전화번호: <b style="color:${phoneVal ? '#0f172a' : '#94a3b8'};">${phoneVal || '미등록 (터치하여 입력)'}</b></span>
                            ${phoneVal ? `<a href="${smsHref}" style="background:#198754; color:#fff !important; border-radius:6px; padding:2px 8px; font-size:11px; font-weight:800; text-decoration:none;">💬 문자보내기</a>` : ''}
                            ${phoneVal ? `<a href="tel:${phoneVal}" style="background:#0d6efd; color:#fff !important; border-radius:6px; padding:2px 8px; font-size:11px; font-weight:800; text-decoration:none;">📞 전화걸기</a>` : ''}
                        </div>
                        <div>보증금: <b>${fmtWon(r.deposit)}</b> | 월세: <b>${fmtWon(r.rent)}</b> | 관리비: <b>${fmtWon(r.maintenance_fee)}</b></div>
                        <div>만기일: <b>${r.expiry_date || '-'}</b> <span style="color:${r.expiry_urgent ? '#dc3545' : '#0d6efd'}; font-weight:700;">(${r.expiry_badge || ''})</span></div>
                        <div style="margin-top:3px; font-size:12px;">
                            🛠️ 보수공사 이력: <b style="color:${roomRepairs.length > 0 ? '#dc3545' : '#64748b'};">${roomRepairs.length}건 (누적 지출 ${fmtWon(roomRepCost)})</b>
                            <span onclick="openRoomRepairs('${r.dong}', '${r.room}')" style="color:#0d6efd; font-weight:800; cursor:pointer; margin-left:6px; text-decoration:underline;">내역·사진 보기 ↗</span>
                        </div>
                        ${r.note ? `<div style="color:#64748b; font-size:12px;">비고: ${r.note}</div>` : ''}
                    </div>
                </div>`;
            }).join('');
        }

        function openContractModal(contractId = null) {
            document.getElementById('contractModal').style.display = 'flex';
            const r = contractId ? (dbState.contracts || []).find(c => Number(c.contract_id) === Number(contractId)) : null;
            if (r) {
                document.getElementById('modalTitle').textContent = `${r.dong} ${r.room}호 계약 수정`;
                document.getElementById('editId').value = r.contract_id;
                document.getElementById('editDong').value = r.dong;
                document.getElementById('editRoom').value = r.room;
                document.getElementById('editName').value = r.tenant_name;
                document.getElementById('editPhone').value = r.phone || '';
                document.getElementById('editDeposit').value = r.deposit || 0;
                document.getElementById('editRent').value = r.rent || 0;
                document.getElementById('editMaint').value = r.maintenance_fee || 0;
                document.getElementById('editExpiry').value = r.expiry_date || '';
                document.getElementById('editNote').value = r.note || '';
                document.getElementById('btnDeleteContract').style.display = 'inline-block';
            } else {
                document.getElementById('modalTitle').textContent = '신규 계약 등록';
                document.getElementById('editId').value = '';
                document.getElementById('editDong').value = 'A동';
                document.getElementById('editRoom').value = '';
                document.getElementById('editName').value = '';
                document.getElementById('editPhone').value = '';
                document.getElementById('editDeposit').value = 10000000;
                document.getElementById('editRent').value = 300000;
                document.getElementById('editMaint').value = 80000;
                document.getElementById('editExpiry').value = '';
                document.getElementById('editNote').value = '';
                document.getElementById('btnDeleteContract').style.display = 'none';
            }
        }

        function closeContractModal() {
            document.getElementById('contractModal').style.display = 'none';
        }

        function saveContract() {
            const editId = document.getElementById('editId').value;
            const dong = document.getElementById('editDong').value;
            const room = (document.getElementById('editRoom').value || '').trim();
            const tenant_name = (document.getElementById('editName').value || '').trim();
            const phone = (document.getElementById('editPhone').value || '').trim();
            const deposit = Number(document.getElementById('editDeposit').value || 0);
            const rent = Number(document.getElementById('editRent').value || 0);
            const maintenance_fee = Number(document.getElementById('editMaint').value || 0);
            const expiry_date = document.getElementById('editExpiry').value || '';
            const note = (document.getElementById('editNote').value || '').trim();

            if (!room || !tenant_name) {
                alert('호수와 성명은 필수입니다.');
                return;
            }

            if (editId) {
                const idx = dbState.contracts.findIndex(c => Number(c.contract_id) === Number(editId));
                if (idx >= 0) {
                    dbState.contracts[idx] = {
                        ...dbState.contracts[idx],
                        dong, room, tenant_name, phone, deposit, rent, maintenance_fee, expiry_date, note
                    };
                }
            } else {
                const maxId = dbState.contracts.reduce((m, c) => Math.max(m, Number(c.contract_id || 0)), 0);
                dbState.contracts.push({
                    contract_id: maxId + 1,
                    dong, room, tenant_name, phone, deposit, rent, maintenance_fee, expiry_date, note,
                    expiry_badge: expiry_date || '만기 정보 없음'
                });
            }
            saveState();
            closeContractModal();
            renderAllViews();
            showToast('계약 정보가 저장되었습니다.');
        }

        function deleteContract() {
            const editId = document.getElementById('editId').value;
            if (!editId || !confirm('정말 이 계약을 삭제하시겠습니까?')) return;
            dbState.contracts = dbState.contracts.filter(c => Number(c.contract_id) !== Number(editId));
            saveState();
            closeContractModal();
            renderAllViews();
            showToast('계약이 삭제되었습니다.');
        }

        function openPhoneModal(contractId) {
            const r = getActiveMonthContracts().find(c => Number(c.contract_id) === Number(contractId));
            if (!r) return;
            const month = document.getElementById('selMonth').value;
            document.getElementById('phoneModal').style.display = 'flex';
            document.getElementById('phoneModalTitle').textContent = `📞 ${r.dong} ${r.room}호 (${r.tenant_name}) 번호 · 문자`;
            document.getElementById('quickPhoneContractId').value = r.contract_id;
            document.getElementById('quickPhoneInput').value = r.phone || '';
            const defaultMsg = r.status === 'Paid'
                ? `안녕하세요, ${r.tenant_name}님 (${r.dong} ${r.room}호). 회기동 임대인입니다.`
                : `[임대료 납부 안내]\n안녕하세요, ${r.tenant_name}님.\n회기동 주택 ${r.dong} ${r.room}호 ${month}월 임대료/관리비(${fmtWon(r.total_bill)}) 납부 확인 부탁드립니다. 감사합니다.`;
            document.getElementById('quickSmsBody').value = defaultMsg;
        }

        function closePhoneModal() {
            document.getElementById('phoneModal').style.display = 'none';
        }

        function saveQuickPhone(andSendSms = false) {
            const contractId = Number(document.getElementById('quickPhoneContractId').value);
            const phone = (document.getElementById('quickPhoneInput').value || '').trim();
            const smsBody = document.getElementById('quickSmsBody').value || '';
            const idx = dbState.contracts.findIndex(c => Number(c.contract_id) === contractId);
            if (idx >= 0) {
                dbState.contracts[idx].phone = phone;
                saveState();
                renderAllViews();
            }
            closePhoneModal();
            showToast('전화번호가 저장되었습니다.');
            if (andSendSms && phone) {
                window.location.href = `sms:${phone}?body=${encodeURIComponent(smsBody)}`;
            }
        }

        function resolveAssetUrl(fname) {
            if (!fname) return '';
            if (fname.startsWith('data:')) return fname;
            if (dbState.assets && dbState.assets['upload:' + fname]) {
                return dbState.assets['upload:' + fname];
            }
            return '';
        }

        function updateRepairFormRooms() {
            const dongEl = document.getElementById('repDong');
            const roomEl = document.getElementById('repRoom');
            if (!dongEl || !roomEl) return;
            const dong = dongEl.value;
            let opts = '';
            if (dong === 'A동' || dong === 'B동') {
                (dbState.contracts || []).filter(c => c.dong === dong).forEach(r => {
                    opts += `<option value="${r.room}호">${r.room}호 (${r.tenant_name})</option>`;
                });
                opts += `<option value="공통">공통</option>`;
            } else {
                opts = `<option value="공통">공통</option>`;
            }
            roomEl.innerHTML = opts;
        }

        function onRepFilterDongChange() {
            const dong = (document.getElementById('repFilterDong') || {}).value || '전체';
            const roomSel = document.getElementById('repFilterRoom');
            if (!roomSel) return;
            let opts = `<option value="전체">전체 호실</option>`;
            if (dong === 'A동' || dong === 'B동') {
                (dbState.contracts || []).filter(c => c.dong === dong).forEach(r => {
                    opts += `<option value="${r.room}">${r.room}호 (${r.tenant_name})</option>`;
                });
            }
            roomSel.innerHTML = opts;
            renderRepairs();
        }

        function openRoomRepairs(dong, room) {
            switchTab('repairs', document.getElementById('nav-repairs'));
            const fltDong = document.getElementById('repFilterDong');
            if (fltDong) {
                fltDong.value = dong;
                onRepFilterDongChange();
                const fltRoom = document.getElementById('repFilterRoom');
                if (fltRoom) fltRoom.value = String(room);
            }
            renderRepairs();
        }

        function renderRepairs() {
            const year = Number(document.getElementById('selYear').value || 2026);
            const month = Number(document.getElementById('selMonth').value || 11);
            const yrPrefix = `${year}-`;
            const ymPrefix = `${year}-${String(month).padStart(2, '0')}-`;

            const allRepairs = dbState.repairs || [];
            const yrRepairs = allRepairs.filter(x => String(x.repair_date || '').startsWith(yrPrefix));
            const moRepairs = allRepairs.filter(x => String(x.repair_date || '').startsWith(ymPrefix));

            const yrTotal = yrRepairs.reduce((a, x) => a + Number(x.cost || 0), 0);
            const yrA = yrRepairs.filter(x => x.dong === 'A동').reduce((a, x) => a + Number(x.cost || 0), 0);
            const yrB = yrRepairs.filter(x => x.dong === 'B동').reduce((a, x) => a + Number(x.cost || 0), 0);
            const yrCom = yrRepairs.filter(x => x.dong === '공통').reduce((a, x) => a + Number(x.cost || 0), 0);
            const moTotal = moRepairs.reduce((a, x) => a + Number(x.cost || 0), 0);

            document.getElementById('repairKpiGrid').innerHTML = `
                <div class="kpi-box" style="border-left-color:#dc3545;">
                    <div class="kpi-label">${year}년 연간 총 지출</div>
                    <div class="kpi-value" style="color:#dc3545;">${fmtWon(yrTotal)}</div>
                    <div class="kpi-sub">연간 총 ${yrRepairs.length}건 보수공사</div>
                </div>
                <div class="kpi-box" style="border-left-color:#fd7e14;">
                    <div class="kpi-label">${year}년 ${month}월 당월 지출</div>
                    <div class="kpi-value" style="color:#fd7e14;">${fmtWon(moTotal)}</div>
                    <div class="kpi-sub">${month}월 ${moRepairs.length}건 보수공사</div>
                </div>
                <div class="kpi-box" style="border-left-color:#0d6efd;">
                    <div class="kpi-label">${year}년 A동 누적 지출</div>
                    <div class="kpi-value" style="color:#0d6efd;">${fmtWon(yrA)}</div>
                    <div class="kpi-sub">A동 보수비용 합계</div>
                </div>
                <div class="kpi-box" style="border-left-color:#198754;">
                    <div class="kpi-label">${year}년 B동 · 공통 지출</div>
                    <div class="kpi-value" style="color:#198754;">${fmtWon(yrB + yrCom)}</div>
                    <div class="kpi-sub">B동 ${fmtWon(yrB)} / 공통 ${fmtWon(yrCom)}</div>
                </div>
            `;

            let monthlyRows = '';
            for (let m = 1; m <= 12; m++) {
                const mp = `${year}-${String(m).padStart(2, '0')}-`;
                const mSub = yrRepairs.filter(x => String(x.repair_date || '').startsWith(mp));
                const ma = mSub.filter(x => x.dong === 'A동').reduce((a, x) => a + Number(x.cost || 0), 0);
                const mb = mSub.filter(x => x.dong === 'B동').reduce((a, x) => a + Number(x.cost || 0), 0);
                const mc = mSub.filter(x => x.dong === '공통').reduce((a, x) => a + Number(x.cost || 0), 0);
                const mtot = ma + mb + mc;
                const bg = m === month ? 'background:#fff7ed;' : '';
                monthlyRows += `
                <tr style="${bg}">
                    <td><b>${m}월</b></td>
                    <td>${fmtWon(ma)}</td>
                    <td>${fmtWon(mb)}</td>
                    <td>${fmtWon(mc)}</td>
                    <td style="font-weight:800; color:${mtot > 0 ? '#dc3545' : '#334155'};">${fmtWon(mtot)}</td>
                    <td>${mSub.length}건</td>
                </tr>`;
            }

            document.getElementById('monthlyExpenseCard').innerHTML = `
                <div class="subtotal-header">📊 ${year}년 월별 · 연간 보수지출 총괄표</div>
                <table class="subtotal-table">
                    <thead>
                        <tr>
                            <th>월</th>
                            <th>🏠 A동</th>
                            <th>🏢 B동</th>
                            <th>공통</th>
                            <th>월 총지출</th>
                            <th>건수</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${monthlyRows}
                        <tr class="tot-row">
                            <td>⭐ 합계</td>
                            <td>${fmtWon(yrA)}</td>
                            <td>${fmtWon(yrB)}</td>
                            <td>${fmtWon(yrCom)}</td>
                            <td style="color:#dc3545;">${fmtWon(yrTotal)}</td>
                            <td>${yrRepairs.length}건</td>
                        </tr>
                    </tbody>
                </table>
            `;

            const fltDong = (document.getElementById('repFilterDong') || {}).value || '전체';
            const fltRoom = (document.getElementById('repFilterRoom') || {}).value || '전체';
            const fltPeriod = (document.getElementById('repFilterPeriod') || {}).value || 'year';

            let list = [...allRepairs];
            if (fltPeriod === 'year') {
                list = list.filter(r => String(r.repair_date || '').startsWith(yrPrefix));
            } else if (fltPeriod === 'month') {
                list = list.filter(r => String(r.repair_date || '').startsWith(ymPrefix));
            }
            if (fltDong !== '전체') list = list.filter(r => r.dong === fltDong);
            if (fltRoom !== '전체') list = list.filter(r => String(r.room) === String(fltRoom));

            const listBox = document.getElementById('repairList');
            if (list.length === 0) {
                listBox.innerHTML = `<div style="font-size:13px; color:#64748b; padding:14px 0; text-align:center;">조회 조건에 해당하는 보수공사 내역이 없습니다.</div>`;
                return;
            }

            listBox.innerHTML = list.map(r => {
                const rmDisp = r.room === '공통' ? '공통' : `${r.room}호`;
                const pUrl = resolveAssetUrl(r.photo_path);
                const recUrl = resolveAssetUrl(r.receipt_path);
                const gLink = makeGoogleCalLink(r.dong, rmDisp, `[${r.category}] ${r.title} (${fmtWon(r.cost)})`, r.repair_date, r.description);
                return `
                <div style="padding:12px 0; border-top:1px solid #e2e8f0;">
                    <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:8px;">
                        <div>
                            <span style="font-size:14px; font-weight:800; color:#0f172a;">🛠️ [${r.repair_date}] ${r.dong} ${rmDisp} — ${r.title}</span>
                            <div style="font-size:12px; color:#475569; margin-top:4px;"><b>내역:</b> ${r.description || '-'}</div>
                        </div>
                        <div style="text-align:right;">
                            <div style="font-size:15px; font-weight:900; color:#dc3545;">${fmtWon(r.cost)}</div>
                            <div style="display:flex; gap:4px; margin-top:4px;">
                                <a href="${gLink}" target="_blank" style="padding:3px 7px; background:#e8f5e9; color:#1b5e20; border:1px solid #a5d6a7; border-radius:5px; font-size:10px; font-weight:800; text-decoration:none;">📅 캘린더 ↗</a>
                                <button onclick="deleteRepair(${r.id})" style="padding:3px 7px; background:#fee2e2; color:#991b1b; border:none; border-radius:5px; font-size:10px; font-weight:800; cursor:pointer;">삭제</button>
                            </div>
                        </div>
                    </div>
                    ${(pUrl || recUrl) ? `
                    <div style="display:flex; gap:10px; margin-top:8px; flex-wrap:wrap;">
                        ${pUrl ? `<div onclick="openImagePreview('${pUrl}', '📷 수리 현장 사진')" style="cursor:pointer;"><img src="${pUrl}" style="width:110px; height:82px; object-fit:cover; border-radius:8px; border:1px solid #cbd5e1;" /></div>` : ''}
                        ${recUrl ? `<div onclick="openImagePreview('${recUrl}', '🧾 영수증 사진')" style="cursor:pointer;"><img src="${recUrl}" style="width:110px; height:82px; object-fit:cover; border-radius:8px; border:1px solid #cbd5e1;" /></div>` : ''}
                    </div>` : ''}
                </div>`;
            }).join('');
        }

        function readFileAsDataUrl(file) {
            return new Promise((resolve) => {
                if (!file) return resolve('');
                const reader = new FileReader();
                reader.onload = (e) => resolve(e.target.result || '');
                reader.onerror = () => resolve('');
                reader.readAsDataURL(file);
            });
        }

        async function saveRepair() {
            const dong = document.getElementById('repDong').value;
            const room = (document.getElementById('repRoom').value || '공통').replace('호', '');
            const repair_date = document.getElementById('repDate').value;
            const category = document.getElementById('repCat').value;
            const title = (document.getElementById('repTitle').value || '').trim() || category;
            const cost = Number(document.getElementById('repCost').value || 0);
            const description = (document.getElementById('repDesc').value || '').trim();
            const photoFile = (document.getElementById('repPhoto') || {}).files?.[0];
            const receiptFile = (document.getElementById('repReceipt') || {}).files?.[0];

            const photoDataUrl = await readFileAsDataUrl(photoFile);
            const receiptDataUrl = await readFileAsDataUrl(receiptFile);

            const maxId = (dbState.repairs || []).reduce((m, r) => Math.max(m, Number(r.id || 0)), 0);
            dbState.repairs.unshift({
                id: maxId + 1,
                dong, room, repair_date, category, title, description, cost,
                photo_path: photoDataUrl,
                receipt_path: receiptDataUrl
            });
            saveState();

            document.getElementById('repTitle').value = '';
            document.getElementById('repDesc').value = '';
            const gLink = makeGoogleCalLink(dong, `${room}호`, `[${category}] ${title} (${fmtWon(cost)})`, repair_date, description);
            document.getElementById('repGcalLink').href = gLink;
            document.getElementById('repGcalBanner').style.display = 'block';
            renderAllViews();
            showToast('보수공사 내역이 저장되었습니다!');
        }

        function deleteRepair(id) {
            if (!confirm('이 보수공사 내역을 삭제하시겠습니까?')) return;
            dbState.repairs = (dbState.repairs || []).filter(r => Number(r.id) !== Number(id));
            saveState();
            renderAllViews();
            showToast('보수내역이 삭제되었습니다.');
        }

        function openImagePreview(url, title) {
            document.getElementById('imgPreviewTitle').textContent = title || '📷 사진/영수증 원본 보기';
            document.getElementById('imgPreviewEl').src = url;
            document.getElementById('imagePreviewModal').style.display = 'flex';
        }

        function closeImagePreview() {
            document.getElementById('imagePreviewModal').style.display = 'none';
        }

        function renderAlerts() {
            const rows = getActiveMonthContracts();
            const month = document.getElementById('selMonth').value;
            const unpaid = rows.filter(r => r.status !== 'Paid');
            if (unpaid.length === 0) {
                document.getElementById('alertList').innerHTML = `<div class="room-card" style="text-align:center; color:#198754; font-weight:700;">🎉 이번 달 미납 세대가 없습니다! (수납율 100%)</div>`;
                return;
            }
            document.getElementById('alertList').innerHTML = unpaid.map(r => {
                const phoneVal = (r.phone || '').trim();
                const msg = `[임대료 납부 안내]\n안녕하세요, ${r.tenant_name}님.\n회기동 주택 ${r.dong} ${r.room}호 ${month}월 임대료(월세/관리비) 미납 안내드립니다.\n■ 납부 금액: ${fmtWon(r.total_bill)} (월세 ${fmtWon(r.rent)} + 관리비 ${fmtWon(r.maintenance_fee)})\n확인 부탁드립니다. 감사합니다.`;
                const smsHref = `sms:${phoneVal}?body=${encodeURIComponent(msg)}`;
                return `
                <div class="room-card">
                    <div class="room-top">
                        <div class="room-title">🚪 ${r.dong} ${r.room}호 (${r.tenant_name}님)</div>
                        <span class="status-pill-unpaid">미납 ${fmtWon(r.total_bill)}</span>
                    </div>
                    <div style="font-size:12px; color:#64748b; margin-bottom:10px; line-height:1.6;">
                        월세 ${fmtWon(r.rent)} + 관리비 ${fmtWon(r.maintenance_fee)}<br/>
                        📞 전화번호: <b style="color:${phoneVal ? '#0f172a' : '#dc3545'};">${phoneVal || '미등록'}</b>
                        <button onclick="openPhoneModal(${r.contract_id})" style="margin-left:6px; border:1px solid #cbd5e1; background:#f8fafc; color:#0d6efd; border-radius:6px; padding:2px 7px; font-size:11px; font-weight:800; cursor:pointer;">${phoneVal ? '번호수정' : '+ 번호입력'}</button>
                    </div>
                    <div style="display:flex; gap:8px;">
                        <a href="${smsHref}" class="action-btn btn-mark-paid" style="flex:1.2; text-align:center; text-decoration:none;">💬 문자 보내기</a>
                        ${phoneVal ? `<a href="tel:${phoneVal}" class="action-btn" style="background:#198754; color:#fff; text-align:center; text-decoration:none;">📞 전화</a>` : ''}
                    </div>
                </div>`;
            }).join('');
        }

        function setLawType(type) {
            document.getElementById('lawTarget').value = type;
            document.getElementById('btnLawTypeLaw').className = type === 'law' ? 'action-btn btn-mark-paid' : 'action-btn btn-mark-unpaid';
            document.getElementById('btnLawTypePrec').className = type === 'prec' ? 'action-btn btn-mark-paid' : 'action-btn btn-mark-unpaid';
            searchLaw();
        }

        function quickLawSearch(kw) {
            document.getElementById('lawQuery').value = kw;
            searchLaw();
        }

        function searchLaw() {
            const target = document.getElementById('lawTarget').value;
            const query = (document.getElementById('lawQuery').value || '주택임대차').trim();
            const box = document.getElementById('lawResults');
            const exactKey = `${target}:${query}`;
            let items = (dbState.law_search && dbState.law_search[exactKey]) || null;

            if (!items) {
                items = [];
                Object.keys(dbState.law_search || {}).forEach(k => {
                    if (k.startsWith(target + ':')) {
                        (dbState.law_search[k] || []).forEach(it => {
                            if (it.title.includes(query) || query.includes('임대')) {
                                if (!items.some(x => x.id === it.id)) items.push(it);
                            }
                        });
                    }
                });
            }

            if (!items || items.length === 0) {
                const extUrl = `https://www.law.go.kr/lsSc.do?menuId=1&query=${encodeURIComponent(query)}`;
                box.innerHTML = `<div style="padding:12px 0; font-size:13px;">국가법령정보센터에서 <b>'${query}'</b> 실시간 결과를 확인하세요:<br/><a href="${extUrl}" target="_blank" style="display:inline-block; margin-top:8px; padding:8px 14px; background:#0d6efd; color:#fff; border-radius:8px; text-decoration:none; font-weight:800;">국가법령정보센터 '${query}' 바로가기 ↗</a></div>`;
                return;
            }

            box.innerHTML = `
                <div style="font-size:12px; font-weight:700; color:#198754; margin-bottom:6px;">총 ${items.length}건이 조회되었습니다. ('📖 본문 보기' 터치 시 즉시 표시)</div>
            ` + items.map((it, idx) => `
                <div style="padding:12px 0; border-top:1px solid #f1f5f9;">
                    <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:8px;">
                        <div onclick="openLawDetail('${it.target}', '${it.id}', '${it.title.replace(/'/g, '')}')" style="font-size:14px; font-weight:800; color:${target === 'law' ? '#0d6efd' : '#198754'}; flex:1; cursor:pointer;">${idx + 1}. ${it.title}</div>
                        <button onclick="openLawDetail('${it.target}', '${it.id}', '${it.title.replace(/'/g, '')}')" style="border:none; cursor:pointer; padding:6px 11px; background:${target === 'law' ? '#0d6efd' : '#198754'}; color:#fff !important; border-radius:6px; font-size:12px; font-weight:800; white-space:nowrap;">📖 본문 보기</button>
                    </div>
                    <div style="font-size:12px; color:#64748b; margin-top:4px;">${it.sub}</div>
                </div>
            `).join('');
        }

        function openLawDetail(target, id, titleFallback) {
            const modal = document.getElementById('lawDetailModal');
            const titleEl = document.getElementById('lawModalTitle');
            const metaEl = document.getElementById('lawModalMeta');
            const bodyEl = document.getElementById('lawModalBody');
            modal.style.display = 'flex';
            const dkey = `${target}:${id}`;
            const cached = dbState.law_detail && dbState.law_detail[dkey];
            if (cached) {
                titleEl.textContent = cached.title;
                metaEl.textContent = cached.meta;
                bodyEl.innerHTML = cached.html;
            } else {
                const extUrl = target === 'law'
                    ? `https://www.law.go.kr/DRF/lawService.do?OC=sdgpks21&target=law&MST=${id}&type=HTML`
                    : `https://www.law.go.kr/DRF/lawService.do?OC=sdgpks21&target=prec&ID=${id}&type=HTML`;
                titleEl.textContent = titleFallback || '법령/판례 전문';
                metaEl.textContent = '국가법령정보센터 연동';
                bodyEl.innerHTML = `<div style="padding:20px 0; text-align:center;"><a href="${extUrl}" target="_blank" style="display:inline-block; padding:10px 18px; background:#0d6efd; color:#fff; border-radius:8px; text-decoration:none; font-weight:800;">국가법령정보센터 원문 전체 열기 ↗</a></div>`;
            }
        }

        function closeLawDetailModal() {
            document.getElementById('lawDetailModal').style.display = 'none';
        }

        function updateScheduleRooms() {
            const dongEl = document.getElementById('scDong');
            const roomEl = document.getElementById('scRoom');
            if (!dongEl || !roomEl) return;
            const dong = dongEl.value;
            let opts = `<option value="공통">공통</option>`;
            if (dong === 'A동' || dong === 'B동') {
                (dbState.contracts || []).filter(c => c.dong === dong).forEach(r => {
                    opts += `<option value="${r.room}호">${r.room}호 (${r.tenant_name})</option>`;
                });
            }
            roomEl.innerHTML = opts;
        }

        function makeGoogleCalLink(dong, room, title, dateStr, desc) {
            if (!dateStr) return 'https://calendar.google.com';
            const d = new Date(dateStr + 'T00:00:00');
            const next = new Date(d.getTime() + 86400000);
            const fmt = (dt) => dt.toISOString().split('T')[0].replace(/-/g, '');
            const datesParam = `${fmt(d)}/${fmt(next)}`;
            const gTitle = `[${dong} ${room}] ${title}`;
            const gDetails = `동호실: ${dong} ${room}\n구분: ${title}\n설명: ${desc || '-'}`;
            const proEmail = dbState.pro_email || 'ferriolacipriano342@gmail.com';
            return `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${encodeURIComponent(gTitle)}&dates=${datesParam}&details=${encodeURIComponent(gDetails)}&authuser=${encodeURIComponent(proEmail)}`;
        }

        function moveCalMonth(delta) {
            calMonth += delta;
            if (calMonth < 1) { calMonth = 12; calYear -= 1; }
            if (calMonth > 12) { calMonth = 1; calYear += 1; }
            renderInteractiveCalendar();
        }

        function selectCalDate(dateStr) {
            const el = document.getElementById('scDate');
            if (el) {
                el.value = dateStr;
                showToast(`📅 ${dateStr} 날짜가 선택되었습니다.`);
            }
            renderInteractiveCalendar();
        }

        function renderInteractiveCalendar() {
            const box = document.getElementById('interactiveCalendar');
            const titleEl = document.getElementById('calHeaderTitle');
            if (!box || !titleEl) return;

            titleEl.textContent = `🗓️ ${calYear}년 ${calMonth}월 달력`;
            const firstDay = new Date(calYear, calMonth - 1, 1).getDay();
            const lastDate = new Date(calYear, calMonth, 0).getDate();
            const selectedDateStr = (document.getElementById('scDate') || {}).value || '';
            const ymPrefix = `${calYear}-${String(calMonth).padStart(2, '0')}-`;
            const schedMap = {};
            (dbState.schedules || []).forEach(s => {
                if ((s.event_date || '').startsWith(ymPrefix)) {
                    schedMap[s.event_date] = schedMap[s.event_date] || [];
                    schedMap[s.event_date].push({ type: 'sched', label: `${s.room} ${s.title}` });
                }
            });
            (dbState.contracts || []).forEach(c => {
                if ((c.expiry_date || '').startsWith(ymPrefix)) {
                    schedMap[c.expiry_date] = schedMap[c.expiry_date] || [];
                    schedMap[c.expiry_date].push({ type: 'exp', label: `${c.dong}${c.room}만기` });
                }
            });

            const daysHeader = ['일', '월', '화', '수', '목', '금', '토'].map((d, idx) => {
                const col = idx === 0 ? '#dc3545' : (idx === 6 ? '#0d6efd' : '#475569');
                return `<th style="padding:6px 2px; font-size:12px; color:${col}; border-bottom:1px solid #e2e8f0;">${d}</th>`;
            }).join('');

            let cells = [];
            for (let i = 0; i < firstDay; i++) {
                cells.push(`<td style="padding:6px 2px; height:48px; background:#f8fafc;"></td>`);
            }
            for (let day = 1; day <= lastDate; day++) {
                const dStr = `${ymPrefix}${String(day).padStart(2, '0')}`;
                const dow = (firstDay + day - 1) % 7;
                const numColor = dow === 0 ? '#dc3545' : (dow === 6 ? '#0d6efd' : '#0f172a');
                const isSelected = dStr === selectedDateStr;
                const bg = isSelected ? '#dbeafe' : '#ffffff';
                const border = isSelected ? '2px solid #0d6efd' : '1px solid #f1f5f9';
                const evs = schedMap[dStr] || [];
                const badges = evs.slice(0, 2).map(e => {
                    const bColor = e.type === 'sched' ? '#0d6efd' : '#fd7e14';
                    return `<div style="font-size:9px; background:${bColor}; color:#fff; border-radius:3px; padding:1px 2px; margin-top:2px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${e.label}</div>`;
                }).join('');

                cells.push(`
                    <td onclick="selectCalDate('${dStr}')" style="padding:4px 2px; height:50px; vertical-align:top; background:${bg}; border:${border}; cursor:pointer;">
                        <div style="font-size:12px; font-weight:800; color:${numColor};">${day}</div>
                        ${badges}
                    </td>
                `);
            }
            while (cells.length % 7 !== 0) {
                cells.push(`<td style="padding:6px 2px; height:48px; background:#f8fafc;"></td>`);
            }
            let rowsHtml = '';
            for (let i = 0; i < cells.length; i += 7) {
                rowsHtml += `<tr>${cells.slice(i, i + 7).join('')}</tr>`;
            }
            box.innerHTML = `<table style="width:100%; border-collapse:collapse; table-layout:fixed; text-align:center;"><thead><tr>${daysHeader}</tr></thead><tbody>${rowsHtml}</tbody></table>`;
        }

        function renderSchedules() {
            const box = document.getElementById('scheduleList');
            if (!box) return;
            const filterDong = (document.getElementById('scFilterDong') || {}).value || '전체';
            let list = dbState.schedules || [];
            if (filterDong !== '전체') list = list.filter(s => s.dong === filterDong);
            if (list.length === 0) {
                box.innerHTML = `<div style="font-size:13px; color:#64748b; padding:12px 0; text-align:center;">등록된 일정이 없습니다.</div>`;
                return;
            }
            box.innerHTML = list.map(s => {
                const gLink = makeGoogleCalLink(s.dong, s.room, s.title, s.event_date, s.description);
                return `
                <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; padding:10px 0; border-top:1px solid #f1f5f9; font-size:13px;">
                    <div style="flex:1;">
                        <div style="font-weight:800; color:#0f172a;">🗓️ [${s.event_date}] ${s.dong} ${s.room} - <span style="color:#0d6efd;">${s.title}</span></div>
                        ${s.description ? `<div style="font-size:12px; color:#64748b; margin-top:2px;">메모: ${s.description}</div>` : ''}
                    </div>
                    <div style="display:flex; gap:6px; align-items:center;">
                        <a href="${gLink}" target="_blank" style="padding:5px 8px; background:#e8f5e9; color:#1b5e20; border:1px solid #a5d6a7; border-radius:6px; font-size:11px; font-weight:800; text-decoration:none;">구글캘린더 ↗</a>
                        <button class="action-btn btn-mark-unpaid" style="padding:5px 8px; font-size:11px;" onclick="deleteSchedule(${s.id})">삭제</button>
                    </div>
                </div>`;
            }).join('');
        }

        function addSchedule() {
            const dong = document.getElementById('scDong').value;
            const room = document.getElementById('scRoom').value || '공통';
            const typeVal = document.getElementById('scType').value || '기타';
            const customTitle = (document.getElementById('scTitle').value || '').trim();
            const finalTitle = customTitle || typeVal;
            const event_date = document.getElementById('scDate').value;
            const description = (document.getElementById('scDesc').value || '').trim();
            const cost = Number((document.getElementById('scCost') || {}).value || 0);

            const maxId = (dbState.schedules || []).reduce((m, s) => Math.max(m, Number(s.id || 0)), 0);
            dbState.schedules.push({
                id: maxId + 1,
                dong, room, title: finalTitle, event_date, description
            });
            saveState();
            const gLink = makeGoogleCalLink(dong, room, finalTitle, event_date, description);
            document.getElementById('gcalSaveLink').href = gLink;
            document.getElementById('gcalSaveBanner').style.display = 'block';
            renderAllViews();
            showToast('일정이 등록되었습니다.');
        }

        function deleteSchedule(id) {
            dbState.schedules = (dbState.schedules || []).filter(s => Number(s.id) !== Number(id));
            saveState();
            renderAllViews();
            showToast('일정이 삭제되었습니다.');
        }

        function updateGoogleCalIframe() {
            const input = document.getElementById('gcalIdInput');
            const iframe = document.getElementById('gcalIframe');
            if (!input || !iframe) return;
            const calId = (input.value || '').trim() || 'ferriolacipriano342@gmail.com';
            iframe.src = `https://calendar.google.com/calendar/embed?src=${encodeURIComponent(calId)}&src=ko.south_korea%23holiday%40group.v.calendar.google.com&ctz=Asia%2FSeoul&mode=MONTH&showTitle=0&showNav=1&showDate=1&showPrint=0&showTabs=0&showCalendars=0`;
            showToast('구글 캘린더가 연동되었습니다.');
        }

        function switchTab(tab, btn) {
            ['pay', 'contracts', 'repairs', 'alerts', 'law', 'calendar'].forEach(id => {
                const el = document.getElementById('tab-' + id);
                if (el) el.style.display = (id === tab) ? 'block' : 'none';
            });
            document.querySelectorAll('.bottom-nav .nav-item').forEach(el => el.classList.remove('active'));
            if (btn) btn.classList.add('active');
            if (tab === 'law') searchLaw();
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }

        /* =========================================================
           글씨 크기 조절 기능 (슬라이더 + [- 가-] [125%] [+ 가+] 완벽 연동)
           ========================================================= */
        let currentFontPct = parseInt(localStorage.getItem('hoegi_font_pct_v2') || '125', 10);
        if (isNaN(currentFontPct) || currentFontPct < 85 || currentFontPct > 175) {
            currentFontPct = 125;
        }

        function applyFontScale(showMsg = false) {
            const scale = currentFontPct / 100.0;
            document.documentElement.style.setProperty('--fs-scale', String(scale));
            const badge = document.getElementById('fontScaleLabel');
            if (badge) badge.textContent = currentFontPct + '%';
            const slider = document.getElementById('fontSlider');
            if (slider && Number(slider.value) !== currentFontPct) {
                slider.value = String(currentFontPct);
            }
            if (showMsg) {
                showToast(`🔍 글씨 크기: ${currentFontPct}%`);
            }
        }

        function onFontSliderChange(val) {
            currentFontPct = Math.min(175, Math.max(85, parseInt(val, 10) || 125));
            localStorage.setItem('hoegi_font_pct_v2', String(currentFontPct));
            applyFontScale(true);
        }

        function changeFontSize(deltaPct) {
            currentFontPct = Math.min(175, Math.max(85, currentFontPct + deltaPct));
            localStorage.setItem('hoegi_font_pct_v2', String(currentFontPct));
            applyFontScale(true);
        }

        function resetFontSize() {
            currentFontPct = 125;
            localStorage.setItem('hoegi_font_pct_v2', String(currentFontPct));
            applyFontScale(true);
        }

        (function init() {
            loadState();
            const todayStr = new Date().toISOString().split('T')[0];
            document.getElementById('selYear').value = '2026';
            document.getElementById('selMonth').value = '11';
            if (document.getElementById('scDate')) document.getElementById('scDate').value = todayStr;
            if (document.getElementById('repDate')) document.getElementById('repDate').value = todayStr;
            applyFontScale(false);
            renderAllViews();
        })();
    </script>
</body>
</html>
"""

final_html = (
    MOBILE_WEBAPP_HTML
    .replace("__ICON_DATA_URI__", ICON_DATA_URI)
    .replace("__BUNDLE_JSON__", json.dumps(BUNDLE_DATA, ensure_ascii=False))
)

components.html(final_html, height=920, scrolling=True)
