import time
import sensor

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
# LCD utilities (safe)
# -------------------------
def lcd_ok():
    return (lcd is not None) and getattr(config, "USE_LCD", True)


def lcd_msg(msg, y=0):
    """
    Best-effort text overlay. We intentionally do NOT call lcd.clear()
    because on some MaixPy LCD drivers, clear() can disturb display() refresh.
    """
    if not lcd_ok():
        return
    try:
        # Some firmwares define colors as lcd.WHITE etc. If missing, just try.
        fg = getattr(lcd, "WHITE", 0xFFFF)
        bg = getattr(lcd, "BLACK", 0x0000)
        lcd.draw_string(0, y, msg, fg, bg)
    except Exception:
        pass


def init_lcd():
    if lcd is None or not getattr(config, "USE_LCD", True):
        return False

    # Give panel time to power up
    time.sleep_ms(150)

    for i in range(3):
        try:
            # deinit may not exist; ignore if absent
            try:
                lcd.deinit()
                time.sleep_ms(50)
            except Exception:
                pass

            lcd.init()
            lcd_msg("LCD OK", y=0)
            return True
        except Exception as e:
            # Don't rely on LCD here; print for serial log
            print("[LCD] init failed (%d): %s" % (i, e))
            time.sleep_ms(200)

    return False


# -------------------------
# Camera utilities (binocular)
# -------------------------
def _config_one_side():
    sensor.set_pixformat(_pixformat_from_str(config.PIXFORMAT))
    sensor.set_framesize(_framesize_from_str(config.FRAME_SIZE))

    # These may not exist on all builds; ignore safely
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


def init_binocular(warmup_pairs=20):
    """
    Robust binocular init for MaixDuino / GC0328 binocular.
    """
    # Some firmwares benefit from a reset before binocular_reset
    try:
        sensor.reset()
        time.sleep_ms(50)
    except Exception:
        pass

    # Official binocular init pattern
    sensor.binocular_reset()
    time.sleep_ms(80)

    # Configure left
    sensor.shutdown(False)
    _config_one_side()

    # Configure right
    sensor.shutdown(True)
    _config_one_side()

    # Start sensors
    sensor.run(1)
    time.sleep_ms(50)

    # Warm-up frames (both sides)
    for _ in range(warmup_pairs):
        try:
            sensor.shutdown(False)
            sensor.snapshot()
            sensor.shutdown(True)
            sensor.snapshot()
        except Exception:
            # ignore warmup hiccups
            pass
        time.sleep_ms(25)

    print("[CAM] binocular ready")
    lcd_msg("CAM OK", y=12)
    return True


def capture_left():
    sensor.shutdown(False)
    return sensor.snapshot()


def capture_right():
    sensor.shutdown(True)
    return sensor.snapshot()


# -------------------------
# Main loop
# -------------------------
def main():
    # power-up settle (important after kflash reboot)
    time.sleep_ms(300)

    print("=== MaixPy Binocular Preview (stable) ===")

    if getattr(config, "USE_LCD", True):
        init_lcd()

    # Init camera with visible failure message
    try:
        init_binocular()
    except Exception as e:
        print("[CAM] init failed:", e)
        lcd_msg("CAM INIT ERR", y=24)
        # stop here so you can see the error text on LCD
        while True:
            time.sleep_ms(1000)

    # Simple heartbeat so you know loop is alive even if display freezes
    beat = 0

    while True:
        try:
            # Left
            imgL = capture_left()
            if lcd_ok():
                lcd.display(imgL)
                lcd_msg("L %d" % beat, y=0)
            print("[L] %dx%d" % (imgL.width(), imgL.height()))
            time.sleep_ms(int(getattr(config, "SWITCH_MS", 200)))

            # Right
            imgR = capture_right()
            if lcd_ok():
                lcd.display(imgR)
                lcd_msg("R %d" % beat, y=0)
            print("[R] %dx%d" % (imgR.width(), imgR.height()))
            time.sleep_ms(int(getattr(config, "SWITCH_MS", 200)))

            beat = (beat + 1) % 10000

        except Exception as e:
            print("[LOOP] error:", e)
            if lcd_ok():
                lcd_msg("LOOP ERR", y=24)

            # Try to recover camera
            time.sleep_ms(200)
            try:
                init_binocular(warmup_pairs=10)
                if lcd_ok():
                    lcd_msg("RECOVER OK", y=24)
            except Exception as e2:
                print("[RECOVER] failed:", e2)
                if lcd_ok():
                    lcd_msg("RECOVER FAIL", y=24)
                # wait a bit and retry again
                time.sleep_ms(800)


if __name__ == "__main__":
    main()
