# k210/stereo_lcd_wifi/config.py

# --- Camera/LCD ---
USE_LCD = True
FRAME_SIZE = "QVGA"  # "QQVGA" / "QVGA" / "VGA"
PIXFORMAT = "RGB565"  # "RGB565" or "GRAYSCALE"
SWITCH_MS = 200  # preview switch interval (LCD)

# --- WiFi stream ---
WIFI_ENABLE = True
WIFI_SSID = "MYSSID"
WIFI_PASS = "MYPASSWD"

# HTTP server endpoint on your PC (same LAN)
# Example: http://192.168.1.100:5005/upload
SERVER_URL = "http://192.168.1.100:5005/upload"

# JPEG settings
JPEG_QUALITY = 60  # 10..95 (higher = better quality/larger)
STREAM_INTERVAL_MS = 1200  # upload every N ms (tune for bandwidth)
SEND_CHUNK = 512
SOCKET_TIMEOUT = 12

# If True, send one stitched image (Left|Right). Recommended.
STITCH_LR = True

# Add a simple increasing frame id in header
SEND_FRAME_ID = True

# --- ESP32 SPI network (MaixPy variants)
# Different MaixPy builds expose different APIs.
# We'll try several in code; set these pins if your build needs them.

# If your firmware supports auto ESP32 init, you can ignore these.
ESP32_SPI = {
    "fpioa": {
        "cs": 25,
        "rst": 8,
        "rdy": 9,
        "mosi": 28,
        "miso": 26,
        "sclk": 27,
    },
    "gpiohs": {
        "cs": 0,
        "rst": 1,
        "rdy": 2,
        "mosi": 3,
        "miso": 4,
        "sclk": 5,
    },
    "spi": 1,  # 🔥 强制硬 SPI1
    "timeout_ms": 20000,
}
