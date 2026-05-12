# Energy Data Analytics & Live Streaming Dashboard

Real-time monitoring dashboard for the Australian National Electricity Market (NEM), combining historical data analytics with live MQTT-streamed power generation and emissions data.

## Overview

This project implements a complete ETL-to-dashboard pipeline:

1. **Data Acquisition** — Ingests energy facility data, emissions records, and economic indicators from Australian government sources (CER DataHub, ABS, OpenElectricity API)
2. **Data Integration & Augmentation** — Cleans, normalizes, and geocodes facility records; builds a relational spatial database in DuckDB
3. **Real-Time Streaming** — Publishes live power generation and emissions metrics via MQTT
4. **Interactive Visualization** — Streamlit dashboard with PyDeck geospatial mapping, KPI cards, and filterable data tables

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌───────────────────┐
│  OpenElectricity │────▶│  MQTT Publisher   │────▶│  MQTT Broker       │
│  API (Live Data) │     │  (A2 Notebook)    │     │  test.mosquitto.org │
└─────────────────┘     └──────────────────┘     └────────┬──────────┘
                                                          │
┌─────────────────┐     ┌──────────────────┐              │
│  CER DataHub     │────▶│  Data Pipeline    │              │
│  ABS Statistics  │     │  (A1 Notebook)    │              ▼
└─────────────────┘     └────────┬─────────┘     ┌───────────────────┐
                                 │                │  Streamlit App     │
                                 ▼                │  (PyDeck Map +     │
                          ┌──────────────┐        │   KPI Dashboard)   │
                          │   DuckDB      │        └───────────────────┘
                          │  (Spatial DB) │
                          └──────────────┘
```

## Features

- **Multi-source data ingestion** — CER emissions API, ABS economic data, OpenElectricity live metrics
- **Spatial database** — DuckDB with spatial extension, relational schema with 7 normalized tables
- **Geocoding pipeline** — OpenStreetMap Nominatim geocoding of Australian facility addresses
- **MQTT streaming** — Real-time power/emissions data published to configurable MQTT topics
- **Interactive map** — PyDeck scatter plot with region-colored, power-scaled facility markers
- **Live KPIs** — Facility count, active plants, market price, network demand
- **Filterable views** — Sidebar filters by NEM region and fuel technology

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.10+ |
| Data Processing | pandas, numpy |
| Database | DuckDB (in-process OLAP), duckdb-engine (SQLAlchemy) |
| Spatial | DuckDB Spatial, geopandas, shapely, geoalchemy2 |
| Messaging | paho-mqtt (MQTT v5) |
| Dashboard | Streamlit, PyDeck (deck.gl) |
| APIs | OpenElectricity, CER DataHub, OpenStreetMap Nominatim |
| Notebook | Jupyter, JupySQL (%%sql magic) |

## Project Structure

```
├── 01_data_pipeline.ipynb          # Data acquisition, cleaning, spatial DB
├── 02_mqtt_streaming.ipynb         # Live data fetch, MQTT publisher
├── dashboard.py                    # Streamlit + PyDeck dashboard
├── requirements.txt                      # Python dependencies
├── .env.example                          # Environment variable template
├── .gitignore
└── README.md
```

## Setup

### Prerequisites

- Python 3.10 or higher
- Jupyter Notebook / JupyterLab (for `.ipynb` files)

### Installation

```bash
# Clone the repository
git clone <repo-url>
cd <repo-directory>

# Create and activate a virtual environment
python -m venv venv
source venv/bin/activate   # Linux/Mac
# venv\Scripts\activate    # Windows

# Install dependencies
pip install -r requirements.txt
```

### Configuration

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `OPENELECTRICITY_API_KEY` | Yes | API key from [openelectricity.org.au](https://openelectricity.org.au) |
| `MQTT_BROKER` | No | MQTT broker address (default: `test.mosquitto.org`) |
| `MQTT_PORT` | No | MQTT broker port (default: `1883`) |
| `MQTT_TOPIC` | No | MQTT topic to subscribe to |
| `USE_MQTT` | No | Set to `0` to run dashboard offline with CSV only |

## Usage

### Step 1 — Data Pipeline

Run `01_data_pipeline.ipynb` in Jupyter to:
- Fetch 10 years of NGER emissions data from CER DataHub
- Download CER power station registries
- Ingest ABS economic indicators
- Clean, normalize, and geocode facility data
- Build a DuckDB spatial database with relational schema

### Step 2 — Live Data & MQTT Streaming

Run `02_mqtt_streaming.ipynb` in Jupyter to:
- Fetch live power generation and market data from OpenElectricity
- Merge facility metadata, metrics, and market pricing
- Output `total.csv` (the merged dataset)
- Start MQTT publisher streaming each facility's data

### Step 3 — Launch Dashboard

```bash
streamlit run dashboard.py
```

Open your browser to `http://localhost:8501`. The dashboard will:
- Load initial data from `total.csv`
- Connect to the MQTT broker for live updates
- Display an interactive map with real-time facility metrics

## Data Sources

- [OpenElectricity](https://openelectricity.org.au) — Live NEM generation and emissions data
- [CER DataHub](https://www.cleanenergyregulator.gov.au/) — National Greenhouse and Energy Reporting
- [ABS](https://www.abs.gov.au/) — Australian Bureau of Statistics economic indicators
- [OpenStreetMap Nominatim](https://nominatim.org/) — Geocoding service (fair-use limits apply)
