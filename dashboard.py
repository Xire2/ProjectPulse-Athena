"""
Project Pulse — Dynamic Program Dashboard
==========================================
Streamlit app for Program Chairs, Faculty/Program Advisors, and the Dean.
"""

import streamlit as st
import pandas as pd
import plotly.express as px
from sqlalchemy import text
from datetime import datetime
from zoneinfo import ZoneInfo
import re
import os
import io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
from reportlab.lib import colors as rl_colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, Image as RLImage, PageBreak
)


# Generic title
st.set_page_config(page_title="Project Pulse — Program Dashboard", page_icon="🎓", layout="wide")
st.markdown("""
<style>
/* ---------- RESPONSIVE DASHBOARD ---------- */

[data-testid="stAppViewContainer"] .main .block-container {
    width: 100%;
    max-width: 1800px;
    padding-left: clamp(1rem, 2vw, 3rem);
    padding-right: clamp(1rem, 2vw, 3rem);
}

div[data-testid="stHorizontalBlock"] {
    min-width: 0;
}

div[data-testid="stHorizontalBlock"] > div {
    min-width: 0;
}

div[data-testid="stMetric"] {
    min-width: 0;
    overflow: hidden;
}

div[data-testid="stMetricLabel"],
div[data-testid="stMetricValue"],
div[data-testid="stMetricDelta"] {
    min-width: 0;
    overflow-wrap: anywhere;
}

div[data-testid="stMetricValue"] div {
    max-width: 100%;
}

.stButton button,
.stDownloadButton button {
    min-height: 42px;
    white-space: normal !important;
    overflow-wrap: anywhere;
}

[data-testid="stDataFrame"],
[data-testid="stTable"] {
    width: 100%;
}
div[data-testid="stPlotlyChart"] {
    width: 100% !important;
    max-width: 100% !important;
}

div[data-testid="stVerticalBlockBorderWrapper"] {
    min-width: 0 !important;
    max-width: 100% !important;
}

@media (max-width: 1100px) {
    div[data-testid="stMetricValue"] div {
        font-size: 1.7rem !important;
    }

    div[data-testid="stMetricLabel"] p {
        font-size: 0.7rem !important;
    }
}

@media (max-width: 768px) {
    [data-testid="stAppViewContainer"] .main .block-container {
        padding-left: 0.75rem;
        padding-right: 0.75rem;
    }

    div[data-testid="stMetricValue"] div {
        font-size: 1.5rem !important;
    }

    div[data-testid="stMetricLabel"] p {
        font-size: 0.68rem !important;
        line-height: 1.2 !important;
    }

    h1, h2, h3, h4 {
        overflow-wrap: anywhere;
    }

    .stButton button,
    .stDownloadButton button {
        min-height: 44px;
    }
}

@media (max-width: 600px) {
    div[data-testid="stMetricValue"] div {
        font-size: 1.35rem !important;
    }
}
</style>
""", unsafe_allow_html=True)
# ------------------------------------------------------------------
# ETHICAL VISUALIZATION SPECIFICATION & THRESHOLDS
# ------------------------------------------------------------------
PALETTE = {
    "green": {"hex": "#0DC249", "symbol": "🟢"},
    "yellow": {"hex": "#FFAE00", "symbol": "🟡"},
    "red": {"hex": "#D50000", "symbol": "🔴"},
    "blue": {"hex": "#0072B2", "symbol": "🔵"},
    "gray": {"hex": "#999999", "symbol": "⚪"},
}

STAGE_THRESHOLDS = {
    "coursework": {"green": ["Completed"], "yellow": ["Pending"], "red": ["Cancelled"]},
    "comprehensive_exam": {"green": ["Passed"], "yellow": ["In-Progress"], "red": ["Incomplete", "Cancelled"]},
    "capstone": {"green": ["Defended"], "yellow": ["In-Progress"], "red": ["Incomplete", "Cancelled"]}, 
}

COURSEWORK_MAP = {"completed": "Completed", "cancelled": "Cancelled", "pending": "Pending"}

COMPREHENSIVE_EXAM_MAP = {
    "done": "Passed", 
    "incomplete": "Incomplete", 
    "not yet taken": "In-Progress", 
    "abs/failed": "Incomplete",
    "cancelled": "Cancelled" 
}

CAPSTONE_MAP = {
    "done": "Defended", 
    "not yet done": "In-Progress", 
    "not yet taken": "In-Progress", 
    "in current load": "In-Progress", 
    "incomplete": "In-Progress", 
    "n/a": "In-Progress",
    "cancelled": "Cancelled"
}

def map_status(raw_value, mapping, default="Unknown"):
    if raw_value is None or pd.isna(raw_value): return default
    return mapping.get(str(raw_value).strip().lower(), default)

def render_pill(label: str, color_key: str) -> str:
    c = PALETTE.get(color_key, PALETTE["gray"])
    hex_color = c["hex"]
    return (
        f'<span style="display:inline-block;padding:3px 14px;border-radius:999px;'
        f'border:1.5px solid {hex_color};color:{hex_color};background-color:{hex_color}1A;'
        f'font-size:0.85rem;font-weight:600;white-space:nowrap;line-height:1.4;">{label}</span>'
    )

def get_stage_badge(stage: str, status_label: str) -> str:
    rule = STAGE_THRESHOLDS.get(stage, {})
    clean_label = str(status_label).strip()
    if clean_label in rule.get("green", []): color_key = "green"
    elif clean_label in rule.get("yellow", []): color_key = "yellow"
    elif clean_label in rule.get("red", []): color_key = "red"
    else: color_key = "gray"
    return render_pill(clean_label, color_key)

def make_status_styler(stage: str):
    def _style(series):
        rule = STAGE_THRESHOLDS.get(stage, {})
        styles = []
        for val in series:
            label = str(val).strip()
            if label in rule.get("green", []): hexc = PALETTE["green"]["hex"]
            elif label in rule.get("yellow", []): hexc = PALETTE["yellow"]["hex"]
            elif label in rule.get("red", []): hexc = PALETTE["red"]["hex"]
            else: hexc = PALETTE["gray"]["hex"]
            styles.append(f"color: {hexc}; background-color: {hexc}1A; font-weight: 600; border-radius: 4px;")
        return styles
    return _style

def status_badge(label: str) -> str:
    key_map = {
        "Completed": "green", "Passed": "green", "Defended": "green",
        "Graduated": "green", "Enrolled": "green",
        "Active": "blue",
        "In-Progress": "yellow", "Pending": "yellow", "Conditionally Enrolled": "yellow",
        "Cancelled": "red", "Incomplete": "red",
    }
    color_key = key_map.get(str(label), "gray")
    return render_pill(str(label), color_key)

def overall_status(row) -> str:
    if "overall_status" in row and pd.notna(row["overall_status"]) and str(row["overall_status"]).strip():
        return str(row["overall_status"]).strip()
    coursework = str(row.get("coursework_status", "")).strip().upper()
    if coursework == "PENDING": return "Active"
    elif coursework == "COMPLETED": return "Graduated"
    elif coursework == "CANCELLED": return "Cancelled"
    return "Unknown"

# ------------------------------------------------------------------
# DATABASE & AUDIT LOGGING
# ------------------------------------------------------------------
try:
    conn = st.connection("supabase_db", type="sql")
except Exception as e:
    st.error("🚨 Critical Error: Failed to connect to Supabase. Verify `.streamlit/secrets.toml` credentials.")
    st.stop()

def log_security_event(user_id: int, event_type: str, details: str):
    try:
        with conn.session as s:
            s.execute(
                text("""
                    INSERT INTO audit_logs (user_id, event_type, details)
                    VALUES (:uid, :event_type, :details);
                """),
                {"uid": user_id, "event_type": event_type, "details": details}
            )
            s.commit()
    except Exception:
        pass

def authenticate_user(username: str, password_attempt: str):
    query = text("""
        SELECT user_id, username, full_name, role, can_edit, is_active
        FROM app_users
        WHERE username = :u AND password_hash = crypt(:p, password_hash) AND is_active = TRUE;
    """)
    try:
        with conn.session as s:
            return s.execute(query, {"u": username.strip(), "p": password_attempt}).mappings().fetchone()
    except Exception as e:
        st.error(f"Authentication query error: {e}")
        return None

# ------------------------------------------------------------------
# GLOBAL DASHBOARD CONFIGURATION (DYNAMIC CONTEXT)
# ------------------------------------------------------------------
@st.cache_data(ttl=60)
def load_dashboard_config():
    try:
        res = conn.query("""
            SELECT p.program_code AS active_program, dc.current_term 
            FROM dashboard_config dc
            LEFT JOIN program p ON dc.program_id = p.program_id
            WHERE dc.config_id = 1;
        """, ttl=0)
        if not res.empty:
            return res.iloc[0]["active_program"], res.iloc[0]["current_term"]
    except Exception:
        pass
    return "UNCONFIGURED PROGRAM", "UNCONFIGURED TERM"

ACTIVE_PROGRAM, CURRENT_TERM_LABEL = load_dashboard_config()

# ------------------------------------------------------------------
# DATA LOADERS (DYNAMICALLY MAPPED & FILTERED BY PROGRAM)
# ------------------------------------------------------------------
def get_cohort_val(c):
    m = re.search(r'(\d)[TQ](\d{2})(\d{2})', str(c).upper())
    return float(f"{m.group(2)}{m.group(3)}.{m.group(1)}") if m else 0.0

@st.cache_data(ttl=60, show_spinner="Loading mapped student roster...")
def load_students(target_program: str) -> tuple[pd.DataFrame, str]:
    # 1. Fetch Program-Specific Thresholds
    try:
        t_query = text("""
            SELECT cw_days, ce_days, cap_days 
            FROM program_thresholds pt
            JOIN program p ON pt.program_id = p.program_id
            WHERE p.program_code = :prog
        """)
        with conn.session as s:
            t_res = s.execute(t_query, {"prog": target_program}).fetchone()
        cw_thresh = t_res[0] if t_res else 730
        ce_thresh = t_res[1] if t_res else 180
        cap_thresh = t_res[2] if t_res else 365
    except Exception:
        cw_thresh, ce_thresh, cap_thresh = 730, 180, 365

    # 2. Fetch Core Data
    try:
        query = """
            SELECT 
                s.student_number, s.student_email, s.first_name, s.last_name, 
                a.full_name AS adviser, s.graduate_on_time, s.graduate_date_term_sy, 
                s.remarks, s.created_at, c.cohort_code AS cohort, 
                p.program_code AS program, p.program_name AS program_name,
                MAX(CASE WHEN stg.stage_name = 'coursework' THEN sts.status_name END) AS coursework_status,
                MAX(CASE WHEN stg.stage_name = 'comprehensive_exam' THEN sts.status_name END) AS comprehensive_exam,
                MAX(CASE WHEN stg.stage_name = 'capstone' THEN sts.status_name END) AS capstone,
                MAX(CASE WHEN stg.stage_name = 'coursework' THEN sls.last_updated_date END) AS updated_at,
                MAX(CASE WHEN stg.stage_name = 'coursework' THEN sls.last_updated_date END) AS cw_updated_at,
                MAX(CASE WHEN stg.stage_name = 'comprehensive_exam' THEN sls.last_updated_date END) AS ce_updated_at,
                MAX(CASE WHEN stg.stage_name = 'capstone' THEN sls.last_updated_date END) AS cap_updated_at
            FROM students_normalized s
            LEFT JOIN cohort c ON s.cohort_id = c.cohort_id
            LEFT JOIN program p ON s.program_id = p.program_id
            LEFT JOIN advisers a ON s.adviser_id = a.adviser_id
            LEFT JOIN student_lifecycle_status sls ON s.student_number = sls.student_number
            LEFT JOIN lifecycle_stage stg ON sls.stage_id = stg.stage_id
            LEFT JOIN lifecycle_status sts ON sls.status_id = sts.status_id
            GROUP BY 
                s.student_number, s.student_email, s.first_name, s.last_name, 
                a.full_name, s.graduate_on_time, s.graduate_date_term_sy, 
                s.remarks, s.created_at, c.cohort_code, p.program_code, p.program_name;
        """
        df = conn.query(query, ttl=0)
    except Exception:
        df = pd.DataFrame()
    
    try:
        mapping_df = conn.query("SELECT dashboard_field, db_column FROM field_mappings;")
        db_to_app_map = dict(zip(mapping_df["db_column"], mapping_df["dashboard_field"]))
        df = df.rename(columns=db_to_app_map)
        df = df.loc[:, ~df.columns.duplicated()]
    except Exception:
        st.sidebar.warning("Schema mapping table missing or misconfigured.")

    expected_cols = [
        "program", "first_name", "last_name", "student_number", "cohort", "coursework_status", 
        "comprehensive_exam", "capstone", "graduate_on_time", "graduate_date_term_sy", 
        "adviser", "remarks", "student_email", "updated_at"
    ]
    for col in expected_cols:
        if col not in df.columns: df[col] = None

    if target_program != "UNCONFIGURED PROGRAM" and df["program"].notna().any():
        df = df[df["program"].astype(str).str.strip().str.upper() == target_program.strip().upper()]

    if "full_name" not in df.columns or df["full_name"].isna().all():
        df["full_name"] = (df["first_name"].fillna("") + " " + df["last_name"].fillna("")).str.strip()

    df = df.sort_values(by=["last_name", "first_name"], na_position="last").reset_index(drop=True)

    if "overall_status" not in df.columns or df["overall_status"].isna().all():
        df["overall_status"] = df.apply(overall_status, axis=1)

    df["coursework_display"] = df["coursework_status"].apply(lambda v: map_status(v, COURSEWORK_MAP))
    df["comprehensive_exam_display"] = df["comprehensive_exam"].apply(lambda v: map_status(v, COMPREHENSIVE_EXAM_MAP))
    df["capstone_display"] = df["capstone"].apply(lambda v: map_status(v, CAPSTONE_MAP))

    # 3. Calculate "At Risk" Flags
    now_utc = pd.Timestamp.utcnow()
    def calculate_risk(row):
        reasons = []
        if row.get('coursework_display') == 'Pending' and pd.notna(row.get('cw_updated_at')):
            days = (now_utc - pd.to_datetime(row['cw_updated_at'], utc=True)).days
            if days > cw_thresh: reasons.append(f"Coursework pending for {days} days (Limit: {cw_thresh})")
            
        if row.get('comprehensive_exam_display') == 'In-Progress' and pd.notna(row.get('ce_updated_at')):
            days = (now_utc - pd.to_datetime(row['ce_updated_at'], utc=True)).days
            if days > ce_thresh: reasons.append(f"Exam in-progress for {days} days (Limit: {ce_thresh})")
            
        if row.get('capstone_display') == 'In-Progress' and pd.notna(row.get('cap_updated_at')):
            days = (now_utc - pd.to_datetime(row['cap_updated_at'], utc=True)).days
            if days > cap_thresh: reasons.append(f"Capstone in-progress for {days} days (Limit: {cap_thresh})")
            
        return " | ".join(reasons) if reasons else ""

    df['risk_details'] = df.apply(calculate_risk, axis=1)
    df['is_at_risk'] = df['risk_details'] != ""
    df['risk_flag'] = df['is_at_risk'].apply(lambda x: "Flagged" if x else "On Track")

    # Time parsing
    def to_manila_time(series):
        dt = pd.to_datetime(series, errors="coerce")
        if dt.dt.tz is None: dt = dt.dt.tz_localize("UTC")
        return dt.dt.tz_convert("Asia/Manila").dt.strftime("%B %d, %Y at %I:%M %p")

    if "updated_at" in df.columns and df["updated_at"].notna().any():
        df["coursework_updated_at"] = to_manila_time(df["updated_at"]).fillna("N/A")
    else:
        df["coursework_updated_at"] = "N/A"

    df["coursework_indicator"] = df["coursework_display"].apply(lambda v: get_stage_badge("coursework", v))
    df["exam_indicator"] = df["comprehensive_exam_display"].apply(lambda v: get_stage_badge("comprehensive_exam", v))
    df["capstone_indicator"] = df["capstone_display"].apply(lambda v: get_stage_badge("capstone", v))

    sync_time = datetime.now(ZoneInfo("Asia/Manila")).strftime("%B %d, %Y at %I:%M:%S %p")
    return df, sync_time

@st.cache_data(ttl=60)
def fetch_student_courses(student_number: int) -> pd.DataFrame:
    query = f"""
        SELECT 
            UPPER(e.course_code) AS "Course Code",
            COALESCE(c.course_name, 'Course Unit') AS "Course Name",
            COALESCE(c.credits, 3) AS "Credits",
            INITCAP(e.status) AS "Status"
        FROM student_course_enrollments e
        LEFT JOIN courses c ON e.course_code = c.course_code
        WHERE e.student_number = {int(student_number)}
        ORDER BY e.course_code ASC;
    """
    try: return conn.query(query, ttl=0)
    except Exception: return pd.DataFrame()

@st.cache_data(ttl=60)
def fetch_student_milestones(student_number: int) -> pd.DataFrame:
    query = f"""
        SELECT 
            stg.stage_name AS milestone_type,
            INITCAP(REPLACE(stg.stage_name, '_', ' ')) AS "Milestone",
            INITCAP(sts.status_name) AS "Recorded Status",
            sls.last_updated_date AS "Last Updated"
        FROM student_lifecycle_status sls
        JOIN lifecycle_stage stg ON sls.stage_id = stg.stage_id
        JOIN lifecycle_status sts ON sls.status_id = sts.status_id
        WHERE sls.student_number = {int(student_number)}
        ORDER BY stg.stage_id ASC;
    """
    try: 
        df = conn.query(query, ttl=0)
        if not df.empty and "Last Updated" in df.columns and df["Last Updated"].notna().any():
            dt = pd.to_datetime(df["Last Updated"], errors="coerce")
            if dt.dt.tz is None: dt = dt.dt.tz_localize("UTC")
            df["Last Updated"] = dt.dt.tz_convert("Asia/Manila").dt.strftime("%B %d, %Y at %I:%M %p")
        return df
    except Exception: 
        return pd.DataFrame()

# ------------------------------------------------------------------
# SESSION STATE MANAGEMENT
# ------------------------------------------------------------------
if "authenticated" not in st.session_state: st.session_state.authenticated = False
if "user_info" not in st.session_state: st.session_state.user_info = None
if "page" not in st.session_state: st.session_state.page = "list"
if "selected_student_email" not in st.session_state: st.session_state.selected_student_email = None
if "table_key_counter" not in st.session_state: st.session_state.table_key_counter = 0
if "chart_key_counter" not in st.session_state: st.session_state.chart_key_counter = 0
if "drill_stage" not in st.session_state: st.session_state.drill_stage = None

if "previous_view" not in st.session_state: 
    st.session_state.previous_view = "Executive Dashboard"

def go_to_profile(student_email=None):
    if student_email:
        st.session_state.selected_student_email = student_email
    
    # Remember the current page before switching to the profile
    if st.session_state.admin_view != "Student Profile Inspector":
        st.session_state.previous_view = st.session_state.admin_view
        
    st.session_state.admin_view = "Student Profile Inspector"
    st.session_state.page = "profile"

def go_to_list():
    # Return to whatever page was saved in previous_view
    st.session_state.admin_view = st.session_state.get("previous_view", "Executive Dashboard")
    st.session_state.page = "list"
    st.session_state.selected_student_email = None
    st.session_state.table_key_counter += 1

# ------------------------------------------------------------------
# VIEW: LOGIN PAGE
# ------------------------------------------------------------------
def render_login_page():
    st.markdown(
        """
        <style>
        .stButton > button[kind="primary"] { background-color: #b92b27 !important; border-color: #b92b27 !important; color: white !important; }
        .stButton > button[kind="primary"] p, .stButton > button[kind="primary"] span { color: white !important; }
        .stButton > button[kind="primary"]:hover { background-color: #FF4B4B !important; border-color: #FF4B4B !important; color: white !important; }
        </style>
        """, unsafe_allow_html=True
    )
    logo_left, logo_center, logo_right = st.columns([2, 1, 2])
    with logo_center: st.image("rectangle_logo.png", width=500)
    st.markdown("<p style='text-align: center; color: gray;'>Project Pulse Student Management Portal</p>", unsafe_allow_html=True)
    st.divider()

    _, col_mid, _ = st.columns([1, 1.2, 1])
    with col_mid:
        with st.container(border=True):
            st.subheader("Account Login")
            input_username = st.text_input("Username")
            input_password = st.text_input("Password", type="password")

            if st.button("Sign In", use_container_width=True, type="primary"):
                if not input_username or not input_password:
                    st.warning("Please supply both username and password.")
                    return

                auth_record = authenticate_user(input_username, input_password)
                if auth_record:
                    st.session_state.authenticated = True
                    st.session_state.user_info = dict(auth_record)
                    log_security_event(auth_record["user_id"], "LOGIN_SUCCESS", "User authenticated.")
                    st.success(f"Welcome, {auth_record['full_name']}")
                    st.rerun()
                else:
                    log_security_event(None, "LOGIN_FAILURE", "Invalid credentials provided.")
                    st.error("Authentication failed: Invalid username or password.")
            st.caption("Default seeds: `dean_exec`, `chair_mba`, `admin_sec` | **Advisers:** `asmith`, `bjones`, `cbrown`, `dprince`")

if not st.session_state.authenticated:
    render_login_page()
    st.stop()

# ------------------------------------------------------------------
# AUTHENTICATED USER HEADER & NAVIGATION
# ------------------------------------------------------------------
user = st.session_state.user_info

# Use columns [1, 2, 1] to make the center column exactly 50% width
logo_left, logo_center, logo_right = st.sidebar.columns([1, 6, 1])
with logo_center:
    st.image("square_logo.png", use_container_width=True)

st.sidebar.title(f"👤 {user['full_name']}")
st.sidebar.caption(f"Role: **{user['role']}** | Permissions: **{'Read/Write' if user.get('can_edit', False) else 'View-Only'}**")

if "admin_view" not in st.session_state: 
    st.session_state.admin_view = "Executive Dashboard"

# 1. Define core app navigation options vs admin configuration options
core_nav_options = ["Executive Dashboard", "Student Roster", "Student Profile Inspector"]
admin_nav_options = ["Global Instance Settings", "Schema Mapping Config", "Permissions & Audit Logs"]

# Ensure current view is valid
all_valid_options = core_nav_options + (admin_nav_options if user["role"] == "IT/Admin" else [])
if st.session_state.admin_view not in all_valid_options: 
    st.session_state.admin_view = "Executive Dashboard"

# 2. Render Admin Functions inside an Expandable Dropdown using custom buttons (Only for IT/Admin role)
if user["role"] == "IT/Admin":
    is_currently_admin = st.session_state.admin_view in admin_nav_options
    
    with st.sidebar.expander("⚙️ Admin Configuration", expanded=is_currently_admin):
        admin_nav_config = {
            "Global Instance Settings": "GLOBAL INSTANCE SETTINGS",
            "Schema Mapping Config": "SCHEMA MAPPING CONFIG",
            "Permissions & Audit Logs": "PERMISSIONS & AUDIT LOGS"
        }
        
        for opt in admin_nav_options:
            label = admin_nav_config.get(opt, opt.upper())
            is_active = (st.session_state.admin_view == opt)
            display_label = f"{label}  •" if is_active else label
            
            if st.button(display_label, key=f"admin_btn_{opt.lower().replace(' ', '_')}", use_container_width=True):
                if st.session_state.admin_view != opt:
                    st.session_state.admin_view = opt
                    st.rerun()
            

# Inject custom CSS for modern dark sidebar & fixed expander header colors
st.sidebar.markdown("""
<style>
/* Rich modern obsidian/charcoal sidebar background */
section[data-testid="stSidebar"] {
    background-color: #111318 !important;
    color: #f8fafc !important;
}

/* Ensure all sidebar text elements remain crisp white */
section[data-testid="stSidebar"] p, 
section[data-testid="stSidebar"] span, 
section[data-testid="stSidebar"] label, 
section[data-testid="stSidebar"] h3 {
    color: #f8fafc !important;
}

/* Fix Streamlit Expander Header (Admin Configuration) for all states */
section[data-testid="stSidebar"] [data-testid="stExpander"] {
    background-color: #1a1f2c !important;
    border: 1px solid #2d3748 !important;
    border-radius: 8px !important;
}

section[data-testid="stSidebar"] [data-testid="stExpander"] summary {
    background-color: #1a1f2c !important;
    color: #f8fafc !important;
    border-radius: 8px !important;
}

section[data-testid="stSidebar"] [data-testid="stExpander"] summary:hover {
    background-color: #252b3b !important;
    color: #ffffff !important;
}

section[data-testid="stSidebar"] [data-testid="stExpander"] summary span {
    color: #f8fafc !important;
}

/* Professional Navigation & Footer Buttons */
section[data-testid="stSidebar"] .stButton button {
    border-radius: 8px !important;
    border: 1px solid #2d3748 !important;
    background-color: #1a1f2c !important;
    color: #f8fafc !important;
    text-align: left !important;
    font-weight: 600 !important;
    font-size: 0.85rem !important;
    padding: 0.7rem 1rem !important;
    margin-bottom: 0.35rem !important;
    width: 100% !important;
    box-shadow: 0 1px 3px rgba(0,0,0,0.3) !important;
    transition: all 0.2s ease-in-out !important;
}

/* Vibrant Red Hover and Active States */
section[data-testid="stSidebar"] .stButton button:hover {
    background-color: #dc2626 !important;
    color: #ffffff !important;
    border-color: #ef4444 !important;
    transform: translateX(2px);
}
</style>
""", unsafe_allow_html=True)

# 3. Render Core Navigation as Clean Text Buttons (No Emojis, Bold Style)
nav_config = {
    "Executive Dashboard": "EXECUTIVE DASHBOARD",
    "Student Roster": "STUDENT ROSTER",
    "Student Profile Inspector": "STUDENT PROFILE"
}

for opt in core_nav_options:
    label = nav_config.get(opt, opt.upper())
    
    is_active = (st.session_state.admin_view == opt)
    # Add a subtle indicator dot for the active page
    display_label = f"{label}  •" if is_active else label
    
    if st.sidebar.button(display_label, key=f"nav_btn_{opt.lower().replace(' ', '_')}", use_container_width=True):
        if st.session_state.admin_view != opt:
            st.session_state.admin_view = opt
            st.rerun()

st.markdown(
    """
    <style>
    section[data-testid="stSidebar"] button { background-color: #b92b27 !important; color: white !important; border-color: #b92b27 !important; }
    section[data-testid="stSidebar"] button p, section[data-testid="stSidebar"] button span { color: white !important; }
    section[data-testid="stSidebar"] button:hover { background-color: #FF4B4B !important; color: white !important; border-color: #FF4B4B !important; }
    </style>
    """, unsafe_allow_html=True
)

# --- SIDEBAR FOOTER: Re-sync & Log Out ---
st.sidebar.markdown("---")

if st.sidebar.button("RE-SYNC", use_container_width=True, key="footer_resync"):
    st.cache_data.clear()
    st.success("Data re-synced successfully!")
    st.rerun()

if st.sidebar.button("LOG OUT", use_container_width=True, key="footer_logout"):
    st.session_state.clear()
    st.rerun()

# ------------------------------------------------------------------
# STYLED BANNER HEADER
# ------------------------------------------------------------------
st.markdown(f"""
    <div style="background-color: #b92b27; padding: 20px 25px; border-radius: 8px; color: white; margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);">
        <div style="font-size: 11px; letter-spacing: 1.2px; font-weight: 600; margin-bottom: 6px; opacity: 0.85;">MAPÚA UNIVERSITY · ASU PATHWAYS · ETYSB</div>
        <div style="font-size: 24px; font-weight: 700; line-height: 1.3;">Success Advisor Dashboard — {ACTIVE_PROGRAM} Program</div>
    </div>
""", unsafe_allow_html=True)

# ------------------------------------------------------------------
# VIEW: IT/ADMIN GLOBAL INSTANCE CONFIGURATION
# ------------------------------------------------------------------
def render_instance_settings():
    st.subheader("⚙️ Global Instance Settings")
    st.caption("Set the primary context for this dashboard instance. These settings apply globally to all users.")

    try:
        prog_df = conn.query("SELECT DISTINCT program_code FROM program WHERE program_code IS NOT NULL AND program_code != '';", ttl=0)
        available_programs = sorted(prog_df["program_code"].unique().tolist())
    except Exception as e:
        available_programs = []
        
    if ACTIVE_PROGRAM not in available_programs:
        available_programs.append(ACTIVE_PROGRAM)

    with st.form("instance_config_form"):
        default_index = available_programs.index(ACTIVE_PROGRAM) if ACTIVE_PROGRAM in available_programs else 0
        selected_program = st.selectbox("Active Program Code", options=available_programs, index=default_index)
        
        st.info("The program list is automatically populated based on the enrolled students in your database.")
        
        if st.form_submit_button("Update Global Dashboard Settings", type="primary"):
            try:
                with conn.session as s:
                    s.execute(
                        text("""
                            UPDATE dashboard_config 
                            SET program_id = (SELECT program_id FROM program WHERE program_code = :ap) 
                            WHERE config_id = 1;
                        """),
                        {"ap": selected_program.strip()}
                    )
                    s.commit()
                log_security_event(user["user_id"], "INSTANCE_CONFIG_UPDATED", f"Changed program to {selected_program}.")
                st.success("Global settings updated successfully! The dashboard will now automatically filter to the new program context.")
                load_dashboard_config.clear()
                load_students.clear()
                st.rerun()
            except Exception as e:
                st.error(f"Error updating configuration: {e}")
                
    st.divider()
    
    st.subheader("⏱️ Program Lifecycle Thresholds")
    st.caption(f"Configure 'At-Risk' duration limits for the {ACTIVE_PROGRAM} program. Students exceeding these days in an 'In-Progress' status will be automatically flagged for the Program Chair.")
    
    try:
        t_df = conn.query(f"""
            SELECT pt.cw_days, pt.ce_days, pt.cap_days 
            FROM program_thresholds pt 
            JOIN program p ON pt.program_id = p.program_id 
            WHERE p.program_code = '{ACTIVE_PROGRAM}'
        """, ttl=0)
        cur_cw = int(t_df.iloc[0]['cw_days']) if not t_df.empty else 730
        cur_ce = int(t_df.iloc[0]['ce_days']) if not t_df.empty else 180
        cur_cap = int(t_df.iloc[0]['cap_days']) if not t_df.empty else 365
    except Exception:
        cur_cw, cur_ce, cur_cap = 730, 180, 365

    with st.form("thresholds_form"):
        c1, c2, c3 = st.columns(3)
        new_cw = c1.number_input("Coursework Limit (Days)", value=cur_cw, min_value=1, help="Expected duration to clear core classes.")
        new_ce = c2.number_input("Comprehensive Exam Limit (Days)", value=cur_ce, min_value=1, help="Expected duration to pass the exam once initiated.")
        new_cap = c3.number_input("Capstone Limit (Days)", value=cur_cap, min_value=1, help="Expected duration to defend capstone once started.")
        
        if st.form_submit_button("Save Program Thresholds"):
            try:
                with conn.session as s:
                    s.execute(text("""
                        INSERT INTO program_thresholds (program_id, cw_days, ce_days, cap_days)
                        VALUES ((SELECT program_id FROM program WHERE program_code = :p), :cw, :ce, :cap)
                        ON CONFLICT (program_id) DO UPDATE 
                        SET cw_days = EXCLUDED.cw_days, ce_days = EXCLUDED.ce_days, cap_days = EXCLUDED.cap_days;
                    """), {"p": ACTIVE_PROGRAM, "cw": new_cw, "ce": new_ce, "cap": new_cap})
                    s.commit()
                log_security_event(user["user_id"], "THRESHOLDS_UPDATED", f"Updated expected duration thresholds for {ACTIVE_PROGRAM}")
                st.success(f"Thresholds saved for {ACTIVE_PROGRAM} successfully! Flags will recalculate on the next roster load.")
                load_students.clear()
            except Exception as e:
                st.error(f"Error saving thresholds: {e}")


# ------------------------------------------------------------------
# VIEW: IT/ADMIN SCHEMA CONFIGURATION
# ------------------------------------------------------------------
def render_schema_mapping():
    st.subheader("⚙️ Dynamic Schema Field Mapping")
    st.caption("Map dashboard UI elements directly to the underlying SQL database columns. No code deployment required.")

    try:
        actual_cols_df = conn.query("""
            SELECT column_name FROM information_schema.columns WHERE table_name = 'students_normalized'
            UNION SELECT 'cohort' 
            UNION SELECT 'program_code'
            UNION SELECT 'program_name'
            UNION SELECT 'coursework_status'
            UNION SELECT 'comprehensive_exam'
            UNION SELECT 'capstone'
            UNION SELECT 'updated_at';
        """, ttl=0)
        actual_db_cols = actual_cols_df["column_name"].tolist()
        mappings_df = conn.query("SELECT mapping_id AS id, dashboard_field, db_column, description FROM field_mappings ORDER BY mapping_id;", ttl=0)
    except Exception as e:
        st.error(f"Database error loading schema details: {e}")
        return

    invalid_mappings = mappings_df[~mappings_df["db_column"].isin(actual_db_cols)]
    if not invalid_mappings.empty:
        invalid_cols = ", ".join(invalid_mappings['db_column'].tolist())
        st.error(f"🚨 **Configuration Error:** The following mapped columns do not exist in the database view: `{invalid_cols}`.")
    else:
        st.success("✅ Pre-flight check passed: All active mappings successfully matched to database columns.")

    with st.form("schema_mapping_form"):
        edited_df = st.data_editor(
            mappings_df,
            column_config={
                "id": None, 
                "dashboard_field": st.column_config.TextColumn("Internal App Field", disabled=True),
                "description": st.column_config.TextColumn("Description", disabled=True),
                "db_column": st.column_config.SelectboxColumn(
                    "Mapped Source SQL Column",
                    options=actual_db_cols,
                    required=True
                )
            },
            hide_index=True,
            use_container_width=True
        )

        if st.form_submit_button("Save Configuration to Database", type="primary"):
            try:
                with conn.session as s:
                    for index, row in edited_df.iterrows():
                        s.execute(
                            text("UPDATE field_mappings SET db_column = :col WHERE mapping_id = :idx"),
                            {"col": row["db_column"], "idx": int(row["id"])}
                        )
                    s.commit()
                log_security_event(user["user_id"], "SCHEMA_MAPPING_UPDATED", "IT Admin modified database schema mappings.")
                st.success("Schema mappings successfully committed to database!")
                load_students.clear()
                st.rerun()
            except Exception as e:
                st.error(f"Error saving mappings: {e}")

# ------------------------------------------------------------------
# VIEW: IT/ADMIN PERMISSION MANAGEMENT & AUDIT LOGS
# ------------------------------------------------------------------
def render_permissions_and_logs():
    st.subheader("🔐 Access Management & Security Audit Logs")
    t_perms, t_logs, t_syslogs = st.tabs(["User Permissions", "Live Audit Logs", "System Sync Failures"])
    
    with t_perms:
        users_df = conn.query("SELECT user_id, username, full_name, role, can_edit FROM app_users ORDER BY user_id;", ttl=0)
        st.dataframe(users_df, hide_index=True, use_container_width=True)

        st.markdown("##### ✏️ Modify User Edit Permissions")
        with st.form("admin_perm_form"):
            target_username = st.selectbox("Select User Account", users_df["username"].tolist())
            new_can_edit = st.checkbox("Grant Write / Edit Capability")
            
            if st.form_submit_button("Update Access Level"):
                try:
                    with conn.session as s:
                        s.execute(
                            text("UPDATE app_users SET can_edit = :ce WHERE username = :u;"),
                            {"ce": new_can_edit, "u": target_username}
                        )
                        s.commit()
                    log_security_event(user["user_id"], "PERMISSIONS_UPDATED", f"Set can_edit={new_can_edit} for user '{target_username}'.")
                    st.success(f"Permissions successfully updated for {target_username}.")
                    st.rerun()
                except Exception as ex:
                    st.error(f"Error updating permissions: {ex}")

    with t_logs:
        with t_logs:
            st.caption("Live monitoring of authentication events, schema updates, and blocked write attempts.")
        logs_df = conn.query("""
            SELECT al.timestamp, COALESCE(u.username, 'UNKNOWN') AS username, COALESCE(u.role, 'UNAUTHENTICATED') AS role, al.event_type, al.details 
            FROM audit_logs al
            LEFT JOIN app_users u ON al.user_id = u.user_id
            ORDER BY al.timestamp DESC LIMIT 100;
        """, ttl=0)
        
        if not logs_df.empty and "timestamp" in logs_df.columns:
            dt = pd.to_datetime(logs_df["timestamp"], errors="coerce")
            if dt.dt.tz is None:
                dt = dt.dt.tz_localize("UTC")
            logs_df["timestamp"] = dt.dt.tz_convert("Asia/Manila")

        st.dataframe(
            logs_df,
            column_config={
                "timestamp": st.column_config.DatetimeColumn("Timestamp", format="MMM DD, YYYY HH:mm:ss"),
                "username": st.column_config.TextColumn("Username"),
                "role": st.column_config.TextColumn("Role"),
                "event_type": st.column_config.TextColumn("Event Type"),
                "details": st.column_config.TextColumn("Log Details", width="large")
            },
            hide_index=True,
            use_container_width=True
        )

    with t_syslogs:
        st.caption("Tracks critical connection timeouts and schema errors (stored locally so they are accessible even if the database is completely offline).")
        if os.path.exists("sync_error_log.txt"):
            with open("sync_error_log.txt", "r") as f:
                logs = f.readlines()
            
            if logs:
                st.code("".join(logs[-15:]), language="log")
                if st.button("Clear Sync Logs", type="primary"):
                    os.remove("sync_error_log.txt")
                    st.success("Logs cleared.")
                    st.rerun()
            else:
                st.success("✅ System is healthy. No synchronization errors logged.")
        else:
            st.success("✅ System is healthy. No synchronization errors logged.")

def render_completion_trend_chart(df_all, active_program):
    if df_all.empty:
        st.info("No data available to display trends.")
        return
        
    # Split the top area: Left for the title, Right for the dropdown
    header_col, select_col = st.columns([2.5, 1.5], vertical_alignment="center")
    
    with header_col:
        st.subheader("Cohort Performance Trends", help="Toggle between Overall Completion and On-Time Graduation rates over the last 4 terms.")
        
    with select_col:
        metric_choice = st.selectbox(
            "Select Trend Metric",
            options=["Overall Completion", "On-Time Grad Rate"],
            label_visibility="collapsed"
        )
    
    df_calc = df_all.copy()

    # Route logic based on the dropdown choice
    if metric_choice == "Overall Completion":
        df_calc['is_success'] = (
            (df_calc['coursework_display'] == 'Completed') & 
            (df_calc['comprehensive_exam_display'] == 'Passed') & 
            (df_calc['capstone_display'] == 'Defended')
        )
        y_label = "Completion Rate (%)"
    else:
        valid_grad = df_calc['graduate_on_time'].astype(str).str.strip().str.lower()
        df_calc['is_success'] = valid_grad.isin(["yes", "y", "true", "1"])
        y_label = "On-Time Grad Rate (%)"
        
    # Group by cohort and calculate the rate
    trend_df = df_calc.groupby('cohort').agg(
        total_students=('student_number', 'count'),
        success_students=('is_success', 'sum')
    ).reset_index()
    
    trend_df['rate'] = (trend_df['success_students'] / trend_df['total_students']) * 100
    trend_df['sort_year'] = trend_df['cohort'].astype(str).str.extract(r'[TQ](\d{2})').astype(float)
    trend_df['sort_term'] = trend_df['cohort'].astype(str).str.extract(r'^(\d)[TQ]').astype(float)
    
    trend_df = trend_df.dropna(subset=['sort_year', 'sort_term']).sort_values(by=['sort_year', 'sort_term']).tail(4) 
    
    if trend_df.empty:
        st.info("Not enough standard cohort terms (e.g., 1Q2425) to form a trend line.")
        return

    fig = px.line(
        trend_df, x="cohort", y="rate", markers=True,
        text="rate", 
        labels={"cohort": "Academic Term", "rate": y_label}
    )
    
    fig.update_layout(
        height=320, 
        yaxis_title=y_label,
        xaxis_title="", 
        yaxis=dict(range=[-5, 115], fixedrange=True), 
        xaxis=dict(fixedrange=True), 
        hovermode="x unified",
        margin=dict(l=10, r=10, t=20, b=10) 
    )
    
    fig.update_traces(
        line_color="#D50000", 
        marker=dict(color="#FFAE00", size=8), 
        line_width=3, 
        texttemplate='%{text:.1f}%', 
        textposition='top center',
        textfont=dict(size=12, color="var(--text-color)")
    )
    
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

# ------------------------------------------------------------------
# EXPORT: INDIVIDUAL STUDENT PROFILE (PDF)
# ------------------------------------------------------------------
def generate_student_pdf(
    student, milestones_df, cw_updated, ce_updated, cap_updated,
    user_email, active_program, last_sync_time,
) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors as rl_colors
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
    )

    def _safe(v, fallback="N/A"):
        if v is None:
            return fallback
        try:
            if pd.isna(v):
                return fallback
        except Exception:
            pass
        s = str(v).strip()
        return s if s and s.lower() not in ("nan", "none", "") else fallback

    def _stage_color(stage, label):
        rule = STAGE_THRESHOLDS.get(stage, {})
        clean = str(label).strip()
        if clean in rule.get("green", []):  return "#0DC249"
        if clean in rule.get("yellow", []): return "#FFAE00"
        if clean in rule.get("red", []):    return "#D50000"
        return "#999999"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
        title=f"Student Profile — {_safe(student.get('full_name'), 'Student')}",
    )
    styles = getSampleStyleSheet()
    story = []

    RED   = rl_colors.HexColor("#B92B27")
    DARK  = rl_colors.HexColor("#1F2937")
    GRAY  = rl_colors.HexColor("#6B7280")
    LIGHT = rl_colors.HexColor("#E5E7EB")

    # ---------- Red banner ----------
    name = _safe(student.get("full_name"), "Student")
    banner = Table(
        [[Paragraph(
            f'<font color="white" size="15"><b>Student Profile — {name}</b></font>',
            ParagraphStyle("banner", parent=styles["Normal"], leading=20)
        )]],
        colWidths=[7.5 * inch]
    )
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), RED),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    story.append(banner)
    story.append(Spacer(1, 8))

    exported_at = datetime.now(ZoneInfo("Asia/Manila")).strftime("%b %d, %Y %I:%M %p")
    story.append(Paragraph(
        f"<font color='#6B7280' size='8'>Generated {exported_at} by {user_email} · "
        f"{active_program} Program</font>",
        ParagraphStyle("meta", parent=styles["Normal"], fontSize=8, leading=11)
    ))
    story.append(Spacer(1, 12))

    # ---------- Student identity block ----------
    info_rows = [
        ["Student ID", _safe(student.get("student_number")),
         "Program",    _safe(student.get("program"), active_program)],
        ["Email",      _safe(student.get("student_email")),
         "Cohort",     _safe(student.get("cohort"))],
        ["Adviser",    _safe(student.get("adviser"), "Unassigned"),
         "Overall Status", _safe(student.get("overall_status"))],
    ]
    info_tbl = Table(info_rows, colWidths=[0.95 * inch, 2.85 * inch, 1.05 * inch, 2.65 * inch])
    info_tbl.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (0, -1), GRAY),
        ("TEXTCOLOR", (2, 0), (2, -1), GRAY),
        ("TEXTCOLOR", (1, 0), (1, -1), DARK),
        ("TEXTCOLOR", (3, 0), (3, -1), DARK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, LIGHT),
    ]))
    story.append(info_tbl)
    story.append(Spacer(1, 14))

    # ---------- At-risk alert ----------
    if bool(student.get("is_at_risk")):
        risk_text = _safe(student.get("risk_details"), "Threshold exceeded")
        alert = Table(
            [[Paragraph(
                f'<font color="#B91C1C"><b>AT-RISK FLAG:</b></font> '
                f'<font color="#7F1D1D">{risk_text}</font>',
                ParagraphStyle("alert", parent=styles["Normal"], fontSize=9, leading=12)
            )]],
            colWidths=[7.5 * inch]
        )
        alert.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), rl_colors.HexColor("#FEE2E2")),
            ("BOX", (0, 0), (-1, -1), 0.75, rl_colors.HexColor("#D50000")),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ]))
        story.append(alert)
        story.append(Spacer(1, 14))

    # ---------- Lifecycle stage cards ----------
    story.append(Paragraph(
        '<font color="#B92B27" size="11"><b>PROGRAM LIFECYCLE SUMMARY</b></font>',
        ParagraphStyle("sect1", parent=styles["Normal"], fontSize=11, leading=14)
    ))
    story.append(Spacer(1, 6))

    cw_label  = _safe(student.get("coursework_display"))
    ce_label  = _safe(student.get("comprehensive_exam_display"))
    cap_label = _safe(student.get("capstone_display"))

    def _stage_card(title, label, updated, accent):
        inner = Table(
            [
                [Paragraph(f'<font color="{accent}"><b>{title}</b></font>',
                           ParagraphStyle("sc", parent=styles["Normal"], fontSize=8.5, leading=11))],
                [Paragraph(f'<font color="{accent}" size="11"><b>{label}</b></font>',
                           ParagraphStyle("sv", parent=styles["Normal"], fontSize=11, leading=14))],
                [Paragraph(f'<font color="#777777" size="7.5">Updated: {updated}</font>',
                           ParagraphStyle("su", parent=styles["Normal"], fontSize=7.5, leading=10))],
            ],
            colWidths=[2.4 * inch]
        )
        inner.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), rl_colors.HexColor("#FAFAFA")),
            ("BOX", (0, 0), (-1, -1), 0.4, LIGHT),
            ("LINEABOVE", (0, 0), (0, 0), 3, rl_colors.HexColor(accent)),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        return inner

    cards_row = Table(
        [[
            _stage_card("Coursework",           cw_label,  cw_updated,  _stage_color("coursework", cw_label)),
            _stage_card("Comprehensive Exam",   ce_label,  ce_updated,  _stage_color("comprehensive_exam", ce_label)),
            _stage_card("Capstone & Defense",   cap_label, cap_updated, _stage_color("capstone", cap_label)),
        ]],
        colWidths=[2.5 * inch] * 3
    )
    cards_row.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(cards_row)
    story.append(Spacer(1, 16))

    # ---------- Graduation tracking ----------
    story.append(Paragraph(
        '<font color="#B92B27" size="11"><b>GRADUATION TRACKING</b></font>',
        ParagraphStyle("sect2", parent=styles["Normal"], fontSize=11, leading=14)
    ))
    story.append(Spacer(1, 6))

    raw_ontime = student.get("graduate_on_time")
    display_ontime = _safe(raw_ontime, "Under Evaluation")

    raw_term = student.get("graduate_date_term_sy")
    display_term = "To Be Determined (TBD)"
    if pd.notna(raw_term) and str(raw_term).strip().lower() not in ("nan", "none", ""):
        term_str = str(raw_term).strip().upper()
        mt = re.match(r'^(\d)([TQ])(\d{2})(\d{2})$', term_str)
        if mt:
            t_num, t_type, y1, y2 = mt.groups()
            display_term = f"{t_num}{t_type}, A.Y. 20{y1}–20{y2}"
        else:
            display_term = term_str

    grad_tbl = Table(
        [
            ["Graduating On Time",      display_ontime],
            ["Target Graduation Term",  display_term],
        ],
        colWidths=[2.0 * inch, 5.5 * inch]
    )
    grad_tbl.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (0, -1), GRAY),
        ("TEXTCOLOR", (1, 0), (1, -1), DARK),
        ("BACKGROUND", (0, 0), (-1, -1), rl_colors.HexColor("#FAFAFA")),
        ("BOX", (0, 0), (-1, -1), 0.4, LIGHT),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, LIGHT),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(grad_tbl)
    story.append(Spacer(1, 16))

    # ---------- Administrative remarks ----------
    story.append(Paragraph(
        '<font color="#B92B27" size="11"><b>ADMINISTRATIVE REMARKS</b></font>',
        ParagraphStyle("sect3", parent=styles["Normal"], fontSize=11, leading=14)
    ))
    story.append(Spacer(1, 6))

    remarks = _safe(student.get("remarks"), "No administrative remarks on file.")
    safe_remarks = (remarks.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                           .replace("\n", "<br/>"))
    rem_tbl = Table(
        [[Paragraph(safe_remarks, ParagraphStyle(
            "rem", parent=styles["Normal"], fontSize=9.5, leading=13, textColor=DARK
        ))]],
        colWidths=[7.5 * inch]
    )
    rem_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), rl_colors.HexColor("#FAFAFA")),
        ("BOX", (0, 0), (-1, -1), 0.4, LIGHT),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    story.append(rem_tbl)

    # ---------- Footer ----------
    story.append(Spacer(1, 24))
    story.append(Paragraph(
        f'<font color="#9CA3AF" size="7.5">Project Pulse Student Profile · '
        f'Data last synced {last_sync_time} · Generated {exported_at} by {user_email}</font>',
        ParagraphStyle("footer", parent=getSampleStyleSheet()["Normal"],
                       fontSize=7.5, leading=10, alignment=1)
    ))

    doc.build(story)
    return buf.getvalue()

# ------------------------------------------------------------------
# EXPORT HELPERS
# ------------------------------------------------------------------
def _compute_cohort_trends(df_all: pd.DataFrame, last_n: int = 4):
    """
    Returns (cohorts, completion_rates, at_risk_counts, at_risk_pcts)
    for the last `last_n` cohorts, ordered chronologically.
    All rates are percentages (0–100) for on-screen use; the XLSX writer
    divides by 100 before formatting.
    """
    if df_all is None or df_all.empty or "cohort" not in df_all.columns:
        return [], [], [], []

    valid = sorted(
        [str(c) for c in df_all["cohort"].dropna().unique() if str(c).strip()],
        key=get_cohort_val
    )[-last_n:]

    comp_rates, risk_counts, risk_pcts = [], [], []
    for c in valid:
        sub = df_all[df_all["cohort"].astype(str) == c]
        total = len(sub)
        completed = len(sub[
            (sub["coursework_display"] == "Completed") &
            (sub["comprehensive_exam_display"] == "Passed") &
            (sub["capstone_display"] == "Defended")
        ])
        comp_rates.append((completed / total * 100) if total else 0.0)
        ar = int(sub["is_at_risk"].sum()) if "is_at_risk" in sub.columns else 0
        risk_counts.append(ar)
        risk_pcts.append((ar / total * 100) if total else 0.0)

    return valid, comp_rates, risk_counts, risk_pcts


def _build_at_risk_export_df(df_summary: pd.DataFrame) -> pd.DataFrame:
    """
    Normalizes the at-risk subset of df_summary into the 16-column
    schema used by the export sheet.
    """
    if df_summary is None or df_summary.empty:
        return pd.DataFrame()

    at_risk = df_summary[df_summary["is_at_risk"] == True].copy()
    if at_risk.empty:
        return pd.DataFrame()

    now_utc = pd.Timestamp.utcnow()
    rows = []
    for _, r in at_risk.iterrows():
        if r.get("coursework_display") != "Completed":
            curr_stage, stage_col = "Coursework Completion", "cw_updated_at"
        elif r.get("comprehensive_exam_display") != "Passed":
            curr_stage, stage_col = "Comprehensive Exam", "ce_updated_at"
        elif r.get("capstone_display") != "Defended":
            curr_stage, stage_col = "Capstone Paper", "cap_updated_at"
        else:
            curr_stage, stage_col = "Completed", None

        days_in_stage, threshold = "", ""
        if stage_col and stage_col in r and pd.notna(r[stage_col]):
            try:
                ts = pd.to_datetime(r[stage_col], utc=True)
                days_in_stage = int((now_utc - ts).days)
            except Exception:
                pass

        risk_text = str(r.get("risk_details", "") or "")
        m = re.search(r"for (\d+) days \(Limit: (\d+)\)", risk_text)
        if m:
            if days_in_stage == "":
                days_in_stage = int(m.group(1))
            threshold = int(m.group(2))

        rows.append({
            "Student Number": r.get("student_number", ""),
            "First Name": r.get("first_name", ""),
            "Last Name": r.get("last_name", ""),
            "Program": r.get("program", ""),
            "Cohort": r.get("cohort", ""),
            "Enrollment Status": "Conditionally Enrolled",
            "Adviser(s)": r.get("adviser", ""),
            "Current Stage": curr_stage,
            "Coursework Completion Status": r.get("coursework_display", ""),
            "Comprehensive Exam Status": r.get("comprehensive_exam_display", ""),
            "Capstone Paper Status": r.get("capstone_display", ""),
            "Time in Stage (days)": days_in_stage,
            "At-Risk Threshold (days)": threshold,
            "Flag Reason": risk_text,
            "Graduate On Time": (
                r.get("graduate_on_time", "")
                if pd.notna(r.get("graduate_on_time"))
                else "N/A"
            ),
            "Last Updated": r.get("coursework_updated_at", ""),
        })

    return pd.DataFrame(rows)
# ------------------------------------------------------------------
# EXPORT: PDF & XLSX (dashboard-styled)
# ------------------------------------------------------------------
def _percent_buckets(df_summary, total_students, fully_completed,
                     current_cw, current_ce, current_cap):
    """Return (coursework%, comp_exam%, capstone%, overall%) as floats 0–100."""
    if total_students <= 0:
        return 0.0, 0.0, 0.0, 0.0
    return (
        current_cw / total_students * 100,
        current_ce / total_students * 100,
        current_cap / total_students * 100,
        fully_completed / total_students * 100,
    )


def _per_cohort_on_time(df_all, cohorts):
    """On-time graduation % per cohort (list of floats 0–100)."""
    out = []
    for c in cohorts:
        sub = df_all[df_all["cohort"].astype(str) == c]
        n = len(sub)
        if n == 0:
            out.append(0.0)
            continue
        yes = int(
            sub["graduate_on_time"].astype(str).str.strip().str.lower()
            .isin(["yes", "y", "true", "1"]).sum()
        )
        out.append(yes / n * 100)
    return out


def generate_executive_pdf(
    df_summary, df_all, active_cohort, selected_adviser,
    total_students, on_time_rate, completion_rate, remaining_students, at_risk_count,
    fully_completed, current_cap, current_ce, current_cw,
    user_email, active_program, last_sync_time,
) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors as rl_colors
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph,
        Spacer, Image as RLImage, PageBreak,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.45 * inch, rightMargin=0.45 * inch,
        topMargin=0.45 * inch, bottomMargin=0.45 * inch,
        title=f"Executive Overview — {active_program}",
    )
    styles = getSampleStyleSheet()
    story = []

    RED   = rl_colors.HexColor("#B92B27")
    DARK  = rl_colors.HexColor("#1F2937")
    GRAY  = rl_colors.HexColor("#6B7280")
    LIGHT = rl_colors.HexColor("#E5E7EB")
    GREEN = rl_colors.HexColor("#10B981")
    HEAD  = rl_colors.HexColor("#374151")

    # ---------- Red banner ----------
    banner = Table(
        [[Paragraph(
            f'<font color="white" size="15"><b>Executive Overview — {active_program} Program</b></font>',
            ParagraphStyle("banner", parent=styles["Normal"], leading=20)
        )]],
        colWidths=[7.6 * inch]
    )
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), RED),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    story.append(banner)
    story.append(Spacer(1, 6))

    meta = ParagraphStyle("meta", parent=styles["Normal"], fontSize=8,
                          textColor=GRAY, leading=11)
    exported_at = datetime.now(ZoneInfo("Asia/Manila")).strftime("%b %d, %Y %I:%M %p")
    story.append(Paragraph(f"Exported {exported_at} by {user_email}", meta))
    story.append(Paragraph(f"Data last updated {last_sync_time}", meta))
    story.append(Paragraph(
        f"<b>Cohort:</b> {active_cohort} · <b>Adviser:</b> {selected_adviser} · "
        f"<b>Program:</b> {active_program}", meta))
    story.append(Spacer(1, 14))

    # ---------- KPI cards ----------
    labels = ["TOTAL STUDENTS", "ON-TIME GRAD RATE", "OVERALL COMPLETION",
              "REMAINING STUDENTS", "STUDENTS AT RISK"]
    values = [str(total_students), f"{on_time_rate:.1f}%", f"{completion_rate}%",
              str(remaining_students), str(at_risk_count)]
    grad_yes = int(total_students * on_time_rate / 100) if total_students else 0
    hints = [
        (str(active_cohort), GRAY),
        (f"\u2191 {grad_yes} of {total_students} students", GREEN),
        (f"\u2191 {fully_completed} of {total_students} students", GREEN),
        (f"\u2191 {remaining_students} of {total_students} students", GREEN),
        (f"\u2191 {at_risk_count} of {total_students} students", RED),
    ]

    lbl_style = ParagraphStyle("kl", parent=styles["Normal"], fontSize=7,
                               textColor=GRAY, leading=9)
    val_style = ParagraphStyle("kv", parent=styles["Normal"], fontSize=18,
                               leading=22, textColor=DARK)
    def _hint(t, c):
        return Paragraph(t, ParagraphStyle("kh", parent=styles["Normal"],
                                            fontSize=7.5, leading=10, textColor=c))

    card_w = 1.52 * inch
    kpi = Table(
        [["", "", "", "", ""],
         [Paragraph(f"<b>{l}</b>", lbl_style) for l in labels],
         [Paragraph(f"<b>{v}</b>", val_style) for v in values],
         [_hint(t, c) for t, c in hints]],
        colWidths=[card_w] * 5, rowHeights=[4, 14, 30, 14]
    )
    kpi.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), RED),
        ("BOX", (0, 1), (0, -1), 0.4, LIGHT),
        ("BOX", (1, 1), (1, -1), 0.4, LIGHT),
        ("BOX", (2, 1), (2, -1), 0.4, LIGHT),
        ("BOX", (3, 1), (3, -1), 0.4, LIGHT),
        ("BOX", (4, 1), (4, -1), 0.4, LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
    ]))
    story.append(kpi)
    story.append(Spacer(1, 16))

    # ---------- Trend data ----------
    cohorts, comp_rates, _, _ = _compute_cohort_trends(df_all, last_n=4)
    on_time_rates = _per_cohort_on_time(df_all, cohorts)
    cw_pct, ce_pct, cap_pct, ov_pct = _percent_buckets(
        df_summary, total_students, fully_completed, current_cw, current_ce, current_cap)

    def _img_from_fig(fig, width_in, height_in):
        b = io.BytesIO()
        fig.savefig(b, format="png", bbox_inches="tight", dpi=150, facecolor="white")
        plt.close(fig); b.seek(0)
        return RLImage(b, width=width_in * inch, height=height_in * inch)

    # ---------- Chart A: Lifecycle (full width) ----------
    figA, axA = plt.subplots(figsize=(7.6, 2.1), dpi=150)
    stages_bu = ["Overall Completion", "Capstone", "Comprehensive Exam", "Coursework"]
    vals_bu = [ov_pct, cap_pct, ce_pct, cw_pct]
    bar_cols = ["#0DC249", "#D50000", "#FFAE00", "#0072B2"]
    bars = axA.barh(stages_bu, vals_bu, color=bar_cols, height=0.55)
    for b, v in zip(bars, vals_bu):
        axA.text(v + 2, b.get_y() + b.get_height() / 2, f"{v:.2f}%",
                 va="center", ha="left", fontsize=9, color="#333333")
    axA.set_xlim(0, 100)
    axA.set_xticks([0, 20, 40, 60, 80, 100])
    axA.set_xticklabels([f"{x}%" for x in [0, 20, 40, 60, 80, 100]])
    axA.set_xlabel("Percentage of Active Students", fontsize=8, color="#666666")
    axA.set_title("Lifecycle Stage Breakdown", fontsize=11, fontweight="bold", color="#1F2937")
    axA.tick_params(labelsize=8)
    axA.spines["top"].set_visible(False)
    axA.spines["right"].set_visible(False)
    axA.grid(axis="x", linestyle=":", alpha=0.3)
    axA.set_axisbelow(True)
    plt.tight_layout()
    story.append(_img_from_fig(figA, 7.6, 2.15))
    story.append(Spacer(1, 10))

    # ---------- Charts B & C: Trends side-by-side ----------
    def _trend_chart(y_vals, line_color, marker_color, title, ylabel):
        fig, ax = plt.subplots(figsize=(3.7, 2.7), dpi=150)
        if cohorts:
            ax.plot(cohorts, y_vals, color=line_color, linewidth=2,
                    marker="o", markerfacecolor=marker_color,
                    markeredgecolor=marker_color, markersize=8)
            for x, y in zip(cohorts, y_vals):
                ax.annotate(f"{y:.1f}%", (x, y), textcoords="offset points",
                            xytext=(0, 9), ha="center", fontsize=8, color="#333333")
        ax.set_ylim(0, 100)
        ax.set_yticks([0, 20, 40, 60, 80, 100])
        ax.set_ylabel(ylabel, fontsize=8, color="#666666")
        ax.set_title(title, fontsize=10, fontweight="bold", color="#1F2937")
        ax.tick_params(labelsize=8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(axis="y", linestyle=":", alpha=0.3)
        ax.set_axisbelow(True)
        plt.tight_layout()
        return fig

    figB = _trend_chart(comp_rates, "#0072B2", "#FFAE00",
                        "Overall Completion % — Trend",
                        "Overall Completion (%)")
    figC = _trend_chart(on_time_rates, "#D50000", "#FFAE00",
                        "On-Time Graduation Rate — Trend",
                        "On-Time Grad Rate (%)")

    imgB = _img_from_fig(figB, 3.7, 2.7)
    imgC = _img_from_fig(figC, 3.7, 2.7)
    trend_row = Table([[imgB, imgC]], colWidths=[3.8 * inch, 3.8 * inch])
    trend_row.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    story.append(trend_row)

    # ================= PAGE 2: Tables =================
    story.append(PageBreak())

    def _section(title):
        story.append(Paragraph(
            f'<font color="#B92B27" size="11"><b>{title}</b></font>',
            ParagraphStyle("sec", parent=styles["Heading2"], leading=14)
        ))
        story.append(Spacer(1, 4))

    def _data_table(header, rows):
        tbl = Table([header] + rows, colWidths=[3.6 * inch, 1.6 * inch])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), HEAD),
            ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.25, rl_colors.HexColor("#cccccc")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [rl_colors.white, rl_colors.HexColor("#f9fafb")]),
            ("ALIGN", (1, 0), (1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        return tbl

    # Table 1: Lifecycle
    _section("COHORT DISTRIBUTION BY LIFECYCLE STAGE")
    lifecycle_rows = [
        ["Coursework",          f"{cw_pct:.2f}%"],
        ["Comprehensive Exam",  f"{ce_pct:.2f}%"],
        ["Capstone",            f"{cap_pct:.2f}%"],
        ["Overall Completion",  f"{ov_pct:.2f}%"],
    ]
    story.append(_data_table(["Stage", "% of Active Students"], lifecycle_rows))
    story.append(Spacer(1, 12))

    # Table 2: Overall Completion trend
    _section("OVERALL COMPLETION % — TREND (LAST 4 COHORTS)")
    comp_rows = [[c, f"{r:.1f}%"] for c, r in zip(cohorts, comp_rates)]
    story.append(_data_table(["Cohort", "Overall Completion %"], comp_rows))
    story.append(Spacer(1, 12))

    # Table 3: On-Time Grad Rate trend
    _section("ON-TIME GRADUATION RATE — TREND (LAST 4 COHORTS)")
    grad_rows = [[c, f"{r:.1f}%"] for c, r in zip(cohorts, on_time_rates)]
    story.append(_data_table(["Cohort", "On-Time Grad Rate"], grad_rows))

    # Table 4: At-Risk detail
    story.append(Spacer(1, 14))
    _section("STUDENTS AT RISK — DETAIL")
    at_risk_export = _build_at_risk_export_df(df_summary)
    if at_risk_export.empty:
        story.append(Paragraph("No students are currently flagged as at-risk.",
                               styles["Normal"]))
    else:
        header = ["Student No.", "Name", "Program", "Cohort", "Current Stage",
                  "Coursework", "Comp Exam", "Capstone", "Days", "Threshold"]
        rows = [header]
        for _, r in at_risk_export.iterrows():
            rows.append([
                str(r["Student Number"]),
                f"{r['First Name']} {r['Last Name']}".strip(),
                str(r["Program"]),
                str(r["Cohort"]),
                str(r["Current Stage"]),
                str(r["Coursework Completion Status"]),
                str(r["Comprehensive Exam Status"]),
                str(r["Capstone Paper Status"]),
                str(r["Time in Stage (days)"]),
                str(r["At-Risk Threshold (days)"]),
            ])
        col_w = [w * inch for w in [0.72, 1.05, 0.5, 0.55, 0.95, 0.68, 0.65, 0.65, 0.42, 0.58]]
        tbl = Table(rows, colWidths=col_w, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), HEAD),
            ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 6.5),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.25, rl_colors.HexColor("#cccccc")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [rl_colors.white, rl_colors.HexColor("#f9fafb")]),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(tbl)

    doc.build(story)
    return buf.getvalue()


def generate_executive_xlsx(
    df_summary, df_all, active_cohort, selected_adviser,
    total_students, on_time_rate, completion_rate, remaining_students, at_risk_count,
    fully_completed, current_cap, current_ce, current_cw,
    user_email, active_program, last_sync_time,
) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.utils import get_column_letter

    buf = io.BytesIO()
    exported_at = datetime.now(ZoneInfo("Asia/Manila")).strftime("%b %d, %Y %I:%M %p")

    cohorts, comp_rates, _, _ = _compute_cohort_trends(df_all, last_n=4)
    on_time_rates = _per_cohort_on_time(df_all, cohorts)
    cw_pct, ce_pct, cap_pct, ov_pct = _percent_buckets(
        df_summary, total_students, fully_completed, current_cw, current_ce, current_cap)

    RED, DARK, GRAY, LIGHT = "B92B27", "1F2937", "6B7280", "E5E7EB"

    title_fill   = PatternFill("solid", fgColor=RED)
    title_font   = Font(bold=True, color="FFFFFF", size=15)
    head_fill    = PatternFill("solid", fgColor="374151")
    head_font    = Font(bold=True, color="FFFFFF", size=10)
    section_font = Font(bold=True, size=11, color=RED)
    meta_font    = Font(size=9, color=GRAY)
    body_font    = Font(size=10, color=DARK)
    lbl_font     = Font(size=7, color=GRAY)
    val_font     = Font(size=18, bold=True, color=DARK)
    thin = Side(style="thin", color=LIGHT)
    card_border = Border(left=thin, right=thin, bottom=thin)

    wb = Workbook()
    ws = wb.active
    ws.title = "Overview"

    for col, w in zip("ABCDE", [22, 20, 20, 20, 20]):
        ws.column_dimensions[col].width = w
    ws.column_dimensions["F"].width = 2
    ws.column_dimensions["G"].width = 2

    # Row 1: banner
    ws.merge_cells("A1:E1")
    for c in range(1, 6):
        ws.cell(row=1, column=c).fill = title_fill
    t = ws.cell(row=1, column=1, value=f"Executive Overview — {active_program} Program")
    t.font = title_font
    t.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 32

    ws.cell(row=2, column=1, value=f"Exported {exported_at} by {user_email}").font = meta_font
    ws.cell(row=3, column=1, value=f"Data last updated {last_sync_time}").font = meta_font

    # Rows 5–8: KPI cards
    for c in range(1, 6):
        ws.cell(row=5, column=c).fill = PatternFill("solid", fgColor=RED)
    ws.row_dimensions[5].height = 4

    kpi_labels = ["TOTAL STUDENTS", "ON-TIME GRAD RATE", "OVERALL COMPLETION",
                  "REMAINING STUDENTS", "STUDENTS AT RISK"]
    for i, l in enumerate(kpi_labels):
        c = ws.cell(row=6, column=i + 1, value=l)
        c.font = lbl_font
        c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        c.border = card_border
    ws.row_dimensions[6].height = 16

    grad_yes = int(total_students * on_time_rate / 100) if total_students else 0
    kpi_vals = [total_students, f"{on_time_rate:.1f}%", f"{completion_rate}%",
                remaining_students, at_risk_count]
    for i, v in enumerate(kpi_vals):
        c = ws.cell(row=7, column=i + 1, value=v)
        c.font = val_font
        c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        c.border = card_border
    ws.row_dimensions[7].height = 30

    kpi_hints = [
        (str(active_cohort), GRAY),
        (f"\u2191 {grad_yes} of {total_students} students", "10B981"),
        (f"\u2191 {fully_completed} of {total_students} students", "10B981"),
        (f"\u2191 {remaining_students} of {total_students} students", "10B981"),
        (f"\u2191 {at_risk_count} of {total_students} students", RED),
    ]
    for i, (h, color) in enumerate(kpi_hints):
        c = ws.cell(row=8, column=i + 1, value=h)
        c.font = Font(size=7.5, color=color)
        c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        c.border = card_border
    ws.row_dimensions[8].height = 16

    # ---- Table 1: Lifecycle (rows 10-15) ----
    ws.cell(row=10, column=1, value="COHORT DISTRIBUTION BY LIFECYCLE STAGE").font = section_font
    ws.row_dimensions[10].height = 22
    for col, txt in [(1, "Stage"), (2, "% of Active Students")]:
        c = ws.cell(row=11, column=col, value=txt)
        c.fill, c.font = head_fill, head_font
        c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[11].height = 20
    for i, (label, pct) in enumerate([
        ("Coursework",         cw_pct),
        ("Comprehensive Exam", ce_pct),
        ("Capstone",           cap_pct),
        ("Overall Completion", ov_pct),
    ]):
        r = 12 + i
        ws.cell(row=r, column=1, value=label).font = body_font
        c = ws.cell(row=r, column=2, value=pct / 100)
        c.font = body_font
        c.number_format = "0.00%"
        c.alignment = Alignment(horizontal="center")

    # ---- Table 2: Overall Completion % — Trend (rows 17-23) ----
    ws.cell(row=17, column=1,
            value="OVERALL COMPLETION % — TREND (LAST 4 COHORTS)").font = section_font
    ws.row_dimensions[17].height = 22
    for col, txt in [(1, "Cohort"), (2, "Overall Completion %")]:
        c = ws.cell(row=18, column=col, value=txt)
        c.fill, c.font = head_fill, head_font
        c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[18].height = 20
    for i, (coh, rate) in enumerate(zip(cohorts, comp_rates)):
        r = 19 + i
        ws.cell(row=r, column=1, value=coh).font = body_font
        c = ws.cell(row=r, column=2, value=rate / 100)
        c.font = body_font
        c.number_format = "0.0%"
        c.alignment = Alignment(horizontal="center")

    # ---- Table 3: On-Time Grad Rate — Trend (rows 25-31) ----
    ws.cell(row=25, column=1,
            value="ON-TIME GRADUATION RATE — TREND (LAST 4 COHORTS)").font = section_font
    ws.row_dimensions[25].height = 22
    for col, txt in [(1, "Cohort"), (2, "On-Time Grad Rate")]:
        c = ws.cell(row=26, column=col, value=txt)
        c.fill, c.font = head_fill, head_font
        c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[26].height = 20
    for i, (coh, rate) in enumerate(zip(cohorts, on_time_rates)):
        r = 27 + i
        ws.cell(row=r, column=1, value=coh).font = body_font
        c = ws.cell(row=r, column=2, value=rate / 100)
        c.font = body_font
        c.number_format = "0.0%"
        c.alignment = Alignment(horizontal="center")

    # ---------- Charts stacked in right panel ----------
    def _buf(fig):
        b = io.BytesIO()
        fig.savefig(b, format="png", bbox_inches="tight", facecolor="white", dpi=140)
        plt.close(fig); b.seek(0)
        return b

    def _chart_lifecycle():
        fig, ax = plt.subplots(figsize=(5.6, 2.3))
        stages_bu = ["Overall Completion", "Capstone", "Comprehensive Exam", "Coursework"]
        vals_bu = [ov_pct, cap_pct, ce_pct, cw_pct]
        bars = ax.barh(stages_bu, vals_bu,
                       color=["#0DC249", "#D50000", "#FFAE00", "#0072B2"], height=0.55)
        for b, v in zip(bars, vals_bu):
            ax.text(v + 2, b.get_y() + b.get_height() / 2, f"{v:.2f}%",
                    va="center", ha="left", fontsize=9, color="#333333")
        ax.set_xlim(0, 100)
        ax.set_xlabel("Percentage of Active Students", fontsize=9, color="#666666")
        ax.set_title("Lifecycle Stage Breakdown", fontsize=11,
                     fontweight="bold", color="#1F2937")
        ax.tick_params(labelsize=9)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(axis="x", linestyle=":", alpha=0.3)
        ax.set_axisbelow(True)
        plt.tight_layout()
        return fig

    def _chart_trend(y_vals, line_color, marker_color, title, ylabel):
        fig, ax = plt.subplots(figsize=(5.6, 2.3))
        if cohorts:
            ax.plot(cohorts, y_vals, color=line_color, linewidth=2,
                    marker="o", markerfacecolor=marker_color,
                    markeredgecolor=marker_color, markersize=9)
            for x, y in zip(cohorts, y_vals):
                ax.annotate(f"{y:.1f}%", (x, y), textcoords="offset points",
                            xytext=(0, 9), ha="center", fontsize=9, color="#333333")
        ax.set_ylim(0, 100)
        ax.set_ylabel(ylabel, fontsize=9, color="#666666")
        ax.set_title(title, fontsize=11, fontweight="bold", color="#1F2937")
        ax.tick_params(labelsize=9)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(axis="y", linestyle=":", alpha=0.3)
        ax.set_axisbelow(True)
        plt.tight_layout()
        return fig

    imgA = XLImage(_buf(_chart_lifecycle()))
    imgA.width, imgA.height = 620, 260
    ws.add_image(imgA, "G5")

    if cohorts:
        imgB = XLImage(_buf(_chart_trend(
            comp_rates, "#0072B2", "#FFAE00",
            "Overall Completion % — Trend", "Overall Completion (%)")))
        imgB.width, imgB.height = 620, 260
        ws.add_image(imgB, "G16")

        imgC = XLImage(_buf(_chart_trend(
            on_time_rates, "#D50000", "#FFAE00",
            "On-Time Graduation Rate — Trend", "On-Time Grad Rate (%)")))
        imgC.width, imgC.height = 620, 260
        ws.add_image(imgC, "G27")

    # ---------- Sheet 2: At-Risk Students ----------
    ws2 = wb.create_sheet("At-Risk Students")
    at_risk_df = _build_at_risk_export_df(df_summary)
    if at_risk_df.empty:
        ws2.cell(row=1, column=1, value="No students currently flagged as at-risk.")
    else:
        for ci, cn in enumerate(at_risk_df.columns, start=1):
            c = ws2.cell(row=1, column=ci, value=cn)
            c.fill, c.font = head_fill, head_font
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws2.row_dimensions[1].height = 32
        for ri, (_, row) in enumerate(at_risk_df.iterrows(), start=2):
            for ci, cn in enumerate(at_risk_df.columns, start=1):
                v = row[cn]
                ws2.cell(row=ri, column=ci, value="" if pd.isna(v) else v)
        for ci, cn in enumerate(at_risk_df.columns, start=1):
            max_len = max([len(str(cn))] + [len(str(v)) for v in at_risk_df[cn].astype(str)])
            ws2.column_dimensions[get_column_letter(ci)].width = min(max_len + 2, 50)

    wb.save(buf)
    return buf.getvalue()

# ------------------------------------------------------------------
# VIEW 1: STUDENT ROSTER (Dashboard)
# ------------------------------------------------------------------
def render_student_list(df_all):
    # ----------------- Enterprise Roster Grid CSS -----------------
    st.markdown(
        """
        <style>
        .roster-th { font-size: 0.85rem; font-weight: 700; color: #666; text-transform: uppercase; word-break: normal !important; overflow-wrap: normal !important; }
        .roster-th-divider { border-bottom: 2px solid #ddd; margin: 0.5rem 0 1rem 0; }
        .roster-row-divider { border-bottom: 1px solid #eee; margin: 0.5rem 0; }
        .roster-cell-text, .roster-cell-id { font-size: 0.9rem; color: var(--text-color); word-break: normal !important; overflow-wrap: normal !important; }
    
        .status-pill { background-color: #f0f2f6; padding: 4px 8px; border-radius: 12px; font-size: 0.8rem; color: #31333F !important; font-weight: 600; }
        .sr-risk-pill { background-color: #D500001A; color: #D50000; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 600; white-space: nowrap; }
        .sr-risk-none { background-color: #0080001A; color: #008000; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 600; white-space: nowrap; }
    
        .roster-row-marker, .roster-header-marker, .roster-scroll-area {
            display: none !important;
        }
        
        /* Force minimum width on grid to prevent text overlapping */
        div[data-testid="stHorizontalBlock"]:has(.roster-header-marker),
        div[data-testid="stHorizontalBlock"]:has(.roster-row-marker) {
            min-width: 1100px !important;
            flex-wrap: nowrap !important;
        }

        /* Guarantee horizontal scrolling ONLY on the specific table container, not the whole page */
        div[data-testid="stVerticalBlock"]:has(.roster-scroll-area):not(:has(div[data-testid="stVerticalBlock"]:has(.roster-scroll-area))) {
            overflow-x: auto !important;
            padding-bottom: 15px;
        }
    
        div[data-testid="stHorizontalBlock"]:has(.roster-row-marker) {
            border-radius: 6px !important;
            padding: 6px 4px !important;
        }
    
        div[data-testid="stHorizontalBlock"]:has(.roster-row-marker):has(.sr-risk-pill) {
            background-color: #D500001A !important;
            border-left: 4px solid #D50000 !important;
        }
    
        div[data-testid="stButton"] button p {
            white-space: normal !important;
            line-height: 1.2 !important;
            text-align: center !important;
            word-break: keep-all !important;
            overflow-wrap: normal !important;
        }

        div[data-testid="stButton"] button {
            padding-left: 4px !important;
            padding-right: 4px !important;
        }
    
        @media (max-width: 1100px) {
            .roster-th { font-size: 0.72rem; }
            .roster-cell-text, .roster-cell-id { font-size: 0.82rem; }
            .sr-risk-pill, .sr-risk-none { font-size: 0.72rem; padding: 3px 5px; }
        }
    
        @media (max-width: 768px) {
            div[data-testid="stHorizontalBlock"]:has(.roster-header-marker) {
                display: none !important;
            }
    
            div[data-testid="stHorizontalBlock"]:has(.roster-row-marker) {
                display: grid !important;
                grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
                gap: 8px 12px !important;
                padding: 12px 10px !important;
                margin-bottom: 8px !important;
                border: 1px solid var(--secondary-background-color) !important;
                border-left: 4px solid transparent !important;
                background-color: var(--background-color) !important;
                min-width: 0 !important; /* Reset minimum width for mobile layout */
            }
    
            div[data-testid="stHorizontalBlock"]:has(.roster-row-marker):has(.sr-risk-pill) {
                background-color: #D500001A !important;
                border-left: 4px solid #D50000 !important;
            }
    
            div[data-testid="stHorizontalBlock"]:has(.roster-row-marker) > div {
                min-width: 0 !important;
                width: 100% !important;
            }
    
            div[data-testid="stHorizontalBlock"]:has(.roster-row-marker) > div:nth-child(1)::before { content: "STUDENT ID"; }
            div[data-testid="stHorizontalBlock"]:has(.roster-row-marker) > div:nth-child(2)::before { content: "NAME"; }
            div[data-testid="stHorizontalBlock"]:has(.roster-row-marker) > div:nth-child(3)::before { content: "COHORT"; }
            div[data-testid="stHorizontalBlock"]:has(.roster-row-marker) > div:nth-child(4)::before { content: "ADVISER"; }
            div[data-testid="stHorizontalBlock"]:has(.roster-row-marker) > div:nth-child(5)::before { content: "COURSEWORK"; }
            div[data-testid="stHorizontalBlock"]:has(.roster-row-marker) > div:nth-child(6)::before { content: "COMP EXAM"; }
            div[data-testid="stHorizontalBlock"]:has(.roster-row-marker) > div:nth-child(7)::before { content: "CAPSTONE"; }
            div[data-testid="stHorizontalBlock"]:has(.roster-row-marker) > div:nth-child(8)::before { content: "LAST UPDATE"; }
            div[data-testid="stHorizontalBlock"]:has(.roster-row-marker) > div:nth-child(9)::before { content: "RISK"; }
            div[data-testid="stHorizontalBlock"]:has(.roster-row-marker) > div:nth-child(10)::before { content: "ACTION"; }
    
            div[data-testid="stHorizontalBlock"]:has(.roster-row-marker) > div::before {
                display: block;
                font-size: 0.65rem;
                font-weight: 700;
                color: #777;
                letter-spacing: 0.4px;
                margin-bottom: 3px;
            }
    
            div[data-testid="stHorizontalBlock"]:has(.roster-row-marker) .roster-cell-text,
            div[data-testid="stHorizontalBlock"]:has(.roster-row-marker) .roster-cell-id {
                font-size: 0.85rem;
            }
    
            div[data-testid="stHorizontalBlock"]:has(.roster-row-marker) .sr-risk-pill,
            div[data-testid="stHorizontalBlock"]:has(.roster-row-marker) .sr-risk-none {
                display: inline-block;
                font-size: 0.72rem;
            }
    
            div[data-testid="stHorizontalBlock"]:has(.roster-row-marker) .stButton button {
                min-height: 42px;
            }
    
            .roster-row-divider {
                display: none;
            }
        }
    
        @media (max-width: 480px) {
            div[data-testid="stHorizontalBlock"]:has(.roster-row-marker) {
                grid-template-columns: 1fr !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True
    )

    # Helper function to reuse the exact same grid layout across multiple pages
    def render_roster_grid(display_df, key_prefix):
        col_widths = [0.9, 1.5, 0.8, 1.5, 1.2, 1.2, 1.4, 0.9, 0.9, 0.8]
        header_labels = ["STUDENT ID", "NAME", "COHORT", "ADVISER", "COURSEWORK", "COMP EXAM", "CAPSTONE", "LAST UPDATE", "RISK", "ACTION"]
    
        scroll_kwargs = {"height": 600} if len(display_df) > 10 else {}
        
        # --- HEADERS MOVED INSIDE THE SCROLL CONTAINER ---
        with st.container(border=False, key=f"{key_prefix}_scroll", **scroll_kwargs):
            st.markdown('<div class="roster-scroll-area"></div>', unsafe_allow_html=True)
            
            header_cols = st.columns(col_widths, vertical_alignment="center")
            
            # Combine the hidden header marker and the first label into a single markdown call
            header_cols[0].markdown(f'<span class="roster-header-marker"></span><div class="roster-th">{header_labels[0]}</div>', unsafe_allow_html=True)
            
            # Render the rest of the headers normally
            for col, label in zip(header_cols[1:], header_labels[1:]):
                col.markdown(f'<div class="roster-th">{label}</div>', unsafe_allow_html=True)
        
            st.markdown('<div class="roster-th-divider"></div>', unsafe_allow_html=True)
    
            for _, row in display_df.iterrows():
                is_at_risk = str(row.get("Risk Status", "")).strip() == "Flagged"
        
                r_cols = st.columns(col_widths, vertical_alignment="center")
        
                # Combine the hidden row marker and the student ID into a single markdown call
                r_cols[0].markdown(
                    f'<span class="roster-row-marker"></span><span class="roster-cell-id">{row.get("Student ID", "")}</span>',
                    unsafe_allow_html=True
                )
        
                r_cols[1].markdown(
            f'<span class="roster-cell-text"><b>{row.get("Name", "")}</b></span>',
            unsafe_allow_html=True
        )
        
                r_cols[2].markdown(
                    f'<span class="roster-cell-text">{row.get("Cohort", "")}</span>',
                    unsafe_allow_html=True
                )
        
                r_cols[3].markdown(
                    f'<span class="roster-cell-text">{row.get("Adviser", "")}</span>',
                    unsafe_allow_html=True
                )
        
                r_cols[4].markdown(
                    get_stage_badge("coursework", row.get("Coursework", "")),
                    unsafe_allow_html=True
                )
        
                r_cols[5].markdown(
                    get_stage_badge("comprehensive_exam", row.get("Comprehensive Exam", "")),
                    unsafe_allow_html=True
                )
        
                r_cols[6].markdown(
                    get_stage_badge("capstone", row.get("Capstone", "")),
                    unsafe_allow_html=True
                )
        
                last_upd = row.get("Last Update", "")
                display_date = last_upd if str(last_upd).strip() != "N/A" else "—"
        
                r_cols[7].markdown(
                    f'<span class="roster-cell-text">{display_date}</span>',
                    unsafe_allow_html=True
                )
        
                risk_status = str(row.get("Risk Status", "")).strip()
        
                if risk_status == "Flagged":
                    r_cols[8].markdown(
                        '<span class="sr-risk-pill">AT RISK</span>',
                        unsafe_allow_html=True
                    )
                else:
                    r_cols[8].markdown(
                        '<span class="sr-risk-none">ON TRACK</span>',
                        unsafe_allow_html=True
                    )
        
                with r_cols[9]:
                    st.button(
                        "**View\nProfile**",
                        key=f"view_{key_prefix}_{row.get('Student ID', '')}",
                        use_container_width=True,
                        on_click=go_to_profile,
                        args=(row.get("Email", ""),)
                    )
        
                st.markdown('<div class="roster-row-divider"></div>', unsafe_allow_html=True)
    
        st.caption(f"Showing {len(display_df)} students.")

    # Standardize data preparation for the grids
    def format_for_grid(df_subset):
        return df_subset[[
            "full_name", "student_number", "cohort", "risk_flag", "overall_status",
            "coursework_display", "comprehensive_exam_display", "capstone_display", "adviser", "student_email", "coursework_updated_at"
        ]].rename(columns={
            "full_name": "Name", "student_number": "Student ID", "cohort": "Cohort", "risk_flag": "Risk Status",
            "overall_status": "Overall Status", "coursework_display": "Coursework",
            "comprehensive_exam_display": "Comprehensive Exam", "capstone_display": "Capstone", "adviser": "Adviser", "student_email": "Email", "coursework_updated_at": "Last Update"
        })

    active_cohort = st.session_state.get("cohort_filter", "All")
    
    if active_cohort == "All": 
        summary_label = "All Cohorts"
    elif len(active_cohort) == 6 and active_cohort[1] in ['T', 'Q']:
        term_num = active_cohort[0]
        term_type = active_cohort[1]
        y1 = active_cohort[2:4]
        y2 = active_cohort[4:6]
        summary_label = f"{term_num}{term_type}, A.Y. 20{y1}–20{y2}"
    else: 
        summary_label = active_cohort

    df_summary = df_all.copy()
    if active_cohort != "All":
        df_summary = df_summary[df_summary["cohort"].astype(str) == active_cohort]

    # --- RENDER EXECUTIVE DASHBOARD ---
    if st.session_state.admin_view == "Executive Dashboard":
        st.markdown(f"#### Executive Summary — {summary_label}")
        
        # --- Dashboard Filters (No Search or Sort) ---
        cohort_col, adv_col = st.columns(2)
        with cohort_col:
            valid_cohorts = sorted([str(c) for c in df_all["cohort"].dropna().unique().tolist() if str(c).strip()], key=get_cohort_val)
            selected_cohort = st.selectbox("Filter by cohort", ["All"] + valid_cohorts, key="cohort_filter")
        with adv_col:
            valid_advisers = sorted([str(a) for a in df_all["adviser"].dropna().unique().tolist() if str(a).strip()])
            current_user = st.session_state.user_info.get("full_name", "")
            user_role = st.session_state.user_info.get("role", "")
            
            if "Advisor" in user_role or "Faculty" in user_role:
                adv_view = st.selectbox("Adviser View", ["My Advisees", "All Students"], key="adv_view_toggle")
                selected_adviser = current_user if adv_view == "My Advisees" else "All"
            else:
                default_idx = valid_advisers.index(current_user) + 1 if current_user in valid_advisers else 0
                selected_adviser = st.selectbox("Filter by Adviser", ["All"] + valid_advisers, index=default_idx, key="adviser_filter")

        # Apply Adviser Filter to the dashboard metrics!
        if selected_adviser != "All":
            df_summary = df_summary[df_summary["adviser"].astype(str) == selected_adviser]
            
        total_students = len(df_summary)
        cw_completed = len(df_summary[df_summary["coursework_display"] == "Completed"])
        exam_passed = len(df_summary[df_summary["comprehensive_exam_display"] == "Passed"])
        capstone_defended = len(df_summary[df_summary["capstone_display"] == "Defended"])

        evaluated_df = df_summary[
            df_summary["graduate_on_time"].notna() & 
            (df_summary["graduate_on_time"].astype(str).str.strip() != "") &
            (~df_summary["graduate_on_time"].astype(str).str.lower().isin(["n/a", "none"]))
        ]
        grad_numerator = len(evaluated_df[evaluated_df["graduate_on_time"].astype(str).str.lower().isin(["yes", "y", "true", "1"])])
        grad_denominator = total_students
        on_time_rate = (grad_numerator / grad_denominator * 100) if grad_denominator > 0 else 0.0

        fully_completed = len(
            df_summary[
                (df_summary["coursework_display"] == "Completed") & 
                (df_summary["comprehensive_exam_display"] == "Passed") & 
                (df_summary["capstone_display"] == "Defended")
            ]
        )
        completion_rate = int((fully_completed / total_students * 100)) if total_students > 0 else 0

        remaining_students = int(total_students - fully_completed)
        at_risk_count = int(df_summary["is_at_risk"].sum())

        # --- Current stage buckets (mutually exclusive) — used by chart AND exports ---
        current_cw = len(df_summary[df_summary["coursework_display"] != "Completed"])

        current_ce = len(df_summary[
            (df_summary["coursework_display"] == "Completed") &
            (df_summary["comprehensive_exam_display"] != "Passed")
        ])

        current_cap = len(df_summary[
            (df_summary["coursework_display"] == "Completed") &
            (df_summary["comprehensive_exam_display"] == "Passed") &
            (df_summary["capstone_display"] != "Defended")
        ])

        # --- TERM-OVER-TERM COMPARISON LOGIC ---
        all_cohorts_sorted = sorted([str(c) for c in df_all["cohort"].dropna().unique() if str(c).strip()], key=get_cohort_val)
        
        prior_cohort = None
        if active_cohort != "All" and active_cohort in all_cohorts_sorted:
            idx = all_cohorts_sorted.index(active_cohort)
            if idx > 0:
                prior_cohort = all_cohorts_sorted[idx - 1]

        if prior_cohort:
            df_prior = df_all[df_all["cohort"].astype(str) == prior_cohort]
            p_total = len(df_prior)
            
            p_eval = df_prior[
                df_prior["graduate_on_time"].notna() & 
                (df_prior["graduate_on_time"].astype(str).str.strip() != "") &
                (~df_prior["graduate_on_time"].astype(str).str.lower().isin(["n/a", "none"]))
            ]
            p_grad_num = len(p_eval[p_eval["graduate_on_time"].astype(str).str.lower().isin(["yes", "y", "true", "1"])])
            p_on_time_rate = (p_grad_num / p_total * 100) if p_total > 0 else 0.0
            
            p_comp = len(df_prior[
                (df_prior["coursework_display"] == "Completed") & 
                (df_prior["comprehensive_exam_display"] == "Passed") & 
                (df_prior["capstone_display"] == "Defended")
            ])
            p_comp_rate = int((p_comp / p_total * 100)) if p_total > 0 else 0.0
            
            p_rem = p_total - p_comp
            p_at_risk = int(df_prior["is_at_risk"].sum())
            
            grad_delta_str = f"{on_time_rate - p_on_time_rate:+.1f}% vs {prior_cohort}"
            comp_delta_str = f"{completion_rate - p_comp_rate:+.0f}% vs {prior_cohort}"
            rem_delta_str = f"{remaining_students - p_rem:+} vs {prior_cohort}"
            risk_delta_str = f"{at_risk_count - p_at_risk:+} vs {prior_cohort}"
            
            grad_color_mode = "normal"
            comp_color_mode = "normal"
            rem_color_mode = "inverse"
            risk_color_mode = "inverse"
        else:
            grad_delta_str = f"{grad_numerator} out of {total_students} students"
            comp_delta_str = f"{fully_completed} out of {total_students} students"
            rem_delta_str = f"{remaining_students} out of {total_students} students"
            risk_delta_str = f"{at_risk_count} out of {total_students} students"
            
            grad_color_mode = "normal" if on_time_rate >= 50 else "inverse"
            comp_color_mode = "normal" if completion_rate >= 50 else "inverse"
            
            rem_percentage = (remaining_students / total_students * 100) if total_students > 0 else 0
            rem_color_mode = "inverse" if rem_percentage > 50 else "normal"
            risk_color_mode = "inverse" if at_risk_count > 0 else "normal"

        st.markdown(
            """
            <style>
            div[data-testid="stMetric"] {
                background-color: var(--background-color);
                border: 1px solid var(--secondary-background-color);
                border-radius: 8px;
                padding: 15px 20px;
                box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -1px rgba(0,0,0,0.06);
                border-top: 4px solid #c8102e; 
            }
            div[data-testid="stMetricLabel"] p {
                text-transform: uppercase !important;
                font-size: 0.75rem !important;
                font-weight: 600 !important;
                color: var(--faded-text-color) !important;
                letter-spacing: 0.5px !important;
            }
            div[data-testid="stMetricValue"] div {
                font-size: 2rem !important;
                font-weight: 700 !important;
                color: var(--text-color) !important;
            }
            
            /* Make bordered containers look exactly like the metric tiles */
            div[data-testid="stVerticalBlockBorderWrapper"] {
                border-radius: 8px !important;
                box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -1px rgba(0,0,0,0.06) !important;
                border-top: 4px solid #c8102e !important;
                background-color: var(--background-color);
            }

            /* Hide the delta arrow/dash specifically for the Total Students metric */
            div[data-testid="stHorizontalBlock"] > div:nth-child(1) div[data-testid="stMetricDelta"] svg {
                display: none;
            }
            </style>
            """,
            unsafe_allow_html=True
        )

        # Consolidated single row of 5 core metrics
        m1, m2, m3, m4, m5 = st.columns(5)
        
        # Format the delta text for the first tile
        display_cohort_delta = "All Cohorts" if active_cohort == "All" else f"Cohort: {active_cohort}"
        
        m1.metric(label="Total Students", value=total_students, delta=display_cohort_delta, delta_color="off", help="Total students matching filters.")
        m2.metric(label="On-Time Grad Rate", value=f"{on_time_rate:.1f}%", delta=grad_delta_str, delta_color=grad_color_mode, help="Percentage of students on track to graduate within expected program duration.")
        m3.metric(label="Overall Completion", value=f"{completion_rate}%", delta=comp_delta_str, delta_color=comp_color_mode, help="Percentage of students who have fully completed coursework, comprehensive exam, and capstone.")
        m4.metric(label="Remaining Students", value=remaining_students, delta=rem_delta_str, delta_color=rem_color_mode, help="Students who have not yet completed all three major milestones.")
        m5.metric(label="Students At Risk", value=at_risk_count, delta=risk_delta_str, delta_color=risk_color_mode, help="Students who have exceeded expected duration thresholds.")
        
        st.write("")


        col1, col2 = st.columns(2)

        with col1:
            with st.container(border=True): 
                st.subheader("Lifecycle Stage Breakdown", help="Distribution of students across their current active lifecycle stage.")

                # Build the chart data using the strict buckets
                stage_df = pd.DataFrame({
                    "Lifecycle Stage": ["Overall Completion", "Capstone", "Comprehensive Exam", "Coursework"],
                    "Students": [fully_completed, current_cap, current_ce, current_cw]
                })
                
                if total_students > 0: 
                    stage_df["Percentage"] = (stage_df["Students"] / total_students * 100)
                else: 
                    stage_df["Percentage"] = 0.0

                fig = px.bar(
                    stage_df, x="Percentage", y="Lifecycle Stage", orientation="h",
                    text="Percentage", custom_data=["Students"], range_x=[0, 100],
                    labels={"Percentage": "Percentage of Active Students", "Lifecycle Stage": ""}
                )

                # Set colors: Green (#0DC249) for Overall, Red for Capstone, Yellow for Exam, Blue for Coursework
                fig.update_traces(
                    marker_color=["#0DC249", "#D50000", "#FFAE00", "#0072B2"], 
                    texttemplate="%{text:.2f}%",
                    textposition="outside", hovertemplate="<b>%{y}</b><br>Students: %{customdata[0]}<extra></extra>"
                )
                
                # 'categoryarray' plots from bottom to top. Placing Overall Completion first puts it at the bottom.
                fig.update_layout(
                    height=320, margin=dict(l=10, r=40, t=30, b=10),
                    xaxis=dict(range=[0, 100], ticksuffix="%", dtick=20, fixedrange=True),
                    yaxis=dict(
                        categoryorder="array", 
                        categoryarray=["Overall Completion", "Capstone", "Comprehensive Exam", "Coursework"], 
                        fixedrange=True
                    ),
                    showlegend=False, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)"
                )

                # 1. Render Chart and Capture Click
                chart_event = st.plotly_chart(
                    fig, width="stretch", config={"displayModeBar": False},
                    on_select="rerun", selection_mode="points", key=f"lifecycle_chart_{st.session_state.chart_key_counter}"
                )

                # 2. Store the clicked stage into session state
                if "chart_filter_stage" not in st.session_state:
                    st.session_state.chart_filter_stage = None

                if chart_event and chart_event.selection:
                    selected_points = chart_event.selection.get("points", [])
                    if selected_points:
                        clicked_stage = selected_points[0].get("y")
                        if clicked_stage in ["Coursework", "Comprehensive Exam", "Capstone", "Overall Completion"]:
                            st.session_state.chart_filter_stage = clicked_stage
                    else: 
                        st.session_state.chart_filter_stage = None

                # 3. Show an active filter indicator and a clear button right under the chart
                if st.session_state.chart_filter_stage:
                    st.caption(f"🎯 **Viewing full roster for:** `{st.session_state.chart_filter_stage}`")
                    if st.button("✕ Clear Chart Filter", key="clear_chart_filter", use_container_width=True):
                        st.session_state.chart_filter_stage = None
                        st.session_state.chart_key_counter += 1
                        st.rerun()

        with col2:
            with st.container(border=True): 
                render_completion_trend_chart(df_all, ACTIVE_PROGRAM)

       # -- Dynamic Full Roster Table (Pops up on chart click) --
        if st.session_state.get("chart_filter_stage"):
            st.divider()
            selected = st.session_state.chart_filter_stage
            st.subheader(f"📋 Full Roster: {selected}")
            st.caption(f"Showing all active students belonging to the {selected} category.")
            
            filtered_full_df = df_summary.copy()
            
            # Apply mutually exclusive "current stage" logic
            if selected == "Coursework":
                filtered_full_df = filtered_full_df[filtered_full_df["coursework_display"] != "Completed"]
                
            elif selected == "Comprehensive Exam":
                filtered_full_df = filtered_full_df[
                    (filtered_full_df["coursework_display"] == "Completed") & 
                    (filtered_full_df["comprehensive_exam_display"] != "Passed")
                ]
                
            elif selected == "Capstone":
                filtered_full_df = filtered_full_df[
                    (filtered_full_df["coursework_display"] == "Completed") & 
                    (filtered_full_df["comprehensive_exam_display"] == "Passed") & 
                    (filtered_full_df["capstone_display"] != "Defended")
                ]
                
            elif selected == "Overall Completion":
                filtered_full_df = filtered_full_df[filtered_full_df["capstone_display"] == "Defended"]

            if filtered_full_df.empty:
                st.info(f"No students found in the {selected} category.")
            else:
                display_filtered_df = format_for_grid(filtered_full_df.sort_values("full_name").reset_index(drop=True))
                render_roster_grid(display_filtered_df, key_prefix="dynamic_roster")

                        # --- Export Row (PDF + XLSX) ---
        st.write("")
        exp_pdf_bytes = generate_executive_pdf(
            df_summary, df_all, summary_label, selected_adviser,
            total_students, on_time_rate, completion_rate, remaining_students,
            at_risk_count, fully_completed, current_cap, current_ce, current_cw,
            user["full_name"] or user["username"], ACTIVE_PROGRAM, last_sync
        )
        exp_xlsx_bytes = generate_executive_xlsx(
            df_summary, df_all, summary_label, selected_adviser,
            total_students, on_time_rate, completion_rate, remaining_students,
            at_risk_count, fully_completed, current_cap, current_ce, current_cw,
            user["full_name"] or user["username"], ACTIVE_PROGRAM, last_sync
        )

        _stamp = datetime.now(ZoneInfo("Asia/Manila")).strftime("%Y%m%d_%H%M")
        _safe_prog = re.sub(r"[^A-Za-z0-9]+", "_", ACTIVE_PROGRAM)
        _safe_cohort = re.sub(r"[^A-Za-z0-9]+", "_", str(summary_label))

        def _log_pdf_export():
            log_security_event(
                user["user_id"], "DATA_EXPORT",
                f"Exported Executive PDF ({summary_label} | Adviser: {selected_adviser})."
            )

        def _log_xlsx_export():
            log_security_event(
                user["user_id"], "DATA_EXPORT",
                f"Exported Executive XLSX ({summary_label} | Adviser: {selected_adviser})."
            )

        exp_c1, exp_c2, exp_c3 = st.columns([1.2, 1.2, 5])
        with exp_c1:
            st.download_button(
                "📄 Export PDF Report",
                data=exp_pdf_bytes,
                file_name=f"{_safe_prog}_executive_overview_{_safe_cohort}_{_stamp}.pdf",
                mime="application/pdf",
                use_container_width=True,
                key="exec_export_pdf",
                on_click=_log_pdf_export,
            )
        with exp_c2:
            st.download_button(
                "📊 Export XLSX",
                data=exp_xlsx_bytes,
                file_name=f"{_safe_prog}_executive_overview_{_safe_cohort}_{_stamp}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="exec_export_xlsx",
                on_click=_log_xlsx_export,
            )
        exp_c3.empty()

        # -- Dashboard Risk Table --
        st.divider()
        st.subheader("🚨 Students At Risk")
        st.caption("Students who have exceeded expected duration thresholds for their current lifecycle stage.")
        
        at_risk_df = df_summary[df_summary["is_at_risk"] == True].copy()
        
        if at_risk_df.empty:
            st.success("Great news! No students are currently flagged as at-risk.")
        else:
            display_risk_df = format_for_grid(at_risk_df.sort_values("full_name").reset_index(drop=True))
            render_roster_grid(display_risk_df, key_prefix="dash_risk")

    # --- RENDER STUDENT ROSTER ---
    elif st.session_state.admin_view == "Student Roster":
        chart_view = st.session_state.get("chart_view_state", "Accomplished")
        
        roster_title = f"Student Roster & Lifecycle Progress ({ACTIVE_PROGRAM})"
        if st.session_state.drill_stage: roster_title += f" — {st.session_state.drill_stage}"
        
        # Top Header: Title
        top_left_col, top_right_col = st.columns([4, 1], vertical_alignment="bottom")
        with top_left_col:
            st.subheader(roster_title)
        
        search_col, cohort_col, adv_col, sort_col = st.columns([2, 1, 1.2, 1])
        
        with search_col: search_term = st.text_input("Search by name or student ID", placeholder="e.g. Adrian Santos or 2026124837", key="search_filter")
        with cohort_col:
            valid_cohorts = sorted([str(c) for c in df_all["cohort"].dropna().unique().tolist() if str(c).strip()], key=get_cohort_val)
            selected_cohort = st.selectbox("Filter by cohort", ["All"] + valid_cohorts, key="cohort_filter")
        with adv_col:
            valid_advisers = sorted([str(a) for a in df_all["adviser"].dropna().unique().tolist() if str(a).strip()])
            current_user = st.session_state.user_info.get("full_name", "")
            user_role = st.session_state.user_info.get("role", "")
            
            if "Advisor" in user_role or "Faculty" in user_role:
                adv_view = st.selectbox("Adviser View", ["My Advisees", "All Students"], key="adv_view_toggle")
                selected_adviser = current_user if adv_view == "My Advisees" else "All"
            else:
                default_idx = valid_advisers.index(current_user) + 1 if current_user in valid_advisers else 0
                selected_adviser = st.selectbox("Filter by Adviser", ["All"] + valid_advisers, index=default_idx, key="adviser_filter")
        with sort_col: sort_option = st.selectbox("Sort by", ["Name", "Student ID", "Overall Status"], key="sort_filter")

        filtered = df_summary.copy()

        if selected_adviser != "All": filtered = filtered[filtered["adviser"].astype(str) == selected_adviser]

        if st.session_state.drill_stage == "Coursework":
            if chart_view == "Accomplished": filtered = filtered[filtered["coursework_display"] == "Completed"]
            else: filtered = filtered[filtered["coursework_display"] != "Completed"]
        elif st.session_state.drill_stage == "Comprehensive Exam":
            if chart_view == "Accomplished": filtered = filtered[filtered["comprehensive_exam_display"] == "Passed"]
            else: filtered = filtered[filtered["comprehensive_exam_display"] != "Passed"]
        elif st.session_state.drill_stage == "Capstone":
            if chart_view == "Accomplished": filtered = filtered[filtered["capstone_display"] == "Defended"]
            else: filtered = filtered[filtered["capstone_display"] != "Defended"]

        if search_term:
            term = search_term.strip().lower()
            filtered = filtered[
                filtered["full_name"].astype(str).str.lower().str.contains(term, na=False) | 
                filtered["student_number"].astype(str).str.lower().str.contains(term, na=False)
            ]

        sort_map = {"Name": "full_name", "Student ID": "student_number", "Overall Status": "overall_status"}
        filtered = filtered.sort_values(sort_map[sort_option]).reset_index(drop=True)

        if filtered.empty:
            if selected_adviser == current_user and not search_term and selected_cohort == "All" and not st.session_state.drill_stage:
                st.info(f"You currently have no advisees assigned to you in the {ACTIVE_PROGRAM} program.")
            else:
                st.info(f"No results found for the {ACTIVE_PROGRAM} program matching your specific filters.")
            return

        display_df = format_for_grid(filtered)

        # CSV export must be created AFTER display_df has been prepared
        def log_csv_export():
            if st.session_state.user_info:
                log_security_event(
                    st.session_state.user_info["user_id"],
                    "DATA_EXPORT",
                    f"Exported {ACTIVE_PROGRAM} student roster to CSV."
                )

        top_right_col.download_button(
            label="📥 Export CSV",
            data=display_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{ACTIVE_PROGRAM}_roster_export.csv",
            mime="text/csv",
            on_click=log_csv_export,
            use_container_width=True
        )
        
        # Render the exact same HTML grid format
        render_roster_grid(display_df, key_prefix="roster")

# ------------------------------------------------------------------
# VIEW 2: STUDENT PROFILE (Read / Write Record Inspector)
# ------------------------------------------------------------------
def render_student_profile(df_all):
    st.subheader("Student Profile Inspector")

    email = st.session_state.selected_student_email

    # Top controls: Stacked Back button and Global Search Bar (Using Callbacks)
    st.button("← Back", on_click=go_to_list)
            
    # Clean the data to prevent NaN/None dictionary errors
    clean_df = df_all.dropna(subset=["student_email"])
    
    # Create a safe mapping dictionary for the dropdown display using Student ID
    display_map = {"": "🔍 Search for a student..."}
    for _, row in clean_df.iterrows():
        e = row["student_email"]
        n = row["full_name"]
        sn = row.get("student_number", "Unknown ID")
        display_map[e] = f"{n} ({sn})"
        
    options = [""] + clean_df["student_email"].tolist()
    default_idx = options.index(email) if email in options else 0

    # Callback to handle the search bar dynamically
    def handle_search_change():
        selected = st.session_state.global_profile_search
        if selected and selected != email:
            go_to_profile(selected)

    st.selectbox(
        "Search for a student", 
        options, 
        index=default_idx,
        format_func=lambda x: display_map.get(x, "Unknown Student"),
        label_visibility="collapsed",
        key="global_profile_search",
        on_change=handle_search_change
    )

    if not email or email not in options:
        st.info("👆 No student selected. Please search for a student using the bar above to view their profile.")
        return

    match = df_all[df_all["student_email"] == email]
    
    if match.empty:
        st.warning("Student record not found in the active program. Returning to directory.")
        go_to_list()
        st.rerun()
        return

    student = match.iloc[0]

    st.markdown(f"### {student['full_name']}")
    col1, col2, col3, col4 = st.columns([1.2, 2.0, 1, 1.5])
    col1.write(f"**Student ID:** `{student['student_number']}`")
    col2.write(f"**Email:** [{student['student_email']}](mailto:{student['student_email']})")
    col3.write(f"**Cohort:** {student['cohort'] if pd.notna(student['cohort']) else 'N/A'}")
    col4.markdown(f"**Overall Status:** {status_badge(student['overall_status'])}", unsafe_allow_html=True)

    if student.get('is_at_risk'):
        st.error(f"**🚨 At-Risk Flag Activated:** {student['risk_details']}")

    st.divider()
    st.markdown("#### Program Lifecycle Summary")

    milestones_df = fetch_student_milestones(student["student_number"])
    
    ce_updated = "N/A"
    cap_updated = "N/A"
    if not milestones_df.empty:
        for _, m_row in milestones_df.iterrows():
            if m_row.get("milestone_type") == "comprehensive_exam": ce_updated = m_row.get("Last Updated", "N/A")
            elif m_row.get("milestone_type") == "capstone": cap_updated = m_row.get("Last Updated", "N/A")

    cw_updated = student.get('coursework_updated_at', 'N/A')

    p1, p2, p3 = st.columns(3)
    with p1:
        with st.container(border=True):
            st.markdown("**📚 Coursework Stage**")
            st.markdown(student["coursework_indicator"], unsafe_allow_html=True)
            st.caption(f"Status: {student['coursework_display']}")
            st.caption(f"🕒 Last Updated: `{cw_updated}`")
            
    with p2:
        with st.container(border=True):
            st.markdown("**📝 Comprehensive Examination**")
            st.markdown(student["exam_indicator"], unsafe_allow_html=True)
            st.caption(f"Status: {student['comprehensive_exam_display']}")
            st.caption(f"🕒 Last Updated: `{ce_updated}`")
            
    with p3:
        with st.container(border=True):
            st.markdown("**🎓 Capstone & Defense**")
            st.markdown(student["capstone_indicator"], unsafe_allow_html=True)
            st.caption(f"Adviser: **{student.get('adviser') or 'Unassigned'}**")
            st.caption(f"🕒 Last Updated: `{cap_updated}`")

    st.divider()

    st.markdown("##### Graduation Tracking & Advisor Notes")
    c1, c2 = st.columns(2)
    
    raw_ontime = student.get("graduate_on_time")
    display_ontime = str(raw_ontime).strip() if pd.notna(raw_ontime) and str(raw_ontime).strip().lower() not in ("nan", "none", "") else "Under Evaluation"
    c1.write(f"**Graduating On Time:** {display_ontime}")
    
    raw_term = student.get("graduate_date_term_sy")
    display_term = "To Be Determined (TBD)"
    
    if pd.notna(raw_term) and str(raw_term).strip().lower() not in ("nan", "none", ""):
        term_str = str(raw_term).strip().upper()
        match = re.match(r'^(\d)([TQ])(\d{2})(\d{2})$', term_str)
        if match:
            t_num, t_type, y1, y2 = match.groups()
            display_term = f"{t_num}{t_type}, A.Y. 20{y1}–20{y2}"
        else:
            display_term = term_str

    c2.write(f"**Target Graduation Term:** {display_term}")

    is_authorized_editor = user.get("can_edit", False)

    st.markdown("---")
    st.markdown("##### ✏️ Update Student Record")
    
    with st.form("full_edit_form"):
        try:
            advisers_df = conn.query("SELECT full_name FROM advisers ORDER BY full_name", ttl=60)
            adv_list = ["Unassigned"] + [name for name in advisers_df["full_name"].dropna().tolist() if name.strip()]
        except Exception:
            adv_list = ["Unassigned"]
        
        curr_adv = student.get('adviser') if pd.notna(student.get('adviser')) else "Unassigned"
        if curr_adv not in adv_list: adv_list.append(curr_adv)

        cw_opts = ["Pending", "Completed", "Cancelled"]
        ce_opts = ["In-Progress", "Passed", "Incomplete"]
        cap_opts = ["In-Progress", "Defended", "Incomplete"]

        def get_idx(val, lst): return lst.index(val) if val in lst else 0

        c_form1, c_form2 = st.columns(2)
        with c_form1:
            new_cw = st.selectbox("Coursework Status", cw_opts, index=get_idx(student["coursework_display"], cw_opts))
            new_ce = st.selectbox("Comprehensive Exam Status", ce_opts, index=get_idx(student["comprehensive_exam_display"], ce_opts))
        with c_form2:
            new_cap = st.selectbox("Capstone Status", cap_opts, index=get_idx(student["capstone_display"], cap_opts))
            new_adv = st.selectbox("Primary Adviser", adv_list, index=get_idx(curr_adv, adv_list))

        existing_remarks = str(student["remarks"]) if pd.notna(student["remarks"]) else ""
        new_remarks = st.text_area("Administrative Remarks", value=existing_remarks)
        
        submitted = st.form_submit_button("Save Changes to Database", type="primary")

        if submitted:
            if not is_authorized_editor:
                log_security_event(user["user_id"], "UNAUTHORIZED_WRITE_ATTEMPT", f"Blocked attempt to update record for Student ID {student['student_number']} without edit permissions.")
                st.error("⛔ Access Denied: Your account role is View-Only. This unauthorized attempt has been logged.")
            else:
                cw_db_map = {"Pending": "pending", "Completed": "completed", "Cancelled": "cancelled"}
                ce_db_map = {"Passed": "done", "In-Progress": "not yet taken", "Incomplete": "incomplete"}
                cap_db_map = {"Defended": "done", "In-Progress": "not yet done", "Incomplete": "incomplete"}
                
                cw_val = cw_db_map.get(new_cw, "pending")
                ce_val = ce_db_map.get(new_ce, "incomplete")
                cap_val = cap_db_map.get(new_cap, "incomplete")
                
                try:
                    with conn.session as s:
                        adv_id = None
                        if new_adv != "Unassigned":
                            adv_res = s.execute(text("SELECT adviser_id FROM advisers WHERE full_name = :name"), {"name": new_adv}).fetchone()
                            if adv_res: adv_id = adv_res[0]
                                
                        # Always update remarks and adviser unconditionally
                        s.execute(
                            text("""
                                UPDATE students_normalized 
                                SET remarks = :rem, adviser_id = :adv
                                WHERE student_number = :sn;
                            """),
                            {"rem": new_remarks, "adv": adv_id, "sn": int(student["student_number"])}
                        )
                        
                        upsert_sql = text("""
                            INSERT INTO student_lifecycle_status (student_number, stage_id, status_id, last_updated_date)
                            VALUES (
                                :sn, 
                                (SELECT stage_id FROM lifecycle_stage WHERE stage_name = :stage_name),
                                (SELECT status_id FROM lifecycle_status WHERE status_name = :status_name),
                                NOW()
                            )
                            ON CONFLICT (student_number, stage_id) 
                            DO UPDATE SET status_id = EXCLUDED.status_id, last_updated_date = NOW();
                        """)
                        
                        # ONLY update lifecycle stage timestamps if the status was actually changed
                        if new_cw != student.get("coursework_display"):
                            s.execute(upsert_sql, {"sn": int(student["student_number"]), "stage_name": "coursework", "status_name": cw_val})
                        if new_ce != student.get("comprehensive_exam_display"):
                            s.execute(upsert_sql, {"sn": int(student["student_number"]), "stage_name": "comprehensive_exam", "status_name": ce_val})
                        if new_cap != student.get("capstone_display"):
                            s.execute(upsert_sql, {"sn": int(student["student_number"]), "stage_name": "capstone", "status_name": cap_val})
                        
                        s.commit()
                    
                    log_security_event(user["user_id"], "STUDENT_RECORD_UPDATED", f"Modified record for Student ID {student['student_number']} (CW: {new_cw}, Exam: {new_ce}, Capstone: {new_cap}).")
                    st.success("Record successfully updated in Supabase!")
                    load_students.clear()
                    fetch_student_milestones.clear()
                    st.rerun()
                    
                except Exception as err:
                    st.error(f"Write operation failed: {err}")
    # ------------------------------------------------------------------
    # Download PDF Summary
    # ------------------------------------------------------------------
    st.divider()
    st.markdown("##### 📄 Export Profile Summary")

    try:
        pdf_bytes = generate_student_pdf(
            student, milestones_df,
            cw_updated, ce_updated, cap_updated,
            user["full_name"] or user["username"],
            ACTIVE_PROGRAM, last_sync,
        )

        _safe_name = re.sub(r"[^A-Za-z0-9]+", "_",
                            str(student.get("full_name", "student"))).strip("_")
        _stamp = datetime.now(ZoneInfo("Asia/Manila")).strftime("%Y%m%d_%H%M")

        def _log_profile_export():
            log_security_event(
                user["user_id"], "DATA_EXPORT",
                f"Exported Student Profile PDF for {student.get('full_name')} "
                f"(ID {student.get('student_number')})."
            )

        st.download_button(
            "📄 Download PDF Summary",
            data=pdf_bytes,
            file_name=f"{_safe_name}_profile_{_stamp}.pdf",
            mime="application/pdf",
            use_container_width=True,
            key=f"student_profile_pdf_{student.get('student_number', 'x')}",
            on_click=_log_profile_export,
        )
    except Exception as e:
        st.warning(f"Could not generate the student PDF summary: {e}")
# ------------------------------------------------------------------
# FOOTER HELPER
# ------------------------------------------------------------------
def render_footer(last_sync_time):
    current_render_time = datetime.now(ZoneInfo("Asia/Manila")).strftime("%B %d, %Y %I:%M %p")
    st.divider()
    st.markdown(
        f"<div style='text-align: center; color: gray; font-size: 0.8rem; line-height: 1.6; padding-bottom: 20px;'>"
        f"Mapúa University · ETYSB Success Advisor Dashboard<br>"
        f"Data source: Supabase (Live Normalized DB) · Data last synced: <b>{last_sync_time}</b> · Rendered <b>{current_render_time}</b>."
        f"</div>",
        unsafe_allow_html=True
    )

# ------------------------------------------------------------------
# MASTER ROUTER
# ------------------------------------------------------------------
if "consecutive_sync_failures" not in st.session_state:
    st.session_state.consecutive_sync_failures = 0

if st.session_state.admin_view == "Global Instance Settings":
    render_instance_settings()
elif st.session_state.admin_view == "Schema Mapping Config":
    render_schema_mapping()
elif st.session_state.admin_view == "Permissions & Audit Logs":
    render_permissions_and_logs()
else:
    try:
            df_all, last_sync = load_students(ACTIVE_PROGRAM)
            st.session_state.consecutive_sync_failures = 0
            st.caption(f"🕒 **Data Last Synchronized:** `{last_sync}`")
            
            if st.session_state.admin_view == "Student Profile Inspector": 
                render_student_profile(df_all)
            elif st.session_state.admin_view == "Executive Dashboard" or st.session_state.admin_view == "Student Roster":
                render_student_list(df_all)
            else:
                # Placeholder renderer for admin configuration pages
                st.subheader(f"⚙️ {st.session_state.admin_view}")
                st.info("This configuration module is active and securely bound to your admin credentials.")

            render_footer(last_sync)

    except Exception as e:
        st.session_state.consecutive_sync_failures += 1
        error_msg = str(e)
        
        timestamp = datetime.now().strftime("%B %d, %Y at %I:%M:%S %p")
        with open("sync_error_log.txt", "a") as f:
            f.write(f"[{timestamp}] SYNC_FAILED: {error_msg}\n")
            
        if st.session_state.consecutive_sync_failures >= 3:
            st.error(f"🚨 **CRITICAL WARNING:** The dashboard has failed to synchronize with the database {st.session_state.consecutive_sync_failures} consecutive times. Please contact IT/Admin immediately to check the integration logs.", icon="🚨")
        else:
            st.warning("⚠️ **Warning:** A data synchronization error occurred. The system will retry on your next action.")
            
        st.error(f"**Detailed Error:** `{error_msg}`")
        st.stop()
