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
CVU_GREEN    = "#B4E817"   # Volt Green – primary accent

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

DATA_DIR = Path(__file__).parent   # app lives alongside the .xlsx files

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
        if not path.exists():
            st.warning(f"File not found, skipping: {path.name}")
            continue
        wb = openpyxl.load_workbook(path, data_only=True)
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

def workdays(person: str) -> list:
    """Sorted list of all tracked working dates for this person."""
    wb = openpyxl.load_workbook(STAFF[person], data_only=True)
    return sorted(_date_cols(wb[TRACKER_SHEET]).keys())


def hours_on_date(person: str, d: date) -> dict:
    """Return {task_name: hours} for a given person + date (0 if not entered)."""
    wb = openpyxl.load_workbook(STAFF[person], data_only=True)
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
    wb = openpyxl.load_workbook(STAFF[person], data_only=True)
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

    # Step 1: resolve date → column
    wb_r = openpyxl.load_workbook(path, data_only=True)
    dc   = _date_cols(wb_r[TRACKER_SHEET])
    c    = dc.get(d)
    if c is None:
        return False, f"Date {d} not found in the tracker spreadsheet."

    # Step 2: open without data_only so formulas stay intact
    wb = openpyxl.load_workbook(path)
    ws = wb[TRACKER_SHEET]
    for t, r in _task_rows(ws):
        if t in hours:
            v = hours[t]
            ws.cell(row=r, column=c).value = v if v > 0 else None

    if _is_cloud():
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        ok, msg = _github_commit(path.name, buf.read())
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

    new_vals = {}
    with st.form("daily_form"):
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

        submitted = st.form_submit_button(
            "Save Entry", use_container_width=True, type="primary"
        )

    if submitted:
        ok, msg = save_hours(person, selected, new_vals)
        if ok:
            st.success(msg)
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
        height=max(260, len(cat_df) * 38),
        yaxis=dict(automargin=True, tickfont=dict(color=CVU_WHITE, size=11),
                   gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER, dtick=1),
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

    fdf = df.copy()
    if sel_q != "All":
        fdf = fdf[fdf["quarter"] == sel_q]
    if sel_staff:
        fdf = fdf[fdf["person"].isin(sel_staff)]
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
        height=max(300, len(cat_df) * 42),
        yaxis=dict(automargin=True, tickfont=dict(color=CVU_WHITE, size=11),
                   gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER, dtick=1),
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
    weekly     = fdf.groupby("week")["hours"].sum().reset_index().sort_values("week")
    avg_weekly = weekly["hours"].mean()

    fig5 = go.Figure()
    fig5.add_trace(go.Bar(
        x=weekly["week"],
        y=weekly["hours"],
        marker_color=CVU_GREEN,
        opacity=0.85,
        name="Weekly Hours",
        hovertemplate="Week: %{x}<br>Hours: %{y:.1f}<extra></extra>",
    ))
    fig5.add_hline(
        y=avg_weekly,
        line_dash="dot",
        line_color=CVU_PALETTE[0],
        annotation_text=f"Avg {avg_weekly:.1f} hrs / wk",
        annotation_font_color=CVU_PALETTE[0],
        annotation_position="top right",
    )
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
        height=max(300, len(cost_df) * 42),
        yaxis=dict(automargin=True, tickfont=dict(color=CVU_WHITE, size=11),
                   gridcolor=CVU_BORDER, zerolinecolor=CVU_BORDER, dtick=1),
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
# SIDEBAR + MAIN APP
# ══════════════════════════════════════════════════════════════════════════════

def main():
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
        person = st.selectbox("Staff Member", list(STAFF.keys()))

        st.divider()
        if st.button("Refresh Data", use_container_width=True):
            load_all.clear()
            st.rerun()

        st.caption(f"Data folder: `{DATA_DIR.name}/`")
        st.caption("Files are read from and saved directly to the Excel trackers.")

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
