#!/bin/bash
set -euo pipefail

LOG_DIR="/var/log/pisched"
CSV_FILE="$LOG_DIR/network_metrics.csv"
TARGET="192.168.76.71"

mkdir -p "$LOG_DIR"

TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")

# ---------- PiSugar 电池 ----------
BATTERY=$(echo "get battery" | nc -q 0 -U /tmp/pisugar-server.sock | awk '{print $2}')
BATTERY_V=$(echo "get battery_v" | nc -q 0 -U /tmp/pisugar-server.sock | awk '{print $2}')
TEMP=$(echo "get temperature" | nc -q 0 -U /tmp/pisugar-server.sock | awk '{print $2}')

# ---------- WiFi 信号 ----------
RSSI=$(iwconfig wlan0 2>/dev/null | grep -i signal | awk -F'=' '{print $3}' | awk '{print $1}')

# ---------- Ping ----------
PING_OUTPUT=$(ping -c 20 -i 0.2 "$TARGET" 2>&1 || true)

PACKET_LOSS=$(echo "$PING_OUTPUT" | grep -oP '\d+(?=% packet loss)' || echo "100")

AVG_LATENCY=$(echo "$PING_OUTPUT" | grep -oP 'rtt .* = .*?/(\d+\.\d+)/' | awk -F'/' '{print $5}')

# 如果解析失败
AVG_LATENCY=${AVG_LATENCY:-NaN}
RSSI=${RSSI:-NaN}

# ---------- CSV header ----------
if [ ! -f "$CSV_FILE" ]; then
    echo "timestamp,battery_percent,battery_voltage,temperature,wifi_rssi,ping_loss,ping_avg_latency" > "$CSV_FILE"
fi

# ---------- 写入一行 ----------
echo "$TIMESTAMP,$BATTERY,$BATTERY_V,$TEMP,$RSSI,$PACKET_LOSS,$AVG_LATENCY" >> "$CSV_FILE"

sync

# ---------- EPD ----------
if [ -x /home/wlt/IT8951/epd ]; then
    echo "$TIMESTAMP running epd" >> "$LOG_DIR/epd.log"
    /home/wlt/IT8951/epd >> "$LOG_DIR/epd.log" 2>&1 || true
else
    echo "$TIMESTAMP epd not found or not executable" >> "$LOG_DIR/epd.log"
fi

sync
