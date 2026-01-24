# k210/stereo_lcd_wifi/main.py

import time
import sensor
import image

try:
    import lcd
except Exception:
    lcd = None

import config


# -------------------------
# Helpers: config parsing
# -------------------------
def _framesize_from_str(s):
    s = str(s).upper().strip()
    if s == "QQVGA":
        return sensor.QQVGA
    if s == "QVGA":
        return sensor.QVGA
    if s == "VGA":
        return sensor.VGA
    return sensor.QVGA


def _pixformat_from_str(s):
    s = str(s).upper().strip()
    if s in ("RGB565", "RGB"):
        return sensor.RGB565
    if s in ("GRAYSCALE", "GRAY"):
        return sensor.GRAYSCALE
    return sensor.RGB565


# -------------------------
# LCD (safe)
# -------------------------
def lcd_ok():
    return (lcd is not None) and getattr(config, "USE_LCD", True)


def lcd_msg(msg, y=0):
    if not lcd_ok():
        return
    try:
        fg = getattr(lcd, "WHITE", 0xFFFF)
        bg = getattr(lcd, "BLACK", 0x0000)
        lcd.draw_string(0, y, msg, fg, bg)
    except Exception:
        pass


def init_lcd():
    if lcd is None or not getattr(config, "USE_LCD", True):
        return False
    time.sleep_ms(150)
    for _ in range(3):
        try:
            try:
                lcd.deinit()
                time.sleep_ms(50)
            except Exception:
                pass
            lcd.init()
            lcd_msg("LCD OK", 0)
            return True
        except Exception:
            time.sleep_ms(200)
    return False


# -------------------------
# Binocular camera init
# -------------------------
def _config_one_side():
    sensor.set_pixformat(_pixformat_from_str(config.PIXFORMAT))
    sensor.set_framesize(_framesize_from_str(config.FRAME_SIZE))
    # optional tuning
    try:
        sensor.set_auto_gain(True)
    except Exception:
        pass
    try:
        sensor.set_auto_exposure(True)
    except Exception:
        pass
    try:
        sensor.set_auto_whitebal(True)
    except Exception:
        pass
    time.sleep_ms(30)


def init_binocular(warmup_pairs=15):
    try:
        sensor.reset()
        time.sleep_ms(50)
    except Exception:
        pass

    sensor.binocular_reset()
    time.sleep_ms(80)

    sensor.shutdown(False)
    _config_one_side()

    sensor.shutdown(True)
    _config_one_side()

    sensor.run(1)
    time.sleep_ms(50)

    for _ in range(warmup_pairs):
        try:
            sensor.shutdown(False)
            sensor.snapshot()
            sensor.shutdown(True)
            sensor.snapshot()
        except Exception:
            pass
        time.sleep_ms(25)

    lcd_msg("CAM OK", 12)
    print("[CAM] binocular ready")


def capture_left():
    sensor.shutdown(False)
    return sensor.snapshot()


def capture_right():
    sensor.shutdown(True)
    return sensor.snapshot()


# -------------------------
# WiFi bring-up (ESP32)
# -------------------------
def wifi_connect():
    """
    Tries common MaixPy WiFi APIs.
    Returns an object (nic) if available; otherwise returns None.
    """
    if not getattr(config, "WIFI_ENABLE", True):
        return None

    ssid = getattr(config, "WIFI_SSID", "")
    pwd = getattr(config, "WIFI_PASS", "")
    if not ssid:
        print("[WIFI] SSID empty, skip.")
        return None

    # Variant A: network.ESP32_SPI() style
    try:
        import network

        pins = getattr(config, "ESP32_SPI_PINS", {})
        try:
            nic = network.ESP32_SPI(
                cs=pins.get("cs", 10),
                rst=pins.get("rst", 11),
                rdy=pins.get("rdy", 12),
                mosi=pins.get("mosi", 13),
                miso=pins.get("miso", 14),
                sclk=pins.get("sclk", 15),
            )
        except Exception:
            # some builds require no args
            nic = network.ESP32_SPI()

        nic.active(True)
        print("[WIFI] connecting (network.ESP32_SPI)...")
        nic.connect(ssid, pwd)

        t0 = time.ticks_ms()
        while not nic.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > 15000:
                raise Exception("connect timeout")
            time.sleep_ms(200)

        print("[WIFI] connected:", nic.ifconfig())
        return nic
    except Exception as e:
        print("[WIFI] network.ESP32_SPI failed:", e)

    # Variant B: maix.ESP32_Network style
    try:
        import maix

        print("[WIFI] connecting (maix.ESP32_Network)...")
        nic = maix.ESP32_Network()
        nic.connect(ssid, pwd)
        t0 = time.ticks_ms()
        while not nic.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > 15000:
                raise Exception("connect timeout")
            time.sleep_ms(200)
        print("[WIFI] connected")
        return nic
    except Exception as e:
        print("[WIFI] maix.ESP32_Network failed:", e)

    print("[WIFI] no supported WiFi API in this MaixPy build.")
    return None


# -------------------------
# HTTP upload
# -------------------------
def _jpeg_bytes(img, quality):
    """
    MaixPy image JPEG API differs across versions:
    - some: img.compress(quality=Q) -> bytes
    - some: img.compressed(quality=Q) -> bytes
    """
    if hasattr(img, "compress"):
        return img.compress(quality=quality)
    if hasattr(img, "compressed"):
        return img.compressed(quality=quality)
    # fallback: try default
    return img.compress()


def stitch_lr(imgL, imgR):
    """
    Create one side-by-side image: [Left | Right]
    """
    w = imgL.width()
    h = imgL.height()
    out = image.Image(w * 2, h)
    out.draw_image(imgL, 0, 0)
    out.draw_image(imgR, w, 0)
    return out


def http_post_jpeg(jpeg, frame_id=None):
    url = getattr(config, "SERVER_URL", "")
    if not url:
        return False

    try:
        import urequests as requests
    except Exception:
        try:
            import requests
        except Exception as e:
            print("[HTTP] no requests/urequests:", e)
            return False

    headers = {
        "Content-Type": "image/jpeg",
    }
    if frame_id is not None and getattr(config, "SEND_FRAME_ID", True):
        headers["X-Frame-Id"] = str(frame_id)

    try:
        r = requests.post(url, data=jpeg, headers=headers)
        try:
            r.close()
        except Exception:
            pass
        return True
    except Exception as e:
        print("[HTTP] POST failed:", e)
        return False


def main():
    time.sleep_ms(300)
    print("=== MaixPy Stereo LCD + WiFi Stream ===")

    if getattr(config, "USE_LCD", True):
        init_lcd()

    # Camera init
    try:
        init_binocular()
    except Exception as e:
        print("[CAM] init failed:", e)
        lcd_msg("CAM INIT ERR", 24)
        while True:
            time.sleep_ms(1000)

    # WiFi init (optional)
    nic = None
    if getattr(config, "WIFI_ENABLE", True):
        nic = wifi_connect()
        if nic is None:
            lcd_msg("WIFI FAIL", 24)
        else:
            lcd_msg("WIFI OK", 24)

    frame_id = 0
    last_send = time.ticks_ms()

    while True:
        try:
            imgL = capture_left()
            if lcd_ok():
                lcd.display(imgL)
                lcd_msg("L", 0)
            time.sleep_ms(int(getattr(config, "SWITCH_MS", 120)))

            imgR = capture_right()
            if lcd_ok():
                lcd.display(imgR)
                lcd_msg("R", 0)
            time.sleep_ms(int(getattr(config, "SWITCH_MS", 120)))

            # Stream periodically
            if nic is not None and getattr(config, "WIFI_ENABLE", True):
                now = time.ticks_ms()
                if time.ticks_diff(now, last_send) >= int(
                    getattr(config, "STREAM_INTERVAL_MS", 150)
                ):
                    last_send = now

                    if getattr(config, "STITCH_LR", True):
                        img = stitch_lr(imgL, imgR)
                    else:
                        # if you want: send only left or alternate
                        img = imgL

                    jpeg = _jpeg_bytes(img, int(getattr(config, "JPEG_QUALITY", 70)))
                    ok = http_post_jpeg(jpeg, frame_id=frame_id)
                    frame_id += 1

                    if lcd_ok():
                        lcd_msg("TX %d" % (frame_id), 12)
                        if not ok:
                            lcd_msg("HTTP ERR", 24)

        except Exception as e:
            print("[LOOP] error:", e)
            if lcd_ok():
                lcd_msg("LOOP ERR", 24)
            time.sleep_ms(200)
            # try recover camera
            try:
                init_binocular(warmup_pairs=8)
                if lcd_ok():
                    lcd_msg("RECOVER OK", 24)
            except Exception:
                pass


if __name__ == "__main__":
    main()
