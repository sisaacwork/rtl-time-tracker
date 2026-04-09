"""
RTL Time Tracker — Streamlit Web App
=====================================
Reads from and writes back to individual staff Excel tracking files.

To run locally:
    pip install -r requirements.txt
    streamlit run time_tracker_app.py

Put this file in the same folder as the *_2026TimeTracking.xlsx files.
"""

import streamlit as st
import pandas as pd
import openpyxl
import requests
import base64
import io
from pathlib import Path
from datetime import datetime, date, timedelta
import plotly.graph_objects as go

# ══════════════════════════════════════════════════════════════════════════════
# PAGE CONFIG
# ══════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="RTL Time Tracker",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ══════════════════════════════════════════════════════════════════════════════
# CVU BRAND TOKENS  (from Brand Guidelines V3.0 – dark theme)
# ══════════════════════════════════════════════════════════════════════════════

CVU_BLACK    = "#171717"   # Primary background
CVU_SURFACE  = "#282828"   # Card / chart background
CVU_BORDER   = "#4F4F4F"   # Grid lines, dividers
CVU_WHITE    = "#FCFCFC"   # Primary text
CVU_GRAY     = "#9E9E9E"   # Secondary text / axis labels
CVU_GREEN    = "#66CC00"   # Volt Green – primary accent

# Ordered accent palette for chart series (Indigo, Solar, Aqua, Teal, Plum, Ember)
CVU_PALETTE = [
    "#516BFF",
    "#FF9F18",
    "#54D9E7",
    "#34C684",
    "#C63AD2",
    "#FA3F26",
]

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', Arial, sans-serif;
}

/* ── Sidebar ── */
section[data-testid="stSidebar"] {
    background-color: #171717 !important;
    border-right: 1px solid #282828;
}
section[data-testid="stSidebar"] * {
    color: #FCFCFC !important;
}

/* ── Metric cards ── */
div[data-testid="stMetric"] {
    background: #282828;
    border-radius: 6px;
    padding: 12px 16px;
    border-left: 3px solid #B4E817;
}

/* ── Tab active indicator ── */
div[data-testid="stTabs"] button[aria-selected="true"] {
    color: #B4E817 !important;
    border-bottom-color: #B4E817 !important;
}

/* ── Primary buttons ── */
.stButton > button[kind="primary"] {
    background-color: #B4E817 !important;
    color: #171717 !important;
    font-weight: 600;
    border: none;
    font-family: 'Inter', Arial, sans-serif;
}
.stButton > button[kind="primary"]:hover {
    background-color: #c8f020 !important;
    color: #171717 !important;
}

/* ── Category section headers ── */
.cat-header {
    background: #282828;
    border-left: 3px solid #B4E817;
    padding: 6px 12px;
    border-radius: 0 4px 4px 0;
    font-weight: 600;
    color: #FCFCFC;
    font-size: 0.88rem;
    letter-spacing: 0.03em;
    margin-top: 14px;
    margin-bottom: 4px;
}
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

# When running locally, set DATA_DIR in .streamlit/secrets.toml to point at
# your OneDrive folder so reads and writes go to the right place.
# On Streamlit Cloud this falls back to the repo directory (files come from GitHub).
try:
    _candidate = Path(st.secrets["DATA_DIR"])
    # Only use the secret path if it actually exists on this machine.
    # On Streamlit Cloud the OneDrive path won't exist, so it falls back
    # to the repo directory where the Excel files are committed.
    DATA_DIR = _candidate if _candidate.exists() else Path(__file__).parent
except Exception:
    DATA_DIR = Path(__file__).parent

STAFF = {
    "D. Safarik":  "DSafarik_2026TimeTracking.xlsx",
    "I. Work":     "IWork_2026TimeTracking.xlsx",
    "S. Ursini":   "SUrsini_2026TimeTracking.xlsx",
    "W. Miranda":  "WMiranda_2026TimeTracking.xlsx",
}
STAFF = {name: DATA_DIR / fname for name, fname in STAFF.items()}

TRACKER_SHEET  = "Tracking"
DATE_ROW       = 5   # Row with actual datetime values (row 4 has =C5 formulas)
DATA_START_ROW = 7   # First row with task data
DATE_START_COL = 3   # Column C = first date column

GITHUB_OWNER  = "sisaacwork"
GITHUB_REPO   = "rtl-time-tracker"
GITHUB_BRANCH = "main"

# Codes to treat as absences — excluded from the 900s/other and funded/unfunded charts
ABSENCE_CODES = {"120", "121", "122", "123", "123", "124"}

# Sub-codes that count as externally funded work
FUNDED_CODES = {
    "903b", "903c",
    "904a", "904b",
    "905a", "905b",
    "906a", "906b",
    "910", "915", "916", "917",
}

# ══════════════════════════════════════════════════════════════════════════════
# FINANCIAL TRACKING CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

FINANCES_FILE = "finances.xlsx"

INCOME_STATUSES = ["Paid", "Invoiced", "Contracted", "Verbal", "Pipeline"]

# Base accounting codes — all staff costs are bundled under 903.
# Users can add custom codes inside the app.
ACCOUNTING_CODES = {
    "901a": "Venice Research Office",
    "901b": "Canada Research Office",
    "902":  "Seed Funding",
    "903":  "Short-term Projects",
    "904":  "Cities for People",
    "905":  "Sustainability Program",
    "906":  "T+U Innovation",
    "907":  "Megatalls Assembly",
    "910":  "ClimateWorks Code Research",
    "917":  "Commissioned Research",
}

STATUS_COLORS = {
    "Paid":       "#66CC00",   # CVU_GREEN
    "Invoiced":   "#516BFF",   # CVU_PALETTE[0]
    "Contracted": "#54D9E7",   # CVU_PALETTE[2]
    "Verbal":     "#FF9F18",   # CVU_PALETTE[1]
    "Pipeline":   "#4F4F4F",   # CVU_BORDER (muted)
}

# ══════════════════════════════════════════════════════════════════════════════
# GITHUB / CLOUD HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _github_token():
    """Return the GitHub token from Streamlit secrets, or None if running locally."""
    try:
        return st.secrets["GITHUB_TOKEN"]
    except Exception:
        return None


def _is_cloud() -> bool:
    """True when running on Streamlit Community Cloud (token available)."""
    return _github_token() is not None


def _github_commit(filename: str, content_bytes: bytes) -> tuple:
    """
    Upload (or update) a file in the GitHub repo.
    Returns (success: bool, message: str).
    """
    token = _github_token()
    if not token:
        return False, "No GitHub token configured."

    url = (
        f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
        f"/contents/{filename}"
    )
    headers = {
        "Authorization": f"token {token}",
        "Accept":        "application/vnd.github.v3+json",
    }

    # GET current SHA (required for updating an existing file)
    r   = requests.get(url, headers=headers)
    sha = r.json().get("sha") if r.status_code == 200 else None

    payload = {
        "message": (
            f"Time entry update: {filename} "
            f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC]"
        ),
        "content": base64.b64encode(content_bytes).decode(),
        "branch":  GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(url, headers=headers, json=payload)
    if resp.status_code in (200, 201):
        return True, "Saved to GitHub."
    return False, f"GitHub error {resp.status_code}: {resp.text[:200]}"


# ══════════════════════════════════════════════════════════════════════════════
# FINANCES DATA — load / save
# ══════════════════════════════════════════════════════════════════════════════

TXNS_COLS = ["id", "date", "type", "amount", "code", "code_name",
             "description", "status", "notes"]


def _empty_transactions() -> pd.DataFrame:
    return pd.DataFrame(columns=TXNS_COLS)


def _fetch_finances_bytes():
    """
    Return raw bytes of finances.xlsx from GitHub (cloud) or disk (local).
    Returns None if the file doesn't exist yet.
    """
    if _is_cloud():
        url = (
            f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
            f"/contents/{FINANCES_FILE}?ref={GITHUB_BRANCH}"
        )
        resp = requests.get(url, headers={
            "Authorization": f"token {_github_token()}",
            "Accept":        "application/vnd.github.v3+json",
        })
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise RuntimeError(f"GitHub error fetching finances: {resp.status_code}")
        return base64.b64decode(resp.json()["content"].replace("\n", ""))
    else:
        local = DATA_DIR / FINANCES_FILE
        if not local.exists():
            return None
        return local.read_bytes()


@st.cache_data(ttl=30, show_spinner="Loading financial data...")
def load_finances():
    """
    Returns (transactions_df, settings_dict, all_codes_dict).
    Creates empty structures if the file doesn't exist yet.
    """
    raw = _fetch_finances_bytes()

    if raw is None:
        return _empty_transactions(), {}, dict(ACCOUNTING_CODES)

    wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)

    # --- transactions sheet ---
    records = []
    if "transactions" in wb.sheetnames:
        ws = wb["transactions"]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[0] is None:
                continue
            records.append(dict(zip(TXNS_COLS, row)))

    txns = pd.DataFrame(records, columns=TXNS_COLS) if records else _empty_transactions()
    if not txns.empty:
        txns["date"]   = pd.to_datetime(txns["date"])
        txns["amount"] = pd.to_numeric(txns["amount"], errors="coerce").fillna(0.0)

    # --- settings sheet ---
    settings     = {}
    custom_codes = {}
    if "settings" in wb.sheetnames:
        ws = wb["settings"]
        for row in ws.iter_rows(min_row=1, values_only=True):
            if row[0] is None:
                continue
            key = str(row[0])
            val = row[1]
            if key.startswith("custom_code_"):
                code = key[len("custom_code_"):]
                custom_codes[code] = str(val) if val else code
            else:
                try:
                    settings[key] = float(val) if val is not None else 0.0
                except (TypeError, ValueError):
                    settings[key] = val

    all_codes = dict(ACCOUNTING_CODES)
    all_codes.update(custom_codes)

    return txns, settings, all_codes


def save_finances(txns: pd.DataFrame, settings: dict, custom_codes: dict) -> tuple:
    """
    Persist transactions + settings to finances.xlsx and commit to GitHub.
    Returns (success: bool, message: str).
    """
    wb    = openpyxl.Workbook()
    ws_t  = wb.active
    ws_t.title = "transactions"
    ws_t.append(TXNS_COLS)

    for _, row in txns.iterrows():
        ws_t.append([
            int(row.get("id", 0)),
            row["date"].date() if pd.notna(row.get("date")) else None,
            str(row.get("type", "")),
            float(row.get("amount", 0)),
            str(row.get("code", "")),
            str(row.get("code_name", "")),
            str(row.get("description", "")),
            str(row.get("status", "")),
            str(row.get("notes", "")),
        ])

    ws_s = wb.create_sheet("settings")
    for key, val in settings.items():
        ws_s.append([key, val])
    for code, name in custom_codes.items():
        ws_s.append([f"custom_code_{code}", name])

    buf = io.BytesIO()
    wb.save(buf)
    content_bytes = buf.getvalue()

    local_path = DATA_DIR / FINANCES_FILE
    if _is_cloud():
        ok, msg = _github_commit(FINANCES_FILE, content_bytes)
        if ok:
            local_path.write_bytes(content_bytes)
    else:
        local_path.write_bytes(content_bytes)
        ok, msg = True, "Saved."

    load_finances.clear()
    return ok, msg


# ══════════════════════════════════════════════════════════════════════════════
# EXCEL PARSING HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _date_cols(ws) -> dict:
    """Return {date_object: col_index} for every date column in the sheet."""
    out = {}
    for c in range(DATE_START_COL, ws.max_column + 1):
        v = ws.cell(row=DATE_ROW, column=c).value
        if isinstance(v, datetime):
            out[v.date()] = c
    return out


def _task_rows(ws) -> list:
    """Return [(task_name, row_index), ...] for every task row in the sheet."""
    out = []
    for r in range(DATA_START_ROW, ws.max_row + 1):
        v = ws.cell(row=r, column=1).value
        if v and isinstance(v, str) and v.strip():
            out.append((v.strip(), r))
    return out


def is_child(task: str) -> bool:
    """Child tasks start with '- '."""
    return task.startswith("- ")


def short_name(task: str) -> str:
    """Strip the '- ' prefix for cleaner display."""
    return task[2:].strip() if is_child(task) else task


def category_code(task: str) -> str:
    """Map any task to its top-level 3-digit code (e.g. '- 901a:...' → '900')."""
    t = task.lstrip("- ").strip()
    if t and t[0].isdigit():
        return t[0] + "00"
    return "Other"


def task_subcode(task: str) -> str:
    """
    Extract the full alphanumeric sub-code from a task name.
    e.g. '- 903b: CTBUHx Chicago' → '903b'
         '- 120: Annual Leave'    → '120'
    Returns an empty string if no leading code is found.
    """
    import re
    t = task.lstrip("- ").strip()
    m = re.match(r"^(\d+[a-z]*)", t, re.IGNORECASE)
    return m.group(1).lower() if m else ""


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING  (cached 30 s — Refresh button busts manually)
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=30, show_spinner="Loading tracker data...")
def load_all() -> pd.DataFrame:
    """
    Read every staff Excel file → single long-format DataFrame:
        person | date | task | hours | week | month | quarter | code | category
    Only rows with hours > 0 are included.
    """
    records = []
    for person, path in STAFF.items():
        if not _is_cloud() and not path.exists():
            st.warning(f"File not found, skipping: {path.name}")
            continue
        try:
            wb = _fetch_workbook(person, data_only=True)
        except Exception as e:
            st.warning(f"Could not load {path.name}: {e}")
            continue
        ws = wb[TRACKER_SHEET]
        dc = _date_cols(ws)
        tr = _task_rows(ws)
        for task, r in tr:
            for d, c in dc.items():
                h = ws.cell(row=r, column=c).value
                if h:
                    records.append({
                        "person": person,
                        "date":   d,
                        "task":   task,
                        "hours":  float(h),
                    })

    if not records:
        return pd.DataFrame(columns=["person", "date", "task", "hours",
                                      "week", "month", "quarter", "code", "category"])

    df = pd.DataFrame(records)
    df["date"]    = pd.to_datetime(df["date"])
    df["week"]    = df["date"].dt.to_period("W").astype(str)
    df["month"]   = df["date"].dt.to_period("M").astype(str)
    df["quarter"] = df["date"].dt.to_period("Q").astype(str)
    df["code"]    = df["task"].apply(category_code)

    # Build code → full parent category label (e.g. "900" → "900 Research & Thought Leadership")
    code_names = {}
    for task in df["task"].unique():
        if not is_child(task):
            code_names[category_code(task)] = task
    df["category"] = df["code"].map(code_names).fillna(df["code"])

    return df


# ══════════════════════════════════════════════════════════════════════════════
# PER-PERSON DATA ACCESS
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_workbook(person: str, data_only: bool = True):
    """
    Load a person's workbook.
    On Streamlit Cloud: fetches the latest bytes directly from GitHub API
    so the app always reflects the current state of the repo, not a stale
    snapshot from deploy time.
    Locally: reads straight from disk as before.
    """
    filename = STAFF[person].name
    if _is_cloud():
        url = (
            f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
            f"/contents/{filename}?ref={GITHUB_BRANCH}"
        )
        resp = requests.get(url, headers={
            "Authorization": f"token {_github_token()}",
            "Accept":        "application/vnd.github.v3+json",
        })
        if resp.status_code != 200:
            raise FileNotFoundError(f"Could not fetch {filename} from GitHub: {resp.status_code}")
        raw = base64.b64decode(resp.json()["content"].replace("\n", ""))
        return openpyxl.load_workbook(io.BytesIO(raw), data_only=data_only)
    else:
        return openpyxl.load_workbook(STAFF[person], data_only=data_only)


def workdays(person: str) -> list:
    """Sorted list of all tracked working dates for this person."""
    wb = _fetch_workbook(person, data_only=True)
    return sorted(_date_cols(wb[TRACKER_SHEET]).keys())


def hours_on_date(person: str, d: date) -> dict:
    """Return {task_name: hours} for a given person + date (0 if not entered)."""
    wb = _fetch_workbook(person, data_only=True)
    ws = wb[TRACKER_SHEET]
    dc = _date_cols(ws)
    c  = dc.get(d)
    if c is None:
        return {}
    return {
        t: float(ws.cell(row=r, column=c).value or 0)
        for t, r in _task_rows(ws)
    }


def task_structure(person: str) -> list:
    """Return [(task_name, is_child_bool), ...] in spreadsheet order."""
    wb = _fetch_workbook(person, data_only=True)
    ws = wb[TRACKER_SHEET]
    return [(t, is_child(t)) for t, _ in _task_rows(ws)]


def save_hours(person: str, d: date, hours: dict) -> tuple:
    """
    Write updated hours back to the person's Excel file.

    Two-step approach:
      1. Open data_only=True to resolve column index for the target date.
      2. Open without data_only (preserving formulas) to write the values.

    If running on Streamlit Cloud, commits the result to GitHub instead of
    writing directly to disk.

    Returns (success: bool, message: str).
    """
    path = STAFF[person]

    # Step 1: resolve date → column (data_only=True to get computed date values)
    wb_r = _fetch_workbook(person, data_only=True)
    dc   = _date_cols(wb_r[TRACKER_SHEET])
    c    = dc.get(d)
    if c is None:
        return False, f"Date {d} not found in the tracker spreadsheet."

    # Step 2: open without data_only so formulas stay intact
    wb = _fetch_workbook(person, data_only=False)
    ws = wb[TRACKER_SHEET]
    for t, r in _task_rows(ws):
        if t in hours:
            v = hours[t]
            ws.cell(row=r, column=c).value = v if v > 0 else None

    if _is_cloud():
        buf = io.BytesIO()
        wb.save(buf)
        content_bytes = buf.getvalue()
        ok, msg = _github_commit(path.name, content_bytes)
        if ok:
            # Also write to the local snapshot so load_all sees fresh data
            # without waiting for a GitHub API round-trip on the next read.
            path.write_bytes(content_bytes)
    else:
        wb.save(path)
        ok, msg = True, "Saved to Excel."

    load_all.clear()
    return ok, msg


# ══════════════════════════════════════════════════════════════════════════════
# CHART THEME HELPER
# ══════════════════════════════════════════════════════════════════════════════

def _chart_base(**overrides) -> dict:
    """
    Base Plotly layout dict with CVU dark-theme styling.
    Pass keyword args to override any key.
    """
    layout = dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor=CVU_BLACK,
        font=dict(family="Inter, Arial, sans-serif", color=CVU_WHITE, size=12),
        xaxis=dict(
            gridcolor=CVU_BORDER,
            zerolinecolor=CVU_BORDER,
            tickfont=dict(color=CVU_GRAY, size=11),
        ),
        yaxis=dict(
            gridcolor=CVU_BORDER,
            zerolinecolor=CVU_BORDER,
            tickfont=dict(color=CVU_GRAY, size=11),
            automargin=True,
        ),
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(color=CVU_WHITE, size=11),
        ),
        margin=dict(l=10, r=20, t=36, b=30),
        hoverlabel=dict(
            bgcolor=CVU_SURFACE,
            font=dict(color=CVU_WHITE, family="Inter, Arial, sans-serif"),
        ),
    )
    layout.update(overrides)
    return layout


# ══════════════════════════════════════════════════════════════════════════════
# VIEW: DAILY ENTRY
# ══════════════════════════════════════════════════════════════════════════════

def view_daily_entry(person: str):
    st.subheader("Daily Time Entry")

    days = workdays(person)
    if not days:
        st.error("No tracked dates found in this person's file.")
        return

    today   = date.today()
    default = today if today in days else days[-1]

    selected = st.date_input(
        "Select date",
        value=default,
        min_value=days[0],
        max_value=days[-1],
    )

    if selected not in days:
        st.warning("That date is not a tracked workday in the spreadsheet.")
        return

    current   = hours_on_date(person, selected)
    structure = task_structure(person)

    # Group tasks into (parent_category, [(child_task, current_hours), ...])
    groups       = []
    cur_parent   = None
    cur_children = []
    for t, child in structure:
        if not child:
            if cur_parent is not None:
                groups.append((cur_parent, cur_children))
            cur_parent   = t
            cur_children = []
        else:
            cur_children.append((t, current.get(t, 0.0)))
    if cur_parent is not None:
        groups.append((cur_parent, cur_children))

    st.caption(
        f"**{selected.strftime('%A, %B %d, %Y')}** — "
        "enter hours in 0.25 increments (0.25 = 15 min, 1.0 = 1 hr)"
    )

    # Inputs live outside a form so the total updates on every change
    new_vals = {}
    for parent, kids in groups:
        if not kids:
            continue
        st.markdown(
            f"<div class='cat-header'>{parent}</div>",
            unsafe_allow_html=True,
        )
        for task, cur_h in kids:
            label = short_name(task)
            left, right = st.columns([5, 1])
            left.markdown(f"&nbsp;&nbsp;&nbsp;{label}")
            v = right.number_input(
                label, label_visibility="collapsed",
                min_value=0.0, max_value=24.0, step=0.25,
                value=float(cur_h),
                key=f"de_{task}_{selected}",
            )
            new_vals[task] = v

    total    = sum(new_vals.values())
    overtime = max(0.0, total - 8.0)
    st.divider()
    tcol1, tcol2 = st.columns(2)
    tcol1.metric("Daily Total", f"{total:.2f} h")
    tcol2.metric("Overtime",    f"{overtime:.2f} h", delta_color="inverse")

    if st.button("Save Entry", type="primary", use_container_width=True):
        ok, msg = save_hours(person, selected, new_vals)
        if ok:
            st.toast("Entry saved successfully.")
            st.rerun()
        else:
            st.error(msg)


# ══════════════════════════════════════════════════════════════════════════════
# VIEW: BULK EDIT
# ══════════════════════════════════════════════════════════════════════════════

def view_bulk_edit(person: str):
    st.subheader("Bulk Edit")

    days = workdays(person)
    if not days:
        st.error("No tracked dates found.")
        return

    today = date.today()
    c1, c2 = st.columns(2)
    d_from = c1.date_input("From", value=max(days[0],  today - timedelta(days=13)))
    d_to   = c2.date_input("To",   value=min(days[-1], today))

    in_range = [d for d in days if d_from <= d <= d_to]
    if not in_range:
        st.info("No tracked workdays in that range.")
        return

    structure = task_structure(person)
    parents   = [t for t, ch in structure if not ch]
    sel_cats  = st.multiselect(
        "Filter by category (select which to show)",
        options=parents,
        default=parents,
    )

    allowed = set()
    cur_p   = None
    for t, ch in structure:
        if not ch:
            cur_p = t
        elif cur_p in sel_cats:
            allowed.add(t)

    child_tasks = [t for t, ch in structure if ch and t in allowed]
    if not child_tasks:
        st.info("No tasks to show for the selected categories.")
        return

    display_names = [short_name(t) for t in child_tasks]
    name_map      = dict(zip(display_names, child_tasks))

    rows        = []
    date_labels = []
    for d in in_range:
        dh    = hours_on_date(person, d)
        label = d.strftime("%Y-%m-%d (%a)")
        date_labels.append(label)
        row   = {dn: dh.get(ft, 0.0) for dn, ft in name_map.items()}
        rows.append(row)

    df_edit = pd.DataFrame(rows, index=date_labels)
    df_edit.index.name = "Date"

    st.caption("Edit cells directly. Click Save All Changes when done.")
    edited = st.data_editor(df_edit, use_container_width=True, num_rows="fixed")

    if st.button("Save All Changes", type="primary", use_container_width=True):
        saved  = 0
        errors = []
        for d, label in zip(in_range, date_labels):
            row     = edited.loc[label]
            updates = {
                name_map[col]: float(row[col])
                for col in row.index if col in name_map
            }
            ok, msg = save_hours(person, d, updates)
            if ok:
                saved += 1
            else:
                errors.append(msg)

        if errors:
            st.error("\n".join(errors))
        else:
            st.success(f"Saved {saved} of {len(in_range)} days.")
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# VIEW: HISTORY (personal time history)
# ══════════════════════════════════════════════════════════════════════════════

def view_history(person: str, df: pd.DataFrame):
    st.subheader("My History")

    pdf = df[(df["person"] == person) & df["task"].str.startswith("- ")]
    if pdf.empty:
        st.info("No time entries found yet.")
        return

    # Date range filter
    min_d = pdf["date"].min().date()
    max_d = pdf["date"].max().date()
    c1, c2 = st.columns(2)
    d_from = c1.date_input("From", value=min_d, key="hist_from")
    d_to   = c2.date_input("To",   value=max_d, key="hist_to")

    pdf = pdf[(pdf["date"].dt.date >= d_from) & (pdf["date"].dt.date <= d_to)]
    if pdf.empty:
        st.info("No entries in that date range.")
        return

    # KPI summary
    daily_totals = pdf.groupby("date")["hours"].sum()
    k1, k2, k3 = st.columns(3)
    k1.metric("Total Hours",      f"{pdf['hours'].sum():,.1f}")
    k2.metric("Days Worked",      str(pdf["date"].dt.date.nunique()))
    k3.metric("Avg Hours / Day",  f"{daily_totals.mean():.1f}")
    st.divider()

    # Chart 1: Hours by category
    cat_df = (
        pdf.groupby("category")["hours"]
           .sum()
           .reset_index()
           .sort_values("hours")
    )
    fig_cat = go.Figure(go.Bar(
        y=cat_df["category"],
        x=cat_df["hours"],
        orientation="h",
        marker_color=CVU_GREEN,
        hovertemplate="%{y}<br>Hours: %{x:.1f}<extra></extra>",
    ))
    fig_cat.update_layout(**_chart_base(
        title=dict(text="Hours by Category", font=dict(color=CVU_WHITE, size=14)),
        height=max(300, len(cat_df) * 55),
        yaxis=dict(automargin=True, tickfont=dict(color=CVU_WHITE, size=11),
                   gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER),
        xaxis=dict(title="Hours", tickfont=dict(color=CVU_GRAY),
                   gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER),
    ))
    st.plotly_chart(fig_cat, use_container_width=True)

    # Chart 2: Daily hours + cumulative line
    daily = pdf.groupby("date")["hours"].sum().reset_index().sort_values("date")
    daily["cumulative"] = daily["hours"].cumsum()

    fig_line = go.Figure()
    fig_line.add_trace(go.Bar(
        x=daily["date"],
        y=daily["hours"],
        name="Daily Hours",
        marker_color=CVU_GREEN,
        opacity=0.75,
        hovertemplate="Date: %{x|%b %d}<br>Hours: %{y:.2f}<extra></extra>",
    ))
    fig_line.add_trace(go.Scatter(
        x=daily["date"],
        y=daily["cumulative"],
        name="Cumulative",
        line=dict(color=CVU_PALETTE[0], width=2),
        yaxis="y2",
        hovertemplate="Date: %{x|%b %d}<br>Cumulative: %{y:.1f} hrs<extra></extra>",
    ))
    fig_line.update_layout(**_chart_base(
        title=dict(text="Daily Hours & Running Total", font=dict(color=CVU_WHITE, size=14)),
        height=320,
        yaxis=dict(title="Daily Hours", gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER,
                   tickfont=dict(color=CVU_GRAY)),
        yaxis2=dict(
            title="Cumulative Hours",
            overlaying="y",
            side="right",
            tickfont=dict(color=CVU_PALETTE[0]),
            showgrid=False,
            zerolinecolor=CVU_BORDER,
        ),
        legend=dict(orientation="h", y=1.08, font=dict(color=CVU_WHITE)),
    ))
    st.plotly_chart(fig_line, use_container_width=True)

    # Detailed log (collapsed)
    with st.expander("View detailed entry log"):
        tbl = (
            pdf.assign(Date=pdf["date"].dt.date)
               .groupby(["Date", "category", "task"])["hours"]
               .sum()
               .round(2)
               .reset_index()
               .sort_values(["Date", "category"])
               .rename(columns={"category": "Category", "task": "Task", "hours": "Hours"})
        )
        tbl["Task"] = tbl["Task"].apply(short_name)
        st.dataframe(tbl, hide_index=True, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# VIEW: TEAM OVERVIEW (manager dashboard)
# ══════════════════════════════════════════════════════════════════════════════

def view_team(df: pd.DataFrame):
    st.subheader("Team Overview Dashboard")
    if df.empty:
        st.info("No time data found. Make sure the Excel files are in the same folder as the app.")
        return

    # ── Filters ───────────────────────────────────────────────────────────────
    fc1, fc2 = st.columns(2)
    with fc1:
        quarters  = ["All"] + sorted(df["quarter"].unique().tolist())
        sel_q     = st.selectbox("Quarter", quarters)
    with fc2:
        all_staff = sorted(df["person"].unique())
        sel_staff = st.multiselect("Staff members", all_staff, default=all_staff)

    include_future = st.checkbox(
        "Include future entries (e.g. pre-entered vacation)",
        value=False,
    )

    fdf = df.copy()
    if sel_q != "All":
        fdf = fdf[fdf["quarter"] == sel_q]
    if sel_staff:
        fdf = fdf[fdf["person"].isin(sel_staff)]
    if not include_future:
        fdf = fdf[fdf["date"].dt.date <= date.today()]
    fdf = fdf[fdf["task"].str.startswith("- ")]   # child rows only

    if fdf.empty:
        st.info("Nothing matches the current filters.")
        return

    # ── KPI row ───────────────────────────────────────────────────────────────
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Hours Logged", f"{fdf['hours'].sum():,.1f}")
    k2.metric("Staff Members",      str(fdf["person"].nunique()))
    k3.metric("Unique Task Codes",  str(fdf["task"].nunique()))
    k4.metric("Days with Entries",  str(fdf["date"].dt.date.nunique()))
    st.divider()

    # Assign consistent colors to categories and staff
    cats          = sorted(fdf["category"].unique())
    cat_color_map = {cat: CVU_PALETTE[i % len(CVU_PALETTE)] for i, cat in enumerate(cats)}
    staff_list    = sorted(fdf["person"].unique())
    staff_colors  = {s: CVU_PALETTE[i % len(CVU_PALETTE)] for i, s in enumerate(staff_list)}

    # ─────────────────────────────────────────────────────────────────────────
    # CHART A + B: Summary donut pair — 900s split & funded/unfunded split
    # Absence codes (120-124) are excluded from both charts.
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("##### Time Allocation Summary")
    non_absence = fdf[~fdf["task"].apply(lambda t: task_subcode(t) in ABSENCE_CODES)]

    donut_left, donut_right = st.columns(2)

    # ── Donut A: 900-series vs all other codes ────────────────────────────────
    with donut_left:
        hrs_900   = non_absence[non_absence["code"] == "900"]["hours"].sum()
        hrs_other = non_absence[non_absence["code"] != "900"]["hours"].sum()
        total_ab  = hrs_900 + hrs_other

        if total_ab > 0:
            figA = go.Figure(go.Pie(
                labels=["900-Series", "Other Codes"],
                values=[hrs_900, hrs_other],
                hole=0.58,
                marker_colors=[CVU_GREEN, CVU_PALETTE[0]],
                hovertemplate="%{label}<br>%{value:.1f} hrs (%{percent})<extra></extra>",
                textinfo="percent",
                textfont=dict(color=CVU_WHITE, size=13, family="Inter, Arial, sans-serif"),
            ))
            figA.update_layout(**_chart_base(
                height=300,
                title=dict(text="Research (900s) vs Other",
                           font=dict(color=CVU_WHITE, size=13), x=0.5, xanchor="center"),
                showlegend=True,
                legend=dict(orientation="h", y=-0.08, x=0.5, xanchor="center"),
                margin=dict(t=50, b=40, l=20, r=20),
            ))
            st.plotly_chart(figA, use_container_width=True)
        else:
            st.info("No data for this chart.")

    # ── Donut B: Funded vs Unfunded ───────────────────────────────────────────
    with donut_right:
        non_absence["_funded"] = non_absence["task"].apply(
            lambda t: task_subcode(t) in FUNDED_CODES
        )
        hrs_funded   = non_absence[non_absence["_funded"]]["hours"].sum()
        hrs_unfunded = non_absence[~non_absence["_funded"]]["hours"].sum()
        total_fu     = hrs_funded + hrs_unfunded

        if total_fu > 0:
            figB = go.Figure(go.Pie(
                labels=["Funded", "Unfunded"],
                values=[hrs_funded, hrs_unfunded],
                hole=0.58,
                marker_colors=[CVU_GREEN, CVU_PALETTE[1]],
                hovertemplate="%{label}<br>%{value:.1f} hrs (%{percent})<extra></extra>",
                textinfo="percent",
                textfont=dict(color=CVU_WHITE, size=13, family="Inter, Arial, sans-serif"),
            ))
            figB.update_layout(**_chart_base(
                height=300,
                title=dict(text="Funded vs Unfunded Time",
                           font=dict(color=CVU_WHITE, size=13), x=0.5, xanchor="center"),
                showlegend=True,
                legend=dict(orientation="h", y=-0.08, x=0.5, xanchor="center"),
                margin=dict(t=50, b=40, l=20, r=20),
            ))
            st.plotly_chart(figB, use_container_width=True)
        else:
            st.info("No data for this chart.")

    st.divider()

    # ─────────────────────────────────────────────────────────────────────────
    # CHART 1: Hours by category (horizontal bar)
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("##### Hours by Category")
    cat_df = (
        fdf.groupby("category")["hours"]
           .sum()
           .reset_index()
           .sort_values("hours")
    )
    fig1 = go.Figure(go.Bar(
        y=cat_df["category"],
        x=cat_df["hours"],
        orientation="h",
        marker_color=[cat_color_map.get(c, CVU_GREEN) for c in cat_df["category"]],
        hovertemplate="%{y}<br>Hours: %{x:.1f}<extra></extra>",
    ))
    fig1.update_layout(**_chart_base(
        height=max(300, len(cat_df) * 55),
        yaxis=dict(automargin=True, tickfont=dict(color=CVU_WHITE, size=11),
                   gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER),
        xaxis=dict(title="Total Hours", tickfont=dict(color=CVU_GRAY),
                   gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER),
    ))
    st.plotly_chart(fig1, use_container_width=True)
    st.divider()

    # ─────────────────────────────────────────────────────────────────────────
    # CHART 2: Hours over time — stacked bar by staff member
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("##### Hours Over Time")
    agg  = st.radio("Group by", ["Day", "Week", "Month"], horizontal=True, key="agg")
    pcol = {"Day": "date", "Week": "week", "Month": "month"}[agg]

    time_df = fdf.groupby([pcol, "person"])["hours"].sum().reset_index()
    time_df["label"] = (
        time_df["date"].dt.strftime("%Y-%m-%d")
        if agg == "Day"
        else time_df[pcol].astype(str)
    )

    fig2 = go.Figure()
    for person in staff_list:
        pdata = time_df[time_df["person"] == person]
        fig2.add_trace(go.Bar(
            x=pdata["label"],
            y=pdata["hours"],
            name=person,
            marker_color=staff_colors[person],
            hovertemplate=f"{person}<br>%{{x}}<br>Hours: %{{y:.1f}}<extra></extra>",
        ))
    fig2.update_layout(**_chart_base(
        barmode="stack",
        height=360,
        xaxis=dict(tickangle=-40, tickfont=dict(color=CVU_GRAY),
                   gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER),
        yaxis=dict(title="Hours", tickfont=dict(color=CVU_GRAY),
                   gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    ))
    st.plotly_chart(fig2, use_container_width=True)
    st.divider()

    # ─────────────────────────────────────────────────────────────────────────
    # CHART 3: Hours per staff member by category
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("##### Hours per Staff Member by Category")
    pp_df = fdf.groupby(["person", "category"])["hours"].sum().reset_index()

    fig3 = go.Figure()
    for cat in cats:
        cdata = pp_df[pp_df["category"] == cat]
        fig3.add_trace(go.Bar(
            x=cdata["person"],
            y=cdata["hours"],
            name=cat,
            marker_color=cat_color_map[cat],
            hovertemplate=f"{cat}<br>%{{x}}<br>Hours: %{{y:.1f}}<extra></extra>",
        ))
    fig3.update_layout(**_chart_base(
        barmode="stack",
        height=420,
        xaxis=dict(tickfont=dict(color=CVU_WHITE, size=12),
                   gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER),
        yaxis=dict(title="Hours", tickfont=dict(color=CVU_GRAY),
                   gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, font=dict(size=10)),
    ))
    st.plotly_chart(fig3, use_container_width=True)
    st.divider()

    # ─────────────────────────────────────────────────────────────────────────
    # CHART 4: Cumulative YTD hours by staff member
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("##### Cumulative Hours YTD (by Staff Member)")
    daily_person = (
        fdf.groupby(["date", "person"])["hours"]
           .sum()
           .reset_index()
           .sort_values("date")
    )
    fig4 = go.Figure()
    for person in staff_list:
        pdata = daily_person[daily_person["person"] == person].copy()
        pdata["cumulative"] = pdata["hours"].cumsum()
        fig4.add_trace(go.Scatter(
            x=pdata["date"],
            y=pdata["cumulative"],
            name=person,
            mode="lines",
            line=dict(color=staff_colors[person], width=2),
            hovertemplate=f"{person}<br>%{{x|%b %d}}<br>Total: %{{y:.1f}} hrs<extra></extra>",
        ))
    fig4.update_layout(**_chart_base(
        height=340,
        xaxis=dict(title="", tickfont=dict(color=CVU_GRAY),
                   gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER),
        yaxis=dict(title="Cumulative Hours", tickfont=dict(color=CVU_GRAY),
                   gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER),
        legend=dict(orientation="h", y=1.08),
    ))
    st.plotly_chart(fig4, use_container_width=True)
    st.divider()

    # ─────────────────────────────────────────────────────────────────────────
    # CHART 5: Weekly team pace
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("##### Weekly Team Pace")
    weekly = fdf.groupby("week")["hours"].sum().reset_index().sort_values("week")

    # Average only weeks up to and including the current week
    current_week_str = str(pd.Period(date.today(), freq="W"))
    completed_weeks  = weekly[weekly["week"] <= current_week_str]
    avg_weekly       = completed_weeks["hours"].mean() if not completed_weeks.empty else 0

    fig5 = go.Figure()
    fig5.add_trace(go.Bar(
        x=weekly["week"],
        y=weekly["hours"],
        marker_color=CVU_GREEN,
        opacity=0.85,
        name="Weekly Hours",
        hovertemplate="Week: %{x}<br>Hours: %{y:.1f}<extra></extra>",
    ))
    # Draw the average line only across completed weeks (not future empty weeks)
    if not completed_weeks.empty:
        fig5.add_trace(go.Scatter(
            x=[completed_weeks["week"].iloc[0], completed_weeks["week"].iloc[-1]],
            y=[avg_weekly, avg_weekly],
            mode="lines",
            line=dict(color=CVU_PALETTE[0], width=2, dash="dot"),
            name=f"Avg {avg_weekly:.1f} hrs / wk",
            hovertemplate=f"Avg (completed weeks): {avg_weekly:.1f} hrs<extra></extra>",
        ))
    fig5.update_layout(**_chart_base(
        height=320,
        xaxis=dict(tickangle=-40, tickfont=dict(color=CVU_GRAY),
                   gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER),
        yaxis=dict(title="Hours", tickfont=dict(color=CVU_GRAY),
                   gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER),
    ))
    st.plotly_chart(fig5, use_container_width=True)
    st.divider()

    # ─────────────────────────────────────────────────────────────────────────
    # CHART 6: Estimated cost by category
    # Note: uses a fixed blended rate. Update BLENDED_RATE below as needed.
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("##### Estimated Cost by Category")

    BLENDED_RATE = 85.0   # $/hr — update this to reflect your actual blended rate

    cost_df = fdf.groupby("category")["hours"].sum().reset_index()
    cost_df["est_cost"] = cost_df["hours"] * BLENDED_RATE
    cost_df = cost_df.sort_values("est_cost")

    fig6 = go.Figure(go.Bar(
        y=cost_df["category"],
        x=cost_df["est_cost"],
        orientation="h",
        marker_color=[cat_color_map.get(c, CVU_GREEN) for c in cost_df["category"]],
        hovertemplate="%{y}<br>Est. Cost: $%{x:,.0f}<extra></extra>",
    ))
    fig6.update_layout(**_chart_base(
        height=max(300, len(cost_df) * 55),
        yaxis=dict(automargin=True, tickfont=dict(color=CVU_WHITE, size=11),
                   gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER),
        xaxis=dict(title=f"Estimated Cost (USD @ ${BLENDED_RATE:.0f}/hr)",
                   tickprefix="$", tickfont=dict(color=CVU_GRAY),
                   gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER),
    ))
    st.plotly_chart(fig6, use_container_width=True)
    st.divider()

    # ── Raw data table (collapsed) ────────────────────────────────────────────
    with st.expander("View detailed data table"):
        tbl = (
            fdf.groupby(["person", "category", "task"])["hours"]
               .sum()
               .round(2)
               .reset_index()
               .sort_values(["person", "category"])
        )
        tbl["task"] = tbl["task"].apply(short_name)
        st.dataframe(tbl, hide_index=True, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# VIEW: FINANCIAL KPIs (admin only)
# ══════════════════════════════════════════════════════════════════════════════

def view_financial_kpis():
    txns, settings, all_codes = load_finances()

    # ── Settings & Goals ──────────────────────────────────────────────────────
    with st.expander("Settings & Goals", expanded=txns.empty):
        s1, s2 = st.columns(2)
        ann_staff = s1.number_input(
            "Annual Total Staff Cost ($)",
            min_value=0.0, step=1000.0,
            value=float(settings.get("annual_staff_cost", 0.0)),
            format="%.2f",
            key="fin_staff_cost",
        )
        ann_goal = s2.number_input(
            "Annual Income Goal ($)",
            min_value=0.0, step=1000.0,
            value=float(settings.get("annual_income_goal", 0.0)),
            format="%.2f",
            key="fin_income_goal",
        )

        st.markdown(
            "<p style='color:#9E9E9E;font-size:0.85rem;margin-top:12px'>"
            "Per-Code Income Goals (optional — leave at 0 to skip)</p>",
            unsafe_allow_html=True,
        )
        code_goals  = {}
        code_items  = list(all_codes.items())
        goal_cols   = st.columns(3)
        for i, (code, name) in enumerate(code_items):
            with goal_cols[i % 3]:
                goal_key = f"goal_{code}"
                code_goals[code] = st.number_input(
                    f"{code} — {name[:28]}",
                    min_value=0.0, step=500.0,
                    value=float(settings.get(goal_key, 0.0)),
                    format="%.2f",
                    key=f"fin_goal_{code}",
                    label_visibility="visible",
                )

        if st.button("Save Settings", type="primary"):
            new_settings = {
                "annual_staff_cost":  ann_staff,
                "annual_income_goal": ann_goal,
            }
            for code, goal in code_goals.items():
                if goal > 0:
                    new_settings[f"goal_{code}"] = goal
            custom_codes = {k: v for k, v in all_codes.items() if k not in ACCOUNTING_CODES}
            ok, msg = save_finances(txns, new_settings, custom_codes)
            if ok:
                st.toast("Settings saved.")
                st.rerun()
            else:
                st.error(msg)

    # ── Burn-rate KPI metrics ─────────────────────────────────────────────────
    today         = date.today()
    days_lapsed   = (today - date(today.year, 1, 1)).days + 1
    ann_staff_val = float(settings.get("annual_staff_cost", 0.0))
    daily_burn    = ann_staff_val / 365 if ann_staff_val > 0 else 0.0
    ytd_staff     = daily_burn * days_lapsed

    income_txns  = txns[txns["type"] == "Income"]  if not txns.empty else pd.DataFrame(columns=TXNS_COLS)
    expense_txns = txns[txns["type"] == "Expense"] if not txns.empty else pd.DataFrame(columns=TXNS_COLS)

    paid_income  = income_txns[income_txns["status"] == "Paid"]["amount"].sum()         if not income_txns.empty else 0.0
    soft_income  = income_txns[~income_txns["status"].isin(["Paid"])]["amount"].sum()   if not income_txns.empty else 0.0
    total_exp    = expense_txns["amount"].sum()                                          if not expense_txns.empty else 0.0
    net          = paid_income - total_exp - ytd_staff

    st.markdown("##### Financial Overview")
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Daily Burn Rate",      f"${daily_burn:,.0f}/day" if daily_burn else "No staff cost set")
    k2.metric("YTD Staff Cost",       f"${ytd_staff:,.0f}")
    k3.metric("Income — Paid",        f"${paid_income:,.0f}")
    k4.metric("Income — Not Yet Paid",f"${soft_income:,.0f}")
    k5.metric("Other Expenses YTD",   f"${total_exp:,.0f}")
    k6.metric("Net (Paid - Costs)",   f"${net:,.0f}")
    st.divider()

    # ── Add Transaction ───────────────────────────────────────────────────────
    with st.expander("Add Transaction", expanded=False):
        custom_codes = {k: v for k, v in all_codes.items() if k not in ACCOUNTING_CODES}

        ac1, ac2, ac3 = st.columns([1, 1, 1])
        txn_date = ac1.date_input("Date", value=today, key="txn_date")
        txn_type = ac2.selectbox("Type", ["Income", "Expense"], key="txn_type")
        txn_amt  = ac3.number_input("Amount ($)", min_value=0.0, step=100.0,
                                    format="%.2f", key="txn_amt")

        code_options     = [f"{k} — {v}" for k, v in all_codes.items()] + ["+ Add new code"]
        bc1, bc2         = st.columns(2)
        sel_code_str     = bc1.selectbox("Accounting Code", code_options, key="txn_code_sel")
        txn_desc         = bc2.text_input("Description", key="txn_desc")

        if sel_code_str == "+ Add new code":
            nc1, nc2      = st.columns(2)
            new_code_id   = nc1.text_input("Code (e.g. 918)", key="new_code_id")
            new_code_name = nc2.text_input("Code name", key="new_code_name")
            txn_code      = new_code_id.strip()
            txn_code_name = new_code_name.strip()
            save_new_code = st.checkbox("Save this code for future use", value=True, key="save_new_code")
        else:
            parts         = sel_code_str.split(" — ", 1)
            txn_code      = parts[0].strip()
            txn_code_name = parts[1].strip() if len(parts) > 1 else txn_code
            save_new_code = False

        txn_status = ""
        if txn_type == "Income":
            txn_status = st.selectbox("Status", INCOME_STATUSES, key="txn_status")

        txn_notes = st.text_input("Notes (optional)", key="txn_notes")

        if st.button("Add Transaction", type="primary", use_container_width=True):
            if txn_amt <= 0:
                st.error("Please enter an amount greater than 0.")
            elif not txn_code:
                st.error("Please select or enter an accounting code.")
            else:
                new_id  = int(txns["id"].max() + 1) if not txns.empty and txns["id"].notna().any() else 1
                new_row = pd.DataFrame([{
                    "id":          new_id,
                    "date":        pd.Timestamp(txn_date),
                    "type":        txn_type,
                    "amount":      txn_amt,
                    "code":        txn_code,
                    "code_name":   txn_code_name,
                    "description": txn_desc,
                    "status":      txn_status,
                    "notes":       txn_notes,
                }])
                updated_txns   = pd.concat([txns, new_row], ignore_index=True)
                updated_custom = dict(custom_codes)
                if save_new_code and txn_code and txn_code not in ACCOUNTING_CODES:
                    updated_custom[txn_code] = txn_code_name
                ok, msg = save_finances(updated_txns, dict(settings), updated_custom)
                if ok:
                    st.toast("Transaction added.")
                    st.rerun()
                else:
                    st.error(msg)

    if txns.empty:
        st.info("No transactions yet — use the form above to add income or expenses.")
        return

    st.divider()

    # ── Chart row 1: Income by status donut  +  Progress to annual goal ───────
    st.markdown("##### Income Pipeline")
    ch1, ch2 = st.columns(2)

    with ch1:
        if not income_txns.empty:
            status_grp = income_txns.groupby("status")["amount"].sum().reset_index()
            figA = go.Figure(go.Pie(
                labels=status_grp["status"],
                values=status_grp["amount"],
                hole=0.58,
                marker_colors=[STATUS_COLORS.get(s, CVU_GRAY) for s in status_grp["status"]],
                hovertemplate="%{label}<br>$%{value:,.0f} (%{percent})<extra></extra>",
                textinfo="percent",
                textfont=dict(color=CVU_WHITE, size=12, family="Inter, Arial, sans-serif"),
            ))
            figA.update_layout(**_chart_base(
                height=300,
                title=dict(text="Income by Status",
                           font=dict(color=CVU_WHITE, size=13), x=0.5, xanchor="center"),
                showlegend=True,
                legend=dict(orientation="h", y=-0.12, x=0.5, xanchor="center"),
                margin=dict(t=50, b=55, l=20, r=20),
            ))
            st.plotly_chart(figA, use_container_width=True)
        else:
            st.info("No income transactions yet.")

    with ch2:
        annual_goal_val = float(settings.get("annual_income_goal", 0.0))
        if annual_goal_val > 0 and not income_txns.empty:
            seg_data = {s: income_txns[income_txns["status"] == s]["amount"].sum()
                        for s in INCOME_STATUSES}
            figB = go.Figure()
            for s in INCOME_STATUSES:
                figB.add_trace(go.Bar(
                    x=[seg_data.get(s, 0)],
                    y=["Income"],
                    orientation="h",
                    name=s,
                    marker_color=STATUS_COLORS.get(s, CVU_GRAY),
                    hovertemplate=f"{s}: $%{{x:,.0f}}<extra></extra>",
                ))
            figB.add_vline(
                x=annual_goal_val,
                line_dash="dot",
                line_color=CVU_WHITE,
                annotation_text=f"Goal  ${annual_goal_val:,.0f}",
                annotation_font_color=CVU_WHITE,
                annotation_position="top right",
            )
            figB.update_layout(**_chart_base(
                barmode="stack",
                height=220,
                title=dict(text="Progress to Annual Income Goal",
                           font=dict(color=CVU_WHITE, size=13), x=0.5, xanchor="center"),
                showlegend=True,
                legend=dict(orientation="h", y=-0.25, x=0.5, xanchor="center"),
                margin=dict(t=50, b=90, l=20, r=20),
                xaxis=dict(tickprefix="$", tickfont=dict(color=CVU_GRAY),
                           gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER),
                yaxis=dict(showticklabels=False, gridcolor=CVU_BORDER),
            ))
            st.plotly_chart(figB, use_container_width=True)
        elif annual_goal_val == 0:
            st.info("Set an annual income goal in Settings to see progress here.")

    st.divider()

    # ── Chart 2: Income vs Expenses by code ───────────────────────────────────
    st.markdown("##### Income vs Expenses by Code")
    inc_by_code = (income_txns.groupby("code")["amount"].sum().reset_index()
                   .rename(columns={"amount": "income"})
                   if not income_txns.empty else pd.DataFrame(columns=["code", "income"]))
    exp_by_code = (expense_txns.groupby("code")["amount"].sum().reset_index()
                   .rename(columns={"amount": "expenses"})
                   if not expense_txns.empty else pd.DataFrame(columns=["code", "expenses"]))

    by_code = pd.merge(inc_by_code, exp_by_code, on="code", how="outer").fillna(0)
    if not by_code.empty:
        by_code["label"] = by_code["code"].map(all_codes).fillna(by_code["code"])
        by_code["label"] = by_code.apply(lambda r: f"{r['code']} — {r['label']}", axis=1)
        by_code = by_code.sort_values("income", ascending=False)

        figC = go.Figure()
        figC.add_trace(go.Bar(
            x=by_code["label"], y=by_code["income"],
            name="Income", marker_color=CVU_GREEN,
            hovertemplate="%{x}<br>Income: $%{y:,.0f}<extra></extra>",
        ))
        figC.add_trace(go.Bar(
            x=by_code["label"], y=by_code["expenses"],
            name="Expenses", marker_color=CVU_PALETTE[5],
            hovertemplate="%{x}<br>Expenses: $%{y:,.0f}<extra></extra>",
        ))
        figC.update_layout(**_chart_base(
            barmode="group",
            height=360,
            xaxis=dict(tickangle=-35, tickfont=dict(color=CVU_GRAY, size=10),
                       gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER),
            yaxis=dict(title="Amount ($)", tickprefix="$", tickfont=dict(color=CVU_GRAY),
                       gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER),
            legend=dict(orientation="h", y=1.08),
        ))
        st.plotly_chart(figC, use_container_width=True)

    # Per-code goal progress (only show codes that have a goal set)
    code_goal_rows = [(c, float(settings[f"goal_{c}"])) for c in all_codes
                      if f"goal_{c}" in settings and float(settings[f"goal_{c}"]) > 0]
    if code_goal_rows:
        st.markdown("##### Per-Code Income Progress vs Goal")
        goal_labels, goal_vals, actual_vals = [], [], []
        for code, goal in sorted(code_goal_rows):
            actual = income_txns[income_txns["code"] == code]["amount"].sum() if not income_txns.empty else 0
            goal_labels.append(f"{code} — {all_codes.get(code, code)}")
            goal_vals.append(goal)
            actual_vals.append(actual)

        figD = go.Figure()
        figD.add_trace(go.Bar(
            y=goal_labels, x=actual_vals,
            orientation="h",
            name="Actual Income",
            marker_color=CVU_GREEN,
            hovertemplate="%{y}<br>Actual: $%{x:,.0f}<extra></extra>",
        ))
        figD.add_trace(go.Bar(
            y=goal_labels, x=[g - a for g, a in zip(goal_vals, actual_vals)],
            orientation="h",
            name="Remaining to Goal",
            marker_color=CVU_BORDER,
            hovertemplate="%{y}<br>Remaining: $%{x:,.0f}<extra></extra>",
        ))
        figD.update_layout(**_chart_base(
            barmode="stack",
            height=max(300, len(code_goal_rows) * 50),
            yaxis=dict(automargin=True, tickfont=dict(color=CVU_WHITE, size=11),
                       gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER),
            xaxis=dict(title="Amount ($)", tickprefix="$", tickfont=dict(color=CVU_GRAY),
                       gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER),
            legend=dict(orientation="h", y=1.06),
        ))
        st.plotly_chart(figD, use_container_width=True)

    st.divider()

    # ── Chart 3: Monthly cash flow ────────────────────────────────────────────
    st.markdown("##### Monthly Cash Flow")
    txns_m = txns.copy()
    txns_m["month"] = txns_m["date"].dt.to_period("M").astype(str)

    monthly_inc = (txns_m[txns_m["type"] == "Income"]
                   .groupby("month")["amount"].sum())
    monthly_exp = (txns_m[txns_m["type"] == "Expense"]
                   .groupby("month")["amount"].sum())
    all_months  = sorted(set(list(monthly_inc.index) + list(monthly_exp.index)))

    if all_months:
        figE = go.Figure()
        figE.add_trace(go.Bar(
            x=all_months,
            y=[monthly_inc.get(m, 0) for m in all_months],
            name="Income",
            marker_color=CVU_GREEN,
            hovertemplate="Month: %{x}<br>Income: $%{y:,.0f}<extra></extra>",
        ))
        figE.add_trace(go.Bar(
            x=all_months,
            y=[-monthly_exp.get(m, 0) for m in all_months],
            name="Expenses",
            marker_color=CVU_PALETTE[5],
            hovertemplate="Month: %{x}<br>Expenses: $%{y:,.0f}<extra></extra>",
        ))
        figE.update_layout(**_chart_base(
            barmode="relative",
            height=320,
            xaxis=dict(tickfont=dict(color=CVU_GRAY),
                       gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER),
            yaxis=dict(title="Amount ($)", tickprefix="$", tickfont=dict(color=CVU_GRAY),
                       gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER),
            legend=dict(orientation="h", y=1.08),
        ))
        st.plotly_chart(figE, use_container_width=True)

    st.divider()

    # ── Transaction Table ─────────────────────────────────────────────────────
    st.markdown("##### All Transactions")
    tf1, tf2, tf3 = st.columns(3)
    f_type   = tf1.selectbox("Filter: Type",   ["All", "Income", "Expense"], key="tbl_type")
    f_status = tf2.selectbox("Filter: Status", ["All"] + INCOME_STATUSES,   key="tbl_status")
    f_code   = tf3.selectbox("Filter: Code",   ["All"] + sorted(txns["code"].unique().tolist()), key="tbl_code")

    tbl = txns.copy()
    if f_type   != "All": tbl = tbl[tbl["type"]   == f_type]
    if f_status != "All": tbl = tbl[tbl["status"] == f_status]
    if f_code   != "All": tbl = tbl[tbl["code"]   == f_code]

    tbl = tbl.sort_values("date", ascending=False).copy()
    tbl["date"]   = tbl["date"].dt.date
    tbl["amount"] = tbl["amount"].apply(lambda x: f"${x:,.2f}")
    tbl = tbl.drop(columns=["id"])
    tbl.columns   = ["Date", "Type", "Amount", "Code", "Code Name", "Description", "Status", "Notes"]
    st.dataframe(tbl, hide_index=True, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# AUTH — simple password gate
# ══════════════════════════════════════════════════════════════════════════════

def _hide_sidebar():
    st.markdown(
        "<style>section[data-testid='stSidebar']{display:none}</style>",
        unsafe_allow_html=True,
    )


def _check_auth() -> bool:
    """
    Show a password screen if not yet authenticated.

    Two passwords are supported:
      APP_PASSWORD   → staff role  (landing page → time entry)
      ADMIN_PASSWORD → admin role  (straight to dashboard, read-only)

    Both fall back to local defaults if not set in st.secrets.
    Returns True once authenticated.
    """
    if st.session_state.get("authenticated"):
        return True

    _hide_sidebar()

    _, col, _ = st.columns([1, 1.6, 1])
    with col:
        st.markdown("<div style='padding-top:80px'></div>", unsafe_allow_html=True)
        st.markdown(
            "<h2 style='color:#FCFCFC;font-family:Inter,Arial,sans-serif;"
            "font-weight:600;margin-bottom:4px'>RTL Time Tracker</h2>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<p style='color:#9E9E9E;font-family:Inter,Arial,sans-serif;"
            "margin-bottom:28px'>Council on Vertical Urbanism</p>",
            unsafe_allow_html=True,
        )
        pwd = st.text_input("Password", type="password", placeholder="Enter password",
                            label_visibility="collapsed")
        if st.button("Sign In", type="primary", use_container_width=True):
            try:
                staff_pwd = st.secrets["APP_PASSWORD"]
            except Exception:
                staff_pwd = "rtl2026"
            try:
                admin_pwd = st.secrets["ADMIN_PASSWORD"]
            except Exception:
                admin_pwd = "rtladmin"

            if pwd == staff_pwd:
                st.session_state["authenticated"] = True
                st.session_state["role"] = "staff"
                st.rerun()
            elif pwd == admin_pwd:
                st.session_state["authenticated"] = True
                st.session_state["role"] = "admin"
                st.rerun()
            else:
                st.error("Incorrect password.")
    return False


# ══════════════════════════════════════════════════════════════════════════════
# LANDING PAGE — staff member selection
# ══════════════════════════════════════════════════════════════════════════════

def view_landing():
    """Welcome screen — staff pick their name before entering time."""
    _hide_sidebar()

    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.markdown("<div style='padding-top:60px'></div>", unsafe_allow_html=True)
        st.markdown(
            "<h2 style='color:#FCFCFC;font-family:Inter,Arial,sans-serif;"
            "font-weight:600;margin-bottom:6px'>Who are you?</h2>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<p style='color:#9E9E9E;font-family:Inter,Arial,sans-serif;"
            "margin-bottom:32px'>Select your name to start entering your time.</p>",
            unsafe_allow_html=True,
        )

        names = list(STAFF.keys())
        row1, row2 = st.columns(2), st.columns(2)
        for i, name in enumerate(names):
            with (row1 if i < 2 else row2)[i % 2]:
                st.markdown(
                    f"<div style='background:#282828;border:1px solid #4F4F4F;"
                    f"border-left:4px solid #B4E817;border-radius:6px;"
                    f"padding:18px 16px;margin-bottom:4px;'>"
                    f"<span style='color:#FCFCFC;font-family:Inter,Arial,sans-serif;"
                    f"font-weight:600;font-size:1rem'>{name}</span></div>",
                    unsafe_allow_html=True,
                )
                if st.button("Select", key=f"land_{name}", use_container_width=True):
                    st.session_state["person"] = name
                    st.rerun()

        st.markdown("<div style='margin-top:24px'></div>", unsafe_allow_html=True)
        st.markdown(
            "<p style='color:#4F4F4F;font-size:0.75rem;text-align:center;"
            "font-family:Inter,Arial,sans-serif'>Manager? Use the Team Overview "
            "in the sidebar after selecting any name.</p>",
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR + MAIN APP
# ══════════════════════════════════════════════════════════════════════════════

def main():
    # ── Auth gate ─────────────────────────────────────────────────────────────
    if not _check_auth():
        return

    role = st.session_state.get("role", "staff")

    # ══════════════════════════════════════════════════════════════════════════
    # ADMIN (C-LEVEL) ROUTE — Time KPIs or Financial KPIs
    # ══════════════════════════════════════════════════════════════════════════
    if role == "admin":
        with st.sidebar:
            st.title("RTL Dashboard")
            st.divider()
            kpi_mode = st.radio(
                "KPI Module",
                options=["Time KPIs", "Financial KPIs"],
                key="admin_kpi_mode",
            )
            st.divider()
            if st.button("Refresh Data", use_container_width=True):
                load_all.clear()
                load_finances.clear()
                st.rerun()
            st.divider()
            if st.button("Sign Out", use_container_width=True):
                st.session_state.clear()
                st.rerun()

        if kpi_mode == "Time KPIs":
            st.title("Team Overview — Time KPIs")
            view_team(load_all())
        else:
            st.title("Financial KPIs")
            view_financial_kpis()
        return

    # ══════════════════════════════════════════════════════════════════════════
    # STAFF ROUTE
    # ══════════════════════════════════════════════════════════════════════════

    # ── Landing page (no person chosen yet) ───────────────────────────────────
    if not st.session_state.get("person"):
        view_landing()
        return

    person = st.session_state["person"]

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.title("RTL Time Tracker")
        st.divider()

        view = st.radio(
            "View",
            options=["My Time", "Team Overview"],
            label_visibility="collapsed",
        )

        st.divider()

        st.caption(f"Signed in as **{person}**")
        if st.button("Change User", use_container_width=True):
            st.session_state["person"] = None
            st.rerun()

        st.divider()

        if st.button("Refresh Data", use_container_width=True):
            load_all.clear()
            st.rerun()

    # ── Load data (cached) ────────────────────────────────────────────────────
    df = load_all()

    # ── Route to view ─────────────────────────────────────────────────────────
    if view == "My Time":
        st.title(f"My Time — {person}")
        tab1, tab2, tab3 = st.tabs(["Daily Entry", "Bulk Edit", "History"])
        with tab1:
            view_daily_entry(person)
        with tab2:
            view_bulk_edit(person)
        with tab3:
            view_history(person, df)

    else:
        st.title("Team Overview")
        view_team(df)


if __name__ == "__main__":
    main()
