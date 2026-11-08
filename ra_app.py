"""RA PORTAL.  Run with:  python -m streamlit run ra_app.py --server.port 8502"""
import hmac
import html

import folium
import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from streamlit_folium import st_folium

from places import MAP_CENTER, all_places, place_coords, place_label
from storage import load_data, update_status
from timeutil import OVERDUE_AFTER_MIN, human_age, now_naive, parse_times
from ui import apply_theme, page_title, site_footer

st.set_page_config(page_title="RA Shift Handoff | Residence Safety", layout="wide")
apply_theme("1180px")

URGENCY_COLORS = {"High": "#B71C1C", "Medium": "#F2CD00", "Low": "#6B6B6B"}
LABEL_TO_NUMBER = {"High": 4, "Medium": 3, "Low": 2}  # used when a report has no AI score
BADGES = {
    "High": ("high", "HIGH"),
    "Medium": ("medium", "MEDIUM"),
    "Low": ("low", "LOW"),
}


SORT_OPTIONS = [
    "Smart order (distress, overdue, urgency)",
    "Newest first",
    "Oldest first",
    "Highest urgency first",
    "Waiting longest (open reports)",
]
TIME_RANGES = {
    "All time": None,
    "Last hour": 60,
    "Last 6 hours": 360,
    "Last 24 hours": 1440,
    "Last 7 days": 10080,
}
URGENCY_CHOICES = ["High", "Medium", "Low"]
CATEGORY_CHOICES = ["Urgent Safety", "Maintenance", "Noise/Policy", "Harassment", "Wellbeing"]
RESPONDER_CHOICES = ["RA", "facilities", "security", "counselling"]

# Every filter widget has a key, so one button can put them all back
FILTER_DEFAULTS = {
    "f_location": "All locations",
    "f_status": "Open",
    "f_time": "All time",
    "f_sort": SORT_OPTIONS[0],
    "f_urgency": list(URGENCY_CHOICES),
    "f_category": [],
    "f_responder": [],
    "f_overdue": False,
    "f_distress": False,
    "f_search": "",
}


def reset_filters():
    for name, value in FILTER_DEFAULTS.items():
        st.session_state[name] = list(value) if isinstance(value, list) else value


def sort_incidents(frame, how):
    """Put the reports in the order the RA picked."""
    if how == "Newest first":
        return frame.sort_values("timestamp", ascending=False)
    if how == "Oldest first":
        return frame.sort_values("timestamp", ascending=True)
    if how == "Highest urgency first":
        return frame.sort_values(["priority", "timestamp"], ascending=[False, True])
    if how == "Waiting longest (open reports)":
        return frame.assign(_open=(frame["status"] == "Open")).sort_values(
            ["_open", "age_min"], ascending=[False, False]
        ).drop(columns="_open")
    # Smart order: possible distress, then overdue, then urgency, then oldest first
    return frame.assign(
        _calm=(~frame["ai_distress"]).astype(int),
        _late=(~frame["overdue"]).astype(int),
        _prio=-frame["priority"],
    ).sort_values(["_calm", "_late", "_prio", "timestamp"], ascending=True)


def shown_text(row):
    """Text the RA sees. Uses the redacted version when there is one."""
    for column in ("scrubbed_text", "raw_text"):
        value = row.get(column)
        if pd.notna(value) and str(value).strip():
            return str(value)
    return ""


def clean_text(value):
    return "" if value is None or pd.isna(value) else str(value).strip()


def get_passcode():
    """The RA passcode lives in the app's secrets, never in the code."""
    try:
        value = str(st.secrets["RA_PASSWORD"])
        return value or None
    except Exception:
        return None


def resolve_report(row_id):
    """Mark a report resolved, then re-read the database to be sure it really changed."""
    try:
        update_status(row_id, "Resolved")
    except Exception:
        pass  # the check below reports the problem
    fresh = load_data()
    saved = len(fresh) and "id" in fresh.columns and (
        fresh.loc[fresh["id"] == row_id, "status"] == "Resolved"
    ).any()
    if saved:
        st.session_state["flash"] = ("ok", "Report marked as resolved.")
    else:
        st.session_state["flash"] = (
            "error",
            "The database did not save the change, so the report is still open. "
            "This usually means the table has no UPDATE permission for the app "
            "(row level security). Ask Yevhen to check the Supabase policies.",
        )


page_title(
    "RA Shift Handoff",
    "Every report from residence, sorted by urgency. AI helps sort and flag. An RA reads every report and decides.",
)

# --- PASSWORD PROTECTION GATE ---
if "ra_authenticated" not in st.session_state:
    st.session_state["ra_authenticated"] = False

if not st.session_state["ra_authenticated"]:
    _, middle, _ = st.columns([1, 1.4, 1])
    with middle:
        with st.container(key="card_login"):
            st.subheader("Authorized personnel only")
            passcode = get_passcode()
            if passcode is None:
                st.error(
                    "No RA passcode is set. Add a line  RA_PASSWORD=\"...\"  to the app's "
                    "secrets, then restart the app."
                )
            else:
                entered_password = st.text_input("RA passcode", type="password")
                if st.button("Log in", type="primary", key="login_btn"):
                    if hmac.compare_digest(entered_password, passcode):
                        st.session_state["ra_authenticated"] = True
                        st.rerun()
                    else:
                        st.error("Incorrect passcode. Access denied.")
    site_footer()
    st.stop()

# --- RA DASHBOARD (only loads after a successful login) ---
top_left, top_right = st.columns([5, 1])
top_left.caption("Signed in as Resident Advisor. This page checks for new reports every 10 seconds.")
if top_right.button("Log out", key="logout_btn"):
    st.session_state["ra_authenticated"] = False
    st.rerun()

# Auto-refresh the page every 10 seconds
st_autorefresh(interval=10000, limit=None, key="ra_dashboard_refresh")

flash = st.session_state.pop("flash", None)
if flash:
    (st.success if flash[0] == "ok" else st.error)(flash[1])

df = load_data()

if df.empty or "building" not in df.columns:
    st.info("No incidents logged yet.")
    site_footer()
    st.stop()

# --- Older reports have no AI columns, so fill them in ---
for column in ("ai_summary", "ai_responder", "ai_reason", "ai_source"):
    if column not in df.columns:
        df[column] = None
if "ai_urgency" not in df.columns:
    df["ai_urgency"] = float("nan")
if "ai_distress" not in df.columns:
    df["ai_distress"] = False
df["ai_distress"] = df["ai_distress"].fillna(False).astype(bool)
df["ai_urgency"] = pd.to_numeric(df["ai_urgency"], errors="coerce")
# One 1-to-5 priority for every report: the AI score if there is one, else from High/Medium/Low
df["priority"] = df["ai_urgency"].fillna(df["urgency"].map(LABEL_TO_NUMBER)).fillna(1)
df.loc[df["ai_distress"], "priority"] = 5

# --- How long has each report been open? Flag the ones that waited too long. ---
df["timestamp"] = parse_times(df["timestamp"])
df["age_min"] = (now_naive() - df["timestamp"]).dt.total_seconds() / 60
limits = df["urgency"].map(OVERDUE_AFTER_MIN).fillna(OVERDUE_AFTER_MIN["Low"])
df["overdue"] = (df["status"] == "Open") & (df["age_min"] > limits)

distress_open = int((df["ai_distress"] & (df["status"] == "Open")).sum())
if distress_open:
    st.markdown(
        f'<div class="alert-bar"><b>{distress_open} possible distress report(s) need immediate review.</b> '
        "The AI and a keyword safety net flag these. They are listed first. Please read and decide.</div>",
        unsafe_allow_html=True,
    )

overdue_total = int(df["overdue"].sum())
if overdue_total:
    st.markdown(
        f'<div class="alert-bar"><b>{overdue_total} overdue.</b> '
        "These open reports have waited longer than their target response time "
        "(High 15 min, Medium 2 h, Low 24 h).</div>",
        unsafe_allow_html=True,
    )

# --- Filters and sorting ---
for name, value in FILTER_DEFAULTS.items():
    st.session_state.setdefault(name, list(value) if isinstance(value, list) else value)

# Also offer any category / responder that exists in the data but is not in the usual lists
category_options = CATEGORY_CHOICES + sorted(
    c for c in df["category"].dropna().unique() if c not in CATEGORY_CHOICES
)
responder_options = RESPONDER_CHOICES + sorted(
    r for r in df["ai_responder"].dropna().unique() if r not in RESPONDER_CHOICES
)
st.session_state["f_category"] = [c for c in st.session_state["f_category"] if c in category_options]
st.session_state["f_responder"] = [r for r in st.session_state["f_responder"] if r in responder_options]

with st.container(key="card_filters"):
    st.subheader("Filter and sort")
    r1 = st.columns(4)
    r1[0].selectbox("Location", ["All locations"] + all_places(), key="f_location")
    r1[1].selectbox("Status", ["Open", "Resolved", "All"], key="f_status")
    r1[2].selectbox("Logged", list(TIME_RANGES), key="f_time")
    r1[3].selectbox("Sort by", SORT_OPTIONS, key="f_sort")

    r2 = st.columns(3)
    r2[0].multiselect("Urgency", URGENCY_CHOICES, key="f_urgency")
    r2[1].multiselect("Category", category_options, key="f_category", placeholder="All categories")
    r2[2].multiselect("Suggested responder", responder_options, key="f_responder", placeholder="Anyone")

    r3 = st.columns([3, 1.3, 1.5, 1])
    r3[0].text_input("Search the reports", key="f_search", placeholder="Words in the report, summary or place")
    r3[1].checkbox("Overdue only", key="f_overdue")
    r3[2].checkbox("Possible distress only", key="f_distress")
    r3[3].button("Clear filters", key="clear_filters", on_click=reset_filters)

filtered_df = df.copy()
if st.session_state["f_location"] != "All locations":
    filtered_df = filtered_df[filtered_df["building"] == st.session_state["f_location"]]
if st.session_state["f_urgency"]:
    filtered_df = filtered_df[filtered_df["urgency"].isin(st.session_state["f_urgency"])]
if st.session_state["f_category"]:
    filtered_df = filtered_df[filtered_df["category"].isin(st.session_state["f_category"])]
if st.session_state["f_responder"]:
    filtered_df = filtered_df[filtered_df["ai_responder"].isin(st.session_state["f_responder"])]
window = TIME_RANGES[st.session_state["f_time"]]
if window:
    filtered_df = filtered_df[filtered_df["age_min"] <= window]
if st.session_state["f_overdue"]:
    filtered_df = filtered_df[filtered_df["overdue"]]
if st.session_state["f_distress"]:
    filtered_df = filtered_df[filtered_df["ai_distress"]]
query = st.session_state["f_search"].strip().lower()
if query:
    haystack = filtered_df.apply(
        lambda r: " ".join(
            [shown_text(r), clean_text(r["ai_summary"]), clean_text(r["category"]),
             place_label(r["building"], r["floor"], r["zone"])]
        ).lower(),
        axis=1,
    ) if len(filtered_df) else pd.Series([], dtype=str)
    filtered_df = filtered_df[haystack.str.contains(query, regex=False)] if len(filtered_df) else filtered_df

# Status is applied last. scope_df keeps open AND resolved, so the Resolved tile is honest.
scope_df = filtered_df
if st.session_state["f_status"] != "All":
    filtered_df = scope_df[scope_df["status"] == st.session_state["f_status"]]

st.caption(f"Showing {len(filtered_df)} of {len(df)} reports")

# --- Summary tiles ---
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Shown", len(filtered_df))
m2.metric("Possible distress", int(filtered_df["ai_distress"].sum()))
m3.metric("High urgency", int((filtered_df["urgency"] == "High").sum()))
m4.metric("Overdue", int(filtered_df["overdue"].sum()))
m5.metric("Resolved", int((scope_df["status"] == "Resolved").sum()))

# --- DRAW MAP ---
st.subheader("Patrol map")
if filtered_df.empty:
    st.info("No incidents match your current filter.")
else:
    st.markdown(
        '<div class="legend">'
        '<span><i style="background:#B71C1C"></i>High</span>'
        '<span><i style="background:#F2CD00"></i>Medium</span>'
        '<span><i style="background:#6B6B6B"></i>Low</span>'
        '<span>Red ring = overdue or possible distress</span></div>',
        unsafe_allow_html=True,
    )
    ra_map = folium.Map(location=list(MAP_CENTER), zoom_start=17)

    for idx, row in filtered_df.iterrows():
        if pd.notna(row.get("latitude")) and pd.notna(row.get("longitude")):
            lat, lon = row["latitude"], row["longitude"]
        else:
            base_lat, base_lon = place_coords(row["building"])
            lat = base_lat + ((idx % 5) - 2) * 0.00005
            lon = base_lon + (((idx // 5) % 5) - 2) * 0.00005

        color = URGENCY_COLORS.get(row["urgency"], "#6B6B6B")
        flagged = bool(row["overdue"] or row["ai_distress"])
        popup_html = f"""
            <div style="width: 210px; font-family: Arial, sans-serif;">
                <h4 style="margin: 0 0 4px 0; color: #275D38;">{html.escape(str(row['category']))}</h4>
                <b>{html.escape(place_label(row['building'], row['floor'], row['zone']))}</b><br>
                <b>Status:</b> {html.escape(str(row['status']))}<br>
                <hr style="margin: 5px 0;">
                {html.escape(shown_text(row))}
            </div>
        """
        folium.CircleMarker(
            location=[lat, lon],
            radius=9,
            color="#B71C1C" if flagged else "#1F1F1F",
            weight=3 if flagged else 1,
            fill=True,
            fill_color=color,
            fill_opacity=0.95,
            popup=folium.Popup(popup_html, max_width=260),
        ).add_to(ra_map)

    st_folium(ra_map, height=460, use_container_width=True, returned_objects=[])


# --- INCIDENT CARDS ---
def render_triage_board(filtered_df, sort_choice):
    st.subheader("Incidents")
    st.caption(f"Sorted by: {sort_choice}")

    if filtered_df.empty:
        st.info("No incidents match your current filters.")
        return

    ranked = sort_incidents(filtered_df, sort_choice)

    for idx, row in ranked.iterrows():
        uid = row.get("id", idx)
        css_class, label = BADGES.get(row["urgency"], ("low", "LOW"))
        if pd.notna(row["ai_urgency"]):
            label = f"{label} {int(row['ai_urgency'])}/5"
        logged = row["timestamp"].strftime("%b %d, %H:%M") if pd.notna(row["timestamp"]) else "unknown time"
        open_for = f" · Open for {human_age(row['age_min'])}" if row["status"] == "Open" else ""

        if row["ai_distress"]:
            card_key = f"card_incident_distress_{uid}"
        elif row["overdue"]:
            card_key = f"card_incident_overdue_{uid}"
        else:
            card_key = f"card_incident_{uid}"

        with st.container(key=card_key):
            col_a, col_b = st.columns([5, 1])

            with col_a:
                distress_badge = '<span class="badge distress">POSSIBLE DISTRESS</span>' if row["ai_distress"] else ""
                overdue_badge = '<span class="badge overdue">OVERDUE</span>' if row["overdue"] else ""
                st.markdown(
                    f'{distress_badge}<span class="badge {css_class}">{html.escape(label)}</span>{overdue_badge}'
                    f"<b>{html.escape(place_label(row['building'], row['floor'], row['zone']))}</b>",
                    unsafe_allow_html=True,
                )
                st.caption(f"{row['category']} · Logged {logged}{open_for}")
                st.write(shown_text(row))

                summary = clean_text(row["ai_summary"])
                reason = clean_text(row["ai_reason"])
                responder = clean_text(row["ai_responder"])
                source = clean_text(row["ai_source"])
                if summary or reason or responder:
                    bits = []
                    if summary:
                        bits.append(f"<b>Summary:</b> {html.escape(summary)}")
                    if responder:
                        bits.append(f"<b>Suggested responder:</b> {html.escape(responder)}")
                    if reason:
                        bits.append(f"<b>Why:</b> {html.escape(reason)}")
                    if source:
                        bits.append(f"<b>Sorted by:</b> {html.escape(source)}")
                    st.markdown(f'<div class="ai-note">{" · ".join(bits)}</div>', unsafe_allow_html=True)

            with col_b:
                if row["status"] == "Open":
                    row_id = row["id"].item() if hasattr(row["id"], "item") else row["id"]
                    st.button(
                        "Resolve", key=f"resolve_{uid}", type="primary",
                        on_click=resolve_report, args=(row_id,),
                    )
                else:
                    st.markdown('<span class="badge done">RESOLVED</span>', unsafe_allow_html=True)


render_triage_board(filtered_df, st.session_state["f_sort"])
site_footer()