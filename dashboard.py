"""
Project Pulse — Dynamic Program Dashboard
==========================================
Streamlit app for Program Chairs, Faculty/Program Advisors, and the Dean.
Features:
- Dynamic Program Context (Multi-tenant ready)
- Clean cell-click navigation
- Strict View-Only vs Read/Write permission enforcement
- Admin panel for User Permission Management & Security Audit Logs
- Live 'Data Last Synchronized' timestamp
- Dynamic Schema Mapping (IT/Admin configuration screen)
"""

import streamlit as st
import pandas as pd
from sqlalchemy import text
from datetime import datetime
from zoneinfo import ZoneInfo

# Generic title (Dynamic titles are set after DB connection)
st.set_page_config(page_title="Project Pulse — Program Dashboard", page_icon="🎓", layout="wide")

# ------------------------------------------------------------------
# ETHICAL VISUALIZATION SPECIFICATION (CIS310 Standards)
# ------------------------------------------------------------------
PALETTE = {
    "green": {"hex": "#0DC249", "symbol": "🟢", "description": "Passed / Completed / Defended (On Track)"},
    "yellow": {"hex": "#FFAE00", "symbol": "🟡", "description": "In-Progress / Pending Clearance"},
    "red": {"hex": "#D50000", "symbol": "🔴", "description": "Cancelled / Incomplete / Action Required"},
    "blue": {"hex": "#0072B2", "symbol": "🔵", "description": "Active (Ongoing, Not Yet Graduated)"},
    "gray": {"hex": "#999999", "symbol": "⚪", "description": "Not Started / Not Applicable / Unknown"},
}

STAGE_THRESHOLDS = {
    "coursework": {"green": ["Completed"], "yellow": ["Pending"], "red": ["Cancelled"]},
    "comprehensive_exam": {"green": ["Passed"], "yellow": ["In-Progress"], "red": ["Incomplete", "Cancelled"]},  # Added "Cancelled"
    "capstone": {"green": ["Defended for Completion"], "yellow": ["In-Progress"], "red": ["Incomplete", "Cancelled"]},        # Added "Cancelled"
}

COURSEWORK_MAP = {"completed": "Completed", "cancelled": "Cancelled", "pending": "Pending"}

COMPREHENSIVE_EXAM_MAP = {
    "done": "Passed", 
    "incomplete": "Incomplete", 
    "not yet taken": "In-Progress", 
    "abs/failed": "Incomplete",
    "cancelled": "Cancelled"  # <--- Added here
}

CAPSTONE_MAP = {
    "done": "Defended for Completion", 
    "not yet done": "In-Progress", 
    "not yet taken": "In-Progress", 
    "in current load": "In-Progress", 
    "incomplete": "In-Progress", 
    "n/a": "In-Progress",
    "cancelled": "Cancelled"  # <--- Added here
}

def map_status(raw_value, mapping, default="Unknown"):
    if raw_value is None or pd.isna(raw_value): return default
    return mapping.get(str(raw_value).strip().lower(), default)


# ------------------------------------------------------------------
# BADGE / PILL RENDERING (replaces emoji-dot indicators)
# ------------------------------------------------------------------
def render_pill(label: str, color_key: str) -> str:
    """Returns an HTML pill badge — rounded border, tinted background, colored text.
    Only safe to use with st.markdown(..., unsafe_allow_html=True) or similar."""
    c = PALETTE.get(color_key, PALETTE["gray"])
    hex_color = c["hex"]
    return (
        f'<span style="display:inline-block;padding:3px 14px;border-radius:999px;'
        f'border:1.5px solid {hex_color};color:{hex_color};background-color:{hex_color}1A;'
        f'font-size:0.85rem;font-weight:600;white-space:nowrap;line-height:1.4;">{label}</span>'
    )

def get_stage_badge(stage: str, status_label: str) -> str:
    """HTML pill badge for a specific lifecycle stage. Use only where unsafe_allow_html=True
    is set (e.g. the student profile page), NOT inside st.dataframe cells."""
    rule = STAGE_THRESHOLDS.get(stage, {})
    clean_label = str(status_label).strip()
    if clean_label in rule.get("green", []): color_key = "green"
    elif clean_label in rule.get("yellow", []): color_key = "yellow"
    elif clean_label in rule.get("red", []): color_key = "red"
    else: color_key = "gray"
    return render_pill(clean_label, color_key)

def make_status_styler(stage: str):
    """Returns a pandas Styler-compatible function that highlights a status column's
    cells with tinted background + bold colored text, for use inside st.dataframe
    (which cannot render real HTML pills, only cell-level color styling)."""
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
    """HTML pill badge for the generic Overall Status field. Use with unsafe_allow_html=True."""
    key_map = {
        "Completed": "green", "Passed": "green", "Defended for Completion": "green",
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
    if coursework == "CANCELLED": return "Cancelled"
    if row.get("graduate_on_time") not in (None, "", "N/A", float("nan")): return "Graduated"
    return "Active"


# ------------------------------------------------------------------
# DATABASE & AUDIT LOGGING
# ------------------------------------------------------------------
try:
    conn = st.connection("supabase_db", type="sql")
except Exception as e:
    st.error("🚨 Critical Error: Failed to connect to Supabase. Verify `.streamlit/secrets.toml` credentials.")
    st.exception(e)
    st.stop()

def log_security_event(username: str, role: str, event_type: str, details: str):
    try:
        with conn.session as s:
            s.execute(
                text("""
                    INSERT INTO audit_logs (username, role, event_type, details)
                    VALUES (:username, :role, :event_type, :details);
                """),
                {"username": username, "role": role, "event_type": event_type, "details": details}
            )
            s.commit()
    except Exception as err:
        pass

def authenticate_user(username: str, password_attempt: str):
    query = text("""
        SELECT user_id, username, full_name, role, can_edit, is_active
        FROM app_users
        WHERE username = :u AND password_hash = crypt(:p, password_hash) AND is_active = TRUE;
    """)
    try:
        with conn.session as s:
            result = s.execute(query, {"u": username.strip(), "p": password_attempt}).mappings().fetchone()
            return result
    except Exception as e:
        st.error(f"Authentication query error: {e}")
        return None

# ------------------------------------------------------------------
# GLOBAL DASHBOARD CONFIGURATION (DYNAMIC CONTEXT)
# ------------------------------------------------------------------
@st.cache_data(ttl=60)
def load_dashboard_config():
    try:
        res = conn.query("SELECT active_program, current_term FROM dashboard_config WHERE id = 1;", ttl=0)
        if not res.empty:
            return res.iloc[0]["active_program"], res.iloc[0]["current_term"]
    except Exception:
        pass
    return "UNCONFIGURED PROGRAM", "UNCONFIGURED TERM"

ACTIVE_PROGRAM, CURRENT_TERM_LABEL = load_dashboard_config()


# ------------------------------------------------------------------
# DATA LOADERS (DYNAMICALLY MAPPED & FILTERED BY PROGRAM)
# ------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner="Loading mapped student roster...")
def load_students(target_program: str) -> tuple[pd.DataFrame, str]:
    # Ensure we select updated_at from the students table if it exists
    try:
        df = conn.query("SELECT * FROM students;", ttl=0)
    except Exception:
        df = pd.DataFrame()
    
    try:
        mapping_df = conn.query("SELECT dashboard_field, db_column FROM field_mappings;")
        db_to_app_map = dict(zip(mapping_df["db_column"], mapping_df["dashboard_field"]))
        df = df.rename(columns=db_to_app_map)
    except Exception as e:
        st.sidebar.warning("Schema mapping table missing or misconfigured.")

    expected_cols = [
        "program", "first_name", "last_name", "student_number", "cohort", "coursework_status", 
        "comprehensive_exam", "capstone", "graduate_on_time", "graduate_date_term_sy", 
        "adviser", "remarks", "student_email", "updated_at"
    ]
    for col in expected_cols:
        if col not in df.columns: df[col] = None

    def to_manila_time(series):
        dt = pd.to_datetime(series, errors="coerce")
        if dt.dt.tz is None:
            dt = dt.dt.tz_localize("UTC")
        return dt.dt.tz_convert("Asia/Manila").dt.strftime("%B %d, %Y at %I:%M %p")

    if "updated_at" in df.columns and df["updated_at"].notna().any():
        df["coursework_updated_at"] = to_manila_time(df["updated_at"]).fillna("N/A")
    else:
        df["coursework_updated_at"] = "N/A"
        
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

    # Safely handle coursework timestamp formatting
    if "updated_at" in df.columns and df["updated_at"].notna().any():
        df["coursework_updated_at"] = pd.to_datetime(df["updated_at"], errors="coerce").dt.strftime("%B %d, %Y at %I:%M %p").fillna("N/A")
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
            milestone_type,
            INITCAP(REPLACE(milestone_type, '_', ' ')) AS "Milestone",
            INITCAP(status) AS "Recorded Status",
            updated_at AS "Last Updated"
        FROM student_milestones
        WHERE student_number = {int(student_number)}
        ORDER BY milestone_type ASC;
    """
    try: 
        df = conn.query(query, ttl=0)
        if not df.empty and "Last Updated" in df.columns and df["Last Updated"].notna().any():
            dt = pd.to_datetime(df["Last Updated"], errors="coerce")
            if dt.dt.tz is None:
                dt = dt.dt.tz_localize("UTC")
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

def go_to_profile(student_email: str):
    st.session_state.selected_student_email = student_email
    st.session_state.page = "profile"

def go_to_list():
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
        .stButton > button[kind="primary"] {
            background-color: #b92b27 !important;
            border-color: #b92b27 !important;
            color: white !important;
        }
        .stButton > button[kind="primary"] p, 
        .stButton > button[kind="primary"] span {
            color: white !important;
        }
        .stButton > button[kind="primary"]:hover {
            background-color: #FF4B4B !important;
            border-color: #FF4B4B !important;
            color: white !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    logo_left, logo_center, logo_right = st.columns([2, 1, 2])
    with logo_center:
        st.image("rectangle_logo.png", width=500)  # Adjust pixel width here (e.g., 150, 180, 220)
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
                    log_security_event(auth_record["username"], auth_record["role"], "LOGIN_SUCCESS", "User authenticated.")
                    st.success(f"Welcome, {auth_record['full_name']}")
                    st.rerun()
                else:
                    log_security_event(input_username, "UNAUTHENTICATED", "LOGIN_FAILURE", "Invalid credentials provided.")
                    st.error("Authentication failed: Invalid username or password.")
            st.caption("Default seeds: `dean_exec`, `chair_mba`, `advisor_faculty`, `admin_sec` (Password: `Password123!`)")

if not st.session_state.authenticated:
    render_login_page()
    st.stop()


# ------------------------------------------------------------------
# AUTHENTICATED USER HEADER & NAVIGATION
# ------------------------------------------------------------------
user = st.session_state.user_info
st.sidebar.image("square_logo.png", use_container_width=True)
st.sidebar.title(f"👤 {user['full_name']}")
st.sidebar.caption(f"Role: **{user['role']}** | Permissions: **{'Read/Write' if user.get('can_edit', False) else 'View-Only'}**")

if user["role"] == "IT/Admin":
    if "admin_view" not in st.session_state: st.session_state.admin_view = "Dashboard"
    selected_admin_view = st.sidebar.radio(
        "IT Admin Settings", 
        ["Dashboard", "Global Instance Settings", "Schema Mapping Config", "Permissions & Audit Logs"]
    )
    st.session_state.admin_view = selected_admin_view
else:
    st.session_state.admin_view = "Dashboard"

# --- ROBUST CSS FOR RED LOG OUT BUTTON ---
st.markdown(
    """
    <style>
    /* Target all buttons inside the sidebar container */
    section[data-testid="stSidebar"] button {
        background-color: #b92b27 !important;
        color: white !important;
        border-color: #b92b27 !important;
    }
    /* Force text elements inside the sidebar button to be white */
    section[data-testid="stSidebar"] button p, 
    section[data-testid="stSidebar"] button span {
        color: white !important;
    }
    /* Hover state */
    section[data-testid="stSidebar"] button:hover {
        background-color: #FF4B4B !important;
        color: white !important;
        border-color: #FF4B4B !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

if st.sidebar.button("Log Out", use_container_width=True):
  st.session_state.authenticated = False
  st.session_state.user_info = None
  st.session_state.selected_student_email = None
  st.session_state.page = "list"
  st.rerun()

st.sidebar.markdown("---")

# ------------------------------------------------------------------
# STYLED BANNER HEADER
# ------------------------------------------------------------------
st.markdown(f"""
    <div style="
        background-color: #b92b27; 
        padding: 20px 25px; 
        border-radius: 8px; 
        color: white; 
        margin-bottom: 20px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
    ">
        <div style="
            font-size: 11px; 
            letter-spacing: 1.2px; 
            font-weight: 600; 
            margin-bottom: 6px; 
            opacity: 0.85;
        ">
            MAPÚA UNIVERSITY · ASU PATHWAYS · ETYSB
        </div>
        <div style="
            font-size: 24px; 
            font-weight: 700; 
            line-height: 1.3;
        ">
            Success Advisor Dashboard — {ACTIVE_PROGRAM} Program
        </div>
    </div>
""", unsafe_allow_html=True)


# ------------------------------------------------------------------
# VIEW: IT/ADMIN GLOBAL INSTANCE CONFIGURATION
# ------------------------------------------------------------------
def render_instance_settings():
    st.subheader("⚙️ Global Instance Settings")
    st.caption("Set the primary context for this dashboard instance. These settings apply globally to all users.")

    with st.form("instance_config_form"):
        new_program = st.text_input("Active Program Code (e.g., MBA, MSCS, BSB)", value=ACTIVE_PROGRAM)
        new_term = st.text_input("Current Academic Term Label", value=CURRENT_TERM_LABEL)
        
        st.info("Ensure the 'Active Program Code' exactly matches the code stored in your database's underlying program column.")
        
        if st.form_submit_button("Update Global Dashboard Settings", type="primary"):
            try:
                with conn.session as s:
                    s.execute(
                        text("UPDATE dashboard_config SET active_program = :ap, current_term = :ct WHERE id = 1;"),
                        {"ap": new_program.strip(), "ct": new_term.strip()}
                    )
                    s.commit()
                log_security_event(user["username"], user["role"], "INSTANCE_CONFIG_UPDATED", f"Changed program to {new_program} and term to {new_term}.")
                st.success("Global settings updated successfully! The dashboard will now automatically filter to the new program context.")
                load_dashboard_config.clear()
                load_students.clear()
                st.rerun()
            except Exception as e:
                st.error(f"Error updating configuration: {e}")


# ------------------------------------------------------------------
# VIEW: IT/ADMIN SCHEMA CONFIGURATION
# ------------------------------------------------------------------
def render_schema_mapping():
    st.subheader("⚙️ Dynamic Schema Field Mapping")
    st.caption("Map dashboard UI elements directly to the underlying SQL database columns. No code deployment required.")

    try:
        actual_cols_df = conn.query("SELECT column_name FROM information_schema.columns WHERE table_name = 'students';", ttl=0)
        actual_db_cols = actual_cols_df["column_name"].tolist()
        mappings_df = conn.query("SELECT id, dashboard_field, db_column, description FROM field_mappings ORDER BY id;", ttl=0)
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
                            text("UPDATE field_mappings SET db_column = :col WHERE id = :idx"),
                            {"col": row["db_column"], "idx": int(row["id"])}
                        )
                    s.commit()
                log_security_event(user["username"], user["role"], "SCHEMA_MAPPING_UPDATED", "IT Admin modified database schema mappings.")
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
                    log_security_event(user["username"], user["role"], "PERMISSIONS_UPDATED", f"Set can_edit={new_can_edit} for user '{target_username}'.")
                    st.success(f"Permissions successfully updated for {target_username}.")
                    st.rerun()
                except Exception as ex:
                    st.error(f"Error updating permissions: {ex}")

    with t_logs:
        st.caption("Live monitoring of authentication events, schema updates, and blocked write attempts.")
        logs_df = conn.query("SELECT timestamp, username, role, event_type, details FROM audit_logs ORDER BY timestamp DESC LIMIT 100;", ttl=0)
        st.dataframe(
            logs_df,
            column_config={
                "timestamp": st.column_config.DatetimeColumn("Timestamp", format="MMM DD, YYYY HH:mm:ss"),
                "event_type": st.column_config.TextColumn("Event Type"),
                "details": st.column_config.TextColumn("Log Details", width="large")
            },
            hide_index=True,
            use_container_width=True
        )

    with t_syslogs:
        st.caption("Tracks critical connection timeouts and schema errors (stored locally so they are accessible even if the database is completely offline).")
        import os
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


# ------------------------------------------------------------------
# VIEW 1: STUDENT ROSTER (Dashboard)
# ------------------------------------------------------------------
def render_student_list(df_all):
    st.markdown(f"#### Executive Summary — {CURRENT_TERM_LABEL}")
    total_students = len(df_all)
    cw_completed = len(df_all[df_all["coursework_display"] == "Completed"])
    exam_passed = len(df_all[df_all["comprehensive_exam_display"] == "Passed"])
    capstone_defended = len(df_all[df_all["capstone_display"] == "Defended for Completion"])

    # --- Calculations ---
   # On-Time Graduation Calculation
    evaluated_df = df_all[
        df_all["graduate_on_time"].notna() & 
        (df_all["graduate_on_time"].astype(str).str.strip() != "") &
        (~df_all["graduate_on_time"].astype(str).str.lower().isin(["n/a", "none"]))
    ]
    grad_numerator = len(evaluated_df[evaluated_df["graduate_on_time"].astype(str).str.lower().isin(["yes", "y", "true", "1"])])
    
    # Redefine the denominator as the total cohort to prevent the NameError
    grad_denominator = total_students
    on_time_rate = (grad_numerator / grad_denominator * 100) if grad_denominator > 0 else 0.0

    # Overall Completion Calculation
    fully_completed = len(
        df_all[
            (df_all["coursework_display"] == "Completed") & 
            (df_all["comprehensive_exam_display"] == "Passed") & 
            (df_all["capstone_display"] == "Defended for Completion")
        ]
    )
    completion_rate = int((fully_completed / total_students * 100)) if total_students > 0 else 0

    # Remaining Students & Lifecycle Breakdown ---
    remaining_students = int(total_students - fully_completed)
    
    # Sequential lifecycle gaps
    missing_coursework = len(df_all[df_all["coursework_display"] != "Completed"])
    missing_exam = len(df_all[(df_all["coursework_display"] == "Completed") & (df_all["comprehensive_exam_display"] != "Passed")])
    missing_capstone = len(df_all[(df_all["coursework_display"] == "Completed") & (df_all["comprehensive_exam_display"] == "Passed") & (df_all["capstone_display"] != "Defended for Completion")])

    # --- ROW 1: Raw Milestone Counts & Refresh Button ---
    top_c1, top_c2, top_c3, top_c4, top_refresh = st.columns([1, 1, 1, 1, 0.8])
    
    top_c1.metric("Total Cohort", total_students)
    top_c2.metric("Coursework", cw_completed)
    top_c3.metric("Comp Exam", exam_passed)
    top_c4.metric("Capstones", capstone_defended)

    with top_refresh:
        st.write("")
        if st.button("🔄 Refresh", use_container_width=True):
            load_students.clear()
            fetch_student_courses.clear()
            fetch_student_milestones.clear()
            st.rerun()

    st.write("") # Vertical buffer

   # --- ROW 2: Executive Percentages in Styled Cards ---
    st.markdown(
        """
        <style>
        /* Card styling for the main metric box */
        div[data-testid="stMetric"] {
            /* Uses Streamlit's dynamic theme variables instead of hardcoded white */
            background-color: var(--background-color);
            border: 1px solid var(--secondary-background-color);
            border-radius: 8px;
            padding: 15px 20px;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -1px rgba(0,0,0,0.06);
            /* This preserves your red top border */
            border-top: 4px solid #c8102e; 
        }
        
        /* Style the Title (e.g. ON-TIME GRAD RATE) */
        div[data-testid="stMetricLabel"] p {
            text-transform: uppercase !important;
            font-size: 0.75rem !important;
            font-weight: 600 !important;
            /* Dynamically adapts to light/dark mode */
            color: var(--faded-text-color) !important;
            letter-spacing: 0.5px !important;
        }
        
        /* Style the Main Number (e.g. 58.0%) */
        div[data-testid="stMetricValue"] div {
            font-size: 2rem !important;
            font-weight: 700 !important;
            /* Dynamically adapts to light/dark mode */
            color: var(--text-color) !important;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

    bot_c1, bot_c2, spacer = st.columns([1.2, 1.2, 3.4])
    
    bot_c1, bot_c2, bot_c3, spacer = st.columns([1.2, 1.2, 1.2, 2.2])
    
    bot_c1.metric(
        label="On-Time Grad Rate", 
        value=f"{on_time_rate:.1f}%", 
        delta=f"{grad_numerator} of {total_students} total students",
        delta_color="off",
        help=f"**Calculation Logic:**\n\n*Numerator:* Students flagged as graduating on time ({grad_numerator})\n*Denominator:* Total students in the cohort ({total_students})\n*Period:* {CURRENT_TERM_LABEL}"
    )

    bot_c2.metric(
        label="Overall Completion",
        value=f"{completion_rate}%",
        delta=f"{fully_completed} out of {total_students} cohort",
        delta_color="off",
        help="Percentage of the total cohort that has completed coursework, passed the comprehensive exam, and defended the capstone."
    )

    # --- NEW: Remaining Students Tile & Breakdown ---
    bot_c3.metric(
        label="Remaining Students",
        value=remaining_students,
        delta="Active in pipeline",
        delta_color="off",
        help=f"**Pending Milestones (Sequential):**\n\n* **{missing_coursework}** needing Coursework\n* **{missing_exam}** needing Comp Exam\n* **{missing_capstone}** needing Capstone Defense\n\n*(Total enrolled cohort minus fully completed)*"
    )
    
    st.divider()

    st.subheader(f"Student Roster & Lifecycle Progress ({ACTIVE_PROGRAM})")
    search_col, cohort_col, sort_col = st.columns([2, 1, 1])
    with search_col: search_term = st.text_input("Search by name or student ID", placeholder="e.g. Adrian Santos or 2026124837")
    with cohort_col:
        valid_cohorts = sorted([str(c) for c in df_all["cohort"].dropna().unique().tolist()])
        selected_cohort = st.selectbox("Filter by cohort / intake", ["All"] + valid_cohorts)
    with sort_col:
        sort_option = st.selectbox("Sort by", ["Name", "Student ID", "Overall Status"])

    filtered = df_all.copy()
    if selected_cohort != "All": filtered = filtered[filtered["cohort"].astype(str) == selected_cohort]
    if search_term:
        term = search_term.strip().lower()
        filtered = filtered[
            filtered["full_name"].astype(str).str.lower().str.contains(term, na=False) | 
            filtered["student_number"].astype(str).str.lower().str.contains(term, na=False)
        ]

    sort_map = {"Name": "full_name", "Student ID": "student_number", "Overall Status": "overall_status"}
    filtered = filtered.sort_values(sort_map[sort_option]).reset_index(drop=True)

    if filtered.empty:
        st.info(f"No results found for the {ACTIVE_PROGRAM} program. Try a different search term, or verify the database mappings.")
        return

    # NOTE: st.dataframe renders through a canvas-based grid, so it cannot draw real
    # HTML pill badges (rounded borders, padding) inside cells — only cell-level
    # color/background styling via a pandas Styler. That's what we apply below to
    # get a "highlighted" look (bold colored text on a tinted background) while
    # keeping click-to-navigate selection working.

    st.markdown("""
        <style>
            [data-testid="stDataFrame"] td[style*="background-color"] {
                display: inline-flex !important;
                align-items: center !important;
                margin: 6px 4px !important;
                padding: 3px 12px !important;
                border-radius: 999px !important;
                border: 1.5px solid currentColor !important;
                background-clip: padding-box !important;
            }
        </style>
    """, unsafe_allow_html=True)
    
    # 1. Keep names as clean plain text strings
    display_df = filtered[[
        "full_name", "student_number", "cohort", "overall_status",
        "coursework_display", "comprehensive_exam_display", "capstone_display", "adviser"
    ]].rename(columns={
        "full_name": "Name", "student_number": "Student ID", "cohort": "Cohort",
        "overall_status": "Overall Status", "coursework_display": "Coursework",
        "comprehensive_exam_display": "Comp Exam", "capstone_display": "Capstone", "adviser": "Adviser"
    })

    # 2. Add a helper styler for making the Name column bold
    def style_bold_name(series):
        return ["font-weight: bold;" for _ in series]

    # 3. Apply both the name style and the status highlighters to the styler
    styled_df = (
        display_df.style
        .apply(style_bold_name, subset=["Name"])
        .apply(make_status_styler("coursework"), subset=["Coursework"])
        .apply(make_status_styler("comprehensive_exam"), subset=["Comp Exam"])
        .apply(make_status_styler("capstone"), subset=["Capstone"])
    )

    table_key = f"student_table_{st.session_state.table_key_counter}"

# ---> PASTE BLOCK 2 HERE <---
    st.subheader("Program Roster", anchor="student-roster-table")
    
    event = st.dataframe(
        styled_df,
        key=table_key,
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-cell",
        column_config={
            # Keep Name as a standard TextColumn since styling handles the boldness
            "Name": st.column_config.TextColumn("Student Name", width="medium"),
            "Student ID": st.column_config.TextColumn("Student ID", width="small"),
            "Cohort": st.column_config.TextColumn("Cohort", width="small"),
            "Overall Status": st.column_config.TextColumn("Status", width="small"),
            "Coursework": st.column_config.TextColumn("Coursework", width="small"),
            "Comp Exam": st.column_config.TextColumn("Comp Exam", width="small"),
            "Capstone": st.column_config.TextColumn("Capstone", width="medium"),
            "Adviser": st.column_config.TextColumn("Adviser", width="medium"),
        }
    )

    selected_row_idx = None
    if event and hasattr(event, "selection") and event.selection.get("cells"):
        selected_row_idx = event.selection["cells"][0][0]

    if selected_row_idx is not None:
        selected_record = filtered.iloc[selected_row_idx]
        selected_email = selected_record.get("student_email") or selected_record.get("email")
        if selected_email:
            go_to_profile(selected_email)
            st.rerun()

    st.caption("Tip: click on any cell (e.g. Student Name) to inspect the candidate's profile.")


# ------------------------------------------------------------------
# VIEW 2: STUDENT PROFILE (Read / Write Record Inspector)
# ------------------------------------------------------------------
def render_student_profile(df_all):
    st.subheader("Student Profile Inspector")

    if st.button("← Back to student list"):
        go_to_list()
        st.rerun()

    email = st.session_state.selected_student_email
    match = df_all[df_all["student_email"] == email]
    
    if match.empty:
        st.warning("Student record not found in the active program. Returning to directory.")
        go_to_list()
        st.rerun()
        return

    student = match.iloc[0]

    st.markdown(f"### {student['full_name']}")
    info1, info2, info3, info4 = st.columns(4)
    info1.write(f"**Student ID:** `{student['student_number']}`")
    info2.write(f"**Email:** [{student['student_email']}](mailto:{student['student_email']})")
    info3.write(f"**Cohort:** {student['cohort'] if pd.notna(student['cohort']) else 'N/A'}")
    info4.markdown(f"**Overall Status:** {status_badge(student['overall_status'])}", unsafe_allow_html=True)

    st.divider()
    st.markdown("#### Program Lifecycle Summary")

    milestones_df = fetch_student_milestones(student["student_number"])
    
    ce_updated = "N/A"
    cap_updated = "N/A"
    if not milestones_df.empty:
        for _, m_row in milestones_df.iterrows():
            if m_row.get("milestone_type") == "comprehensive_exam":
                ce_updated = m_row.get("Last Updated", "N/A")
            elif m_row.get("milestone_type") == "capstone":
                cap_updated = m_row.get("Last Updated", "N/A")

    cw_updated = student.get('coursework_updated_at', 'N/A')

    p1, p2, p3 = st.columns(3)
    with p1:
        with st.container(border=True):
            st.markdown("**📚 Coursework Stage**")
            st.markdown(student["coursework_indicator"], unsafe_allow_html=True)
            st.caption(f"Status: {student['coursework_display']}")
            st.caption(f"🕒 Last Updated: `{cw_updated}`") # <--- Added coursework timestamp
            
    with p2:
        with st.container(border=True):
            st.markdown("**📝 Comprehensive Examination**")
            st.markdown(student["exam_indicator"], unsafe_allow_html=True)
            st.caption(f"Status: {student['comprehensive_exam_display']}")
            st.caption(f"🕒 Last Updated: `{ce_updated}`") # <--- Added comp exam timestamp
            
    with p3:
        with st.container(border=True):
            st.markdown("**🎓 Capstone & Defense**")
            st.markdown(student["capstone_indicator"], unsafe_allow_html=True)
            st.caption(f"Adviser: **{student.get('adviser') or 'Unassigned'}**")
            st.caption(f"🕒 Last Updated: `{cap_updated}`") # <--- Added capstone timestamp

    st.divider()
    tab_courses, tab_milestones, tab_remarks = st.tabs(["📖 Course Progress", "🚩 Lifecycle Milestones", "📝 Remarks & Admin Actions"])

    with tab_courses:
        st.markdown("##### Enrolled Curriculum & Course Records")
        courses_df = fetch_student_courses(student["student_number"])
        if not courses_df.empty: st.dataframe(courses_df, hide_index=True, use_container_width=True)
        else: st.info("No course enrollment records populated for this student.")

    with tab_milestones:
        st.markdown("##### Milestone Clearances")
        milestones_df = fetch_student_milestones(student["student_number"])
        if not milestones_df.empty: st.dataframe(milestones_df, hide_index=True, use_container_width=True)
        else: st.info("No milestone events recorded in `student_milestones`.")

    with tab_remarks:
        st.markdown("##### Graduation Tracking & Advisor Notes")
        c1, c2 = st.columns(2)
        
        raw_ontime = student.get("graduate_on_time")
        display_ontime = str(raw_ontime).strip() if pd.notna(raw_ontime) and str(raw_ontime).strip().lower() not in ("nan", "none", "") else "Under Evaluation"
        c1.write(f"**Graduating On Time:** {display_ontime}")
        
        raw_term = student.get("graduate_date_term_sy")
        display_term = str(raw_term).strip() if pd.notna(raw_term) and str(raw_term).strip().lower() not in ("nan", "none", "") else "To Be Determined (TBD)"
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
            cap_opts = ["In-Progress", "Defended for Completion", "Incomplete"]

            def get_idx(val, lst): return lst.index(val) if val in lst else 0

            c_form1, c_form2 = st.columns(2)
            with c_form1:
                new_cw = st.selectbox("Coursework Status", cw_opts, index=get_idx(student["coursework_display"], cw_opts))
                new_ce = st.selectbox("Comp Exam Status", ce_opts, index=get_idx(student["comprehensive_exam_display"], ce_opts))
            with c_form2:
                new_cap = st.selectbox("Capstone Status", cap_opts, index=get_idx(student["capstone_display"], cap_opts))
                new_adv = st.selectbox("Primary Adviser", adv_list, index=get_idx(curr_adv, adv_list))

            existing_remarks = str(student["remarks"]) if pd.notna(student["remarks"]) else ""
            new_remarks = st.text_area("Administrative Remarks", value=existing_remarks)
            
            submitted = st.form_submit_button("Save Changes to Database", type="primary")

            if submitted:
                if not is_authorized_editor:
                    log_security_event(user["username"], user["role"], "UNAUTHORIZED_WRITE_ATTEMPT", f"Blocked attempt to update record for Student ID {student['student_number']} without edit permissions.")
                    st.error("⛔ Access Denied: Your account role is View-Only. This unauthorized attempt has been logged.")
                else:
                    ce_db_map = {"Passed": "done", "In-Progress": "not yet taken", "Incomplete": "incomplete"}
                    cap_db_map = {"Defended for Completion": "done", "In-Progress": "not yet done", "Incomplete": "incomplete"}
                    
                    try:
                        with conn.session as s:
                            adv_id = None
                            if new_adv != "Unassigned":
                                adv_res = s.execute(text("SELECT adviser_id FROM advisers WHERE full_name = :name"), {"name": new_adv}).fetchone()
                                if adv_res: adv_id = adv_res[0]
                                    
                            # Update coursework with current timestamp (NOW())
                            s.execute(
                                text("""
                                    UPDATE students_normalized 
                                    SET coursework_status = :cw, 
                                        remarks = :rem,
                                        adviser_id = :adv,
                                        updated_at = NOW()
                                    WHERE student_number = :sn;
                                """),
                                {"cw": new_cw.upper(), "rem": new_remarks, "adv": adv_id, "sn": int(student["student_number"])}
                            )
                            
                            # Update or Insert comprehensive exam milestone with current timestamp
                            s.execute(
                                text("""
                                    INSERT INTO student_milestones (student_number, milestone_type, status, updated_at) 
                                    VALUES (:sn, 'comprehensive_exam', :st, NOW()) 
                                    ON CONFLICT (student_number, milestone_type) 
                                    DO UPDATE SET status = EXCLUDED.status, updated_at = NOW();
                                """),
                                {"sn": int(student["student_number"]), "st": ce_db_map.get(new_ce, "incomplete")}
                            )
                            
                            # Update or Insert capstone milestone with current timestamp
                            s.execute(
                                text("""
                                    INSERT INTO student_milestones (student_number, milestone_type, status, updated_at) 
                                    VALUES (:sn, 'capstone', :st, NOW()) 
                                    ON CONFLICT (student_number, milestone_type) 
                                    DO UPDATE SET status = EXCLUDED.status, updated_at = NOW();
                                """),
                                {"sn": int(student["student_number"]), "st": cap_db_map.get(new_cap, "incomplete")}
                            )
                            
                            s.commit()
                        
                        log_security_event(user["username"], user["role"], "STUDENT_RECORD_UPDATED", f"Modified record for Student ID {student['student_number']} (CW: {new_cw}, Exam: {new_ce}, Capstone: {new_cap}).")
                        st.success("Record successfully updated in Supabase!")
                        load_students.clear()
                        fetch_student_milestones.clear()
                        st.rerun()
                        
                    except Exception as err:
                        st.error(f"Write operation failed: {err}")


# ------------------------------------------------------------------
# FOOTER HELPER
# ------------------------------------------------------------------
def render_footer(last_sync_time):
    # REPLACE datetime.now() WITH ZoneInfo:
    current_render_time = datetime.now(ZoneInfo("Asia/Manila")).strftime("%B %d, %Y %I:%M %p")
    
    st.divider()
    st.markdown(
        f"<div style='text-align: center; color: gray; font-size: 0.8rem; line-height: 1.6; padding-bottom: 20px;'>"
        f"Mapúa University · ETYSB Success Advisor Dashboard · Streamlit build for OBE Agile Pilot Sprint Review<br>"
        f"Data source: Supabase (Live Normalized DB) · Data last synced: <b>{last_sync_time}</b> · Rendered <b>{current_render_time}</b>."
        f"</div>",
        unsafe_allow_html=True
    )


# ------------------------------------------------------------------
# MASTER ROUTER (With Sync Error Handling)
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
        # Step A: Fetch dataset and synchronization timestamp
        df_all, last_sync = load_students(ACTIVE_PROGRAM)
        
        st.session_state.consecutive_sync_failures = 0
        st.caption(f"🕒 **Data Last Synchronized:** `{last_sync}`")
        
        # Step B: Render the active page based on session state
        if st.session_state.page == "profile" and st.session_state.selected_student_email:
            render_student_profile(df_all)
        else:
            render_student_list(df_all)

        # Step C: The Footer Call
        # Placed here so it runs after the main content, regardless of 
        # whether the user is on the list view or a specific student's profile.
        render_footer(last_sync)

    except Exception as e:
        # If anything in Step A, B, or C fails, catch and log it gracefully
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
