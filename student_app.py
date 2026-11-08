"""STUDENT PORTAL.  Run with:  python -m streamlit run student_app.py --server.port 8510"""
import math

import folium
import streamlit as st
from branca.element import MacroElement
from jinja2 import Template
from streamlit_folium import st_folium

from places import (MAP_CENTER as MAP_CENTER_TUPLE, SHARED_FLOOR, SHARED_SPACES,
                    SHARED_ZONE, TOWERS, ZONES)
from privacy_triage import process_incident
from storage import load_data, save_data
from ui import apply_theme, notice, page_title, site_footer

MAP_CENTER = list(MAP_CENTER_TUPLE)


def meters_between(lat1, lon1, lat2, lon2):
    """Straight-line distance between two map points, in metres."""
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(
        math.radians(lon2 - lon1) / 2
    ) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_tower(lat, lon):
    """Name of the residence tower whose centre is closest to a map point."""
    return min(TOWERS, key=lambda name: meters_between(lat, lon, *TOWERS[name]["coords"]))


class SinglePin(MacroElement):
    """Drops ONE marker on the map inside the browser. Tapping again just moves it.
    Because the marker is drawn by the browser, the map never has to reload."""

    _template = Template("""
        {% macro script(this, kwargs) %}
        (function () {
            var map = {{ this._parent.get_name() }};
            var pin = null;
            map.on('click', function (e) {
                if (pin) { map.removeLayer(pin); }
                pin = L.circleMarker(e.latlng, {
                    radius: 10, color: '#275D38', weight: 3,
                    fillColor: '#F2CD00', fillOpacity: 1
                }).addTo(map);
            });
        })();
        {% endmacro %}
    """)


apply_theme("760px")

st.session_state.setdefault("lat", None)
st.session_state.setdefault("lon", None)
st.session_state.setdefault("map_id", 0)
st.session_state.setdefault("ref", None)

# =============== Screen 2: confirmation ===============
if st.session_state["ref"]:
    page_title("Report received", f"Your reference number is <b>{st.session_state['ref']}</b>")
    with st.container(key="card_next"):
        st.subheader("What happens next")
        st.markdown(
            """
            <div class="steps">
              <div class="step done"><b>1</b>Received</div>
              <div class="step"><b>2</b>An RA reviews it</div>
              <div class="step"><b>3</b>Marked resolved</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.caption("Keep your reference number. You never need to give your name.")
    notice(
        "If anyone is in danger right now, call <b>911</b>. "
        "This form is not watched in real time."
    )
    if st.button("Report another issue", key="again_btn"):
        st.session_state["ref"] = None
        st.session_state["lat"] = None
        st.session_state["lon"] = None
        st.session_state["map_id"] += 1
        st.rerun()
    site_footer()
    st.stop()

# =============== Screen 1: the report form ===============
page_title("Report an issue", "Takes about a minute. Your report is confidential.")
notice(
    "In an emergency, call <b>911</b>. "
    "This form is not watched in real time."
)

with st.container(key="card_where"):
    st.subheader("1. Where did it happen?")
    place_type = st.radio(
        "Type of place",
        ["Residence tower", "Shared space"],
        horizontal=True,
        key=f"place_type_{st.session_state['map_id']}",
    )
    if place_type == "Residence tower":
        building = st.selectbox("Building", list(TOWERS.keys()))
        floor = st.selectbox(
            "Floor", [f"Floor {i}" for i in range(1, TOWERS[building]["floors"] + 1)]
        )
        zone = st.selectbox("Location zone", ZONES)
    else:
        building = st.selectbox("Which space?", list(SHARED_SPACES.keys()))
        floor = SHARED_FLOOR
        zone = SHARED_ZONE

with st.container(key="card_what"):
    st.subheader("2. What happened?")
    raw_text = st.text_area(
        "Describe the issue",
        placeholder="Include the exact room number if you know it...",
        height=120,
        key=f"description_{st.session_state['map_id']}",
    )

with st.container(key="card_pin"):
    st.subheader("3. Pin the exact spot (optional)")
    st.caption("Tap the map to drop a marker. Zoom in for a more exact spot.")

    # The map never changes between taps, so it does not reload or jump.
    pin_map = folium.Map(location=MAP_CENTER, zoom_start=18)
    pin_map.add_child(SinglePin())

    map_result = st_folium(
        pin_map,
        height=300,
        use_container_width=True,
        key=f"pin_map_{st.session_state['map_id']}",
        returned_objects=["last_clicked"],
    )

    click = map_result.get("last_clicked") if map_result else None
    if click:
        st.session_state["lat"] = click["lat"]
        st.session_state["lon"] = click["lng"]

    if st.session_state["lat"] is not None:
        closest = nearest_tower(st.session_state["lat"], st.session_state["lon"])
        if place_type == "Residence tower" and closest != building and zone != "Exterior/Grounds":
            st.warning(
                f"Your marker is closest to {closest}, but you chose {building}. "
                "Please check both before you submit."
            )
        else:
            st.success("Location pinned")

if st.button("Submit report", type="primary", key="submit_btn"):
    if not raw_text.strip():
        st.warning("Please describe what happened first.")
    else:
        result = process_incident(raw_text)
        saved = save_data(
            building,
            floor,
            zone,
            raw_text,
            result["scrubbed_text"],
            result["category"],
            result["urgency"],
            st.session_state["lat"],
            st.session_state["lon"],
        )
        if saved is False:
            st.stop()  # storage.py already showed the error message
        st.session_state["ref"] = f"R-{len(load_data()):04d}"
        st.rerun()

site_footer()