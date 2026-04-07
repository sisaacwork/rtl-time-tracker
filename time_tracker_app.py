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
from pathlib import Path
from datetime import datetime, date, timedelta
import plotly.express as px

# ══════════════════════════════════════════════════════════════════════════════
# PAGE CONFIG
# ══════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="RTL Time Tracker",
    page_icon="⏱️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION  —  edit these if file names or staff change
# ══════════════════════════════════════════════════════════════════════════════

DATA_DIR = Path(__file__).parent   # app lives in the same folder as the .xlsx files

STAFF = {
    "D. Safarik":  "DSafarik_2026TimeTracking.xlsx",
    "I. Work":     "IWork_2026TimeTracking.xlsx",
    "S. Ursini":   "SUrsini_2026TimeTracking.xlsx",
    "W. Miranda":  "WMiranda_2026TimeTracking.xlsx",
}
STAFF = {name: DATA_DIR / fname for name, fname in STAFF.items()}

TRACKER_SHEET  = "Tracking"
DATE_ROW       = 5   # Excel row (1-indexed) with plain date values (row 4 has =C5 formulas)
DATA_START_ROW = 7   # First Excel row with actual task data
DATE_START_COL = 3   # Column C (1-indexed) is where dates begin

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
    """Child tasks start with '- ' (they are sub-tasks of a parent category)."""
    return task.startswith("- ")


def short_name(task: str) -> str:
    """Strip the '- ' prefix from child task names for cleaner display."""
    return task[2:].strip() if is_child(task) else task


def category_code(task: str) -> str:
    """
    Map any task name to its top-level 3-digit category code.
    e.g. '900 Research...' → '900', '- 901a: Venice...' → '900'
    """
    t = task.lstrip("- ").strip()
    if t and t[0].isdigit():
        return t[0] + "00"
    return "Other"


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING  (cached for 30 s — click Refresh to bust manually)
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=30, show_spinner="Loading tracker data…")
def load_all() -> pd.DataFrame:
    """
    Read every staff Excel file and return a single long-format DataFrame:
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

    # Build code → full parent category name (e.g. "900" → "900 Research & Thought Leadership")
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
    """Return a sorted list of all tracked working dates for this person."""
    wb = openpyxl.load_workbook(STAFF[person], data_only=True)
    return sorted(_date_cols(wb[TRACKER_SHEET]).keys())


def hours_on_date(person: str, d: date) -> dict:
    """Return {task_name: hours} for a given person and date (0 if not entered)."""
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
      1. Open with data_only=True to resolve the column index for the target date.
      2. Open without data_only (preserving formulas) to actually write the values.

    Returns (success: bool, message: str).
    """
    path = STAFF[person]

    # Step 1: resolve date → column (needs data_only to see computed date values)
    wb_r = openpyxl.load_workbook(path, data_only=True)
    dc   = _date_cols(wb_r[TRACKER_SHEET])
    c    = dc.get(d)
    if c is None:
        return False, f"Date {d} not found in the tracker spreadsheet."

    # Step 2: open without data_only so existing formulas stay intact
    wb = openpyxl.load_workbook(path)
    ws = wb[TRACKER_SHEET]
    for t, r in _task_rows(ws):
        if t in hours:
            v = hours[t]
            # Store None instead of 0 so the cell stays blank (matches original behaviour)
            ws.cell(row=r, column=c).value = v if v > 0 else None

    wb.save(path)
    load_all.clear()   # bust the Streamlit cache so the dashboard reflects changes
    return True, "✅ Saved to Excel!"


# ══════════════════════════════════════════════════════════════════════════════
# VIEW: DAILY ENTRY
# ══════════════════════════════════════════════════════════════════════════════

def view_daily_entry(person: str):
    st.subheader("📅 Daily Time Entry")

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
        st.warning("⚠️ That date isn't a tracked workday in the spreadsheet.")
        return

    current   = hours_on_date(person, selected)
    structure = task_structure(person)

    # ── Group tasks into (parent_category, [(child_task, current_hours), ...])
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
            # Parent category as a styled header
            st.markdown(
                f"<div style='background:#dce6f1;padding:5px 10px;"
                f"border-radius:4px;font-weight:600;margin-top:10px'>"
                f"{parent}</div>",
                unsafe_allow_html=True,
            )
            for task, cur_h in kids:
                label = short_name(task)
                left, right = st.columns([5, 1])
                left.markdown(f"&nbsp;&nbsp;&nbsp;↳ {label}")
                v = right.number_input(
                    label, label_visibility="collapsed",
                    min_value=0.0, max_value=24.0, step=0.25,
                    value=float(cur_h),
                    key=f"de_{task}_{selected}",
                )
                new_vals[task] = v

        total = sum(new_vals.values())
        st.divider()
        overtime = max(0.0, total - 8.0)
        tcol1, tcol2 = st.columns(2)
        tcol1.metric("Daily Total", f"{total:.2f} h")
        tcol2.metric("Overtime", f"{overtime:.2f} h", delta_color="inverse")

        submitted = st.form_submit_button(
            "💾  Save Entry", use_container_width=True, type="primary"
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
    st.subheader("📋 Bulk Edit")

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

    # ── Category filter so users aren't overwhelmed by 80+ rows
    parents  = [t for t, ch in structure if not ch]
    sel_cats = st.multiselect(
        "Filter by category (select which to show)",
        options=parents,
        default=parents,
    )

    # Build allowed child task set for selected categories
    allowed   = set()
    cur_p     = None
    for t, ch in structure:
        if not ch:
            cur_p = t
        elif cur_p in sel_cats:
            allowed.add(t)

    child_tasks = [t for t, ch in structure if ch and t in allowed]
    if not child_tasks:
        st.info("No tasks to show for the selected categories.")
        return

    # ── Build wide-format table: rows = dates, cols = task short names
    display_names = [short_name(t) for t in child_tasks]
    name_map      = dict(zip(display_names, child_tasks))   # display → full task name

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

    st.caption("Edit cells directly. Click **Save All Changes** when done.")
    edited = st.data_editor(df_edit, use_container_width=True, num_rows="fixed")

    if st.button("💾  Save All Changes", type="primary", use_container_width=True):
        saved = 0
        errors = []
        for d, label in zip(in_range, date_labels):
            row = edited.loc[label]
            updates = {
                name_map[col]: float(row[col])
                for col in row.index
                if col in name_map
            }
            ok, msg = save_hours(person, d, updates)
            if ok:
                saved += 1
            else:
                errors.append(msg)

        if errors:
            st.error("\n".join(errors))
        else:
            st.success(f"✅ Saved {saved} of {len(in_range)} days to Excel.")
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# VIEW: TEAM OVERVIEW (manager dashboard)
# ══════════════════════════════════════════════════════════════════════════════

def view_team(df: pd.DataFrame):
    st.subheader("👥 Team Overview Dashboard")
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
    # Dashboard uses only child task rows (parent rows are formula-aggregates)
    fdf = fdf[fdf["task"].str.startswith("- ")]

    if fdf.empty:
        st.info("Nothing matches the current filters.")
        return

    # ── KPI summary row ───────────────────────────────────────────────────────
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Hours Logged",  f"{fdf['hours'].sum():,.1f}")
    k2.metric("Staff Members",        str(fdf["person"].nunique()))
    k3.metric("Unique Task Codes",    str(fdf["task"].nunique()))
    k4.metric("Days with Entries",    str(fdf["date"].dt.date.nunique()))
    st.divider()

    # ─────────────────────────────────────────────────────────────────────────
    # CHART 1: Hours by category (horizontal bar)
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("##### 📊 Hours by Category")
    cat_df = (
        fdf.groupby("category")["hours"]
           .sum()
           .reset_index()
           .sort_values("hours")
    )
    fig1 = px.bar(
        cat_df, y="category", x="hours", orientation="h",
        color="hours", color_continuous_scale="Blues",
        labels={"hours": "Total Hours", "category": ""},
        height=max(300, len(cat_df) * 34),
    )
    fig1.update_layout(
        coloraxis_showscale=False,
        margin=dict(l=10, r=20, t=10, b=10),
    )
    st.plotly_chart(fig1, use_container_width=True)
    st.divider()

    # ─────────────────────────────────────────────────────────────────────────
    # CHART 2: Hours over time (stacked bar, grouped by Day / Week / Month)
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("##### 📈 Hours Over Time")
    agg = st.radio("Group by", ["Day", "Week", "Month"], horizontal=True, key="agg")
    pcol = {"Day": "date", "Week": "week", "Month": "month"}[agg]

    time_df = (
        fdf.groupby([pcol, "person"])["hours"]
           .sum()
           .reset_index()
    )
    if agg == "Day":
        time_df["label"] = time_df["date"].dt.strftime("%Y-%m-%d")
    else:
        time_df["label"] = time_df[pcol].astype(str)

    fig2 = px.bar(
        time_df, x="label", y="hours", color="person",
        barmode="stack",
        labels={"label": "", "hours": "Hours", "person": "Staff"},
        height=360,
    )
    fig2.update_layout(
        xaxis_tickangle=-40,
        margin=dict(t=10, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig2, use_container_width=True)
    st.divider()

    # ─────────────────────────────────────────────────────────────────────────
    # CHART 3: Hours per person broken down by category
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("##### 👤 Hours per Staff Member by Category")
    pp_df = fdf.groupby(["person", "category"])["hours"].sum().reset_index()
    fig3  = px.bar(
        pp_df, x="person", y="hours", color="category",
        barmode="stack",
        labels={"person": "", "hours": "Hours", "category": "Category"},
        height=400,
    )
    fig3.update_layout(
        margin=dict(t=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, font=dict(size=10)),
    )
    st.plotly_chart(fig3, use_container_width=True)
    st.divider()

    # ─────────────────────────────────────────────────────────────────────────
    # CHART 4: Estimated cost by category
    # Uses avg rate derived from the Dashboard file (YE forecast ÷ total hours).
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("##### 💰 Estimated Cost by Category")

    # Pull YE forecast + total hours from 2026 Master.xlsx if available
    master_path = DATA_DIR / "2026 Master.xlsx"
    ye_forecast  = 757_365.08   # fallback values from the Dashboard sheet
    total_hrs_yd = 1_517.75

    if master_path.exists():
        try:
            wb_m  = openpyxl.load_workbook(master_path, data_only=True)
            ws_m  = wb_m["Detailed Tracking"]
            # Row 1 has headers; row 2 is the top-level summary row
            r2    = list(ws_m.iter_rows(min_row=2, max_row=2, values_only=True))[0]
            # Columns: Task, 2026 Total, Q1, Q2, Q3, Q4, % Overall, Est. Cost YTD, …, YE Forecast, Days Lapsed
            if r2 and r2[9] and isinstance(r2[9], (int, float)):
                ye_forecast = float(r2[9])
            if r2 and r2[1] and isinstance(r2[1], (int, float)):
                total_hrs_yd = float(r2[1])
        except Exception:
            pass   # fall back to hardcoded values

    avg_rate = ye_forecast / total_hrs_yd if total_hrs_yd else 499.0

    cost_df = (
        fdf.groupby("category")["hours"]
           .sum()
           .mul(avg_rate)
           .reset_index()
    )
    cost_df.columns = ["Category", "Est. Cost ($)"]
    cost_df = cost_df.sort_values("Est. Cost ($)")

    fig4 = px.bar(
        cost_df, y="Category", x="Est. Cost ($)", orientation="h",
        color="Est. Cost ($)", color_continuous_scale="RdYlGn",
        height=max(300, len(cost_df) * 34),
    )
    fig4.update_layout(
        coloraxis_showscale=False,
        margin=dict(l=10, r=20, t=10, b=10),
    )
    st.plotly_chart(fig4, use_container_width=True)
    st.caption(
        f"*Avg combined staff rate: ${avg_rate:,.2f} / hr  "
        f"(2026 YE Forecast ${ye_forecast:,.0f} ÷ {total_hrs_yd:,.1f} hrs tracked)*"
    )
    st.divider()

    # ── Raw data table (collapsed by default) ─────────────────────────────────
    with st.expander("📄 View detailed data table"):
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
        st.title("⏱️ RTL Time Tracker")
        st.divider()

        view = st.radio(
            "View",
            options=["My Time", "Team Overview"],
            label_visibility="collapsed",
        )

        st.divider()

        person = st.selectbox("Staff Member", list(STAFF.keys()))

        st.divider()

        if st.button("🔄 Refresh Data", use_container_width=True):
            load_all.clear()
            st.rerun()

        st.caption(f"Data folder: `{DATA_DIR.name}/`")
        st.caption("Files are read from and saved directly to the Excel trackers.")

    # ── Load all data (cached) ────────────────────────────────────────────────
    df = load_all()

    # ── Route to the right view ───────────────────────────────────────────────
    if view == "My Time":
        st.title(f"⏱️ My Time — {person}")
        tab1, tab2 = st.tabs(["📅 Daily Entry", "📋 Bulk Edit"])
        with tab1:
            view_daily_entry(person)
        with tab2:
            view_bulk_edit(person)

    else:
        st.title("👥 Team Overview")
        view_team(df)


if __name__ == "__main__":
    main()
