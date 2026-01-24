from flask import Flask, request, jsonify, send_file, abort
from pathlib import Path
from datetime import datetime
import shutil
import os

app = Flask(__name__)

FRAMES_DIR = Path("frames")
FRAMES_DIR.mkdir(parents=True, exist_ok=True)

LATEST_L = FRAMES_DIR / "latest_L.jpg"
LATEST_R = FRAMES_DIR / "latest_R.jpg"
LATEST_S = FRAMES_DIR / "latest.jpg"  # 你暂时不 stitch，就先让它等于 L


def _now_ts():
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _atomic_copy(src: Path, dst: Path):
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)  # atomic rename


def _save_one(side: str, jpg_bytes: bytes):
    if not jpg_bytes or len(jpg_bytes) < 100:  # 粗略防呆：JPEG 不可能这么小
        abort(400, f"empty/too-small jpeg for side={side}")

    ts = _now_ts()
    out = FRAMES_DIR / f"{side}_{ts}.jpg"
    out.write_bytes(jpg_bytes)

    if side == "L":
        _atomic_copy(out, LATEST_L)
        _atomic_copy(out, LATEST_S)  # 暂时 latest.jpg = L
    else:
        _atomic_copy(out, LATEST_R)

    return out, ts


@app.post("/upload/L")
def upload_l():
    # 支持 K210/ESP32 直接 POST JPEG bytes（不要 multipart）
    jpg = request.get_data()
    out, ts = _save_one("L", jpg)
    return jsonify(
        {
            "ok": True,
            "side": "L",
            "ts": ts,
            "saved": str(out),
            "latest": "/latest_L.jpg",
            "also": "/latest.jpg",
            "bytes": len(jpg),
        }
    )


@app.post("/upload/R")
def upload_r():
    jpg = request.get_data()
    out, ts = _save_one("R", jpg)
    return jsonify(
        {
            "ok": True,
            "side": "R",
            "ts": ts,
            "saved": str(out),
            "latest": "/latest_R.jpg",
            "bytes": len(jpg),
        }
    )


@app.get("/latest_L.jpg")
def latest_l():
    if not LATEST_L.exists():
        abort(404)
    resp = send_file(LATEST_L, mimetype="image/jpeg", conditional=False, etag=False)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@app.get("/latest_R.jpg")
def latest_r():
    if not LATEST_R.exists():
        abort(404)
    resp = send_file(LATEST_R, mimetype="image/jpeg", conditional=False, etag=False)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@app.get("/latest.jpg")
def latest_s():
    if not LATEST_S.exists():
        abort(404)
    resp = send_file(LATEST_S, mimetype="image/jpeg", conditional=False, etag=False)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@app.get("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    # 如果你想让局域网其它设备访问，把 host 改成 0.0.0.0
    app.run(host="0.0.0.0", port=5005, debug=False, threaded=True)
