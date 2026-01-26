# pc/server.py
from flask import Flask, request, jsonify, send_file, abort, Response
from pathlib import Path
from datetime import datetime
import os
import shutil
import io
import numpy as np
from PIL import Image

app = Flask(__name__)

# 关键：用 server.py 所在目录作为根，避免“从别的目录启动导致 frames 写到别处”
BASE_DIR = Path(__file__).resolve().parent
FRAMES_DIR = BASE_DIR / "frames"
FRAMES_DIR.mkdir(parents=True, exist_ok=True)

LATEST_L = FRAMES_DIR / "latest_L.jpg"
LATEST_R = FRAMES_DIR / "latest_R.jpg"


def _now_ts():
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _atomic_copy(src: Path, dst: Path):
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def _set_nocache(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


def _save_jpg(prefix: str, jpg_bytes: bytes) -> Path:
    out = FRAMES_DIR / f"{prefix}_{_now_ts()}.jpg"
    out.write_bytes(jpg_bytes)
    return out


def _rgb565_to_rgb888(raw: bytes, w: int, h: int, swap_bytes: bool) -> np.ndarray:
    if len(raw) != w * h * 2:
        raise ValueError(f"raw size mismatch: got={len(raw)} expect={w*h*2}")

    a = np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 2)
    if swap_bytes:
        a = a[..., ::-1]  # swap low/high byte per pixel

    msb = a[..., 0].astype(np.uint16)
    lsb = a[..., 1].astype(np.uint16)
    v = (msb << 8) | lsb

    r = ((v >> 11) & 0x1F).astype(np.uint8)
    g = ((v >> 5) & 0x3F).astype(np.uint8)
    b = (v & 0x1F).astype(np.uint8)

    r = (r << 3) | (r >> 2)
    g = (g << 2) | (g >> 4)
    b = (b << 3) | (b >> 2)

    return np.stack([r, g, b], axis=-1)


def _score_natural(rgb: np.ndarray) -> float:
    x = rgb.astype(np.int16)
    gx = np.abs(x[:, 1:, :] - x[:, :-1, :]).mean()
    gy = np.abs(x[1:, :, :] - x[:-1, :, :]).mean()
    return float(gx + gy)


def _raw565_to_jpeg_best(raw: bytes, w: int, h: int):
    rgb0 = _rgb565_to_rgb888(raw, w, h, swap_bytes=False)
    rgb1 = _rgb565_to_rgb888(raw, w, h, swap_bytes=True)

    s0 = _score_natural(rgb0)
    s1 = _score_natural(rgb1)

    rgb = rgb1 if s1 < s0 else rgb0
    used_swap = s1 < s0

    img = Image.fromarray(rgb, mode="RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, subsampling=0, optimize=False)
    return buf.getvalue(), used_swap, (s0, s1)


def _save_latest(side: str, jpg_bytes: bytes):
    out = _save_jpg(side, jpg_bytes)
    if side == "L":
        _atomic_copy(out, LATEST_L)
    else:
        _atomic_copy(out, LATEST_R)
    return out


@app.get("/ping")
def ping():
    return "ok", 200


@app.post("/upload_raw/<side>")
def upload_raw(side):
    side = side.upper()
    if side not in ("L", "R"):
        abort(404)

    raw = request.get_data()
    if not raw:
        abort(400, "missing raw")

    w = request.headers.get("X-W")
    h = request.headers.get("X-H")
    if not w or not h:
        abort(400, "missing raw or X-W/X-H")
    w = int(w)
    h = int(h)

    if len(raw) != w * h * 2:
        abort(400, f"raw size mismatch: got={len(raw)} expect={w*h*2}")

    jpg, used_swap, scores = _raw565_to_jpeg_best(raw, w, h)
    saved = _save_latest(side, jpg)

    return (
        jsonify(
            {
                "ok": True,
                "mode": "raw565",
                "side": side,
                "w": w,
                "h": h,
                "raw_bytes": len(raw),
                "jpeg_bytes": len(jpg),
                "swap": used_swap,
                "score_no_swap": scores[0],
                "score_swap": scores[1],
                "saved": str(saved),
                "latest": f"/latest_{side}.jpg",
            }
        ),
        201,
    )


@app.post("/upload_jpeg/<side>")
def upload_jpeg(side):
    side = side.upper()
    if side not in ("L", "R"):
        abort(404)

    jpg = request.get_data()
    if not jpg:
        abort(400, "missing jpeg bytes")

    # 基本校验：必须能被 PIL 打开（防止你 K210 端发了“伪 jpeg”）
    try:
        im = Image.open(io.BytesIO(jpg))
        im.verify()
    except Exception as e:
        abort(400, f"invalid jpeg: {e}")

    saved = _save_latest(side, jpg)
    return (
        jsonify(
            {
                "ok": True,
                "mode": "jpeg",
                "side": side,
                "jpeg_bytes": len(jpg),
                "saved": str(saved),
                "latest": f"/latest_{side}.jpg",
            }
        ),
        201,
    )


@app.get("/latest_L.jpg")
def latest_l():
    if not LATEST_L.exists():
        abort(404)
    resp = send_file(
        str(LATEST_L), mimetype="image/jpeg", conditional=False, etag=False
    )
    return _set_nocache(resp)


@app.get("/latest_R.jpg")
def latest_r():
    if not LATEST_R.exists():
        abort(404)
    resp = send_file(
        str(LATEST_R), mimetype="image/jpeg", conditional=False, etag=False
    )
    return _set_nocache(resp)


@app.get("/")
def index():
    return Response(
        f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>MaixDuino Stereo (RAW/JPEG)</title>
  <style>
    body{{font-family:system-ui,Arial;margin:16px;}}
    .row{{display:flex;gap:12px;flex-wrap:wrap;}}
    .card{{border:1px solid #ddd;border-radius:10px;padding:10px;}}
    img{{max-width:48vw;height:auto;display:block;}}
    code{{background:#f6f6f6;padding:2px 6px;border-radius:6px;}}
    @media (max-width:900px){{ img{{max-width:95vw;}} }}
  </style>
</head>
<body>
  <h3>MaixDuino Stereo</h3>
  <div>Frames dir: <code>{FRAMES_DIR}</code></div>
  <div class="row" style="margin-top:12px;">
    <div class="card">
      <div>Left</div>
      <img id="imgL" src="/latest_L.jpg">
    </div>
    <div class="card">
      <div>Right</div>
      <img id="imgR" src="/latest_R.jpg">
    </div>
  </div>
  <script>
    function refresh(){{
      const t = Date.now();
      document.getElementById("imgL").src = "/latest_L.jpg?t=" + t;
      document.getElementById("imgR").src = "/latest_R.jpg?t=" + t;
    }}
    setInterval(refresh, 400);
  </script>
</body>
</html>
        """.strip(),
        mimetype="text/html",
    )


if __name__ == "__main__":
    # pip install flask pillow numpy
    app.run(host="0.0.0.0", port=5005, debug=False, threaded=True)
