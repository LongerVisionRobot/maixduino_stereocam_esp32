# k210/stereo_lcd_wifi/main.py
import time
import sensor
import image
import gc

try:
    import lcd
except Exception:
    lcd = None

import config


def _framesize_from_str(s):
    s = str(s).upper().strip()
    if s == "QQVGA":
        return sensor.QQVGA
    if s == "QVGA":
        return sensor.QVGA
    if s == "VGA":
        return sensor.VGA
    return sensor.QQVGA


def _pixformat_from_str(s):
    s = str(s).upper().strip()
    if s in ("RGB565", "RGB"):
        return sensor.RGB565
    if s in ("GRAYSCALE", "GRAY"):
        return sensor.GRAYSCALE
    return sensor.RGB565


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
                time.sleep_ms(60)
            except Exception:
                pass
            lcd.init()
            lcd_msg("LCD OK", 0)
            return True
        except Exception:
            time.sleep_ms(250)
    return False


def _config_one_side():
    sensor.set_pixformat(_pixformat_from_str(getattr(config, "PIXFORMAT", "RGB565")))
    sensor.set_framesize(_framesize_from_str(getattr(config, "FRAME_SIZE", "QVGA")))
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


def init_binocular(warmup_pairs=12):
    try:
        sensor.reset()
        time.sleep_ms(80)
    except Exception:
        pass

    sensor.binocular_reset()
    time.sleep_ms(120)

    sensor.shutdown(False)
    _config_one_side()

    sensor.shutdown(True)
    _config_one_side()

    sensor.run(1)
    time.sleep_ms(80)

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


def wifi_connect():
    import network
    import time
    from fpioa_manager import fm

    time.sleep_ms(1200)

    spi_cfg = getattr(config, "ESP32_SPI", {}) or {}
    fpioa = spi_cfg.get("fpioa", spi_cfg)
    gpiohs = spi_cfg.get(
        "gpiohs", {"cs": 0, "rst": 1, "rdy": 2, "mosi": 3, "miso": 4, "sclk": 5}
    )
    spi_id = spi_cfg.get("spi", -1)
    timeout_ms = int(spi_cfg.get("timeout_ms", 20000))

    required = ("cs", "rst", "rdy", "mosi", "miso", "sclk")
    for k in required:
        if k not in fpioa:
            print("[WIFI] missing fpioa pin:", k, "ESP32_SPI =", spi_cfg)
            return None

    ssid = getattr(config, "WIFI_SSID", "")
    pwd = getattr(config, "WIFI_PASS", "")
    if not ssid:
        print("[WIFI] SSID empty")
        return None

    vals = [gpiohs.get(k, 0) for k in required]
    if min(vals) >= 8:
        gpiohs = {k: gpiohs[k] - 10 for k in required}
        print("[WIFI] normalize gpiohs 10.. ->", gpiohs)

    def gh(idx):
        return fm.fpioa.GPIOHS0 + int(idx)

    try:
        fm.register(fpioa["cs"], gh(gpiohs["cs"]))
        fm.register(fpioa["rst"], gh(gpiohs["rst"]))
        fm.register(fpioa["rdy"], gh(gpiohs["rdy"]))
        fm.register(fpioa["mosi"], gh(gpiohs["mosi"]))
        fm.register(fpioa["miso"], gh(gpiohs["miso"]))
        fm.register(fpioa["sclk"], gh(gpiohs["sclk"]))
        print("[WIFI] pinmap OK (FPIOA->GPIOHS CONST)", gpiohs)
    except Exception as e:
        print("[WIFI] fm.register failed:", e)

    def do_connect(nic):
        try:
            try:
                print("[WIFI] ESP32 FW:", nic.version())
            except Exception:
                pass

            nic.connect(ssid=ssid, key=pwd)
            t0 = time.ticks_ms()
            while not nic.isconnected():
                time.sleep_ms(250)
                if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                    print("[WIFI] connect timeout")
                    return None
            print("[WIFI] IP:", nic.ifconfig())
            return nic
        except Exception as e:
            print("[WIFI] connect phase failed:", e)
            return None

    try:
        print("[WIFI] try ESP32_SPI with GPIOHS CONSTANTS, spi=", spi_id)
        nic = network.ESP32_SPI(
            cs=gh(gpiohs["cs"]),
            rst=gh(gpiohs["rst"]),
            rdy=gh(gpiohs["rdy"]),
            mosi=gh(gpiohs["mosi"]),
            miso=gh(gpiohs["miso"]),
            sclk=gh(gpiohs["sclk"]),
            spi=spi_id,
        )
        print("[WIFI] ESP32_SPI created (GPIOHS CONST mode)")
        nic2 = do_connect(nic)
        if nic2:
            return nic2
    except Exception as e:
        print("[WIFI] CONST mode failed:", e)

    print("[WIFI] ESP32_SPI unavailable")
    return None


def _parse_http_url(url):
    if not url.startswith("http://"):
        raise ValueError("Only http:// is supported")
    tmp = url[len("http://") :]
    if "/" in tmp:
        hostport, path = tmp.split("/", 1)
        path = "/" + path
    else:
        hostport, path = tmp, "/"
    if ":" in hostport:
        host, port = hostport.split(":", 1)
        port = int(port)
    else:
        host, port = hostport, 80
    return host, port, path


def http_get_raw(url, timeout_s=4):
    import usocket as socket

    host, port, path = _parse_http_url(url)
    addr = socket.getaddrinfo(host, port)[0][-1]
    s = socket.socket()
    s.settimeout(timeout_s)
    s.connect(addr)
    req = "GET %s HTTP/1.1\r\nHost: %s:%d\r\nConnection: close\r\n\r\n" % (
        path,
        host,
        port,
    )
    s.send(req.encode())
    data = s.recv(64)
    s.close()
    return data


def _server_base():
    """
    SERVER_URL examples:
      http://192.168.1.101:5005
      http://192.168.1.101:5005/upload   (we will still use /upload_raw)
    """
    url = (getattr(config, "SERVER_URL", "") or "").strip()
    if not url:
        return None, None
    host, port, _ = _parse_http_url(url)
    return host, port


def _img_to_rgb565_bytes(img):
    # Most MaixPy builds for K210 support img.to_bytes() returning RGB565 for RGB565 images.
    if hasattr(img, "to_bytes"):
        b = img.to_bytes()
        if isinstance(b, (bytes, bytearray)):
            return b
    if hasattr(img, "bytearray"):
        b = img.bytearray()
        if isinstance(b, (bytes, bytearray)):
            return b
    raise Exception("Image->RAW bytes not supported (need img.to_bytes/bytearray)")


def _sendall_chunked(sock, data, chunk=1024):
    # chunked send to avoid EIO on ESP32_SPI soft stack
    mv = memoryview(data)
    n = len(mv)
    off = 0
    while off < n:
        end = off + chunk
        if end > n:
            end = n
        sock.send(mv[off:end])
        off = end


def http_post_raw_rgb565(host, port, path, raw_bytes, w, h, frame_id=None):
    import usocket as socket

    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise Exception("raw must be bytes/bytearray")
    addr = socket.getaddrinfo(host, port)[0][-1]
    s = socket.socket()
    s.settimeout(10)

    try:
        s.connect(addr)

        hdr = ""
        hdr += "POST %s HTTP/1.1\r\n" % path
        hdr += "Host: %s:%d\r\n" % (host, port)
        hdr += "Content-Type: application/octet-stream\r\n"
        hdr += "Content-Length: %d\r\n" % len(raw_bytes)
        hdr += "Connection: close\r\n"
        hdr += "X-Pix: RGB565\r\n"
        hdr += "X-W: %d\r\n" % int(w)
        hdr += "X-H: %d\r\n" % int(h)
        if frame_id is not None and getattr(config, "SEND_FRAME_ID", True):
            hdr += "X-Frame-Id: %s\r\n" % str(frame_id)
        hdr += "\r\n"

        s.send(hdr.encode())
        _sendall_chunked(s, raw_bytes, chunk=int(getattr(config, "SEND_CHUNK", 1024)))

        resp = s.recv(96)
        ok = (b" 200 " in resp) or (b" 201 " in resp)
        s.close()
        return ok, resp
    except Exception as e:
        try:
            s.close()
        except Exception:
            pass
        raise e


def main():
    time.sleep_ms(350)
    print("=== MaixPy Stereo LCD + WiFi Stream (RAW RGB565) ===")

    if getattr(config, "USE_LCD", True):
        init_lcd()

    try:
        init_binocular()
    except Exception as e:
        print("[CAM] init failed:", e)
        lcd_msg("CAM INIT ERR", 24)
        while True:
            time.sleep_ms(1000)

    time.sleep_ms(800)

    nic = None
    if getattr(config, "WIFI_ENABLE", True):
        nic = wifi_connect()
        if nic is None:
            lcd_msg("WIFI FAIL", 24)
        else:
            lcd_msg("WIFI OK", 24)

    host = port = None
    if nic is not None:
        try:
            host, port = _server_base()
            probe_url = "http://%s:%d/ping" % (host, port)
            resp = http_get_raw(probe_url)
            print("[PROBE] resp:", resp)
            lcd_msg("PING OK" if b"200" in resp else "PING BAD", 24)
        except Exception as e:
            print("[PROBE] failed:", e)
            lcd_msg("PING FAIL", 24)
            host = port = None

    frame_id = 0
    last_send = time.ticks_ms()

    # IMPORTANT: slow down by default (soft spi + big payload)
    interval_ms = int(getattr(config, "STREAM_INTERVAL_MS", 1800))
    switch_ms = int(getattr(config, "SWITCH_MS", 120))

    # EIO backoff / reconnect
    consecutive_fail = 0
    fail_reconnect_n = int(getattr(config, "FAIL_RECONNECT_N", 4))

    while True:
        try:
            imgL = capture_left()
            if lcd_ok():
                lcd.display(imgL)
                lcd_msg("L", 0)
            time.sleep_ms(switch_ms)

            imgR = capture_right()
            if lcd_ok():
                lcd.display(imgR)
                lcd_msg("R", 0)
            time.sleep_ms(switch_ms)

            if nic is None or host is None:
                time.sleep_ms(300)
                continue

            now = time.ticks_ms()
            if time.ticks_diff(now, last_send) < interval_ms:
                continue
            last_send = now

            gc.collect()

            okL = okR = False
            bytesL = bytesR = -1

            # LEFT
            try:
                rawL = _img_to_rgb565_bytes(imgL)
                bytesL = len(rawL)
                okL, _ = http_post_raw_rgb565(
                    host,
                    port,
                    "/upload_raw/L",
                    rawL,
                    imgL.width(),
                    imgL.height(),
                    frame_id=str(frame_id) + "L",
                )
            except Exception as e:
                print("[HTTP] L failed:", e)
                okL = False

            gc.collect()
            time.sleep_ms(60)

            # RIGHT
            try:
                rawR = _img_to_rgb565_bytes(imgR)
                bytesR = len(rawR)
                okR, _ = http_post_raw_rgb565(
                    host,
                    port,
                    "/upload_raw/R",
                    rawR,
                    imgR.width(),
                    imgR.height(),
                    frame_id=str(frame_id) + "R",
                )
            except Exception as e:
                print("[HTTP] R failed:", e)
                okR = False

            frame_id += 1
            print(
                "[TX] frame=%d okL=%s okR=%s bytesL=%d bytesR=%d"
                % (frame_id, okL, okR, bytesL, bytesR)
            )

            if lcd_ok():
                lcd_msg("TX %d" % frame_id, 12)
                if (not okL) or (not okR):
                    lcd_msg("HTTP ERR", 24)

            # handle failures
            if okL and okR:
                consecutive_fail = 0
            else:
                consecutive_fail += 1
                # backoff to avoid hammering ESP32 stack
                time.sleep_ms(800 + 300 * consecutive_fail)

                if consecutive_fail >= fail_reconnect_n:
                    print("[WIFI] too many fails -> reconnect wifi")
                    consecutive_fail = 0
                    try:
                        nic = wifi_connect()
                        if nic is not None:
                            host, port = _server_base()
                            probe_url = "http://%s:%d/ping" % (host, port)
                            resp = http_get_raw(probe_url)
                            print("[PROBE] resp:", resp)
                    except Exception as e:
                        print("[WIFI] reconnect failed:", e)
                        nic = None
                        host = port = None

        except Exception as e:
            print("[LOOP] error:", e)
            if lcd_ok():
                lcd_msg("LOOP ERR", 24)
            time.sleep_ms(400)
            try:
                init_binocular(warmup_pairs=6)
                if lcd_ok():
                    lcd_msg("RECOVER OK", 24)
            except Exception:
                pass


if __name__ == "__main__":
    main()
