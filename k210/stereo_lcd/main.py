import time
import sensor

try:
    import lcd
except Exception as e:
    lcd = None

import config


def _framesize_from_str(s):
    s = s.upper().strip()
    if s == "QQVGA":
        return sensor.QQVGA
    if s == "QVGA":
        return sensor.QVGA
    if s == "VGA":
        return sensor.VGA
    return sensor.QVGA


def _pixformat_from_str(s):
    s = s.upper().strip()
    if s in ("RGB565", "RGB"):
        return sensor.RGB565
    if s in ("GRAYSCALE", "GRAY"):
        return sensor.GRAYSCALE
    return sensor.RGB565


def init_lcd():
    if lcd is None:
        return
    try:
        lcd.init()
        lcd.clear()
        print("[LCD] init OK")
    except Exception as e:
        print("[LCD] init failed:", e)


def init_binocular():
    # Official binocular init pattern:
    # binocular_reset() then shutdown(False) configure, shutdown(True) configure, run(1)
    sensor.binocular_reset()  # init binocular camera (PWDN mux) :contentReference[oaicite:3]{index=3}

    # Configure "one side"
    sensor.shutdown(False)  # select one sensor
    sensor.set_pixformat(_pixformat_from_str(config.PIXFORMAT))
    sensor.set_framesize(_framesize_from_str(config.FRAME_SIZE))

    # Configure the other side
    sensor.shutdown(True)  # select the other sensor
    sensor.set_pixformat(_pixformat_from_str(config.PIXFORMAT))
    sensor.set_framesize(_framesize_from_str(config.FRAME_SIZE))

    sensor.run(1)

    # Warm-up
    for _ in range(5):
        sensor.shutdown(False)
        sensor.snapshot()
        sensor.shutdown(True)
        sensor.snapshot()
        time.sleep_ms(30)

    print("[CAM] binocular ready")


def capture_left():
    # Convention: shutdown(False) / shutdown(True) selects sensors.
    # We'll call False = Left, True = Right (if your board is opposite, just swap labels).
    sensor.shutdown(False)
    return sensor.snapshot()


def capture_right():
    sensor.shutdown(True)
    return sensor.snapshot()


def main():
    print("=== MaixPy Binocular Preview ===")
    if config.USE_LCD:
        init_lcd()
    init_binocular()

    while True:
        imgL = capture_left()
        if lcd is not None and config.USE_LCD:
            # show left
            lcd.display(imgL)
        print("[L] %dx%d" % (imgL.width(), imgL.height()))
        time.sleep_ms(config.SWITCH_MS)

        imgR = capture_right()
        if lcd is not None and config.USE_LCD:
            # show right
            lcd.display(imgR)
        print("[R] %dx%d" % (imgR.width(), imgR.height()))
        time.sleep_ms(config.SWITCH_MS)


if __name__ == "__main__":
    main()
