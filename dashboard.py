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
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode
# Generic title
st.set_page_config(page_title="Project Pulse — Program Dashboard", page_icon="🎓", layout="wide")
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
        SELECT user_id, username, full_name, role, can_edit, can_import, is_active
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
# TERM MANAGEMENT
# ------------------------------------------------------------------
def load_available_terms():
    """
    Load academic terms that are currently available to dashboard users.

    Future terms are excluded until their start date.
    The current term is determined from the term table dates.
    """

    today = datetime.now(ZoneInfo("Asia/Manila")).date()

    terms_df = conn.query(
        """
        SELECT
            term_id,
            term_code,
            start_date,
            end_date
        FROM term
        WHERE start_date IS NOT NULL
          AND start_date <= :today
        ORDER BY start_date DESC;
        """,
        params={"today": today},
        ttl=0
    )

    return terms_df


def format_term_label(term_code):
    """
    Converts:
        1Q2627 → 1Q, A.Y. 2026–2027
        1T2526 → 1T, A.Y. 2025–2026
    """

    term_code = str(term_code).strip().upper()

    match = re.match(r"^(\d[TQ])(\d{2})(\d{2})$", term_code)

    if not match:
        return term_code

    term_period = match.group(1)
    start_year = int(match.group(2))
    end_year = int(match.group(3))

    # Convert 26 → 2026 and 27 → 2027
    start_full_year = 2000 + start_year
    end_full_year = 2000 + end_year

    return f"{term_period}, A.Y. {start_full_year}–{end_full_year}"

def get_current_term():
    """
    Determines the current academic term from the term table.

    Current term:
        start_date <= today
        AND
        end_date is NULL OR end_date >= today

    If more than one term qualifies, the term with the
    latest start date is treated as current.
    """

    today = datetime.now(ZoneInfo("Asia/Manila")).date()

    current_term_df = conn.query(
        """
        SELECT
            term_id,
            term_code,
            start_date,
            end_date
        FROM term
        WHERE start_date IS NOT NULL
          AND start_date <= :today
          AND (
              end_date IS NULL
              OR end_date >= :today
          )
        ORDER BY start_date DESC
        LIMIT 1;
        """,
        params={"today": today},
        ttl=0
    )

    if current_term_df.empty:
        return None

    return current_term_df.iloc[0]
# ------------------------------------------------------------------
# ADD DATA / IMPORT HELPERS
# ------------------------------------------------------------------

IMPORT_REQUIRED_FIELDS = ["student_number"]

IMPORT_FIELD_ALIASES = {
    "student_number": ["student number", "student_number", "student id", "student_id", "id number", "student no"],
    "student_email": ["student email", "email", "email address", "student_email"],
    "first_name": ["first", "first name", "firstname", "first_name"],
    "last_name": ["last", "last name", "lastname", "last_name"],
    "cohort": ["cohort", "cohort code", "cohort_code"],
    "adviser": ["adviser", "advisor", "primary adviser", "primary advisor"],
    "coursework_status": ["coursework status", "coursework_status"],
    "comprehensive_exam": ["comprehensive exam", "comprehensive exam status", "comprehensive_exam", "comprehensive_exam_status"],
    "capstone": ["capstone", "capstone/thesis", "capstone status", "capstone_status", "capstone/thesis status"],
    "graduate_on_time": ["graduate on time", "graduated on time", "graduate_on_time"],
    "graduate_date_term_sy": ["graduate date (term/sy)", "graduate date", "graduation date", "graduation term", "graduate_date_term_sy"],
    "remarks": ["remarks", "remark", "notes", "comments"]
}

def normalize_import_value(value):
    if value is None or pd.isna(value):
        return None
    value = str(value).strip()
    return value if value else None

def normalize_import_header(value):
    if value is None or pd.isna(value):
        return ""
    value = str(value).strip().lower()
    value = re.sub(r"[\n\r]+", " ", value)
    value = re.sub(r"[_\-]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()

def detect_import_columns(columns):
    detected = {}
    normalized_columns = {column: normalize_import_header(column) for column in columns}

    for db_field, aliases in IMPORT_FIELD_ALIASES.items():
        normalized_aliases = {normalize_import_header(alias) for alias in aliases}
        for original, normalized in normalized_columns.items():
            if normalized in normalized_aliases:
                detected[db_field] = original
                break

    return detected

def detect_course_columns(columns):
    non_course_columns = {normalize_import_header(value) for aliases in IMPORT_FIELD_ALIASES.values() for value in aliases}
    course_columns = []

    for column in columns:
        normalized = normalize_import_header(column)
        if normalized in non_course_columns:
            continue

        compact = re.sub(r"[^A-Z0-9]", "", str(column).upper())

        if re.fullmatch(r"[A-Z]{2,5}\d{3}", compact):
            course_columns.append(column)

    return course_columns

def read_import_file(uploaded_file):
    try:
        file_name = uploaded_file.name.lower()

        if file_name.endswith(".csv"):
            raw = pd.read_csv(uploaded_file, header=None, dtype=object)
        elif file_name.endswith(".xlsx") or file_name.endswith(".xls"):
            raw = pd.read_excel(uploaded_file, header=None, dtype=object)
        else:
            raise ValueError("Only CSV and Excel files are supported.")

        return raw

    except Exception as e:
        raise ValueError(f"Could not read the uploaded file: {e}")

def clean_import_dataframe(df):
    df = df.copy().dropna(how="all").reset_index(drop=True)

    for column in df.columns:
        df[column] = df[column].apply(
            lambda value: None if pd.isna(value) or str(value).strip().lower() in ("nan", "none") else value
        )

    normalized_rows = [
        [normalize_import_header(value) for value in df.iloc[index]]
        for index in range(min(5, len(df)))
    ]

    header_row = None

    for index, row in enumerate(normalized_rows):
        required_headers = {
            "student number",
            "student email",
            "first",
            "last",
            "cohort"
        }

        if len(required_headers.intersection(row)) >= 3:
            header_row = index
            break

    if header_row is None:
        header_row = 0

    headers = []

    for column_index in range(len(df.columns)):
        values = [
            df.iloc[row_index, column_index]
            for row_index in range(header_row + 1)
        ]

        values = [
            str(value).strip()
            for value in values
            if value is not None
            and str(value).strip()
            and str(value).strip().lower() not in ("nan", "none")
        ]

        header = values[-1] if values else f"Unnamed Column {column_index + 1}"

        course_code = next(
            (
                value
                for value in reversed(values)
                if re.fullmatch(r"[A-Z]{2,5}\d{3}", value.upper())
            ),
            None
        )

        if course_code:
            headers.append(course_code)
        else:
            headers.append(header)

    df = df.iloc[header_row + 1:].reset_index(drop=True)
    df.columns = headers

    return df

def get_issue_cell_style():
    return JsCode("""
    function(params) {
        if (params.data && params.data.__issue_cells) {
            var issues = params.data.__issue_cells;
            if (issues.indexOf(params.colDef.field) !== -1) {
                return {
                    backgroundColor: '#FFF3CD',
                    color: '#856404',
                    fontWeight: '600',
                    border: '2px solid #FFAE00'
                };
            }
        }
        return {};
    }
    """)

def get_import_grid_options(focus_row=None, focus_column=None):
    focus_row = int(focus_row) if focus_row is not None else -1
    focus_column = json.dumps(str(focus_column)) if focus_column else "null"

    on_grid_ready = JsCode(f"""
    function(params) {{
        window.setTimeout(function() {{
            var rowIndex = {focus_row};
            var columnName = {focus_column};

            if (rowIndex >= 0) {{
                params.api.ensureIndexVisible(rowIndex, 'middle');

                if (columnName) {{
                    params.api.setFocusedCell(rowIndex, columnName);
                }}
            }}
        }}, 250);
    }}
    """)

    gb = GridOptionsBuilder.from_dataframe(st.session_state.import_preview_df)

    gb.configure_default_column(editable=True, resizable=True, sortable=True, filter=True, wrapText=False, autoHeight=False)
    gb.configure_grid_options(onGridReady=on_grid_ready, stopEditingWhenCellsLoseFocus=True, rowSelection="single")

    for column in st.session_state.import_preview_df.columns:
        if column != "__issue_cells":
            gb.configure_column(column, editable=True, cellStyle=get_issue_cell_style())

    gb.configure_column("__issue_cells", hide=True)

    return gb.build()


def validate_import_dataframe(df, detected_columns, course_columns):
    warnings = []
    errors = []
    working_df = df.copy()
    working_df["__issue_cells"] = [[] for _ in range(len(working_df))]

    def add_issue(row_index, column, severity, message):
        issue = {
            "row": int(row_index) + 2,
            "student_number": str(working_df.iloc[row_index].get(detected_columns.get("student_number", ""), "")),
            "column": str(column),
            "current_value": working_df.iloc[row_index].get(column, ""),
            "severity": severity,
            "message": message,
            "row_index": int(row_index)
        }

        if severity == "Error":
            errors.append(issue)
        else:
            warnings.append(issue)

        if column in working_df.columns:
            working_df.at[row_index, "__issue_cells"].append(column)

    student_column = detected_columns.get("student_number")

    if not student_column:
        errors.append({
            "row": "—",
            "student_number": "—",
            "column": "Student Number",
            "current_value": "Missing column",
            "severity": "Error",
            "message": "A student number column could not be detected.",
            "row_index": None
        })
    else:
        for index, value in working_df[student_column].items():
            if value is None or str(value).strip() == "":
                add_issue(index, student_column, "Error", "Student number is required.")

    warning_fields = {
        "adviser": "Missing adviser.",
        "graduate_date_term_sy": "Missing graduation date/term.",
        "capstone": "Missing capstone status.",
        "student_email": "Missing student email.",
        "cohort": "Missing cohort.",
        "remarks": "Missing remarks."
    }

    for field, message in warning_fields.items():
        column = detected_columns.get(field)

        if not column:
            continue

        for index, value in working_df[column].items():
            if value is None or str(value).strip() == "":
                add_issue(index, column, "Warning", message)

    return working_df, warnings, errors

def refresh_import_validation():
    detected_columns = st.session_state.get("import_detected_columns", {})
    course_columns = st.session_state.get("import_course_columns", [])
    preview_df = st.session_state.get("import_preview_df")

    if preview_df is None:
        return

    clean_df = preview_df.drop(columns=["__issue_cells"], errors="ignore").copy()
    validated_df, warnings, errors = validate_import_dataframe(clean_df, detected_columns, course_columns)

    st.session_state.import_preview_df = validated_df
    st.session_state.import_warnings = warnings
    st.session_state.import_errors = errors

def render_import_issue_list():
    warnings = st.session_state.get("import_warnings", [])
    errors = st.session_state.get("import_errors", [])
    issues = errors + warnings

    if not issues:
        st.success("✓ No validation issues detected.")
        return

    st.markdown("### Issues Requiring Attention")

    issue_df = pd.DataFrame([
        {
            "Severity": issue["severity"],
            "Row": issue["row"],
            "Student Number": issue["student_number"],
            "Column": issue["column"],
            "Current Value": "Blank" if issue["current_value"] is None or str(issue["current_value"]).strip() == "" else str(issue["current_value"]),
            "Issue": issue["message"]
        }
        for issue in issues
    ])

    if not issue_df.empty:
        st.dataframe(issue_df, hide_index=True, use_container_width=True)

    for index, issue in enumerate(issues):
        severity_icon = "🔴" if issue["severity"] == "Error" else "⚠️"
        issue_col1, issue_col2 = st.columns([6, 1])

        with issue_col1:
            st.markdown(f"{severity_icon} **Row {issue['row']} · {issue['column']}** — {issue['message']}")

        with issue_col2:
            if issue["row_index"] is not None:
                if st.button("✏️ Edit", key=f"import_issue_edit_{index}_{issue['row_index']}_{issue['column']}", use_container_width=True):
                    st.session_state.import_focus_row = issue["row_index"]
                    st.session_state.import_focus_column = issue["column"]
                    st.rerun()

def render_add_data():
    st.subheader("➕ Add Data")
    st.caption("Import or manually enter student academic records. Imported records are assigned to the selected academic term.")

    if not user.get("can_import", False):
        log_security_event(user["user_id"], "UNAUTHORIZED_IMPORT_ACCESS", "User attempted to access Add Data without import permission.")
        st.error("⛔ You do not have permission to add or import data.")
        return

    available_terms_df = load_available_terms()
    current_term = get_current_term()

    if available_terms_df.empty:
        st.error("No academic terms are currently available.")
        return

    term_options = available_terms_df["term_id"].tolist()

    if "import_term_id" not in st.session_state:
        st.session_state.import_term_id = int(current_term["term_id"]) if current_term is not None else int(term_options[0])

    if st.session_state.import_term_id not in term_options:
        st.session_state.import_term_id = int(current_term["term_id"]) if current_term is not None else int(term_options[0])

    selected_import_term_id = st.selectbox(
        "Academic Term",
        options=term_options,
        index=term_options.index(st.session_state.import_term_id),
        format_func=lambda term_id: format_term_label(available_terms_df.loc[available_terms_df["term_id"] == term_id, "term_code"].iloc[0]),
        key="import_term_selector"
    )

    st.session_state.import_term_id = selected_import_term_id

    st.info("The selected academic term will automatically be assigned to all term-specific records created by this import.")

    import_method = st.radio("Add Data Method", ["Import File", "Manual Data Entry"], horizontal=True, key="import_method")

    if import_method == "Import File":
        uploaded_file = st.file_uploader("Upload CSV or Excel file", type=["csv", "xlsx", "xls"], key="student_import_file")
    
        if uploaded_file is None:
            for key in ["import_loaded_file", "import_preview_df", "import_detected_columns", "import_course_columns", "import_warnings", "import_errors", "import_focus_row", "import_focus_column"]:
                st.session_state.pop(key, None)
        elif st.session_state.get("import_loaded_file") != uploaded_file.name:
            try:
                raw_df = clean_import_dataframe(read_import_file(uploaded_file))
                detected_columns = detect_import_columns(raw_df.columns)
                course_columns = detect_course_columns(raw_df.columns)
                validated_df, warnings, errors = validate_import_dataframe(raw_df, detected_columns, course_columns)
    
                st.session_state.import_loaded_file = uploaded_file.name
                st.session_state.import_preview_df = validated_df
                st.session_state.import_detected_columns = detected_columns
                st.session_state.import_course_columns = course_columns
                st.session_state.import_warnings = warnings
                st.session_state.import_errors = errors
                st.session_state.import_focus_row = None
                st.session_state.import_focus_column = None
    
            except Exception as e:
                st.error(str(e))
                return

    else:
        if "import_manual_df" not in st.session_state:
            st.session_state.import_manual_df = pd.DataFrame(columns=["student_number", "student_email", "first_name", "last_name", "cohort", "adviser", "coursework_status", "comprehensive_exam", "capstone", "graduate_on_time", "graduate_date_term_sy", "remarks"])

        st.session_state.import_preview_df = st.data_editor(
            st.session_state.import_manual_df,
            num_rows="dynamic",
            use_container_width=True,
            height=400,
            key="manual_import_editor"
        )

        if st.button("Validate Manual Data", type="secondary"):
            st.session_state.import_detected_columns = {column: column for column in st.session_state.import_preview_df.columns if column in IMPORT_FIELD_ALIASES}
            st.session_state.import_course_columns = []
            refresh_import_validation()
            st.rerun()

    preview_df = st.session_state.get("import_preview_df")

    if preview_df is None:
        return

    st.divider()

    detected_columns = st.session_state.get("import_detected_columns", {})
    course_columns = st.session_state.get("import_course_columns", [])

    st.markdown("### Import Summary")

    summary_col1, summary_col2, summary_col3, summary_col4 = st.columns(4)

    summary_col1.metric("Students Detected", len(preview_df))
    summary_col2.metric("Course Columns", len(course_columns))
    summary_col3.metric("Warnings", len(st.session_state.get("import_warnings", [])))
    summary_col4.metric("Errors", len(st.session_state.get("import_errors", [])))

    st.markdown("### Data Preview & Editor")

    display_df = preview_df.drop(columns=["__issue_cells"], errors="ignore")
    
    edited_df = st.data_editor(
        display_df,
        use_container_width=True,
        height=450,
        num_rows="fixed",
        key="import_main_datasheet"
    )
    
    edited_df["__issue_cells"] = preview_df.get("__issue_cells", pd.Series([[] for _ in range(len(edited_df))])).values
    st.session_state.import_preview_df = edited_df   

    st.caption("You may edit any cell. Highlighted cells correspond to detected warnings or errors.")

    st.divider()

    render_import_issue_list()

    st.divider()

    current_errors = st.session_state.get("import_errors", [])

    if current_errors:
        st.error(f"🔴 {len(current_errors)} error(s) must be fixed before the import can be finalized.")
    else:
        st.success("✓ No blocking errors. Warnings may remain if the missing information is intentional.")

    finalize_col1, finalize_col2 = st.columns(2)

    with finalize_col1:
        if st.button("Revalidate Data", use_container_width=True):
            refresh_import_validation()
            st.rerun()

    with finalize_col2:
        finalize_disabled = bool(current_errors)

        if st.button("Finalize Import", type="primary", use_container_width=True, disabled=finalize_disabled):
            finalize_import_to_database()
def finalize_import_to_database():
    preview_df = st.session_state.get("import_preview_df")
    if preview_df is None or preview_df.empty:
        st.error("There is no data to import.")
        return

    term_id = st.session_state.get("import_term_id")
    if term_id is None:
        st.error("Please select an academic term before importing.")
        return

    errors = st.session_state.get("import_errors", [])
    if errors:
        st.error(f"Import blocked. Please fix {len(errors)} error(s) first.")
        return

    detected_columns = st.session_state.get("import_detected_columns", {})
    course_columns = st.session_state.get("import_course_columns", [])

    term_lookup_df = load_available_terms()
    if term_lookup_df.empty:
        st.error("Unable to find the selected academic term.")
        return

    matching_term = term_lookup_df[term_lookup_df["term_id"] == term_id]
    if matching_term.empty:
        st.error("The selected academic term is no longer available.")
        return

    selected_term_code = matching_term.iloc[0]["term_code"]
    import_date = datetime.now(ZoneInfo("Asia/Manila")).date()

    try:
        with conn.session as s:
            debug_course = s.execute(text("SELECT course_code FROM courses WHERE UPPER(TRIM(course_code)) = 'MBAC602' LIMIT 1;")).fetchone()
            st.write("DEBUG MBAC602:", debug_course)
            imported_students = 0
            imported_courses = 0
            imported_lifecycle = 0
            updated_students = 0

            def clean_value(value):
                if pd.isna(value):
                    return None
                value = str(value).strip()
                return None if value == "" or value.lower() in {"nan", "none", "n/a", "na"} else value

            program_result = s.execute(
                text("SELECT program_id FROM program WHERE program_code = :program_code LIMIT 1;"),
                {"program_code": ACTIVE_PROGRAM}
            ).fetchone()

            if not program_result:
                raise ValueError(f"Program '{ACTIVE_PROGRAM}' was not found in the program table.")

            program_id = program_result[0]

            for _, row in preview_df.iterrows():
                student_number = row.get(detected_columns.get("student_number", "student_number"))

                if pd.isna(student_number) or str(student_number).strip() == "":
                    continue

                try:
                    student_number = int(float(student_number))
                except Exception:
                    continue

                student_email = clean_value(row.get(detected_columns.get("student_email", "student_email")))
                first_name = clean_value(row.get(detected_columns.get("first_name", "first_name")))
                last_name = clean_value(row.get(detected_columns.get("last_name", "last_name")))
                cohort_code = clean_value(row.get(detected_columns.get("cohort", "cohort")))
                adviser_name = clean_value(row.get(detected_columns.get("adviser", "adviser")))
                graduate_on_time = clean_value(row.get(detected_columns.get("graduate_on_time", "graduate_on_time")))
                graduate_date_term_sy = clean_value(row.get(detected_columns.get("graduate_date_term_sy", "graduate_date_term_sy")))
                remarks = clean_value(row.get(detected_columns.get("remarks", "remarks")))

                graduate_on_time_value = None if graduate_on_time is None else graduate_on_time.lower() in {"yes", "y", "true", "1", "on"}

                cohort_id = None
                if cohort_code:
                    cohort_result = s.execute(
                        text("SELECT cohort_id FROM cohort WHERE cohort_code = :cohort_code LIMIT 1;"),
                        {"cohort_code": cohort_code}
                    ).fetchone()
                    if cohort_result:
                        cohort_id = cohort_result[0]

                adviser_id = None
                if adviser_name:
                    adviser_result = s.execute(
                        text("""
                            SELECT adviser_id
                            FROM advisers
                            WHERE LOWER(TRIM(full_name)) = LOWER(TRIM(:adviser_name))
                            LIMIT 1;
                        """),
                        {"adviser_name": adviser_name}
                    ).fetchone()
                    if adviser_result:
                        adviser_id = adviser_result[0]

                existing_student = s.execute(
                    text("SELECT student_number FROM students_normalized WHERE student_number = :student_number LIMIT 1;"),
                    {"student_number": student_number}
                ).fetchone()

                student_params = {
                    "student_number": student_number,
                    "student_email": student_email,
                    "first_name": first_name,
                    "last_name": last_name,
                    "cohort_id": cohort_id,
                    "adviser_id": adviser_id,
                    "program_id": program_id,
                    "graduate_on_time": graduate_on_time_value,
                    "graduate_date_term_sy": graduate_date_term_sy,
                    "remarks": remarks
                }

                if existing_student:
                    s.execute(
                        text("""
                            UPDATE students_normalized
                            SET student_email = :student_email,
                                first_name = :first_name,
                                last_name = :last_name,
                                cohort_id = :cohort_id,
                                adviser_id = :adviser_id,
                                program_id = :program_id,
                                graduate_on_time = :graduate_on_time,
                                graduate_date_term_sy = :graduate_date_term_sy,
                                remarks = :remarks
                            WHERE student_number = :student_number;
                        """),
                        student_params
                    )
                    updated_students += 1
                else:
                    s.execute(
                        text("""
                            INSERT INTO students_normalized (
                                student_number, student_email, first_name, last_name,
                                cohort_id, adviser_id, program_id, graduate_on_time,
                                graduate_date_term_sy, remarks
                            )
                            VALUES (
                                :student_number, :student_email, :first_name, :last_name,
                                :cohort_id, :adviser_id, :program_id, :graduate_on_time,
                                :graduate_date_term_sy, :remarks
                            );
                        """),
                        student_params
                    )
                    imported_students += 1

                for course_column in course_columns:
                    course_code = str(course_column).strip().upper()
                    cleaned_course_status = clean_value(row.get(course_column))

                    if cleaned_course_status is None:
                        continue

                    course_exists = s.execute(
                        text("""
                            SELECT course_code
                            FROM courses
                            WHERE UPPER(course_code) = :course_code
                            LIMIT 1;
                        """),
                        {"course_code": course_code}
                    ).fetchone()
                    
                    if not course_exists:
                        continue
                    
                    course_code = course_exists[0]
                    
                    enrollment_params = {
                        "student_number": student_number,
                        "course_code": course_code,
                        "term_id": term_id,
                        "status": cleaned_course_status
                    }

                    existing_enrollment = s.execute(
                        text("""
                            SELECT 1
                            FROM student_course_enrollments
                            WHERE student_number = :student_number
                              AND UPPER(course_code) = :course_code
                              AND term_id = :term_id
                            LIMIT 1;
                        """),
                        enrollment_params
                    ).fetchone()

                    if existing_enrollment:
                        s.execute(
                            text("""
                                UPDATE student_course_enrollments
                                SET status = :status
                                WHERE student_number = :student_number
                                  AND UPPER(course_code) = :course_code
                                  AND term_id = :term_id;
                            """),
                            enrollment_params
                        )
                    else:
                        s.execute(
                            text("""
                                INSERT INTO student_course_enrollments (
                                    student_number, course_code, term_id, status
                                )
                                VALUES (
                                    :student_number, :course_code, :term_id, :status
                                );
                            """),
                            enrollment_params
                        )
                        imported_courses += 1

                lifecycle_columns = {
                    "coursework": detected_columns.get("coursework_status"),
                    "comprehensive_exam": detected_columns.get("comprehensive_exam"),
                    "capstone": detected_columns.get("capstone")
                }

                lifecycle_values = {
                    stage: clean_value(row.get(column))
                    for stage, column in lifecycle_columns.items()
                    if column
                }

                active_stage = next(
                    (stage for stage in ["capstone", "comprehensive_exam", "coursework"]
                     if lifecycle_values.get(stage, "").lower() in {"in progress", "pending"}),
                    "capstone"
                )

                for stage_name, stage_source_column in lifecycle_columns.items():
                    if not stage_source_column:
                        continue

                    cleaned_stage_status = lifecycle_values.get(stage_name)

                    if cleaned_stage_status is None:
                        continue

                    status_lookup = {
                        "done": "completed",
                        "completed": "completed",
                        "in progress": "in-progress",
                        "incomplete": "incomplete",
                        "pending": "pending",
                        "cancelled": "cancelled"
                    }

                    status_name = status_lookup.get(
                        cleaned_stage_status.lower(),
                        cleaned_stage_status
                    )

                    stage_result = s.execute(
                        text("""
                            SELECT stage_id
                            FROM lifecycle_stage
                            WHERE LOWER(stage_name) = LOWER(:stage_name)
                            LIMIT 1;
                        """),
                        {"stage_name": stage_name}
                    ).fetchone()

                    if not stage_result:
                        continue

                    stage_id = stage_result[0]

                    status_result = s.execute(
                        text("""
                            SELECT status_id
                            FROM lifecycle_status
                            WHERE LOWER(REPLACE(status_name, ' ', '-')) =
                                  LOWER(REPLACE(:status_name, ' ', '-'))
                            LIMIT 1;
                        """),
                        {"status_name": status_name}
                    ).fetchone()

                    if not status_result:
                        continue

                    status_id = status_result[0]

                    existing_lifecycle = s.execute(
                        text("""
                            SELECT student_lifecycle_status_id, status_id, stage_started_date
                            FROM student_lifecycle_status
                            WHERE student_number = :student_number
                              AND stage_id = :stage_id
                              AND term_id = :term_id
                            LIMIT 1;
                        """),
                        {
                            "student_number": student_number,
                            "stage_id": stage_id,
                            "term_id": term_id
                        }
                    ).fetchone()

                    if existing_lifecycle:
                        s.execute(
                            text("""
                                UPDATE student_lifecycle_status
                                SET status_id = :status_id,
                                    last_updated_date = NOW()
                                WHERE student_lifecycle_status_id = :lifecycle_id;
                            """),
                            {
                                "status_id": status_id,
                                "lifecycle_id": existing_lifecycle[0]
                            }
                        )
                    else:
                        stage_started_date = import_date if stage_name == active_stage else None
                        stage_started_date_source = "System Assigned" if stage_name == active_stage else None

                        s.execute(
                            text("""
                                INSERT INTO student_lifecycle_status (
                                    student_number, stage_id, status_id, term_id,
                                    stage_started_date, stage_started_date_source,
                                    last_updated_date
                                )
                                VALUES (
                                    :student_number, :stage_id, :status_id, :term_id,
                                    :stage_started_date, :stage_started_date_source,
                                    NOW()
                                );
                            """),
                            {
                                "student_number": student_number,
                                "stage_id": stage_id,
                                "status_id": status_id,
                                "term_id": term_id,
                                "stage_started_date": stage_started_date,
                                "stage_started_date_source": stage_started_date_source
                            }
                        )
                        imported_lifecycle += 1

            s.commit()

        try:
            load_students.clear()
        except Exception:
            pass

        try:
            fetch_student_lifecycle.clear()
        except Exception:
            pass

        try:
            fetch_student_courses.clear()
        except Exception:
            pass

        try:
            log_security_event(
                user["user_id"],
                "DATA_IMPORT_COMPLETED",
                f"Imported academic data for term '{selected_term_code}'. New students: {imported_students}; updated students: {updated_students}; courses: {imported_courses}; lifecycle records: {imported_lifecycle}."
            )
        except Exception:
            pass

        for key in [
            "import_loaded_file", "import_preview_df", "import_detected_columns",
            "import_course_columns", "import_warnings", "import_errors",
            "import_focus_row", "import_focus_column", "import_manual_df"
        ]:
            st.session_state.pop(key, None)

        st.success(f"✓ Import completed successfully for {format_term_label(selected_term_code)}.")
        st.info(f"New students: {imported_students} · Updated students: {updated_students} · Courses: {imported_courses} · Lifecycle records: {imported_lifecycle}")
        st.rerun()

    except Exception as e:
        try:
            s.rollback()
        except Exception:
            pass

        st.error("The import could not be completed. No changes should be considered finalized.")
        st.exception(e)
# ------------------------------------------------------------------
# DATA LOADERS (DYNAMICALLY MAPPED & FILTERED BY PROGRAM)
# ------------------------------------------------------------------
def get_cohort_val(c):
    m = re.search(r'(\d)[TQ](\d{2})(\d{2})', str(c).upper())
    return float(f"{m.group(2)}{m.group(3)}.{m.group(1)}") if m else 0.0

@st.cache_data(ttl=60, show_spinner="Loading mapped student roster...")
def load_students(
    target_program: str,
    selected_term_id: int | None = None
) -> tuple[pd.DataFrame, str]:
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

                et.term_id,

                MAX(CASE WHEN stg.stage_name = 'coursework' THEN sts.status_name END) AS coursework_status,
                MAX(CASE WHEN stg.stage_name = 'comprehensive_exam' THEN sts.status_name END) AS comprehensive_exam,
                MAX(CASE WHEN stg.stage_name = 'capstone' THEN sts.status_name END) AS capstone,
                MAX(CASE WHEN stg.stage_name = 'coursework' THEN sls.last_updated_date END) AS updated_at,
                MAX(CASE WHEN stg.stage_name = 'coursework' THEN sls.last_updated_date END) AS cw_updated_at,
                MAX(CASE WHEN stg.stage_name = 'comprehensive_exam' THEN sls.last_updated_date END) AS ce_updated_at,
                MAX(CASE WHEN stg.stage_name = 'capstone' THEN sls.last_updated_date END) AS cap_updated_at,
                MAX(CASE WHEN stg.stage_name = 'coursework' THEN sls.stage_started_date END) AS cw_started_at,
                MAX(CASE WHEN stg.stage_name = 'comprehensive_exam' THEN sls.stage_started_date END) AS ce_started_at,
                MAX(CASE WHEN stg.stage_name = 'capstone' THEN sls.stage_started_date END) AS cap_started_at

            FROM students_normalized s

            LEFT JOIN (
                SELECT DISTINCT student_number, term_id
                FROM student_course_enrollments
                WHERE term_id IS NOT NULL
            ) et
                ON s.student_number = et.student_number

            LEFT JOIN cohort c ON s.cohort_id = c.cohort_id
            LEFT JOIN program p ON s.program_id = p.program_id
            LEFT JOIN advisers a ON s.adviser_id = a.adviser_id
            LEFT JOIN student_lifecycle_status sls
                ON s.student_number = sls.student_number
                AND (
                    :selected_term_id IS NULL
                    OR sls.term_id = :selected_term_id
                )
            LEFT JOIN lifecycle_stage stg ON sls.stage_id = stg.stage_id
            LEFT JOIN lifecycle_status sts ON sls.status_id = sts.status_id

            GROUP BY 
                s.student_number, s.student_email, s.first_name, s.last_name, 
                a.full_name, s.graduate_on_time, s.graduate_date_term_sy, 
                s.remarks, s.created_at, c.cohort_code, p.program_code, 
                p.program_name, et.term_id;
        """
        df = conn.query(
            query,
            params={"selected_term_id": selected_term_id},
            ttl=0
        )
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

        if row.get("coursework_display") == "Pending":
            start_date = row.get("cw_started_at") if pd.notna(row.get("cw_started_at")) else row.get("cw_updated_at")

            if pd.notna(start_date):
                days = (now_utc - pd.to_datetime(start_date, utc=True)).days
                if days > cw_thresh:
                    reasons.append(f"Coursework pending for {days} days (Limit: {cw_thresh})")

        if row.get("comprehensive_exam_display") == "In-Progress":
            start_date = row.get("ce_started_at") if pd.notna(row.get("ce_started_at")) else row.get("ce_updated_at")

            if pd.notna(start_date):
                days = (now_utc - pd.to_datetime(start_date, utc=True)).days
                if days > ce_thresh:
                    reasons.append(f"Exam in-progress for {days} days (Limit: {ce_thresh})")

        if row.get("capstone_display") == "In-Progress":
            start_date = row.get("cap_started_at") if pd.notna(row.get("cap_started_at")) else row.get("cap_updated_at")

            if pd.notna(start_date):
                days = (now_utc - pd.to_datetime(start_date, utc=True)).days
                if days > cap_thresh:
                    reasons.append(f"Capstone in-progress for {days} days (Limit: {cap_thresh})")

        return " | ".join(reasons) if reasons else ""

    df["risk_details"] = df.apply(calculate_risk, axis=1)
    df["is_at_risk"] = df["risk_details"] != ""
    df["risk_flag"] = df["is_at_risk"].apply(lambda x: "At Risk" if x else "On Track")

    # Time parsing
    def to_manila_time(series):
        dt = pd.to_datetime(series, errors="coerce")
        if dt.dt.tz is None:
            dt = dt.dt.tz_localize("UTC")
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
def fetch_student_courses(student_number: int, selected_term_id: int | None = None) -> pd.DataFrame:
    query = """
        SELECT 
            UPPER(e.course_code) AS "Course Code",
            COALESCE(c.course_name, 'Course Unit') AS "Course Name",
            COALESCE(c.credits, 3) AS "Credits",
            INITCAP(e.status) AS "Status"
        FROM student_course_enrollments e
        LEFT JOIN courses c ON e.course_code = c.course_code
        WHERE e.student_number = :student_number
          AND (:selected_term_id IS NULL OR e.term_id = :selected_term_id)
        ORDER BY e.course_code ASC;
    """
    try: return conn.query(query, params={"student_number": int(student_number), "selected_term_id": selected_term_id}, ttl=0)
    except Exception: return pd.DataFrame()

@st.cache_data(ttl=60)
def fetch_student_lifecycle(student_number: int, selected_term_id: int | None = None) -> pd.DataFrame:
    query = """
        SELECT 
            stg.stage_name AS milestone_type,
            INITCAP(REPLACE(stg.stage_name, '_', ' ')) AS "Milestone",
            INITCAP(sts.status_name) AS "Recorded Status",
            sls.stage_started_date AS "Stage Started",
            sls.last_updated_date AS "Last Updated"
        FROM student_lifecycle_status sls
        JOIN lifecycle_stage stg ON sls.stage_id = stg.stage_id
        JOIN lifecycle_status sts ON sls.status_id = sts.status_id
        WHERE sls.student_number = :student_number
          AND (:selected_term_id IS NULL OR sls.term_id = :selected_term_id)
        ORDER BY stg.stage_id ASC;
    """
    try:
        df = conn.query(query, params={"student_number": int(student_number), "selected_term_id": selected_term_id}, ttl=0)
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

def go_to_profile(student_email=None):
    if student_email:
        st.session_state.selected_student_email = student_email
    st.session_state.admin_view = "Student Profile Inspector"
    st.session_state.page = "profile"

def go_to_list():
    st.session_state.admin_view = "Student Roster"
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
if user.get("can_import", False):
    core_nav_options.append("Add Data")
admin_nav_options = ["Global Instance Settings", "Schema Mapping Config", "Permissions & Audit Logs", "Academic Terms"]

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
            "Schema Mapping Config": "SCHEMA MAPPING CONFIGURATION",
            "Permissions & Audit Logs": "PERMISSIONS & AUDIT LOGS",
            "Academic Terms": "ACADEMIC TERMS"
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
    "Student Profile Inspector": "STUDENT PROFILE",
    "Add Data": "ADD DATA"
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
        new_ce = c2.number_input("Comp Exam Limit (Days)", value=cur_ce, min_value=1, help="Expected duration to pass the exam once initiated.")
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
        users_df = conn.query(
            "SELECT user_id, username, full_name, role, can_edit, can_import FROM app_users ORDER BY user_id;",
            ttl=0
        )

        st.dataframe(
            users_df,
            hide_index=True,
            use_container_width=True
        )

        st.markdown("##### ✏️ Modify User Permissions")

        with st.form("admin_perm_form"):
            target_username = st.selectbox(
                "Select User Account",
                users_df["username"].tolist()
            )

            target_user_row = users_df[
                users_df["username"] == target_username
            ].iloc[0]

            new_can_edit = st.checkbox(
                "Grant Write / Edit Capability",
                value=bool(target_user_row["can_edit"])
            )

            new_can_import = st.checkbox(
                "Grant Add Data / Import Capability",
                value=bool(target_user_row["can_import"])
            )

            if st.form_submit_button("Update Access Level"):
                try:
                    with conn.session as s:
                        s.execute(
                            text("""
                                UPDATE app_users
                                SET
                                    can_edit = :ce,
                                    can_import = :ci
                                WHERE username = :u;
                            """),
                            {
                                "ce": new_can_edit,
                                "ci": new_can_import,
                                "u": target_username
                            }
                        )
                        s.commit()

                    log_security_event(
                        user["user_id"],
                        "PERMISSIONS_UPDATED",
                        f"Updated can_edit={new_can_edit}, can_import={new_can_import} for user '{target_username}'."
                    )

                    st.success(
                        f"Permissions successfully updated for {target_username}."
                    )

                    st.rerun()

                except Exception as ex:
                    st.error(
                        f"Error updating permissions: {ex}"
                    )

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
# ------------------------------------------------------------------
# VIEW: IT/ADMIN ACADEMIC TERM MANAGEMENT
# ------------------------------------------------------------------
def render_academic_terms():
    st.subheader("📅 Academic Terms")
    st.caption(
        "Create and maintain academic terms used by the dashboard "
        "for term-based filtering and historical reporting."
    )

    # --------------------------------------------------------------
    # EDIT MODE
    # --------------------------------------------------------------
    editing_term_id = st.session_state.get("editing_term_id")

    if editing_term_id is not None:

        st.markdown("#### Edit Academic Term")

        try:
            edit_term_df = conn.query(
                """
                SELECT
                    term_id,
                    term_code,
                    start_date,
                    end_date
                FROM term
                WHERE term_id = :term_id;
                """,
                params={"term_id": editing_term_id},
                ttl=0
            )

            if edit_term_df.empty:
                st.error("The selected academic term could not be found.")
                st.session_state.editing_term_id = None
                st.rerun()

            else:
                current_term = edit_term_df.iloc[0]

                with st.form("edit_academic_term_form"):

                    edit_term_code = st.text_input(
                        "Term Code",
                        value=str(current_term["term_code"]),
                        help="Official academic term code."
                    ).strip().upper()

                    edit_col1, edit_col2 = st.columns(2)

                    with edit_col1:
                        edit_start_date = st.date_input(
                            "Start Date",
                            value=pd.to_datetime(
                                current_term["start_date"]
                            ).date(),
                            help="Official beginning date of the academic term."
                        )

                    with edit_col2:
                        edit_end_date = st.date_input(
                            "End Date",
                            value=pd.to_datetime(
                                current_term["end_date"]
                            ).date(),
                            help="Official ending date of the academic term."
                        )

                    save_edit = st.form_submit_button(
                        "Save Changes",
                        type="primary",
                        use_container_width=True
                    )

                if st.button(
                    "Cancel",
                    use_container_width=True
                ):
                    st.session_state.editing_term_id = None
                    st.rerun()

                if save_edit:

                    # --------------------------------------------------
                    # VALIDATE EDITED TERM
                    # --------------------------------------------------
                    if not edit_term_code:
                        st.error("Please enter a term code.")

                    elif not re.match(
                        r"^\d[TQ]\d{4}$",
                        edit_term_code
                    ):
                        st.error(
                            "Invalid term code format. Use the format "
                            "1T2526 or 1Q2627."
                        )

                    elif edit_end_date < edit_start_date:
                        st.error(
                            "End Date cannot be earlier than Start Date."
                        )

                    else:
                        try:
                            with conn.session as s:

                                # Check whether another term already
                                # uses the edited term code.
                                duplicate_term = s.execute(
                                    text("""
                                        SELECT term_id
                                        FROM term
                                        WHERE term_code = :term_code
                                          AND term_id <> :term_id;
                                    """),
                                    {
                                        "term_code": edit_term_code,
                                        "term_id": editing_term_id
                                    }
                                ).fetchone()

                                if duplicate_term:
                                    st.error(
                                        f"Academic term {edit_term_code} "
                                        "already exists."
                                    )

                                else:
                                    # Update the term
                                    s.execute(
                                        text("""
                                            UPDATE term
                                            SET
                                                term_code = :term_code,
                                                start_date = :start_date,
                                                end_date = :end_date
                                            WHERE term_id = :term_id;
                                        """),
                                        {
                                            "term_code": edit_term_code,
                                            "start_date": edit_start_date,
                                            "end_date": edit_end_date,
                                            "term_id": editing_term_id
                                        }
                                    )

                                    s.commit()

                                    log_security_event(
                                        user["user_id"],
                                        "ACADEMIC_TERM_UPDATED",
                                        f"Updated academic term "
                                        f"{edit_term_code} "
                                        f"({edit_start_date} to "
                                        f"{edit_end_date})."
                                    )

                                    st.session_state.editing_term_id = None

                                    st.success(
                                        f"Academic term {edit_term_code} "
                                        "was successfully updated."
                                    )

                                    st.rerun()

                        except Exception as e:
                            st.error(
                                f"Error updating academic term: {e}"
                            )

        except Exception as e:
            st.error(
                f"Error loading academic term: {e}"
            )

    # --------------------------------------------------------------
    # ADD NEW TERM MODE
    # --------------------------------------------------------------
    else:

        st.markdown("#### Add New Academic Term")

        with st.form("add_academic_term_form"):

            term_code = st.text_input(
                "Term Code",
                placeholder="e.g. 1Q2627",
                help=(
                    "Enter the official academic term code "
                    "used by the institution."
                )
            ).strip().upper()

            date_col1, date_col2 = st.columns(2)

            with date_col1:
                start_date = st.date_input(
                    "Start Date",
                    help="Official beginning date of the academic term."
                )

            with date_col2:
                end_date = st.date_input(
                    "End Date",
                    help="Official ending date of the academic term."
                )

            submitted = st.form_submit_button(
                "Add Academic Term",
                type="primary",
                use_container_width=True
            )

            if submitted:

                # --------------------------------------------------
                # VALIDATE NEW TERM
                # --------------------------------------------------
                if not term_code:
                    st.error("Please enter a term code.")

                elif not re.match(
                    r"^\d[TQ]\d{4}$",
                    term_code
                ):
                    st.error(
                        "Invalid term code format. Use the format "
                        "1T2526 or 1Q2627."
                    )

                elif end_date < start_date:
                    st.error(
                        "End Date cannot be earlier than Start Date."
                    )

                else:
                    try:
                        with conn.session as s:

                            # Check for duplicate term code
                            existing_term = s.execute(
                                text("""
                                    SELECT term_id
                                    FROM term
                                    WHERE term_code = :term_code;
                                """),
                                {
                                    "term_code": term_code
                                }
                            ).fetchone()

                            if existing_term:
                                st.error(
                                    f"Academic term {term_code} "
                                    "already exists."
                                )

                            else:

                                # Insert new term
                                s.execute(
                                    text("""
                                        INSERT INTO term (
                                            term_code,
                                            start_date,
                                            end_date
                                        )
                                        VALUES (
                                            :term_code,
                                            :start_date,
                                            :end_date
                                        );
                                    """),
                                    {
                                        "term_code": term_code,
                                        "start_date": start_date,
                                        "end_date": end_date
                                    }
                                )

                                s.commit()

                                log_security_event(
                                    user["user_id"],
                                    "ACADEMIC_TERM_CREATED",
                                    f"Created academic term "
                                    f"{term_code} "
                                    f"({start_date} to {end_date})."
                                )

                                st.success(
                                    f"Academic term {term_code} "
                                    "was successfully added."
                                )

                                st.rerun()

                    except Exception as e:
                        st.error(
                            f"Error creating academic term: {e}"
                        )

    # --------------------------------------------------------------
    # EXISTING TERMS
    # --------------------------------------------------------------
    st.divider()
    st.markdown("#### Existing Academic Terms")

    try:
        terms_df = conn.query(
            """
            SELECT
                term_id,
                term_code,
                start_date,
                end_date
            FROM term
            ORDER BY start_date DESC NULLS LAST, term_code DESC;
            """,
            ttl=0
        )

        if terms_df.empty:
            st.info("No academic terms have been configured yet.")

        else:

            for _, term_row in terms_df.iterrows():

                term_id = int(term_row["term_id"])
                term_code_display = str(term_row["term_code"])

                start_display = (
                    str(term_row["start_date"])
                    if pd.notna(term_row["start_date"])
                    else "Not set"
                )

                end_display = (
                    str(term_row["end_date"])
                    if pd.notna(term_row["end_date"])
                    else "Not set"
                )

                term_col1, term_col2, term_col3, term_col4 = st.columns(
                    [1.5, 2, 2, 1]
                )

                with term_col1:
                    st.markdown(
                        f"**{term_code_display}**"
                    )

                with term_col2:
                    st.write(
                        f"Start: {start_display}"
                    )

                with term_col3:
                    st.write(
                        f"End: {end_display}"
                    )

                with term_col4:
                    if st.button(
                        "✏️ Edit",
                        key=f"edit_term_{term_id}",
                        use_container_width=True
                    ):
                        st.session_state.editing_term_id = term_id
                        st.rerun()

                st.divider()

    except Exception as e:
        st.error(
            f"Error loading academic terms: {e}"
        )
    # --------------------------------------------------------------
    # EXISTING TERMS
    # --------------------------------------------------------------
    st.divider()
    st.markdown("#### Existing Academic Terms")

    try:
        terms_df = conn.query(
            """
            SELECT
                term_id,
                term_code,
                start_date,
                end_date
            FROM term
            ORDER BY start_date DESC NULLS LAST, term_code DESC;
            """,
            ttl=0
        )

        if terms_df.empty:
            st.info("No academic terms have been configured yet.")

        else:
            display_terms_df = terms_df.copy()

            display_terms_df = display_terms_df.rename(
                columns={
                    "term_id": "ID",
                    "term_code": "Term Code",
                    "start_date": "Start Date",
                    "end_date": "End Date"
                }
            )

            st.dataframe(
                display_terms_df,
                hide_index=True,
                use_container_width=True
            )

    except Exception as e:
        st.error(
            f"Error loading academic terms: {e}"
        )
        
def render_completion_trend_chart(df_all, active_program):
    terms_df = conn.query("""
        SELECT term_id, term_code, start_date
        FROM term
        WHERE start_date IS NOT NULL
        ORDER BY start_date DESC
        LIMIT 4;
    """, ttl=0)

    if terms_df.empty:
        st.info("No academic terms are available for the completion trend.")
        return

    trend_rows = []

    for _, term in terms_df.iterrows():
        term_id = int(term["term_id"])
        term_code = str(term["term_code"])

        lifecycle_count = conn.query("""
            SELECT COUNT(*) AS record_count
            FROM student_lifecycle_status
            WHERE term_id = :term_id;
        """, params={"term_id": term_id}, ttl=0)

        has_data = (
            not lifecycle_count.empty and
            int(lifecycle_count.iloc[0]["record_count"]) > 0
        )

        completion_rate = None

        if has_data:
            term_df, _ = load_students(active_program, term_id)

            if not term_df.empty:
                total_students = len(term_df)

                fully_completed = len(
                    term_df[
                        (term_df["coursework_display"] == "Completed") &
                        (term_df["comprehensive_exam_display"] == "Passed") &
                        (term_df["capstone_display"] == "Defended")
                    ]
                )

                if total_students > 0:
                    completion_rate = fully_completed / total_students * 100

        trend_rows.append({
            "term_code": term_code,
            "completion_rate": completion_rate,
            "sort_date": term["start_date"]
        })

    trend_df = pd.DataFrame(trend_rows).sort_values("sort_date")

    fig = px.line(
        trend_df,
        x="term_code",
        y="completion_rate",
        markers=True,
        text="completion_rate",
        labels={"term_code": "Academic Term", "completion_rate": "Completion Rate (%)"}
    )

    fig.update_layout(
        height=377,
        yaxis_title="Completion Rate (%)",
        xaxis_title="Academic Term",
        yaxis=dict(range=[-5, 115], fixedrange=True),
        xaxis=dict(fixedrange=True),
        hovermode="x unified",
        margin=dict(l=10, r=10, t=30, b=10)
    )

    fig.update_traces(
        line_color="#D50000",
        marker=dict(color="#FFAE00", size=8),
        line_width=3,
        texttemplate="%{text:.2f}%",
        textposition="top center",
        textfont=dict(size=12, color="var(--text-color)"),
        connectgaps=False
    )

    st.subheader(
        "Completion Trend — Last 4 Terms",
        help="Shows the percentage of students who fully completed Coursework, Comprehensive Exam, and Capstone in each academic term."
    )
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
# ------------------------------------------------------------------
# VIEW 1: STUDENT ROSTER (Dashboard)
# ------------------------------------------------------------------
def render_student_list(df_all):
    # ----------------- Enterprise Roster Grid CSS -----------------
    st.markdown(
        """
        <style>
        .roster-th { font-size: 0.85rem; font-weight: 700; color: #666; text-transform: uppercase; }
        .roster-th-divider { border-bottom: 2px solid #ddd; margin: 0.5rem 0 1rem 0; }
        .roster-row-divider { border-bottom: 1px solid #eee; margin: 0.5rem 0; }
        .roster-cell-text, .roster-cell-id { font-size: 0.9rem; color: var(--text-color); }
        .status-pill { background-color: #f0f2f6; padding: 4px 8px; border-radius: 12px; font-size: 0.8rem; color: #31333F !important; font-weight: 600; }
        .sr-risk-pill { background-color: #D500001A; color: #D50000; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 600; white-space: nowrap; }
        .sr-risk-none { background-color: #0080001A; color: #008000; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 600; white-space: nowrap; }
        .sr-risk-row {background-color: #FDE2E2 !important; border-radius: 6px; }
        /* Force standard buttons to allow multi-line text */
        div[data-testid="stButton"] button p {
            white-space: normal !important;
            line-height: 1.2 !important;
            text-align: center !important;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

    # Helper function to reuse the exact same grid layout across multiple pages
    def render_roster_grid(display_df, key_prefix):
        col_widths = [0.9, 1.5, 0.8, 1.5, 1.2, 1.2, 1.4, 0.9, 0.9, 0.8]
        header_labels = [
            "STUDENT ID", "NAME", "COHORT", "ADVISER",
            "COURSEWORK", "COMP EXAM", "CAPSTONE", "LAST UPDATE", "RISK", "ACTION"
        ]

        header_cols = st.columns(col_widths, vertical_alignment="center")
        for col, label in zip(header_cols, header_labels):
            col.markdown(f'<div class="roster-th">{label}</div>', unsafe_allow_html=True)
        st.markdown('<div class="roster-th-divider"></div>', unsafe_allow_html=True)

        if display_df.empty:
            st.info("No students match the current filters.")
        else:
            with st.container(border=False, height=500):
                for row in display_df.to_dict("records"):
                    r_cols = st.columns(col_widths, vertical_alignment="center")

                    r_cols[0].markdown(
                        f'<span class="roster-cell-id">{row.get("Student ID", "")}</span>',
                        unsafe_allow_html=True
                    )
                    r_cols[1].markdown(
                        f'<span class="roster-cell-text" style="font-weight: bold;">{row.get("Name", "")}</span>',
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

                    risk_status = str(row.get("Risk Status", ""))

                    r_cols[8].markdown(
                        '<span class="sr-risk-pill">AT RISK</span>'
                        if risk_status.strip() == "At Risk"
                        else '<span class="sr-risk-none">ON TRACK</span>',
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

                    st.markdown(
                        '<div class="roster-row-divider"></div>',
                        unsafe_allow_html=True
                    )

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
    # --- Shared Summary Data ---
    df_summary = df_all.copy()
    selected_adviser = "All"
    selected_cohort = "All"
    current_user = st.session_state.user_info.get("full_name", "")
    # --- RENDER EXECUTIVE DASHBOARD ---
    if st.session_state.admin_view == "Executive Dashboard":
        # --- Dashboard Filters (No Search or Sort) ---
        term_col, cohort_col, adv_col = st.columns(3)

        # =========================================================
        # TERM FILTER
        # =========================================================
        with term_col:
            available_terms_df = load_available_terms()
            current_term = get_current_term()

            if available_terms_df.empty:
                st.warning("No academic terms are currently available.")
                selected_term_id = None
                selected_term_code = None

            else:
                term_options = available_terms_df["term_id"].tolist()

                # Default to the current term
                if "selected_term_id" not in st.session_state:
                    if current_term is not None:
                        st.session_state.selected_term_id = int(
                            current_term["term_id"]
                        )
                    else:
                        st.session_state.selected_term_id = int(
                            term_options[0]
                        )

                # Make sure saved selection still exists
                if st.session_state.selected_term_id not in term_options:
                    st.session_state.selected_term_id = int(
                        current_term["term_id"]
                        if current_term is not None
                        else term_options[0]
                    )

                selected_term_id = st.selectbox(
                    "Filter by term",
                    options=term_options,
                    index=term_options.index(
                        st.session_state.selected_term_id
                    ),
                    format_func=lambda term_id: format_term_label(
                        available_terms_df.loc[
                            available_terms_df["term_id"] == term_id,
                            "term_code"
                        ].iloc[0]
                    ),
                    key="dashboard_term_filter"
                )

                st.session_state.selected_term_id = selected_term_id

                selected_term_code = available_terms_df.loc[
                    available_terms_df["term_id"] == selected_term_id,
                    "term_code"
                ].iloc[0]

        # =========================================================
        # COHORT FILTER
        # =========================================================
        with cohort_col:
            valid_cohorts = sorted(
                [
                    str(c)
                    for c in df_all["cohort"].dropna().unique().tolist()
                    if str(c).strip()
                ],
                key=get_cohort_val
            )

            selected_cohort = st.selectbox(
                "Filter by cohort",
                ["All"] + valid_cohorts,
                key="cohort_filter"
            )

        # =========================================================
        # ADVISER FILTER
        # =========================================================
        with adv_col:
            valid_advisers = sorted(
                [
                    str(a)
                    for a in df_all["adviser"].dropna().unique().tolist()
                    if str(a).strip()
                ]
            )

            current_user = st.session_state.user_info.get("full_name", "")
            user_role = st.session_state.user_info.get("role", "")

            if "Advisor" in user_role or "Faculty" in user_role:
                adv_view = st.selectbox(
                    "Adviser View",
                    ["My Advisees", "All Students"],
                    key="adv_view_toggle"
                )

                selected_adviser = (
                    current_user
                    if adv_view == "My Advisees"
                    else "All"
                )
            else:
                default_idx = (
                    valid_advisers.index(current_user) + 1
                    if current_user in valid_advisers
                    else 0
                )

                selected_adviser = st.selectbox(
                    "Filter by Adviser",
                    ["All"] + valid_advisers,
                    index=default_idx,
                    key="adviser_filter"
                )

        # =========================================================
        # SELECTED TERM HEADING
        # =========================================================
        if selected_term_code:
            st.markdown(
                f"#### {format_term_label(selected_term_code)}"
            )

        # =========================================================
        # APPLY COHORT + ADVISER FILTERS
        # =========================================================
        df_summary = df_all.copy()

        if selected_term_id is not None:
            df_summary = df_summary[
                df_summary["term_id"] == selected_term_id
            ]

        if selected_cohort != "All":
            df_summary = df_summary[
                df_summary["cohort"].astype(str) == selected_cohort
            ]

        if selected_adviser != "All":
            df_summary = df_summary[
                df_summary["adviser"].astype(str) == selected_adviser
            ]

        total_students = len(df_summary)
        cw_completed = len(
            df_summary[df_summary["coursework_display"] == "Completed"]
        )
        exam_passed = len(
            df_summary[df_summary["comprehensive_exam_display"] == "Passed"]
        )
        capstone_defended = len(
            df_summary[df_summary["capstone_display"] == "Defended"]
        )

        evaluated_df = df_summary[
            df_summary["graduate_on_time"].notna() &
            (df_summary["graduate_on_time"].astype(str).str.strip() != "") &
            (~df_summary["graduate_on_time"].astype(str).str.lower().isin(
                ["n/a", "none"]
            ))
        ]

        grad_numerator = len(
            evaluated_df[
                evaluated_df["graduate_on_time"]
                .astype(str)
                .str.lower()
                .isin(["yes", "y", "true", "1"])
            ]
        )

        grad_denominator = total_students

        on_time_rate = (
            grad_numerator / grad_denominator * 100
            if grad_denominator > 0
            else 0.0
        )

        fully_completed = len(
            df_summary[
                (df_summary["coursework_display"] == "Completed") &
                (df_summary["comprehensive_exam_display"] == "Passed") &
                (df_summary["capstone_display"] == "Defended")
            ]
        )

        completion_rate = (
            int((fully_completed / total_students * 100))
            if total_students > 0
            else 0
        )

        remaining_students = int(total_students - fully_completed)
        missing_coursework = len(
            df_summary[df_summary["coursework_display"] != "Completed"]
        )
        missing_exam = len(
            df_summary[df_summary["comprehensive_exam_display"] != "Passed"]
        )
        missing_capstone = len(
            df_summary[df_summary["capstone_display"] != "Defended"]
        )

        # --- TERM-OVER-TERM COMPARISON LOGIC ---
        all_cohorts_sorted = sorted(
            [
                str(c)
                for c in df_all["cohort"].dropna().unique()
                if str(c).strip()
            ],
            key=get_cohort_val
        )

        prior_cohort = None

        if selected_cohort != "All" and selected_cohort in all_cohorts_sorted:
            idx = all_cohorts_sorted.index(selected_cohort)

            if idx > 0:
                prior_cohort = all_cohorts_sorted[idx - 1]

        if prior_cohort:
            df_prior = df_all[
                df_all["cohort"].astype(str) == prior_cohort
            ]

            p_total = len(df_prior)

            p_eval = df_prior[
                df_prior["graduate_on_time"].notna() &
                (df_prior["graduate_on_time"].astype(str).str.strip() != "") &
                (~df_prior["graduate_on_time"].astype(str).str.lower().isin(
                    ["n/a", "none"]
                ))
            ]

            p_grad_num = len(
                p_eval[
                    p_eval["graduate_on_time"]
                    .astype(str)
                    .str.lower()
                    .isin(["yes", "y", "true", "1"])
                ]
            )

            p_on_time_rate = (
                p_grad_num / p_total * 100
                if p_total > 0
                else 0.0
            )

            p_comp = len(
                df_prior[
                    (df_prior["coursework_display"] == "Completed") &
                    (df_prior["comprehensive_exam_display"] == "Passed") &
                    (df_prior["capstone_display"] == "Defended")
                ]
            )

            p_comp_rate = (
                int((p_comp / p_total * 100))
                if p_total > 0
                else 0.0
            )

            p_rem = p_total - p_comp

            grad_delta_str = (
                f"{on_time_rate - p_on_time_rate:+.1f}% vs {prior_cohort}"
            )

            comp_delta_str = (
                f"{completion_rate - p_comp_rate:+.0f}% vs {prior_cohort}"
            )

            rem_delta_str = (
                f"{remaining_students - p_rem:+} vs {prior_cohort}"
            )

        else:
            grad_delta_str = (
                f"{grad_numerator} out of {total_students} students"
            )

            comp_delta_str = (
                f"{fully_completed} out of {total_students} students"
            )

            rem_delta_str = (
                f"{remaining_students} out of {total_students} students"
            )
            grad_color_mode = "normal"
            comp_color_mode = "normal"
            rem_color_mode = "inverse"

        top_c1, top_c2, top_c3, top_c4 = st.columns(4)

        top_c1.metric(
            label="Total Students",
            value=total_students,
            help="Total students matching filters."
        )

        top_c2.metric(
            label="Coursework",
            value=cw_completed,
            help="Completed required core coursework."
        )

        top_c3.metric(
            label="Comprehensive Exam",
            value=exam_passed,
            help="Passed Comprehensive Examination."
        )

        top_c4.metric(
            label="Capstones",
            value=capstone_defended,
            help="Defended and finalized Capstone project."
        )

        st.write("")

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
            </style>
            """,
            unsafe_allow_html=True
        )

        bot_c1, bot_c2, bot_c3 = st.columns(3)
        bot_c1.metric(label="On-Time Grad Rate", value=f"{on_time_rate:.1f}%", delta=grad_delta_str, delta_color=grad_color_mode)
        bot_c2.metric(label="Overall Completion", value=f"{completion_rate}%", delta=comp_delta_str, delta_color=comp_color_mode)
        bot_c3.metric(label="Remaining Students", value=remaining_students, delta=rem_delta_str, delta_color=rem_color_mode)
        
        col1, col2 = st.columns(2)

        with col1:
            with st.container(border=True): 
                st.subheader("Lifecycle Stage Breakdown", help="Distribution of students across their current active lifecycle stage.")

                # 1. Mutually Exclusive Current Stage Logic
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

    milestones_df = fetch_student_lifecycle(student["student_number"], st.session_state.get("selected_term_id"))
    
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
    tab_courses, tab_milestones, tab_remarks = st.tabs(["📖 Course Progress", "🚩 Lifecycle Milestones", "📝 Remarks & Admin Actions"])

    with tab_courses:
        st.markdown("##### Enrolled Curriculum & Course Records")
        courses_df = fetch_student_courses(student["student_number"], st.session_state.get("selected_term_id"))
        if not courses_df.empty: st.dataframe(courses_df, hide_index=True, use_container_width=True)
        else: st.info("No course enrollment records populated for this student.")

    with tab_milestones:
        st.markdown("##### Milestone Clearances")
        milestones_df = fetch_student_lifecycle(student["student_number"], st.session_state.get("selected_term_id"))
        if not milestones_df.empty: st.dataframe(milestones_df, hide_index=True, use_container_width=True)
        else: st.info("No milestone events recorded in `student_lifecycle_status`.")

    with tab_remarks:
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
                                    
                            s.execute(
                                text("""
                                    UPDATE students_normalized 
                                    SET remarks = :rem, adviser_id = :adv
                                    WHERE student_number = :sn;
                                """),
                                {"rem": new_remarks, "adv": adv_id, "sn": int(student["student_number"])}
                            )
                            lifecycle_updates = [
                                ("coursework", cw_val),
                                ("comprehensive_exam", ce_val),
                                ("capstone", cap_val)
                            ]

                            for stage_name, new_status_name in lifecycle_updates:
                                lookup_sql = text("""
                                    SELECT
                                        stg.stage_id,
                                        sts.status_id
                                    FROM lifecycle_stage stg,
                                         lifecycle_status sts
                                    WHERE stg.stage_name = :stage_name
                                      AND sts.status_name = :status_name;
                                """)

                                lookup_result = s.execute(
                                    lookup_sql,
                                    {
                                        "stage_name": stage_name,
                                        "status_name": new_status_name
                                    }
                                ).fetchone()

                                if not lookup_result:
                                    raise ValueError(
                                        f"Invalid lifecycle status: {stage_name} → {new_status_name}"
                                    )

                                stage_id = lookup_result.stage_id
                                new_status_id = lookup_result.status_id

                                current_sql = text("""
                                    SELECT
                                        student_lifecycle_status_id,
                                        status_id
                                    FROM student_lifecycle_status
                                    WHERE student_number = :sn
                                      AND stage_id = :stage_id
                                      AND term_id = :term_id;
                                """)

                                current_result = s.execute(current_sql, {"sn": int(student["student_number"]), "stage_id": stage_id, "term_id": st.session_state.get("selected_term_id")}).fetchone()

                                if current_result:
                                    lifecycle_status_id = current_result.student_lifecycle_status_id
                                    previous_status_id = current_result.status_id

                                    if previous_status_id != new_status_id:
                                        history_sql = text("""
                                            INSERT INTO lifecycle_status_history (
                                                student_lifecycle_status_id,
                                                previous_status_id,
                                                new_status_id,
                                                changed_by,
                                                updated_date
                                            )
                                            VALUES (
                                                :lifecycle_status_id,
                                                :previous_status_id,
                                                :new_status_id,
                                                :changed_by,
                                                NOW()
                                            );
                                        """)

                                        s.execute(
                                            history_sql,
                                            {
                                                "lifecycle_status_id": lifecycle_status_id,
                                                "previous_status_id": previous_status_id,
                                                "new_status_id": new_status_id,
                                                "changed_by": user["user_id"]
                                            }
                                        )

                                        update_sql = text("""
                                            UPDATE student_lifecycle_status
                                            SET
                                                status_id = :new_status_id,
                                                last_updated_date = NOW()
                                            WHERE student_lifecycle_status_id = :lifecycle_status_id;
                                        """)

                                        s.execute(
                                            update_sql,
                                            {
                                                "new_status_id": new_status_id,
                                                "lifecycle_status_id": lifecycle_status_id
                                            }
                                        )

                                else:
                                    insert_current_sql = text("""
                                        INSERT INTO student_lifecycle_status (
                                            student_number,
                                            stage_id,
                                            status_id,
                                            term_id,
                                            last_updated_date
                                        )
                                        VALUES (
                                            :sn,
                                            :stage_id,
                                            :new_status_id,
                                            :term_id,
                                            NOW()
                                        );
                                    """)

                                    s.execute(insert_current_sql, {"sn": int(student["student_number"]), "stage_id": stage_id, "new_status_id": new_status_id, "term_id": st.session_state.get("selected_term_id")})
                            s.commit()
                        
                        log_security_event(user["user_id"], "STUDENT_RECORD_UPDATED", f"Modified record for Student ID {student['student_number']} (CW: {new_cw}, Exam: {new_ce}, Capstone: {new_cap}).")
                        st.success("Record successfully updated in Supabase!")
                        load_students.clear()
                        fetch_student_lifecycle.clear()
                        st.rerun()
                        
                    except Exception as err:
                        st.error(f"Write operation failed: {err}")

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
elif st.session_state.admin_view == "Academic Terms":
    render_academic_terms()
elif st.session_state.admin_view == "Add Data":
    render_add_data()
else:
    try:
            df_all, last_sync = load_students(
                ACTIVE_PROGRAM,
                st.session_state.get("selected_term_id")
            )
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
