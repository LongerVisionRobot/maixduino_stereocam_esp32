import time
import sensor
import image

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
    sensor.set_pixformat(_pixformat_from_str(config.PIXFORMAT))
    sensor.set_framesize(_framesize_from_str(config.FRAME_SIZE))
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
    from fpioa_manager import fm

    time.sleep_ms(1200)

    fm.register(25, fm.fpioa.GPIOHS10)
    fm.register(8, fm.fpioa.GPIOHS11)
    fm.register(9, fm.fpioa.GPIOHS12)
    fm.register(28, fm.fpioa.GPIOHS13)
    fm.register(26, fm.fpioa.GPIOHS14)
    fm.register(27, fm.fpioa.GPIOHS15)

    print("pinmap OK")

    ssid = getattr(config, "WIFI_SSID", "")
    pwd = getattr(config, "WIFI_PASS", "")
    if not ssid:
        print("[WIFI] SSID empty")
        return None

    nic = None
    last_err = None

    for _ in range(3):
        try:
            nic = network.ESP32_SPI(
                cs=fm.fpioa.GPIOHS10,
                rst=fm.fpioa.GPIOHS11,
                rdy=fm.fpioa.GPIOHS12,
                mosi=fm.fpioa.GPIOHS13,
                miso=fm.fpioa.GPIOHS14,
                sclk=fm.fpioa.GPIOHS15,
                spi=-1,
            )
            break
        except Exception as e:
            last_err = e
            print("[WIFI] ESP32_SPI create failed:", e)
            time.sleep_ms(900)

    if nic is None:
        print("[WIFI] ESP32_SPI unavailable:", last_err)
        return None

    print("ESP32_SPI object created")
    try:
        print("ESP32 FW:", nic.version())
    except Exception as e:
        print("[WIFI] version fail:", e)

    try:
        aps = nic.scan()
        print("APs:", aps)
    except Exception as e:
        print("[WIFI] scan fail:", e)

    try:
        nic.connect(ssid=ssid, key=pwd)
    except Exception as e:
        print("[WIFI] connect call fail:", e)
        return None

    t0 = time.ticks_ms()
    while not nic.isconnected():
        time.sleep_ms(200)
        if time.ticks_diff(time.ticks_ms(), t0) > 20000:
            print("[WIFI] connect timeout")
            return None

    try:
        print("IP:", nic.ifconfig())
    except Exception as e:
        print("[WIFI] ifconfig fail:", e)

    return nic


def _jpeg_bytes(img, quality):
    if hasattr(img, "compress"):
        return img.compress(quality=quality)
    if hasattr(img, "compressed"):
        return img.compressed(quality=quality)
    return img.compress()


def stitch_lr(imgL, imgR):
    w = imgL.width()
    h = imgL.height()
    out = image.Image(w * 2, h)
    out.draw_image(imgL, 0, 0)
    out.draw_image(imgR, w, 0)
    return out


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


def http_post_jpeg_socket(jpeg, frame_id=None):
    import usocket as socket

    url = getattr(config, "SERVER_URL", "")
    if not url:
        return False

    try:
        host, port, path = _parse_http_url(url)
    except Exception as e:
        print("[HTTP] bad SERVER_URL:", e)
        return False

    s = None
    try:
        addr = socket.getaddrinfo(host, port)[0][-1]
        s = socket.socket()
        s.settimeout(8)
        s.connect(addr)

        hdr = ""
        hdr += "POST %s HTTP/1.1\r\n" % path
        hdr += "Host: %s:%d\r\n" % (host, port)
        hdr += "Content-Type: image/jpeg\r\n"
        hdr += "Content-Length: %d\r\n" % len(jpeg)
        hdr += "Connection: close\r\n"
        if frame_id is not None and getattr(config, "SEND_FRAME_ID", True):
            hdr += "X-Frame-Id: %s\r\n" % str(frame_id)
        hdr += "\r\n"

        s.send(hdr.encode())
        s.send(jpeg)

        resp = s.recv(96)
        s.close()
        s = None

        if b" 200 " in resp or b" 201 " in resp:
            return True

        print("[HTTP] resp:", resp)
        return False

    except Exception as e:
        try:
            if s:
                s.close()
        except Exception:
            pass
        print("[HTTP] POST failed:", e)
        return False


def main():
    time.sleep_ms(350)
    print("=== MaixPy Stereo LCD + WiFi Stream (socket) ===")

    if getattr(config, "USE_LCD", True):
        init_lcd()

    try:
        init_binocular()
    except Exception as e:
        print("[CAM] init failed:", e)
        lcd_msg("CAM INIT ERR", 24)
        while True:
            time.sleep_ms(1000)

    time.sleep_ms(1000)

    nic = None
    if getattr(config, "WIFI_ENABLE", True):
        try:
            nic = wifi_connect()
        except Exception as e:
            print("[WIFI] fatal:", e)
            nic = None

        if nic is None:
            lcd_msg("WIFI FAIL", 24)
        else:
            lcd_msg("WIFI OK", 24)

    if nic is not None:
        try:
            host, port, _ = _parse_http_url(config.SERVER_URL)
            probe_url = "http://%s:%d/ping" % (host, port)
            resp = http_get_raw(probe_url)
            print("[PROBE] resp:", resp)
            lcd_msg("PING OK" if b"200" in resp else "PING BAD", 24)
        except Exception as e:
            print("[PROBE] failed:", e)
            lcd_msg("PING FAIL", 24)

    frame_id = 0
    last_send = time.ticks_ms()
    interval_ms = int(getattr(config, "STREAM_INTERVAL_MS", 600))
    q = int(getattr(config, "JPEG_QUALITY", 60))
    switch_ms = int(getattr(config, "SWITCH_MS", 120))

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

            if nic is not None and getattr(config, "WIFI_ENABLE", True):
                now = time.ticks_ms()
                if time.ticks_diff(now, last_send) >= interval_ms:
                    last_send = now

                    if getattr(config, "STITCH_LR", True):
                        img = stitch_lr(imgL, imgR)
                    else:
                        img = imgL

                    jpeg = _jpeg_bytes(img, q)

                    ok = http_post_jpeg_socket(jpeg, frame_id=frame_id)
                    frame_id += 1

                    print("[TX] frame=%d bytes=%d ok=%s" % (frame_id, len(jpeg), ok))
                    if lcd_ok():
                        lcd_msg("TX %d" % frame_id, 12)
                        if not ok:
                            lcd_msg("HTTP ERR", 24)

        except Exception as e:
            print("[LOOP] error:", e)
            if lcd_ok():
                lcd_msg("LOOP ERR", 24)
            time.sleep_ms(300)
            try:
                init_binocular(warmup_pairs=8)
                if lcd_ok():
                    lcd_msg("RECOVER OK", 24)
            except Exception:
                pass


if __name__ == "__main__":
    main()
