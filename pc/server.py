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

FRAMES_DIR = Path("frames")
FRAMES_DIR.mkdir(parents=True, exist_ok=True)

LATEST_L = FRAMES_DIR / "latest_L.jpg"
LATEST_R = FRAMES_DIR / "latest_R.jpg"
LATEST_S = FRAMES_DIR / "latest.jpg"


def _now_ts():
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _atomic_write(dst: Path, data: bytes):
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dst)


def _atomic_copy(src: Path, dst: Path):
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def _set_nocache(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


def _save_bytes(prefix: str, payload: bytes) -> Path:
    out = FRAMES_DIR / f"{prefix}_{_now_ts()}.bin"
    out.write_bytes(payload)
    return out


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

    # now high byte first: [MSB, LSB]
    msb = a[..., 0].astype(np.uint16)
    lsb = a[..., 1].astype(np.uint16)
    v = (msb << 8) | lsb

    r = ((v >> 11) & 0x1F).astype(np.uint8)
    g = ((v >> 5) & 0x3F).astype(np.uint8)
    b = (v & 0x1F).astype(np.uint8)

    # expand to 8-bit
    r = (r << 3) | (r >> 2)
    g = (g << 2) | (g >> 4)
    b = (b << 3) | (b >> 2)

    rgb = np.stack([r, g, b], axis=-1)
    return rgb


def _score_natural(rgb: np.ndarray) -> float:
    """
    Heuristic: correct decode tends to be spatially smooth-ish.
    Wrong byte order often looks like noisy high-frequency patterns.
    We'll score by mean absolute gradient (lower is better).
    """
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
    elif side == "R":
        _atomic_copy(out, LATEST_R)
    else:
        _atomic_copy(out, LATEST_S)
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

    # expected headers
    w = request.headers.get("X-W")
    h = request.headers.get("X-H")
    if not w or not h:
        abort(400, "missing raw or X-W/X-H")
    w = int(w)
    h = int(h)

    if len(raw) != w * h * 2:
        abort(400, f"raw size mismatch: got={len(raw)} expect={w*h*2}")

    # decode -> jpeg (auto choose swap)
    jpg, used_swap, scores = _raw565_to_jpeg_best(raw, w, h)

    saved = _save_latest(side, jpg)

    return (
        jsonify(
            {
                "ok": True,
                "side": side,
                "w": w,
                "h": h,
                "bytes": len(raw),
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


@app.get("/latest_L.jpg")
def latest_l():
    if not LATEST_L.exists():
        abort(404)
    resp = send_file(LATEST_L, mimetype="image/jpeg", conditional=False, etag=False)
    return _set_nocache(resp)


@app.get("/latest_R.jpg")
def latest_r():
    if not LATEST_R.exists():
        abort(404)
    resp = send_file(LATEST_R, mimetype="image/jpeg", conditional=False, etag=False)
    return _set_nocache(resp)


@app.get("/")
def index():
    return Response(
        """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>MaixDuino Stereo RAW</title>
  <style>
    body{font-family:system-ui,Arial;margin:16px;}
    .row{display:flex;gap:12px;flex-wrap:wrap;}
    .card{border:1px solid #ddd;border-radius:10px;padding:10px;}
    img{max-width:48vw;height:auto;display:block;}
    @media (max-width:900px){ img{max-width:95vw;} }
  </style>
</head>
<body>
  <h3>MaixDuino Stereo (RAW RGB565 -> JPEG)</h3>
  <div class="row">
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
    function refresh(){
      const t = Date.now();
      const L = document.getElementById("imgL");
      const R = document.getElementById("imgR");
      L.src = "/latest_L.jpg?t=" + t;
      R.src = "/latest_R.jpg?t=" + t;
    }
    setInterval(refresh, 700);
  </script>
</body>
</html>
        """.strip(),
        mimetype="text/html",
    )


if __name__ == "__main__":
    # pip install flask pillow numpy
    app.run(host="0.0.0.0", port=5005, debug=False, threaded=True)
