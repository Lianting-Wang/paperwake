# 配置与刷新时间表

`config/daytime.example.json` 是日常示例（08:00、12:00、16:00、18:00）；`config/test-half-hour.json` 用于测试。正式时间表尚未选定。复制模板为忽略 Git 的 `config/local.json`，部署时明确传给 `make release CONFIG=...`。

## 服务连接

```json
"server": {
  "base_url": "http://192.168.76.71:8088",
  "device_id": "epaper-pi-zero2",
  "token_file": "/etc/pisched/device.token",
  "timeout_sec": 5,
  "max_attempts": 3,
  "total_timeout_sec": 20,
  "backoff_sec": 1
}
```

本机验证改成 `http://192.168.76.109:8088`。地址必须是 HTTP/HTTPS 的数字 IP，无用户信息、查询或片段，不使用 DNS、环境代理或重定向。设备 token 通过独立文件读取，server 使用 Bearer 校验。设备 ID 应稳定且与 server 注册一致。管理网页使用域名 HTTPS，与设备 IP 入口独立。

报到和报告分别最多 3 次、单次 5 秒、单阶段 20 秒，含等待、响应读取和清理；401/403/其他请求错误不重试。响应 JSON 有大小限制，慢速分片也不能无限延长总期限。队列补传共用一次报告预算，不为每个旧事件重开时间。

## 主要默认值

| 配置 | 默认值/约定 |
| --- | --- |
| `schedule` / `repeat_mask` | 必填且时点不重复；仅每日 127 |
| `timezone` | `America/New_York` |
| `pisugar.uds_path` | `/tmp/pisugar-server.sock` |
| `pisugar.timeout_sec` / `ready_timeout_sec` | 2 / 15 秒 |
| `pisugar.max_attempts` / `retry_delay_sec` | 3 / 1 秒 |
| `task.command` / `timeout_sec` | `/usr/bin/python3 /opt/pisched/collect_metrics.py` / 120 秒 |
| `network.interface` | `wlan0`，不再配置 ping 目标 |
| `display.path` / `timeout_sec` | `/home/wlt/IT8951/epd` / 45 秒 |
| `runtime.max_seconds` | 240 秒，应用总期限上限 |
| `runtime.time_sync_seconds` | NTP 等待最多 30 秒 |
| `runtime.shutdown_margin_seconds` | 90 秒，不得小于所有收尾预算之和 |
| `runtime.low_battery_percent` | 10%，跳过任务、server 交互和可选错误展示，仍尝试闹钟和关机 |
| `runtime.rtc_min_year` | 2025，RTC 还不得早于最近 NTP 可信记录减 5 分钟 |
| `notification.command` | 默认 `[]`，有界可选通知接口 |
| `notification.max_attempts` / `timeout_sec` / `total_timeout_sec` | 3 / 5 / 20 秒 |
| `failure_display.command` / `timeout_sec` | 默认 `[]` / 10 秒 |
| `shutdown.command` / `max_attempts` / `timeout_sec` | `/sbin/shutdown -h now` / 2 / 5 秒 |
| `shutdown.shutdown_even_if_alarm_set_failed` | 必须 true，维护留机使用 DEBUG |
| `state_file` / `metrics_file` / `outbox_file` | `/var/lib/pisched/` 下的 `state.json` / `metrics.json` / `outbox.json` |
| `log_file` | `/var/log/pisched/pisched.log` |

校验拒绝未知字段、错误类型、零超时、无效计划和相互覆盖的持久化路径。收尾预留包括 server 报告、通知、错误展示、关机调用和持久化余量。配置损坏依旧进入默认电池关机策略。

## 内容与时间

server 页面为 `{title,status,rows:[{label,value}]}`；标题/状态/名称必须非空，值可为空。只接受可打印 ASCII，长度依次不超过 40/24/20/48，最多六条。保留本地时间和电量，任意中文与图片不在本版范围内。

```sh
make preview CONFIG=config/daytime.example.json
python3 scripts/preview-schedule.py --config config/daytime.example.json --now 2026-10-22T19:00:00 --count 5
```

采集任务另留 30 秒工作预算给 RTC 闹钟读写，不能耗尽闹钟阶段的时间。任务后按关机余量重算下一次计划。春季不存在的时刻跳过；秋季重复时刻只采用第一次。闹钟按 UTC 时分、repeat、enabled 读回核验；长于 24 小时的 DST 跨越暂用目标 UTC 星期位，防止提前一天唤醒。硬件参数仍为 VCOM −1.56 V、模式 1。
