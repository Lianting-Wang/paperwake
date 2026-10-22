# 配置与刷新时间表

半小时一次用于缩短测试周期，不作为日常频率。正式运行可选白天几个固定时点，并在晚上跨到第二天的第一个时点。

## 配置模板

| 文件 | 用途 | 时间表 | 闹钟失败时关机 |
| --- | --- | --- | --- |
| `config/daytime.example.json` | 日常运行示例，待自行调整 | 08:00、12:00、16:00、18:00 | 否 |
| `config/test-half-hour.json` | 加速测试 | 全天每半小时一次 | 否 |

两个模板的 `shutdown_even_if_alarm_set_failed` 均为 `false`，未成功安排唤醒时保持开机。模板只有部署后才会改变设备运行设置。

选择后复制为 `config/local.json`，修改 `schedule` 即可。该文件被 Git 忽略，不会意外成为团队默认值；需要共享的新方案应另存为命名清楚的模板。

## 主要字段

| 字段 | 含义 |
| --- | --- |
| `schedule` | 每天的本地时区时间，24 小时制 `HH:MM`，至少一个且不能重复 |
| `repeat_mask` | 当前维护工具只接受 `127`，对应每日运行；原程序的下次时间计算未实现按星期过滤 |
| `pisugar.uds_path` | 默认 `/tmp/pisugar-server.sock` |
| `pisugar.timeout_sec` | 单次 socket 通信超时，当前 2 秒 |
| `task.command` | 默认 `/usr/local/bin/my_test_task.sh`，通过 `shlex.split` 启动 |
| `task.timeout_sec` | 当前 120 秒；实现存在阻塞读取问题，见已知问题 |
| `shutdown.command` | 当前 `/sbin/shutdown -h now` |
| `shutdown.shutdown_even_if_alarm_set_failed` | 闹钟设置失败后是否仍关机；模板为 `false` |
| `log_file` | `/var/log/pisched/pisched.log` |

时区来自树莓派系统，JSON 中没有时区字段。检查命令为 `ssh pi timedatectl`。设备当前为 `America/New_York`，夏令时切换的真实 RTC 唤醒还需硬件验证。

## 无硬件预览

```sh
make preview CONFIG=config/daytime.example.json
python3 scripts/preview-schedule.py \
  --config config/daytime.example.json \
  --timezone America/New_York \
  --now 2026-10-22T19:00:00 --count 5
```

从示例中的 19:00 预览，下一次应为次日 08:00。预览调用调度器的纯计算函数，不执行其 `main()`，不连接 PiSugar，也不修改设备。预览默认时区为 `America/New_York`；设备时区变化时需同时修改参数。

## 当前仍在源码中的设置

- `192.168.76.71` 同时出现在调度器和任务脚本，换网络时需核对两处。
- `wlan0`、CSV 路径和 `epd` 路径位于任务脚本。
- 电子纸电池查询 socket 位于 `singlepage/page.c`，与 JSON 各自配置。
- VCOM 与显示模式位于 `singlepage/main.c`。页面中也有一段固定的 VCOM 显示文字，修改硬件参数时需同步核对。

这些值尚未统一配置化，调整设备或网络环境时需同步核对。
