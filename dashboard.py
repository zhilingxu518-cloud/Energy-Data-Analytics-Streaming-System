from __future__ import annotations

import os
import time
import threading
import json
from typing import Dict, Any, Tuple, Optional

import pandas as pd
import streamlit as st
import pydeck as pdk
import paho.mqtt.client as mqtt


# -----------------------------
# Section: data_model.py
# -----------------------------

TOTAL_CSV_PATH = os.getenv("TOTAL_CSV", "total.csv")

# create a reentrant lock to avoid dead lock
_LOCK = threading.RLock()

# overall dictionary allow any type
lookup_by_name: Dict[str, Dict[str, Any]] = {}

# contain the newest recording
latest: Dict[str, Dict[str, Any]] = {}


# change the null into str
def _norm_name(s: Optional[str]) -> str:
    if s is None:
        return ""
    s = str(s).strip()
    return " ".join(s.split())


# read csv and rename the columns
def _read_total_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path).rename(
        columns={
            "network_region": "region",
            "location.lat": "lat",
            "location.lng": "lon",
        }
    )

    required = [
        "timestamp",
        "name",
        "fuel_tech",
        "power",
        "emissions",
        "region",
        "price",
        "demand",
        "lat",
        "lon",
    ]
    missing = set(required) - set(df.columns)
    if missing:
        raise ValueError(f"total.csv missing columns: {sorted(missing)}")

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    for c in ["power", "emissions", "price", "demand", "lat", "lon"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # delect the null in name and ful_tech
    df["name"] = df["name"].map(_norm_name)
    df["fuel_tech"] = df["fuel_tech"].astype(str).str.strip()

    return df


# look up the newest recording for every names and return a dictionary for searching
def _build_lookup(df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    last = (
        df.sort_values(["name", "timestamp"])
        .drop_duplicates("name", keep="last")[["name", "region", "lat", "lon"]]
    )
    return last.set_index("name")[["region", "lat", "lon"]].to_dict("index")


# build the latest recording for each name for quick search
def _build_latest(df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    last = (
        df.sort_values(["name", "timestamp"])
        .drop_duplicates("name", keep="last")
        .assign(ts=lambda d: d["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
    )

    fields = [
        "name",
        "ts",
        "fuel_tech",
        "power",
        "emissions",
        "region",
        "price",
        "demand",
        "lat",
        "lon",
    ]
    records = last[fields].to_dict("records")

    result = {
        r["name"]: {
            "name": r["name"],
            "ts": r["ts"],
            "fuel_tech": r["fuel_tech"],
            "power_mw": float(r["power"]) if pd.notna(r["power"]) else None,
            "emissions_tph": float(r["emissions"]) if pd.notna(r["emissions"]) else None,
            "region": r["region"],
            "price": float(r["price"]) if pd.notna(r["price"]) else None,
            "demand": float(r["demand"]) if pd.notna(r["demand"]) else None,
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
        }
        for r in records
    }
    return result


# built per-name lookup and latest snapshots (auto refresh
def init_from_csv():
    df = _read_total_csv(TOTAL_CSV_PATH)
    with _LOCK:
        lookup_by_name.clear()
        latest.clear()
        lookup_by_name.update(_build_lookup(df))
        latest.update(_build_latest(df))


# thread-safely update latest from a new payload
def update_latest_from_payload(payload: Dict[str, Any]):
    name = _norm_name(payload.get("name"))
    if not name:
        return

    power = payload.get("power")
    emissions = payload.get("emissions")
    region = payload.get("region") or payload.get("network_region")
    lat = payload.get("lat") or payload.get("location.lat")
    lon = payload.get("lon") or payload.get("location.lng")
    price = payload.get("price")
    demand = payload.get("demand")
    fuel_tech = payload.get("fuel_tech")
    ts = payload.get("timestamp") or payload.get("ts")

    with _LOCK:
        base = latest.get(name, {})

        entry = dict(base)

        if ts:
            entry["ts"] = str(ts)
        if fuel_tech is not None:
            entry["fuel_tech"] = str(fuel_tech).strip()
        if power is not None:
            entry["power_mw"] = float(power)
        if emissions is not None:
            entry["emissions_tph"] = float(emissions)
        if region is not None:
            entry["region"] = str(region)
        if price is not None:
            entry["price"] = float(price)
        if demand is not None:
            entry["demand"] = float(demand)
        if lat is not None:
            entry["lat"] = float(lat)
        if lon is not None:
            entry["lon"] = float(lon)

        recent = latest.get(name, {})
        entry.setdefault("ts", recent.get("ts"))
        entry.setdefault("fuel_tech", recent.get("fuel_tech"))
        entry.setdefault("power_mw", recent.get("power_mw"))
        entry.setdefault("emissions_tph", recent.get("emissions_tph"))
        entry.setdefault("region", recent.get("region"))
        entry.setdefault("price", recent.get("price"))
        entry.setdefault("demand", recent.get("demand"))
        entry.setdefault("lat", recent.get("lat"))
        entry.setdefault("lon", recent.get("lon"))

        latest[name] = entry


def get_snapshot() -> Tuple[pd.DataFrame, pd.DataFrame]:
    # copy lk and lt to avoid reading neither the latest or old
    with _LOCK:
        lk = lookup_by_name.copy()
        lt = latest.copy()

    # change dicts into dataframe
    df_lookup = pd.DataFrame.from_dict(lk, orient="index")
    df_lookup.index.name = "name"
    df_lookup = df_lookup.reset_index()

    df_latest = pd.DataFrame.from_dict(lt, orient="index")
    if "name" in df_latest.columns:
        df_latest = df_latest.drop(columns=["name"])
    df_latest.index.name = "name"
    df_latest = df_latest.reset_index()

    
    df_lookup = df_lookup.reindex(columns=["name", "region", "lat", "lon"])
    df_latest = df_latest.reindex(
        columns=[
            "name",
            "ts",
            "fuel_tech",
            "power_mw",
            "emissions_tph",
            "region",
            "price",
            "demand",
            "lat",
            "lon",
        ]
    )
    return df_lookup, df_latest


# -----------------------------
# Section: mqtt_sub.py
# -----------------------------

# ready to connect MQTT use test broker
MQTT_BROKER = os.getenv("MQTT_BROKER", "test.mosquitto.org")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "NEM/PowerEmissions")

USE_MQTT = os.getenv("USE_MQTT", "1") == "1"
MQTT_USER = os.getenv("MQTT_USER", "") or None
MQTT_PASS = os.getenv("MQTT_PASS", "") or None

# for demo we support a simple JSON message per publish
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print("[mqtt] Connected OK")
        client.subscribe(MQTT_TOPIC)
        print(f"[mqtt] Subscribed to {MQTT_TOPIC}")
    else:
        print(f"[mqtt] Bad connection. Returned code={rc}")


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        print(f"[mqtt] RX {msg.topic}: {payload}")
        if isinstance(payload, dict):
            update_latest_from_payload(payload)
    except Exception as e:
        print("[mqtt] on_message error:", e)


def start_mqtt_thread():
    if not USE_MQTT:
        print("[mqtt] USE_MQTT=0; MQTT subscriber not started")
        return

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    except Exception as e:
        print("[mqtt] connect error:", e)
        return

    th = threading.Thread(target=client.loop_forever, daemon=True)
    th.start()
    print("[mqtt] subscriber started")


# -----------------------------
# Section: app.py
# -----------------------------

# Refresh interval for auto-update
REFRESH_SEC = float(os.getenv("REFRESH_SEC", "2.0"))

# Carto basemap style
CARTO_BASEMAP = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"

# Single source of truth for region color mapping
REGION_COLORS: Dict[str, Any] = {
    "NSW1": [0, 122, 255, 180],
    "QLD1": [255, 159, 10, 180],
    "SA1": [255, 59, 48, 180],
    "VIC1": [52, 199, 89, 180],
    "TAS1": [175, 82, 222, 180],
    "WA1": [255, 204, 0, 180],
    "NT1": [142, 142, 147, 180],
    "ACT1": [88, 86, 214, 180],
}

# Default radius for scatter layer
DEFAULT_RADIUS_M = int(os.getenv("DEFAULT_RADIUS_M", "9000"))


def _color_by_region(df: pd.DataFrame) -> pd.Series:
    default = [100, 100, 100, 160]
    return df["region"].apply(lambda r: REGION_COLORS.get(r, default))


def _radius_by_power(df: pd.DataFrame) -> pd.Series:
    # let the dot-size related to power
    p = df["power_mw"].fillna(0.0).clip(lower=0)
    r = (p.pow(0.5) * 800 + DEFAULT_RADIUS_M).clip(upper=25000)
    return r


# side bar and filters
def _render_sidebar(df_latest: pd.DataFrame) -> Dict[str, Any]:
    st.sidebar.write(f"Refresh: `{REFRESH_SEC}` s")
    st.sidebar.divider()
    st.sidebar.markdown("**Filters**")

    region_filter = st.sidebar.multiselect(
        "Region",
        options=list(REGION_COLORS.keys()),
        default=[],
        help="Filter by network region.",
        key = "filter_region"
    )

    fuel_options = sorted([x for x in df_latest["fuel_tech"].dropna().unique().tolist()])
    fuel_filter = st.sidebar.multiselect(
        "Fuel / Technology",
        options=fuel_options,
        default=[],
        help="Filter by generation fuel/technology.",
        key = "filter_fuel"
    )

    return {"region_filter": region_filter, "fuel_filter": fuel_filter}


# Data snapshot and filtering
def _load_snapshot(filters: Dict[str, Any]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    # Thread-safe copy of the latest cache tables
    df_lookup, df_latest = get_snapshot()

    # Apply region filter if any
    if filters.get("region_filter"):
        df_latest = df_latest[df_latest["region"].isin(filters["region_filter"])].copy()

    # Apply fuel_tech filter if any
    if filters.get("fuel_filter"):
        df_latest = df_latest[df_latest["fuel_tech"].isin(filters["fuel_filter"])].copy()

    # Keep consistent column order
    if not df_latest.empty:
        df_latest = df_latest[
            [
                "name",
                "ts",
                "fuel_tech",
                "power_mw",
                "emissions_tph",
                "region",
                "price",
                "demand",
                "lat",
                "lon",
            ]
        ]
    return df_lookup, df_latest


# top metrics
def _render_kpis(df_lookup: pd.DataFrame, df_latest: pd.DataFrame):
    c1, c2, c3, c4, c5 = st.columns(5)

    facilities = len(df_lookup)
    active = int((df_latest["power_mw"].fillna(0.0) > 0).sum()) if not df_latest.empty else 0
    regions = df_latest["region"].nunique() if not df_latest.empty else 0

    price_val = (
        df_latest["price"].dropna().mean() if (not df_latest.empty and df_latest["price"].notna().any()) else None
    )
    demand_val = (
        df_latest["demand"].dropna().mean() if (not df_latest.empty and df_latest["demand"].notna().any()) else None
    )

    c1.metric("Facilities", facilities)
    c2.metric("Active (power>0)", active)
    c3.metric("Regions", regions)
    c4.metric("Current Price ($/MWh)", f"{price_val:.2f}" if price_val is not None else "N/A")
    c5.metric("Network Demand (MW)", f"{demand_val:.0f}" if demand_val is not None else "N/A")


def _render_map(df_latest: pd.DataFrame):
    if df_latest.empty:
        st.info("No data for current filters.")
        return

    dfv = df_latest.dropna(subset=["lat", "lon"]).copy()
    dfv["color"] = _color_by_region(dfv)
    dfv["radius"] = _radius_by_power(dfv)

    layer = pdk.Layer(
        "ScatterplotLayer",
        dfv,
        get_position=["lon", "lat"],
        get_color="color",
        get_radius="radius",
        pickable=True,
        auto_highlight=True,
        radius_scale=1,
        radius_min_pixels=2,
        radius_max_pixels=120,
    )

    
    tooltip = {
        "html": """
        <b>{name}</b><br/>
        <b>Fuel:</b> {fuel_tech}<br/>
        <b>Power:</b> {power_mw} MW<br/>
        <b>Emissions:</b> {emissions_tph} t/h<br/>
        <b>Region:</b> {region}<br/>
        <b>Price:</b> {price} $/MWh<br/>
        <b>Demand:</b> {demand} MW<br/>
        <b>Updated:</b> {ts}
        """,
        "style": {"font-size": "12px"},
    }

    view_state = pdk.ViewState(
        latitude=float(dfv["lat"].mean()),
        longitude=float(dfv["lon"].mean()),
        zoom=4.2,
        pitch=0,
        bearing=0,
    )

    r = pdk.Deck(
        layers=[layer],
        initial_view_state=view_state,
        map_style=CARTO_BASEMAP,
        tooltip=tooltip,
    )

    st.pydeck_chart(r, width="stretch")

# Main streamlit app
def main():
    st.set_page_config(page_title="Power & Emissions Monitor", layout="wide")
    st.title("Power & Emissions — Live Monitor")

    # initial csv and mqtt
    if not latest:
        init_from_csv()
    if not st.session_state.get("mqtt_started", False):
        start_mqtt_thread()
        st.session_state["mqtt_started"] = True

    df_lookup, df_latest = get_snapshot()

    filters = _render_sidebar(df_latest)

    df_lookup, df_latest = _load_snapshot(filters)

    _render_kpis(df_lookup, df_latest)
    _render_map(df_latest)

    with st.expander("Tables", expanded=False):
        # Tables
        st.subheader("Latest snapshot")
        st.dataframe(df_latest, width="stretch", height=360)

        st.subheader("Lookup (static info)")
        st.dataframe(df_lookup, width="stretch", height=260)

        # Sleep for update
    time.sleep(REFRESH_SEC)
    st.rerun()

if __name__ == "__main__":
    main()
