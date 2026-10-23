# 架构与部署关系

设备为 Pi Zero 2、PiSugar 3、IT8951（1872×1404，VCOM −1.56 V，模式 1），运行 Debian 13 ARM64。NAS/本机常驻服务负责接收报告、存储历史、维护 debug 和显示内容；设备仍是开机执行一次的 oneshot。

## 一轮数据流

1. 有界等待 PiSugar，读取电量，NTP 优先校时并核对 RTC。
2. 向配置中的数字 IP 和端口报到，取得本轮 remote debug、固定格式内容及未来唤醒计划并原子缓存。获取不到则保留屏幕原画面、跳过任务。
3. 内容有效时，采集本地电池、电压、温度和 Wi-Fi，记录本次 HTTP 耗时，刷新电子纸。无 CSV 或 ping 采集。
4. 从本轮计划、缓存或配置中选择下一次，在总预算内恢复 PiSugar/校时并设置、读回核验 RTC 闹钟；汇总错误并执行可选通知/错误展示。
5. 把关机意图、闹钟、指标和错误原子加入本地报告队列；有连接且非低电量时，在统一报告期限内发送队列。
6. 保存状态，按本地 DEBUG 或本轮远程 debug 决定留机，否则安全关机。正常关机后由 RTC 再次唤醒。

server 无法通过关机意图证明实际断电；实际关机请求结果在下次报到的 previous 摘要中传递。关机请求自身失败会另外排队 outcome 报告。无法上传的报告留在本地，下次成功报到后补传，旧报告不得覆盖新周期状态。

## 文件和接口

| 仓库 | 设备路径/作用 |
| --- | --- |
| `src/scheduler/pisched.py` | `/opt/pisched/pisched.py`：周期、时钟、闹钟、失败收尾 |
| `src/scheduler/server_client.py` | `/opt/pisched/server_client.py`：有界 HTTP、内容校验、持久报告队列 |
| `src/scheduler/wake_plan.py` | `/opt/pisched/wake_plan.py`：设备与服务器共用的时区、DST 和未来计划计算 |
| `src/tasks/collect_metrics.py` | `/opt/pisched/collect_metrics.py`：本地指标和一次屏幕刷新 |
| `config/*.json` | 显式选定后部署到 `/etc/pisched/config.json` |
| `deploy/systemd/pisched.service` | `/etc/systemd/system/pisched.service` |
| `build/display/epd` | 默认 `/home/wlt/IT8951/epd` |
| `src/server/` | Flask + Gunicorn + SQLite 管理服务，见 [服务指南](server.md) |

设备 token 位于配置的独立文件，不进入配置环境、日志或 Git。发布包不含 token，安装时单独写入并限制为 root 可读。

采集任务通过环境读取已校验配置、事件 ID、页面、HTTP 耗时和父进程本轮实际任务期限。显示进程消费相同一份电池样本，并通过 `PISCHED_PAGE_TITLE`、`PISCHED_PAGE_STATUS`、`PISCHED_PAGE_LABEL_1..6`、`PISCHED_PAGE_VALUE_1..6` 读取内容。C 在硬件初始化/清屏之前验证 ASCII 与长度；内容无效不刷新。

## 留机与兜底

本地 `/boot/DEBUG`、`/boot/firmware/DEBUG` 优先；server 的 false 不能关闭本地维护。本轮成功取得 true 后，后续失败仍可留机，但不从磁盘恢复远程 debug。下次运行必须重新联网获取。

有错误且本轮远程 debug 生效时返回 10，systemd 的 `SuccessExitStatus=10` 允许完成的维护周期留机；错误仍完整保存在状态与报告。崩溃、信号和 300 秒总超时不属于该退出状态，无本地 DEBUG 时仍触发独立关机兜底。DEBUG 留机后不轮询、不循环刷新，网页更改在下一轮执行时生效。

日志仍在 `/var/log/pisched/pisched.log` 和 `epd.log`，每份按 1 MiB、4 个备份轮换。指标与周期状态在 `/var/lib/pisched/` 原子保存。PiSugar 的 package-managed unit、启动配置与电源参数不随应用发布覆盖。
