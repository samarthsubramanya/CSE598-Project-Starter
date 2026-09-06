"""Exports demo.py's fixed scenario as a single, self-contained HTML page
that animates it on a real map — depot, vehicles, orders, incidents, ticks
— using Leaflet + OpenStreetMap tiles.

No API key, no signup, no server: OSM's tile server is free for this kind
of light, non-commercial use (Leaflet's default attribution stays on the
map, as required). The whole trace is embedded as JSON in the HTML file,
so double-clicking map_demo.html and opening it in a browser is enough —
no `python3 -m http.server` needed.

The grid's abstract (x, y) coordinates are linearly mapped onto a small
real bounding box around Tempe/ASU, purely for a recognizable, concrete
"small suburb" backdrop — the simulation itself has no real street data or
routing; a vehicle still moves in straight grid steps, just plotted on
real streets instead of graph paper.
"""
import json

from demo import run_scenario

# A ~2km x 2km box around Tempe/ASU. Depot sits at grid (0, 0) -> this
# box's SW corner.
LAT_MIN, LAT_MAX = 33.415, 33.435
LON_MIN, LON_MAX = -111.950, -111.925
GRID_SIZE = 10  # matches sim.GRID_SIZE — grid coords span [0, GRID_SIZE]


def to_latlon(pos):
    gx, gy = pos
    lat = LAT_MIN + (gy / GRID_SIZE) * (LAT_MAX - LAT_MIN)
    lon = LON_MIN + (gx / GRID_SIZE) * (LON_MAX - LON_MIN)
    return [lat, lon]


def build_data(frames):
    geo_frames = [
        {
            "tick": f["tick"],
            "vehicles": [{**v, "latlon": to_latlon(v["pos"])} for v in f["vehicles"]],
            "orders": [{**o, "latlon": to_latlon(o["dest"])} for o in f["orders"]],
        }
        for f in frames
    ]
    return {"frames": geo_frames, "depot": to_latlon((0, 0))}


TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Fleet Dispatch — Map Replay</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
  html, body { margin: 0; height: 100%; font-family: -apple-system, sans-serif; background: #1e2129; }
  #map { position: absolute; top: 0; bottom: 64px; width: 100%; }
  #bar { position: absolute; bottom: 0; height: 64px; width: 100%; background: #22252e; color: #e1e4eb;
         display: flex; align-items: center; gap: 12px; padding: 0 16px; box-sizing: border-box; }
  button { background: #3a3f4f; color: #e1e4eb; border: none; border-radius: 6px; padding: 8px 14px; cursor: pointer; font-size: 14px; }
  button:hover { background: #4a4f63; }
  #tick-label { min-width: 90px; font-variant-numeric: tabular-nums; }
  input[type=range] { flex: 1; }
  .legend { position: absolute; top: 10px; right: 10px; background: rgba(30,33,41,0.9); color: #e1e4eb;
            padding: 10px 14px; border-radius: 8px; font-size: 12px; line-height: 1.6; z-index: 1000; }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
</style>
</head>
<body>
<div id="map"></div>
<div class="legend">
  <div><span class="dot" style="background:#f4c542"></span>idle</div>
  <div><span class="dot" style="background:#4aa3ff"></span>enroute</div>
  <div><span class="dot" style="background:#7fd97f"></span>returning</div>
  <div><span class="dot" style="background:#8a5cf5"></span>charging</div>
  <div><span class="dot" style="background:#f45c5c"></span>broken</div>
  <div><span class="dot" style="background:#666"></span>depot</div>
</div>
<div id="bar">
  <button id="playBtn">Play</button>
  <span id="tick-label">t = 0</span>
  <input type="range" id="slider" min="0" max="0" value="0">
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const DATA = __DATA__;
const STATUS_COLOR = { idle: "#f4c542", enroute: "#4aa3ff", returning: "#7fd97f", charging: "#8a5cf5", broken: "#f45c5c" };

const map = L.map('map').setView(DATA.depot, 15);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  maxZoom: 19,
}).addTo(map);

L.marker(DATA.depot, {
  icon: L.divIcon({ className: '', html: '<div style="background:#666;width:14px;height:14px;border-radius:3px;border:2px solid white"></div>' })
}).addTo(map).bindTooltip("Depot");

let vehicleMarkers = {};
let orderMarkers = {};

function render(frameIdx) {
  const frame = DATA.frames[frameIdx];
  document.getElementById('tick-label').textContent = 't = ' + frame.tick;
  document.getElementById('slider').value = frameIdx;

  const seenVehicles = new Set();
  frame.vehicles.forEach(v => {
    seenVehicles.add(v.id);
    const color = STATUS_COLOR[v.status] || "#fff";
    if (!vehicleMarkers[v.id]) {
      vehicleMarkers[v.id] = L.circleMarker(v.latlon, { radius: 9, color: "#fff", weight: 1.5, fillColor: color, fillOpacity: 0.95 }).addTo(map);
    }
    vehicleMarkers[v.id].setLatLng(v.latlon);
    vehicleMarkers[v.id].setStyle({ fillColor: color });
    vehicleMarkers[v.id].bindTooltip(`vehicle ${v.id}: ${v.status}, battery ${v.battery.toFixed(0)}%`);
  });

  const seenOrders = new Set();
  frame.orders.forEach(o => {
    if (o.delivered_tick !== null && o.delivered_tick <= frame.tick) return; // stop showing once delivered
    if (o.created_tick > frame.tick) return; // not created yet
    seenOrders.add(o.id);
    if (!orderMarkers[o.id]) {
      orderMarkers[o.id] = L.circleMarker(o.latlon, { radius: 5, color: "#fff", weight: 1, fillColor: "#ffb000", fillOpacity: 0.9 }).addTo(map);
      orderMarkers[o.id].bindTooltip(`order ${o.id} destination`);
    }
  });
  Object.keys(orderMarkers).forEach(id => {
    if (!seenOrders.has(Number(id))) { map.removeLayer(orderMarkers[id]); delete orderMarkers[id]; }
  });
}

const slider = document.getElementById('slider');
slider.max = DATA.frames.length - 1;
slider.addEventListener('input', () => render(Number(slider.value)));

let playing = false, playTimer = null;
document.getElementById('playBtn').addEventListener('click', () => {
  playing = !playing;
  document.getElementById('playBtn').textContent = playing ? 'Pause' : 'Play';
  if (playing) {
    playTimer = setInterval(() => {
      let next = Number(slider.value) + 1;
      if (next > slider.max) { next = 0; }
      render(next);
    }, 500);
  } else {
    clearInterval(playTimer);
  }
});

render(0);
</script>
</body>
</html>
"""


def build_html(frames, out_path="map_demo.html"):
    data_json = json.dumps(build_data(frames))
    html = TEMPLATE.replace("__DATA__", data_json)
    with open(out_path, "w") as fh:
        fh.write(html)
    return out_path


def _try_open_browser(path, timeout=3):
    """webbrowser.open() can hang instead of failing in a headless/sandboxed
    environment (no GUI session for the OS to hand off to) — run it in a
    daemon thread with a timeout so a missing display can never freeze this
    script, only skip the auto-open."""
    import os
    import threading
    import webbrowser

    def _open():
        try:
            webbrowser.open(f"file://{os.path.abspath(path)}")
        except Exception:
            pass

    t = threading.Thread(target=_open, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        print(f"(no browser auto-opened — open {path} manually)")


if __name__ == "__main__":
    _, frames = run_scenario(record_frames=True)
    assert frames, "expected recorded frames from the fixed scenario"
    path = build_html(frames)
    print(f"self-check passed: wrote {len(frames)} frames to {path}.")
    _try_open_browser(path)
