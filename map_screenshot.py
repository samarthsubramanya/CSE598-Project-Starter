"""Renders one static frame of map_export.py's animation as a PNG, for
embedding in the README/proposal doc where a live HTML page isn't
paste-able. Fetches real OSM tiles (same free tile server map_demo.html
uses) and composites markers on top with PIL — a one-off illustration,
not part of the baseline's dispatch/eval logic.
"""
import math
import sys

import requests
from PIL import Image, ImageDraw, ImageFont

from demo import run_scenario
from map_export import LAT_MIN, LAT_MAX, LON_MIN, LON_MAX, to_latlon, build_data

ZOOM = 15
TILE_SIZE = 256
STATUS_COLOR = {
    "idle": (244, 197, 66), "enroute": (74, 163, 255), "returning": (127, 217, 127),
    "charging": (138, 92, 245), "broken": (244, 92, 92),
}


def _latlon_to_pixel(lat, lon, zoom):
    lat_rad = math.radians(lat)
    n = 2 ** zoom
    x = (lon + 180.0) / 360.0 * n * TILE_SIZE
    y = (1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n * TILE_SIZE
    return x, y


def _fetch_tile(z, x, y):
    url = f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    r = requests.get(url, headers={"User-Agent": "capstone-baseline-illustration/1.0"}, timeout=10)
    r.raise_for_status()
    from io import BytesIO
    return Image.open(BytesIO(r.content)).convert("RGB")


def render_frame(frame, out_path="map_demo_screenshot.png"):
    margin = 16  # px padding so edge markers (e.g. the depot at grid (0,0)) aren't clipped
    px0, py0 = _latlon_to_pixel(LAT_MAX, LON_MIN, ZOOM)  # NW corner
    px1, py1 = _latlon_to_pixel(LAT_MIN, LON_MAX, ZOOM)  # SE corner
    px0, py0, px1, py1 = px0 - margin, py0 - margin, px1 + margin, py1 + margin

    tx0, ty0 = int(px0 // TILE_SIZE), int(py0 // TILE_SIZE)
    tx1, ty1 = int(px1 // TILE_SIZE), int(py1 // TILE_SIZE)

    canvas = Image.new("RGB", ((tx1 - tx0 + 1) * TILE_SIZE, (ty1 - ty0 + 1) * TILE_SIZE))
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            canvas.paste(_fetch_tile(ZOOM, tx, ty), ((tx - tx0) * TILE_SIZE, (ty - ty0) * TILE_SIZE))

    crop = canvas.crop((int(px0 - tx0 * TILE_SIZE), int(py0 - ty0 * TILE_SIZE),
                         int(px1 - tx0 * TILE_SIZE), int(py1 - ty0 * TILE_SIZE)))
    img = crop.convert("RGB")
    draw = ImageDraw.Draw(img)

    def to_xy(latlon):
        lat, lon = latlon
        x, y = _latlon_to_pixel(lat, lon, ZOOM)
        return x - px0, y - py0

    depot_xy = to_xy(to_latlon((0, 0)))
    r = 6
    draw.rectangle([depot_xy[0] - r, depot_xy[1] - r, depot_xy[0] + r, depot_xy[1] + r],
                   fill=(90, 90, 90), outline=(255, 255, 255), width=2)

    for o in frame["orders"]:
        if o["delivered_tick"] is not None and o["delivered_tick"] <= frame["tick"]:
            continue
        if o["created_tick"] > frame["tick"]:
            continue
        x, y = to_xy(o["latlon"])
        rr = 4
        draw.ellipse([x - rr, y - rr, x + rr, y + rr], fill=(255, 176, 0), outline=(255, 255, 255))

    for v in frame["vehicles"]:
        x, y = to_xy(v["latlon"])
        rr = 9
        color = STATUS_COLOR.get(v["status"], (255, 255, 255))
        draw.ellipse([x - rr, y - rr, x + rr, y + rr], fill=color, outline=(255, 255, 255), width=2)

    label = f"t = {frame['tick']}"
    draw.rectangle([8, 8, 8 + 8 * len(label) + 12, 30], fill=(30, 33, 41))
    draw.text((14, 12), label, fill=(225, 228, 235))

    img.save(out_path)
    return out_path


if __name__ == "__main__":
    tick = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    _, frames = run_scenario(record_frames=True)
    geo_frames = build_data(frames)["frames"]
    frame = next(f for f in geo_frames if f["tick"] == tick)
    path = render_frame(frame)
    print(f"self-check passed: wrote a {tick}-tick map snapshot to {path}.")
