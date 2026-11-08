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


apply_theme("1180px")

URGENCY_COLORS = {"High": "#B71C1C", "Medium": "#F2CD00", "Low": "#6B6B6B"}
URGENCY_ORDER = {"High": 0, "Medium": 1, "Low": 2}
BADGES = {
    "High": ("high", "HIGH"),
    "Medium": ("medium", "MEDIUM"),
    "Low": ("low", "LOW"),
}


def shown_text(row):
    """Text the RA sees. Uses the redacted version when there is one."""
    for column in ("scrubbed_text", "raw_text"):
        value = row.get(column)
        if pd.notna(value) and str(value).strip():
            return str(value)
    return ""


page_title("RA Shift Handoff", "Every report from residence, sorted by urgency.")


# Auto-refresh the page every 10 seconds
st_autorefresh(interval=10000, limit=None, key="ra_dashboard_refresh")

df = load_data()

if df.empty or "building" not in df.columns:
    st.info("No incidents logged yet.")
    site_footer()
    st.stop()

# --- How long has each report been open? Flag the ones that waited too long. ---
df["timestamp"] = parse_times(df["timestamp"])
df["age_min"] = (now_naive() - df["timestamp"]).dt.total_seconds() / 60
limits = df["urgency"].map(OVERDUE_AFTER_MIN).fillna(OVERDUE_AFTER_MIN["Low"])
df["overdue"] = (df["status"] == "Open") & (df["age_min"] > limits)

overdue_total = int(df["overdue"].sum())
if overdue_total:
    st.markdown(
        f'<div class="alert-bar"><b>{overdue_total} overdue.</b> '
        "These open reports have waited longer than their target response time "
        "(High 15 min, Medium 2 h, Low 24 h).</div>",
        unsafe_allow_html=True,
    )

col1, col2 = st.columns(2)
with col1:
    selected_building = st.selectbox(
        "Filter by location", ["All locations"] + all_places()
    )
with col2:
    status_filter = st.selectbox("Filter by status", ["Open", "Resolved", "All"])

filtered_df = df.copy()
if selected_building != "All locations":
    filtered_df = filtered_df[filtered_df["building"] == selected_building]
if status_filter != "All":
    filtered_df = filtered_df[filtered_df["status"] == status_filter]

# --- Summary tiles ---
m1, m2, m3, m4 = st.columns(4)
m1.metric("Shown", len(filtered_df))
m2.metric("High urgency", int((filtered_df["urgency"] == "High").sum()))
m3.metric("Overdue", int(filtered_df["overdue"].sum()))
m4.metric("Resolved", int((filtered_df["status"] == "Resolved").sum()))

# --- DRAW MAP ---
st.subheader("Patrol map")
if filtered_df.empty:
    st.info("No incidents match your current filter.")
else:
    st.markdown(
        '<div class="legend">'
        '<span><i style="background:#B71C1C"></i>High</span>'
        '<span><i style="background:#F2CD00"></i>Medium</span>'
        '<span><i style="background:#6B6B6B"></i>Low</span></div>',
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
            color="#B71C1C" if row["overdue"] else "#1F1F1F",
            weight=3 if row["overdue"] else 1,
            fill=True,
            fill_color=color,
            fill_opacity=0.95,
            popup=folium.Popup(popup_html, max_width=260),
        ).add_to(ra_map)

    st_folium(ra_map, height=460, use_container_width=True, returned_objects=[])


# --- INCIDENT CARDS ---
@st.fragment
def render_triage_board(filtered_df):
    st.subheader("Incidents: overdue first, then most urgent")

    if filtered_df.empty:
        st.info("No incidents match your current filter.")
        return

    ranked = filtered_df.assign(
        _late=(~filtered_df["overdue"]).astype(int),
        _rank=filtered_df["urgency"].map(URGENCY_ORDER).fillna(3),
    ).sort_values(["_late", "_rank", "timestamp"], ascending=[True, True, True])

    for idx, row in ranked.iterrows():
        uid = row.get("id", idx)
        css_class, label = BADGES.get(row["urgency"], ("low", "LOW"))
        logged = row["timestamp"].strftime("%b %d, %H:%M") if pd.notna(row["timestamp"]) else "unknown time"
        open_for = f" · Open for {human_age(row['age_min'])}" if row["status"] == "Open" else ""
        card_key = f"card_incident_{'overdue_' if row['overdue'] else ''}{uid}"

        with st.container(key=card_key):
            col_a, col_b = st.columns([5, 1])

            with col_a:
                overdue_badge = '<span class="badge overdue">OVERDUE</span>' if row["overdue"] else ""
                st.markdown(
                    f'<span class="badge {css_class}">{label}</span>{overdue_badge}'
                    f"<b>{html.escape(place_label(row['building'], row['floor'], row['zone']))}</b>",
                    unsafe_allow_html=True,
                )
                st.caption(f"{row['category']} · Logged {logged}{open_for}")
                st.write(shown_text(row))

            with col_b:
                if row["status"] == "Open":
                    if st.button("Resolve", key=f"resolve_{uid}", type="primary"):
                        update_status(row["id"], "Resolved")
                        st.rerun()
                else:
                    st.markdown('<span class="badge done">RESOLVED</span>', unsafe_allow_html=True)


render_triage_board(filtered_df)
site_footer()