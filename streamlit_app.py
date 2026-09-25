"""
MBA Program Dashboard — Project Pulse
======================================
Streamlit app for Program Chairs, Faculty/Program Advisors, and the Dean.
Features:
- Clean cell-click navigation (leftmost checkbox column removed)
- Strict View-Only vs Read/Write permission enforcement
- Admin panel for User Permission Management & Security Audit Logs
- Live 'Data Last Synchronized' timestamp
- Dynamic Schema Mapping (IT/Admin configuration screen)
- Top-level Executive Summary cards
"""

import streamlit as st
import pandas as pd
from sqlalchemy import text
from datetime import datetime

st.set_page_config(page_title="Project Pulse — MBA Dashboard", page_icon="🎓", layout="wide")

CURRENT_TERM_LABEL = "1T, A.Y. 2024–2025"

# ------------------------------------------------------------------
# ETHICAL VISUALIZATION SPECIFICATION (CIS310 Standards)
# ------------------------------------------------------------------
PALETTE = {
    "green": {"hex": "#009E73", "symbol": "🟢", "description": "Passed / Completed / Defended (On Track)"},
    "yellow": {"hex": "#E69F00", "symbol": "🟡", "description": "In-Progress / Pending Clearance"},
    "red": {"hex": "#D55E00", "symbol": "🔴", "description": "Cancelled / Incomplete / Action Required"},
    "gray": {"hex": "#999999", "symbol": "⚪", "description": "Not Started / Not Applicable / Unknown"},
}

STAGE_THRESHOLDS = {
    "coursework": {"green": ["Completed"], "yellow": ["Pending"], "red": ["Cancelled"]},
    "comprehensive_exam": {"green": ["Passed"], "yellow": ["In-Progress"], "red": ["Incomplete"]},
    "capstone": {"green": ["Defended for Completion"], "yellow": ["In-Progress"], "red": ["Incomplete"]},
}

COURSEWORK_MAP = {"completed": "Completed", "cancelled": "Cancelled", "pending": "Pending"}
COMPREHENSIVE_EXAM_MAP = {"done": "Passed", "incomplete": "Incomplete", "not yet taken": "In-Progress", "abs/failed": "Incomplete"}
CAPSTONE_MAP = {"done": "Defended for Completion", "not yet done": "In-Progress", "not yet taken": "In-Progress", "in current load": "In-Progress", "incomplete": "In-Progress", "n/a": "In-Progress"}

def map_status(raw_value, mapping, default="Unknown"):
    if raw_value is None or pd.isna(raw_value): return default
    return mapping.get(str(raw_value).strip().lower(), default)

def get_stage_badge(stage: str, status_label: str) -> str:
    rule = STAGE_THRESHOLDS.get(stage, {})
    clean_label = str(status_label).strip()
    if clean_label in rule.get("green", []): return f"{PALETTE['green']['symbol']} {clean_label}"
    if clean_label in rule.get("yellow", []): return f"{PALETTE['yellow']['symbol']} {clean_label}"
    if clean_label in rule.get("red", []): return f"{PALETTE['red']['symbol']} {clean_label}"
    return f"{PALETTE['gray']['symbol']} {clean_label}"

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
        st.sidebar.error(f"Audit log error: {err}")

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
# DATA LOADERS (DYNAMICALLY MAPPED)
# ------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner="Loading mapped student roster...")
def load_students() -> tuple[pd.DataFrame, str]:
    df = conn.query("SELECT * FROM students;", ttl=0)
    
    try:
        mapping_df = conn.query("SELECT dashboard_field, db_column FROM field_mappings;")
        db_to_app_map = dict(zip(mapping_df["db_column"], mapping_df["dashboard_field"]))
        df = df.rename(columns=db_to_app_map)
    except Exception as e:
        st.sidebar.warning("Schema mapping table missing or misconfigured. Falling back to default names.")

    expected_cols = [
        "first_name", "last_name", "student_number", "cohort", "coursework_status", 
        "comprehensive_exam", "capstone", "graduate_on_time", "graduate_date_term_sy", 
        "adviser", "remarks", "student_email"
    ]
    for col in expected_cols:
        if col not in df.columns: df[col] = None

    if "full_name" not in df.columns or df["full_name"].isna().all():
        df["full_name"] = (df["first_name"].fillna("") + " " + df["last_name"].fillna("")).str.strip()

    df = df.sort_values(by=["last_name", "first_name"], na_position="last").reset_index(drop=True)

    if "overall_status" not in df.columns or df["overall_status"].isna().all():
        df["overall_status"] = df.apply(overall_status, axis=1)

    df["coursework_display"] = df["coursework_status"].apply(lambda v: map_status(v, COURSEWORK_MAP))
    df["comprehensive_exam_display"] = df["comprehensive_exam"].apply(lambda v: map_status(v, COMPREHENSIVE_EXAM_MAP))
    df["capstone_display"] = df["capstone"].apply(lambda v: map_status(v, CAPSTONE_MAP))

    df["coursework_indicator"] = df["coursework_display"].apply(lambda v: get_stage_badge("coursework", v))
    df["exam_indicator"] = df["comprehensive_exam_display"].apply(lambda v: get_stage_badge("comprehensive_exam", v))
    df["capstone_indicator"] = df["capstone_display"].apply(lambda v: get_stage_badge("capstone", v))

    sync_time = datetime.now().strftime("%B %d, %Y at %I:%M:%S %p")
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
            INITCAP(REPLACE(milestone_type, '_', ' ')) AS "Milestone",
            INITCAP(status) AS "Recorded Status"
        FROM student_milestones
        WHERE student_number = {int(student_number)}
        ORDER BY milestone_type ASC;
    """
    try: return conn.query(query, ttl=0)
    except Exception: return pd.DataFrame()

def status_badge(label: str) -> str:
    colors = {
        "Completed": "green", "Passed": "green", "Defended for Completion": "green",
        "Graduated": "green", "Active": "blue", "Enrolled": "green", "In-Progress": "orange",
        "Pending": "orange", "Conditionally Enrolled": "orange", "Cancelled": "red", 
        "Incomplete": "red", "Unknown": "gray",
    }
    color = colors.get(str(label), "gray")
    return f":{color}[**{label}**]"


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
    st.markdown("<h1 style='text-align: center;'>🧑‍🎓 Project Pulse Student Management Portal</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: gray;'>Group 1 Dashboard Demo</p>", unsafe_allow_html=True)
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
st.sidebar.title(f"👤 {user['full_name']}")
st.sidebar.caption(f"Role: **{user['role']}** | Permissions: **{'Read/Write' if user.get('can_edit', False) else 'View-Only'}**")

if user["role"] == "IT/Admin":
    if "admin_view" not in st.session_state: st.session_state.admin_view = "Dashboard"
    selected_admin_view = st.sidebar.radio("IT Admin Settings", ["Dashboard", "Schema Mapping Config", "Permissions & Audit Logs"])
    st.session_state.admin_view = selected_admin_view
else:
    st.session_state.admin_view = "Dashboard"

if st.sidebar.button("🚪 Log Out", use_container_width=True):
    st.session_state.authenticated = False
    st.session_state.user_info = None
    st.session_state.selected_student_email = None
    st.session_state.page = "list"
    st.rerun()
st.sidebar.markdown("---")

st.title("🧑‍🎓 Project Pulse — MBA Program Dashboard")


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
    
    # ADDED: Third tab for system sync failures
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

    # ADDED: Logic to view server-side fallback logs
    with t_syslogs:
        st.caption("Tracks critical connection timeouts and schema errors (stored locally so they are accessible even if the database is completely offline).")
        import os
        if os.path.exists("sync_error_log.txt"):
            with open("sync_error_log.txt", "r") as f:
                logs = f.readlines()
            
            if logs:
                st.code("".join(logs[-15:]), language="log")  # Show last 15 lines
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
    st.markdown(f"#### 🏛️ Executive Summary — {CURRENT_TERM_LABEL}")
    total_students = len(df_all)
    cw_completed = len(df_all[df_all["coursework_display"] == "Completed"])
    exam_passed = len(df_all[df_all["comprehensive_exam_display"] == "Passed"])
    capstone_defended = len(df_all[df_all["capstone_display"] == "Defended for Completion"])

    k1, k2, k3, k4, k_refresh = st.columns([1.2, 1.2, 1.2, 1.2, 0.8])
    k1.metric("Total Cohort", total_students)
    k2.metric("Coursework Cleared", cw_completed, delta=f"{(cw_completed / total_students * 100):.1f}%" if total_students else None)
    k3.metric("Passed Comp Exam", exam_passed, delta=f"{(exam_passed / total_students * 100):.1f}%" if total_students else None)
    k4.metric("Defended Capstones", capstone_defended, delta=f"{(capstone_defended / total_students * 100):.1f}%" if total_students else None)
    
    with k_refresh:
        st.write("")
        if st.button("🔄 Refresh", use_container_width=True):
            load_students.clear()
            fetch_student_courses.clear()
            fetch_student_milestones.clear()
            st.rerun()

    with st.expander("ℹ️ Dean's Status Indicator Legend (CIS310 Accessible Palette)", expanded=False):
        l1, l2, l3, l4 = st.columns(4)
        l1.markdown(f"**{PALETTE['green']['symbol']} Green**\n{PALETTE['green']['description']}")
        l2.markdown(f"**{PALETTE['yellow']['symbol']} Amber**\n{PALETTE['yellow']['description']}")
        l3.markdown(f"**{PALETTE['red']['symbol']} Red**\n{PALETTE['red']['description']}")
        l4.markdown(f"**{PALETTE['gray']['symbol']} Gray**\n{PALETTE['gray']['description']}")
    st.divider()

    st.subheader("Student Roster & Lifecycle Progress")
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
        st.info("No results found. Try a different search term or cohort.")
        return

    display_df = filtered[[
        "full_name", "student_number", "cohort", "overall_status",
        "coursework_indicator", "exam_indicator", "capstone_indicator", "adviser"
    ]].rename(columns={
        "full_name": "Name", "student_number": "Student ID", "cohort": "Cohort",
        "overall_status": "Overall Status", "coursework_indicator": "Coursework",
        "exam_indicator": "Comp Exam", "capstone_indicator": "Capstone", "adviser": "Adviser"
    })

    table_key = f"student_table_{st.session_state.table_key_counter}"

    # selection_mode="single-cell" removes the circular checkbox column on the left edge
    event = st.dataframe(
        display_df,
        key=table_key,
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-cell",
        column_config={
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

    # Listen directly for cell selection
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
    # Advisor Caseload & Cohort Filter
    current_advisor = st.session_state.get("advisor_id") or st.session_state.get("user_email")
    user_role = st.session_state.get("role", "Faculty")

    advisor_col = "advisor_id" if "advisor_id" in df_all.columns else "advisor_email"
    cohort_col = "cohort" if "cohort" in df_all.columns else "cohort_year"

    has_assignments = (
        advisor_col in df_all.columns 
        and current_advisor in df_all[advisor_col].dropna().values
    )

    f_col1, f_col2 = st.columns([1, 1])

    with f_col1:
        default_index = 0 if (user_role in ["Faculty", "Advisor"] and has_assignments) else 1
        caseload_mode = st.radio(
            "Filter Scope:",
            options=["My Advisees", "All Students"],
            index=default_index,
            horizontal=True,
            key="advisor_caseload_filter"
        )

    with f_col2:
        if cohort_col in df_all.columns:
            cohort_options = ["All Cohorts"] + sorted(df_all[cohort_col].dropna().unique().tolist())
            selected_cohort = st.selectbox("Cohort / Year:", options=cohort_options, key="cohort_filter")
        else:
            selected_cohort = "All Cohorts"

    if caseload_mode == "My Advisees":
        if not has_assignments:
            st.info(
                "ℹ️ **No Advisees Assigned:** There are currently no students assigned to your profile in the database. "
                "If you believe this is an error, please reach out to your department administrator."
            )
            return
        df_all = df_all[df_all[advisor_col] == current_advisor]

    if selected_cohort != "All Cohorts" and cohort_col in df_all.columns:
        df_all = df_all[df_all[cohort_col] == selected_cohort]

    if df_all.empty:
        st.warning("No students found matching the selected criteria.")
        return
    st.subheader("Student Profile Inspector")

    if st.button("← Back to student list"):
        go_to_list()
        st.rerun()

    email = st.session_state.selected_student_email
    match = df_all[df_all["student_email"] == email]
    
    if match.empty:
        st.warning("Student record not found. Returning to directory.")
        go_to_list()
        st.rerun()
        return

    student = match.iloc[0]

    st.markdown(f"### {student['full_name']}")
    info1, info2, info3, info4 = st.columns(4)
    info1.write(f"**Student ID:** `{student['student_number']}`")
    info2.write(f"**Email:** [{student['student_email']}](mailto:{student['student_email']})")
    info3.write(f"**Cohort:** {student['cohort'] if pd.notna(student['cohort']) else 'N/A'}")
    info4.write(f"**Overall Status:** {status_badge(student['overall_status'])}")

    st.divider()
    st.markdown("#### Program Lifecycle Summary")

    p1, p2, p3 = st.columns(3)
    with p1:
        with st.container(border=True):
            st.markdown("**📚 Coursework Stage**")
            st.markdown(student["coursework_indicator"])
            st.caption(f"Status: {student['coursework_display']}")
    with p2:
        with st.container(border=True):
            st.markdown("**📝 Comprehensive Examination**")
            st.markdown(student["exam_indicator"])
            st.caption(f"Status: {student['comprehensive_exam_display']}")
    with p3:
        with st.container(border=True):
            st.markdown("**🎓 Capstone & Defense**")
            st.markdown(student["capstone_indicator"])
            st.caption(f"Adviser: **{student.get('adviser') or 'Unassigned'}**")

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

        # Clean fallback for Graduating On Time
        raw_ontime = student.get("graduate_on_time")
        display_ontime = (
            str(raw_ontime).strip()
            if pd.notna(raw_ontime) and str(raw_ontime).strip().lower() not in ("nan", "none", "")
            else "Under Evaluation"
        )
        c1.write(f"**Graduating On Time:** {display_ontime}")

        # Clean fallback for Target Graduation Term: To Be Determined (TBD)
        raw_term = student.get("graduate_date_term_sy")
        display_term = (
            str(raw_term).strip()
            if pd.notna(raw_term) and str(raw_term).strip().lower() not in ("nan", "none", "")
            else "To Be Determined (TBD)"
        )
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
                    log_security_event(
                        username=user["username"],
                        role=user["role"],
                        event_type="UNAUTHORIZED_WRITE_ATTEMPT",
                        details=f"Blocked attempt to update record for Student ID {student['student_number']} without edit permissions."
                    )
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
                                    
                            s.execute(
                                text("""
                                    UPDATE students_normalized 
                                    SET coursework_status = :cw, 
                                        remarks = :rem,
                                        adviser_id = :adv
                                    WHERE student_number = :sn;
                                """),
                                {"cw": new_cw.upper(), "rem": new_remarks, "adv": adv_id, "sn": int(student["student_number"])}
                            )
                            
                            s.execute(
                                text("""
                                    INSERT INTO student_milestones (student_number, milestone_type, status) 
                                    VALUES (:sn, 'comprehensive_exam', :st) 
                                    ON CONFLICT (student_number, milestone_type) 
                                    DO UPDATE SET status = EXCLUDED.status;
                                """),
                                {"sn": int(student["student_number"]), "st": ce_db_map.get(new_ce, "incomplete")}
                            )
                            
                            s.execute(
                                text("""
                                    INSERT INTO student_milestones (student_number, milestone_type, status) 
                                    VALUES (:sn, 'capstone', :st) 
                                    ON CONFLICT (student_number, milestone_type) 
                                    DO UPDATE SET status = EXCLUDED.status;
                                """),
                                {"sn": int(student["student_number"]), "st": cap_db_map.get(new_cap, "incomplete")}
                            )
                            
                            s.commit()
                        
                        log_security_event(
                            username=user["username"],
                            role=user["role"],
                            event_type="STUDENT_RECORD_UPDATED",
                            details=f"Modified record for Student ID {student['student_number']} (CW: {new_cw}, Exam: {new_ce}, Capstone: {new_cap})."
                        )
                        st.success("Record successfully updated in Supabase!")
                        load_students.clear()
                        fetch_student_milestones.clear()
                        st.rerun()
                        
                    except Exception as err:
                        st.error(f"Write operation failed: {err}")


# ------------------------------------------------------------------
# MASTER ROUTER (With Sync Error Handling)
# ------------------------------------------------------------------
# Initialize consecutive failure tracking
if "consecutive_sync_failures" not in st.session_state:
    st.session_state.consecutive_sync_failures = 0

if st.session_state.admin_view == "Schema Mapping Config":
    render_schema_mapping()
elif st.session_state.admin_view == "Permissions & Audit Logs":
    render_permissions_and_logs()
else:
    try:
        # Attempt to pull data
        df_all, last_sync = load_students()
        
        # Reset counter on a successful sync
        st.session_state.consecutive_sync_failures = 0
        st.caption(f"🕒 **Data Last Synchronized:** `{last_sync}`")
        
        # -------------------------------------------------------------
        # GLOBAL TERM FILTER (Sidebar / Global Navigation)
        # -------------------------------------------------------------
        # 1. Identify distinct terms sorted newest to oldest
        if "term" in df_all.columns:
            term_options = sorted(df_all["term"].dropna().unique().tolist(), reverse=True)
        else:
            term_options = []

        if term_options:
            # Set default to active/current term (e.g., first element or specific active flag)
            current_term_default = term_options[0]

            if "selected_term" not in st.session_state:
                st.session_state["selected_term"] = current_term_default

            # Global selector in the sidebar so it's accessible across all views
            selected_term = st.sidebar.selectbox(
                "📅 Academic Term / Semester",
                options=term_options,
                index=term_options.index(st.session_state["selected_term"])
                if st.session_state["selected_term"] in term_options
                else 0,
                key="global_term_selector",
            )
            st.session_state["selected_term"] = selected_term

            # 2. Filter dataset so all views, KPIs, and charts sync consistently
            df_filtered = df_all[df_all["term"] == selected_term].copy()
        else:
            df_filtered = df_all.copy()

        # -------------------------------------------------------------
        # VIEW ROUTER (Pass df_filtered to ensure synchronization)
        # -------------------------------------------------------------
        if st.session_state.page == "profile" and st.session_state.selected_student_email:
            render_student_profile(df_filtered)
        else:
            render_student_list(df_filtered)
        if st.session_state.page == "profile" and st.session_state.selected_student_email:
            render_student_profile(df_all)
        else:
            render_student_list(df_all)

    except Exception as e:
        # 1. Increment the failure counter
        st.session_state.consecutive_sync_failures += 1
        error_msg = str(e)
        
        # 2. Write to local server log
        timestamp = datetime.now().strftime("%B %d, %Y at %I:%M:%S %p")
        with open("sync_error_log.txt", "a") as f:
            f.write(f"[{timestamp}] SYNC_FAILED: {error_msg}\n")
            
        # 3. Repeated failures trigger warning banner
        if st.session_state.consecutive_sync_failures >= 3:
            st.error(f"🚨 **CRITICAL WARNING:** The dashboard has failed to synchronize with the database {st.session_state.consecutive_sync_failures} consecutive times. Please contact IT/Admin immediately to check the integration logs.", icon="🚨")
        else:
            st.warning("⚠️ **Warning:** A data synchronization error occurred. The system will retry on your next action.")
            
        # Display the immediate reason
        st.error(f"**Detailed Error:** `{error_msg}`")
        st.stop()
