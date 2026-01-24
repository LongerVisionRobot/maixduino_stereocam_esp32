# pc/server.py
from flask import Flask, request, jsonify
from datetime import datetime
from pathlib import Path
from PIL import Image
import io

app = Flask(__name__)

OUT_DIR = Path("./frames")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def split_lr(img: Image.Image):
    w, h = img.size
    # expected stitched: [L|R]
    if w % 2 != 0:
        # if odd width, just floor
        mid = w // 2
    else:
        mid = w // 2
    left = img.crop((0, 0, mid, h))
    right = img.crop((mid, 0, w, h))
    return left, right


@app.post("/upload")
def upload():
    raw = request.get_data()
    if not raw:
        return jsonify({"ok": False, "err": "empty body"}), 400

    frame_id = request.headers.get("X-Frame-Id", "")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    base = f"{ts}" + (f"_f{frame_id}" if frame_id else "")

    stitched_path = OUT_DIR / f"{base}_stitched.jpg"
    left_path = OUT_DIR / f"{base}_L.jpg"
    right_path = OUT_DIR / f"{base}_R.jpg"

    stitched_path.write_bytes(raw)

    # Split
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        L, R = split_lr(img)
        L.save(left_path, quality=90)
        R.save(right_path, quality=90)
    except Exception as e:
        # still ok if saving stitched succeeded
        return (
            jsonify({"ok": True, "saved": str(stitched_path), "split_err": str(e)}),
            200,
        )

    return (
        jsonify(
            {
                "ok": True,
                "stitched": str(stitched_path),
                "left": str(left_path),
                "right": str(right_path),
            }
        ),
        200,
    )


if __name__ == "__main__":
    # Listen on LAN
    app.run(host="0.0.0.0", port=5005, debug=False)
