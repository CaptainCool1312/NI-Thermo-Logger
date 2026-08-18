#!/usr/bin/env python3
"""Small read-only monitoring web interface for the NI temperature logger."""

from pathlib import Path
import sqlite3

import yaml
from flask import Flask, jsonify, render_template_string

BASE = Path(__file__).parent
CFG = yaml.safe_load((BASE / "config.yaml").read_text(encoding="utf-8"))
DB = Path(CFG["database"])

app = Flask(__name__)

HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>NI Temperature Logger</title>
<style>
body { font-family: sans-serif; margin: 2rem; background:#f5f5f5; }
h1 { margin-bottom:.3rem; }
.status { margin-bottom:1rem; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; }
.card { background:white; padding:16px; border-radius:8px; box-shadow:0 1px 4px #bbb; }
.name { font-weight:bold; }
.temp { font-size:2rem; margin-top:8px; }
.small { color:#666; font-size:.85rem; }
.ok { color:green; }
.err { color:#b00020; }
</style>
</head>
<body>
<h1>NI Temperature Logger</h1>
<div class="status">
DAQ: <strong class="{{ 'ok' if daq_ok else 'err' }}">
{{ 'RUNNING' if daq_ok else 'NO RECENT DATA' }}
</strong>
&nbsp; Last measurement: {{ last_time or 'none' }}
</div>
<div class="grid">
{% for c in channels %}
<div class="card">
<div class="name">{{ c.name }}</div>
<div class="small">{{ c.description }}</div>
<div class="temp">
{{ "%.2f"|format(c.value) if c.value is not none else "—" }} °C
</div>
</div>
{% endfor %}
</div>
<p class="small">Auto-refresh: 30 s</p>
</body>
</html>
"""


def query():
    if not DB.exists():
        return [], None

    conn = sqlite3.connect(DB)
    rows = conn.execute("""
        SELECT m.channel, m.temperature_c, m.timestamp_local
        FROM measurements m
        JOIN (
            SELECT channel, MAX(timestamp_utc) AS max_t
            FROM measurements
            GROUP BY channel
        ) x ON m.channel=x.channel AND m.timestamp_utc=x.max_t
        ORDER BY m.channel
    """).fetchall()
    conn.close()

    latest = {}
    last_time = None
    for channel, value, timestamp in rows:
        latest[channel] = value
        last_time = timestamp

    channels = []
    for c in CFG["channels"]:
        channels.append({
            "name": c["name"],
            "description": c.get("description", ""),
            "value": latest.get(c["name"]),
        })

    return channels, last_time


@app.route("/")
def index():
    channels, last_time = query()
    daq_ok = bool(last_time)
    return render_template_string(
        HTML,
        channels=channels,
        last_time=last_time,
        daq_ok=daq_ok,
    )


@app.route("/api/latest")
def api_latest():
    channels, last_time = query()
    return jsonify({
        "last_measurement": last_time,
        "channels": channels,
    })


if __name__ == "__main__":
    from waitress import serve
    serve(app, host=CFG["web_host"], port=int(CFG["web_port"]))
