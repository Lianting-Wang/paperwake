# 指标采样数据

历史 CSV 按来源分别保存。新增完整放电记录按运行周期冻结，不修改原始读数；它与旧 CSV 的字段不同，不能直接拼接。

| 文件 | 来源 | 数据行数 | 时间范围 |
| --- | --- | --- | --- |
| `network_metrics.local-2026-03-04.csv` | 本次用户提供的本地 `network_metrics.csv` | 50 | 2026-03-04 00:57:05 至 01:02:58 |
| `network_metrics.device-2026-10-22.csv` | 树莓派 `/var/log/pisched/network_metrics.csv` | 3 | 2026-04-11 至 2026-10-22 |
| `battery-discharge-2026-10-23-26.csv` | 管理中心逐轮报告，含缺失读数的周期 | 163 | 2026-10-23 12:01 至 2026-10-26 21:01，America/New_York |

旧本地文件按原行顺序存在 6 处时间回退，不能直接把相邻行当作连续且等间隔的采样。旧设备文件跨越长时间空档，也不构成完整运行记录。这两份旧样本可以用于测试解析，不能据此直接推算无人值守续航。

完整放电 CSV 含 161 个有效电量值、159 个有效电压值，两者同时有效的有 158 个。时间列均为带 UTC 偏移的 ISO 时间，起始与采样时间分别保留；`battery_percent` 使用服务器合并周期后的有效读数，`metric_battery_percent` 和 `startup_battery_percent` 保留各自来源。`fault` 为周期异常标记，`alarm_confirmed` 为闹钟读回确认，`scheduler_elapsed_seconds` 不包含完整启动和关机耗时。数值未平滑，空值留空，无账号和认证信息。

同名 JSON 保存来源及 CSV SHA-256、终点定义和分析结果。用户确认本轮电池耗尽，操作上的终点按最后成功运行至下一次计划唤醒未报到之间取约 81.0 至 81.5 小时。该记录用于本机半小时运行方式的首轮标定，不是跨电池、跨频率的独立验证。详见 [放电分析与估算设计](../../docs/battery-estimation-review-2026-10-27.md)。

## 旧网络指标 CSV 字段

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
