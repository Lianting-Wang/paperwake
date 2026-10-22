# Pi Zero 2 电子纸项目

Pi Zero 2、PiSugar 3 和 IT8951 电子纸组成的周期刷新状态屏。PiSugar 唤醒树莓派后，系统采集指标、刷新屏幕、安排下一次唤醒，然后关机。`pisched` 是开机执行一次的程序；DEBUG 模式下不会自行循环刷新。

电池运行允许闹钟设置失败后关机；失败通知应有次数和总时限，耗尽预算后仍关机。调度器已提供有界通知接口和统一失败收尾，通知命令默认留空；配置及未来电子纸错误状态见 [失败处理约定](docs/failure-handling.md)。

本目录包含项目的源码、配置、构建环境和设备维护工具。

## 目录

```text
src/
  scheduler/pisched.py       开机调度
  tasks/collect_metrics.py   指标采集与屏幕刷新
  display/IT8951/            单页界面和 Waveshare 驱动
config/                     日常时间表示例、半小时测试配置
deploy/                     systemd 服务和设备启动配置参考
docker/                     Debian 13 ARM64 编译环境
scripts/                    本地检查、时间表预览、备份、发布包准备
tests/                      无硬件的维护工具与时间边界检查
docs/                       架构、配置、维护步骤、已知问题
data/samples/               指标采样数据
vendor/                     已固定校验值的 bcm2835 1.60 源码包
backups/                    完整设备快照，只保存在本地
build/                      编译产物和待审阅发布包，只保存在本地
```

在 `src/` 修改源码，在 `config/` 管理刷新计划。

## 本地开始

离线检查只需要 Python 3.9 以上、Bash 和 Make，不需要连接树莓派：

```sh
make check
make preview CONFIG=config/daytime.example.json
# 修改 C 时另用本机 Clang/GCC 做无硬件内存与超时检查
make check-display
```

`daytime.example.json` 暂用 **08:00、12:00、16:00、18:00** 演示日常运行。它是可编辑的示例，尚未作为正式时间表启用。`test-half-hour.json` 保留每半小时一次的测试频率。实际部署前复制选定配置为 `config/local.json` 并调整；本机配置不提交 Git。时间按树莓派的时区解释，当前设备为 `America/New_York`。

构建显示程序需要 Docker 和 Docker Compose。镜像使用 ARM64 Debian 13，与设备系统大版本对齐：

```sh
make image
make build-display
```

产物位于 `build/display/epd`。构建容器没有连接真实 SPI、GPIO 或 PiSugar，不能验证屏幕或电源硬件。

准备发布包时必须提供已选择的配置：

```sh
make release CONFIG=config/local.json
```

发布包位于 `build/releases/`，包含目标文件树与 SHA-256 清单。这一步只生成本地文件；安装、服务启动、重启和关机均不会自动执行。

## 维护入口

- [项目代理维护指引](AGENT.md)
- [架构与文件部署关系](docs/architecture.md)
- [配置和刷新时间表](docs/configuration.md)
- [构建、备份、安装和恢复](docs/maintenance.md)
- [已知问题及后续工作](docs/known-issues.md)
- [采样数据说明](data/samples/README.md)
- [第三方源码说明](THIRD_PARTY.md)

改界面优先看 `src/display/IT8951/singlepage/page.c`；改初始化和刷新流程看 `singlepage/main.c`；改唤醒流程看 `src/scheduler/pisched.py`。

## Git 范围

源码、配置模板、维护文档、采样数据和第三方源码包纳入版本管理。认证信息、运行日志、设备快照、本机配置和二进制产物通过 `.gitignore` 排除。

重要任务完成并验证后自动创建本地 Git commit，具体约定见 [项目代理维护指引](AGENT.md)。推送远端按用户明确要求执行。
