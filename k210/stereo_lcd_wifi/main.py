# k210/stereo_lcd_wifi/main.py
import time
import sensor
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
    return sensor.QVGA


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
        time.sleep_ms(20)

    print("[CAM] binocular ready")
    lcd_msg("CAM OK", 12)


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
    spi_id = int(spi_cfg.get("spi", -1))
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

    # 兼容有人写 GPIOHS10..15 的情况
    vals = [int(gpiohs.get(k, 0)) for k in required]
    if min(vals) >= 8:
        gpiohs = {k: int(gpiohs[k]) - 10 for k in required}
        print("[WIFI] normalize gpiohs 10.. ->", gpiohs)

    # 关键：用 GPIOHS 常量（GPIOHS0 + idx）
    def gh(idx):
        return fm.fpioa.GPIOHS0 + int(idx)

    # 先把 FPIOA -> GPIOHS FUNC 绑定好
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
        return None

    def do_connect(nic):
        try:
            try:
                print("[WIFI] ESP32 FW:", nic.version())
            except Exception:
                pass

            nic.connect(ssid=ssid, key=pwd)
            t0 = time.ticks_ms()
            while not nic.isconnected():
                time.sleep_ms(200)
                if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
                    print("[WIFI] connect timeout")
                    return None
            print("[WIFI] IP:", nic.ifconfig())
            return nic
        except Exception as e:
            print("[WIFI] connect phase failed:", e)
            return None

    # 只走这条（你之前能跑通的那条）
    try:
        print("[WIFI] try ESP32_SPI with GPIOHS CONSTANTS, spi=", spi_id)
        nic = network.ESP32_SPI(
            cs=gh(gpiohs["cs"]),
            rst=gh(gpiohs["rst"]),
            rdy=gh(gpiohs["rdy"]),
            mosi=gh(gpiohs["mosi"]),
            miso=gh(gpiohs["miso"]),
            sclk=gh(gpiohs["sclk"]),
            spi=1,
        )
        print("[WIFI] ESP32_SPI created (GPIOHS CONST mode)")
        return do_connect(nic)
    except Exception as e:
        print("[WIFI] CONST mode failed:", e)
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


def _to_bytes_maybe(obj):
    # 关键：你的固件 img.compress() 返回 Image，需要再转 bytes
    if obj is None:
        return None
    if isinstance(obj, (bytes, bytearray)):
        return obj
    if hasattr(obj, "to_bytes"):
        try:
            b = obj.to_bytes()
            if isinstance(b, (bytes, bytearray)):
                return b
        except Exception:
            pass
    if hasattr(obj, "bytearray"):
        try:
            b = obj.bytearray()
            if isinstance(b, (bytes, bytearray)):
                return b
        except Exception:
            pass
    return None


def _jpeg_bytes(img, quality):
    # 路线：img.compress -> (Image or bytes) -> bytes
    try:
        j = img.compress(quality=quality)
        b = _to_bytes_maybe(j)
        if b and len(b) > 200:
            return b
    except Exception as e:
        print("[JPEG] compress fail:", e)

    # 兼容另一些固件：img.compressed
    try:
        j = img.compressed(quality=quality)
        b = _to_bytes_maybe(j)
        if b and len(b) > 200:
            return b
    except Exception as e:
        print("[JPEG] compressed fail:", e)

    raise Exception("JPEG encode returned non-bytes Image (cannot extract bytes)")


def http_post_jpeg(host, port, path, jpeg_bytes, frame_id=None, timeout_s=10, retry=1):
    import usocket as socket

    def _once():
        addr = socket.getaddrinfo(host, port)[0][-1]
        s = socket.socket()
        s.settimeout(timeout_s)
        s.connect(addr)

        hdr = ""
        hdr += "POST %s HTTP/1.1\r\n" % path
        hdr += "Host: %s:%d\r\n" % (host, port)
        hdr += "Content-Type: image/jpeg\r\n"
        hdr += "Content-Length: %d\r\n" % len(jpeg_bytes)
        hdr += "Connection: close\r\n"
        if frame_id is not None and getattr(config, "SEND_FRAME_ID", True):
            hdr += "X-Frame-Id: %s\r\n" % str(frame_id)
        hdr += "\r\n"

        s.send(hdr.encode())
        s.send(jpeg_bytes)
        resp = s.recv(96)
        s.close()
        return (b" 201 " in resp) or (b" 200 " in resp)

    try:
        if _once():
            return True
    except Exception as e:
        last = e
    else:
        last = Exception("HTTP not 200/201")

    for _ in range(int(retry)):
        time.sleep_ms(60)
        try:
            if _once():
                return True
        except Exception as e:
            last = e

    raise last


def main():
    time.sleep_ms(350)
    print("=== MaixPy Stereo LCD + WiFi Stream (JPEG QVGA) ===")

    if getattr(config, "USE_LCD", True):
        init_lcd()

    init_binocular()

    nic = None
    if getattr(config, "WIFI_ENABLE", True):
        nic = wifi_connect()
        lcd_msg("WIFI OK" if nic else "WIFI FAIL", 24)

    host = port = base_path = None
    if nic:
        host, port, base_path = _parse_http_url(
            getattr(config, "SERVER_URL", "").strip()
        )
        if base_path == "/" or base_path == "":
            base_path = "/upload"
        if base_path.endswith("/"):
            base_path = base_path[:-1]

        try:
            resp = http_get_raw("http://%s:%d/ping" % (host, port))
            print("[PROBE] resp:", resp)
        except Exception as e:
            print("[PROBE] failed:", e)

    interval_ms = int(getattr(config, "STREAM_INTERVAL_MS", 300))
    switch_ms = int(getattr(config, "SWITCH_MS", 40))
    q = int(getattr(config, "JPEG_QUALITY", 60))
    retry = int(getattr(config, "HTTP_RETRY", 1))
    timeout_s = int(getattr(config, "SOCKET_TIMEOUT", 10))

    frame_id = 0
    last_send = time.ticks_ms()

    while True:
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

        if not nic or not host:
            continue

        now = time.ticks_ms()
        if time.ticks_diff(now, last_send) < interval_ms:
            continue
        last_send = now

        okL = okR = False
        bytesL = bytesR = -1

        try:
            gc.collect()
            jpegL = _jpeg_bytes(imgL, q)
            bytesL = len(jpegL)
            okL = http_post_jpeg(
                host,
                port,
                base_path + "/L",
                jpegL,
                frame_id="%dL" % frame_id,
                timeout_s=timeout_s,
                retry=retry,
            )
        except Exception as e:
            print("[HTTP] L failed:", e)

        try:
            gc.collect()
            jpegR = _jpeg_bytes(imgR, q)
            bytesR = len(jpegR)
            okR = http_post_jpeg(
                host,
                port,
                base_path + "/R",
                jpegR,
                frame_id="%dR" % frame_id,
                timeout_s=timeout_s,
                retry=retry,
            )
        except Exception as e:
            print("[HTTP] R failed:", e)

        frame_id += 1
        print(
            "[TX] frame=%d okL=%s okR=%s bytesL=%d bytesR=%d"
            % (frame_id, okL, okR, bytesL, bytesR)
        )


if __name__ == "__main__":
    main()
