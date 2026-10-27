# Pi Zero 2 电子纸项目

Pi Zero 2、PiSugar 3 和 IT8951 电子纸组成的周期刷新状态屏。PiSugar 唤醒树莓派后，系统向常驻 server 报到、下载显示内容、本轮 debug 与未来唤醒计划，采集指标、刷新屏幕、安排下一次唤醒、上报结果，然后关机。`pisched` 是开机执行一次的程序；DEBUG 模式下不会自行循环刷新。本地 DEBUG 始终有效；远程 debug 只作用于本轮，断网时不会使用缓存留机。

电池运行允许闹钟设置失败后关机；失败通知应有次数和总时限，耗尽预算后仍关机。调度器已提供有界通知接口和统一失败收尾，通知命令默认留空；配置及未来电子纸错误状态见 [失败处理约定](docs/failure-handling.md)。

服务器每次下发未来 24 小时内全部唤醒时点，至少五次，设备原子缓存完整每日规则，断网后仍能继续安排 RTC；具体时点用尽后继续离线推算。PiSugar 初始化、校时和闹钟恢复共享 240 秒总预算，最多五轮，失败耗尽后仍按原策略关机。

本目录包含项目源码、配置、构建环境和设备维护工具。常驻服务提供管理员登录、内容编辑、远程 debug、唤醒计划编辑、指标历史及失联标记；Docker 与本机启动步骤见 [服务指南](docs/server.md)。设备直接访问 IP+端口，网页可经现有域名 HTTPS 反向代理。

## 目录

```text
src/
  scheduler/pisched.py       开机调度
  scheduler/wake_plan.py     服务与设备共用的每日计划和离线推算
  tasks/collect_metrics.py   指标采集与屏幕刷新
  server/                   Flask 管理网页、设备 API、SQLite
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

完整离线检查需要 Python 3.10 以上、Bash、Make、C 编译器、FreeType 开发文件、pkg-config 及服务端依赖，不需要连接树莓派。macOS 可使用 `brew install freetype pkgconf`，Debian 使用 `apt-get install libfreetype-dev pkg-config`：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r src/server/requirements.txt
make check
make preview CONFIG=config/daytime.example.json
# 修改 C 时另用本机 Clang/GCC 做无硬件内存与超时检查
make check-display
```

`daytime.example.json` 暂用 **08:00、12:00、16:00、18:00** 演示日常运行。它是可编辑的示例，尚未作为正式时间表启用。`test-half-hour.json` 保留每半小时一次的测试频率。实际部署前复制选定配置为 `config/local.json` 并调整；本机配置不提交 Git。时间表按配置的 `timezone` 解释，当前配置与设备时区均为 `America/New_York`。

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
- [常驻服务与管理网页](docs/server.md)
- [配置和刷新时间表](docs/configuration.md)
- [构建、备份、安装和恢复](docs/maintenance.md)
- [已知问题及后续工作](docs/known-issues.md)
- [采样数据说明](data/samples/README.md)
- [第三方源码说明](THIRD_PARTY.md)

改电子纸版式优先看 `src/display/IT8951/singlepage/page_render.c`，网页屏幕预览也直接使用该文件及相同中文字体。支持纸刊、极简和在线自定义版式，使用方法见 [版式编辑器](docs/layouts.md)。修改后同时构建设备程序与管理服务；本机运行管理服务前执行 `make build-preview`。`singlepage/page.c` 负责读取设备内容、时钟、电量并提交刷新；初始化流程见 `singlepage/main.c`，唤醒流程见 `src/scheduler/pisched.py`。

## Git 范围

源码、配置模板、维护文档、采样数据和第三方源码包纳入版本管理。认证信息、运行日志、设备快照、本机配置和二进制产物通过 `.gitignore` 排除。

重要任务完成并验证后自动创建本地 Git commit，具体约定见 [项目代理维护指引](AGENT.md)。推送远端按用户明确要求执行。
