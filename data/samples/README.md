# 指标采样数据

两份 CSV 按来源分别保存，未合并、排序、去重或修改数值。

| 文件 | 来源 | 数据行数 | 时间范围 |
| --- | --- | --- | --- |
| `network_metrics.local-2026-03-04.csv` | 本次用户提供的本地 `network_metrics.csv` | 50 | 2026-03-04 00:57:05 至 01:02:58 |
| `network_metrics.device-2026-10-22.csv` | 树莓派 `/var/log/pisched/network_metrics.csv` | 3 | 2026-04-11 至 2026-10-22 |

本地文件按原行顺序存在 6 处时间回退，不能直接把相邻行当作连续且等间隔的采样。设备文件跨越长时间空档，也不构成完整运行记录。这些样本可以用于测试解析，不能据此直接推算无人值守续航。

## 字段

| 字段 | 含义与单位 |
| --- | --- |
| `timestamp` | 采集开始时的设备本地时间，无时区偏移；需结合当时设备配置解释 |
| `battery_percent` | PiSugar 报告的电量百分比 |
| `battery_voltage` | 电池电压，V |
| `temperature` | PiSugar 报告的温度，非树莓派 CPU 温度 |
| `wifi_rssi` | `wlan0` 信号，dBm |
| `ping_loss` | 对局域网目标的丢包百分比 |
| `ping_avg_latency` | ping 平均往返延迟，ms |

后续完整采样日志保存于被忽略的 `data/runtime/` 或 `backups/`。需要纳入版本管理的小型采样数据可复制到本目录，避免运行数据不断扩大 Git 仓库。
