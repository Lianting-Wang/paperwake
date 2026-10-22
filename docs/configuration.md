# 配置与刷新时间表

`config/daytime.example.json` 演示每天 08:00、12:00、16:00、18:00；`config/test-half-hour.json` 为现有半小时测试表。正式计划尚未确定。选择后复制为 `config/local.json` 并调整，部署时显式传给 `make release`。

两个模板均使用 `shutdown_even_if_alarm_set_failed=true`，符合电池运行的失败关机策略。运行校验只接受 true；有人值守维护用 DEBUG 标记。部署工具、预览和实际程序共用调度器的校验与默认值；未知字段、空计划、重复时点、字符串布尔值、零超时和非每日 repeat_mask 会被拒绝。配置损坏时真实入口仍按默认电池策略收尾，systemd 另有独立失败关机兜底。

## 主要字段与默认值

| 字段 | 默认值/约定 |
| --- | --- |
| `schedule` | 必填，至少一个不重复的 `HH:MM` |
| `repeat_mask` | 127，仅支持每日 |
| `timezone` | `America/New_York`，IANA 时区名；生产和预览均使用它 |
| `pisugar.uds_path` | `/tmp/pisugar-server.sock` |
| `pisugar.timeout_sec` | 单次交换 2 秒，含所有分片；响应最长 4096 字节 |
| `pisugar.ready_timeout_sec` | 就绪等待总共 15 秒 |
| `pisugar.max_attempts` / `retry_delay_sec` | 3 次 / 1 秒；读写瞬时错误有限重试 |
| `task.command` | `/usr/bin/python3 /opt/pisched/collect_metrics.py` |
| `task.timeout_sec` | 120 秒，管理整个进程组 |
| `runtime.max_seconds` | 应用本轮总预算 240 秒，最多 240；提前为通知、显示和关机预留时间 |
| `runtime.time_sync_seconds` | NTP 最多等待 30 秒；已同步时不会用旧 RTC 覆盖系统时间 |
| `runtime.shutdown_margin_seconds` | 默认 60 秒；任务后重算，配置不得短于通知、错误显示、关机调用和持久化收尾的预留总预算 |
| `runtime.low_battery_percent` | 10%；低电量跳过采集、通知重试和错误页面，仍尝试安排闹钟并关机 |
| `runtime.rtc_min_year` | 2025；离线 RTC 还不得早于上次 NTP 可信记录减 5 分钟 |
| `shutdown.command` | `/sbin/shutdown -h now` |
| `shutdown.max_attempts` / `timeout_sec` | 最多 2 次，每次 5 秒 |
| `notification.command` | 参数数组，默认 `[]`，表示未配置而非发送成功 |
| `notification.max_attempts` | 3，含第一次；所有阶段失败只调用同一轮通知 |
| `notification.timeout_sec` / `total_timeout_sec` | 单次 5 秒 / 含退避和进程清理总共 20 秒 |
| `notification.backoff_sec` | 1 秒，指数退避，单次等待上限 5 秒 |
| `failure_display.command` / `timeout_sec` | 默认 `[]` / 10 秒，未来错误 UI 的末尾输出入口 |
| `network.target` / `interface` | `192.168.76.71` / `wlan0` |
| `network.ping_count` / `ping_timeout_sec` | 20 / 15 秒 |
| `display.path` / `timeout_sec` | `/home/wlt/IT8951/epd` / 45 秒 |
| `state_file` / `metrics_file` | `/var/lib/pisched/state.json` / `metrics.json` |
| `log_file` | `/var/log/pisched/pisched.log` |

可执行命令的超时至少 2 秒，其中最多 1 秒用于进程组终止和回收。更短的剩余预算直接记为超时，跳到收尾。所有等待用单调时钟。

采集程序接收调度器通过环境传入的已校验配置和事件 ID，CSV、指标状态、显示电量使用同一轮样本。可选通知/错误显示命令接收 JSON 标准输入；命令不经过 Shell 展开。具体通道及未来 UI 见 [失败处理约定](failure-handling.md)。

## 时间预览与夏令时

```sh
make preview CONFIG=config/daytime.example.json
python3 scripts/preview-schedule.py --config config/daytime.example.json \
  --now 2026-10-22T19:00:00 --count 5
```

从 19:00 开始，下一次为次日 08:00。预览默认采用 JSON 的 timezone；显式 `--timezone` 可用于比较其他时区。

计算跳过春季不存在的本地时刻；秋季重复时刻只采用第一次（fold=0）。硬件闹钟按 UTC 时分读回校验，并核验实际 repeat 和 enabled=true。通常 repeat=127；若跳过缺失时刻或 DST 切换使下一事件相距至少 24 小时，临时只启用目标 UTC 星期位，防止每日时分闹钟提前一天触发。下一次开机重新计算；这没有开放任意星期时间表。DST 切换日的实际关机唤醒仍需实机验收。

VCOM 和显示模式仍固定于 `singlepage/main.c`，当前硬件参数不变。调整屏幕型号前核对硬件。
