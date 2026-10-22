# 构建与设备维护

以下命令以仓库根目录为本地工作目录，SSH 别名 `pi` 对应当前树莓派。除“安装到设备”中的人工步骤外，本地维护工具不会写设备文件、启动任务、校时、设置闹钟或关机。

## 日常修改

1. 在 `src/` 修改源码，在 `config/` 修改计划模板。
2. 执行 `make check`；改时间表后执行 `make preview CONFIG=配置路径`。
3. 改显示代码后运行 `make image` 和 `make build-display`。
4. 需要部署时，选择配置并准备发布包，先检查文件和变更。

直接执行 `src/scheduler/pisched.py` 会进入真实任务、RTC 和关机流程，不能当作本地测试命令。只验证时间计算应使用 preview。

## 构建环境

`docker/Dockerfile` 固定 ARM64 Debian 13 基础镜像 digest，安装 GCC/Make，校验并安装仓库内 bcm2835 1.60 源码包。APT 包版本尚未逐项固定。

```sh
make image
make build-display
```

源目录只读挂载，实际编译在容器临时目录中进行，输出写入 `build/display/`。运行中的编译容器禁用网络，不挂载真实硬件。Intel 主机需要 Docker 支持 ARM64 模拟。

- `epd`：Linux ARM64 可执行文件。
- `epd.sha256`：相对路径的 SHA-256 校验值。
- `build-info.txt`：编译时间、GCC 和运行库包版本。

bcm2835 的 `make check` 会访问真实 GPIO，应在设备上执行。第三方源码编译时有指针转换和未使用函数警告。

## 备份设备

```sh
make snapshot HOST=pi
```

结果保存于 `backups/device/UTC时间戳/`，包括：

- `rootfs/`：调度器、任务、配置、systemd unit、启动配置、显示源码及设备上的 `epd`、日志和 CSV。
- `manifest.json`：每个文件的远端路径与 SHA-256，以及设备时间、系统、服务状态和启动统计。

脚本只读取指定路径，不获取 `.ssh`、PiSugar Web 密码配置或其他账号数据。单个文件超过 8 MiB 会跳过并返回非零退出码；后续日志变大时应单独归档。该快照是项目级备份，不是整张 SD 卡镜像，也不包含网络凭据、全部系统包或系统还原能力。

快照目录被 Git 忽略；需要跨电脑使用的设备备份应另行复制。

## 准备发布包

先将选定模板复制到 `config/local.json` 并调整正式时间，然后：

```sh
make check
make preview CONFIG=config/local.json
make release CONFIG=config/local.json
```

`prepare-release.py` 检查配置和 ARM64 ELF 标识，拒绝覆盖已有输出目录。发布包位于 `build/releases/UTC时间戳/`，包含 `rootfs/`、`release.json` 和 `INSTALL.txt`。构建产物与配置按原字节复制，不会悄悄替换时间表。

发布包将显示程序安装到 `/home/wlt/IT8951/epd`。更换设备用户名或部署目录时，必须先同步修改任务脚本和打包脚本。产物在目标系统上的运行及真实屏幕刷新需设备验证。

## 安装到设备

当前设备有 RTC 异常，有人值守的部署验证期间保留 DEBUG。正常电池运行允许失败后关机，通知重试耗尽后也应关机；DEBUG 是维护例外，不能作为日常电池运行的故障策略，详见 [失败处理约定](failure-handling.md)。本次策略更新不自动修改设备或移除其 DEBUG。

需要设备备份时先使用 snapshot，再将选定发布目录复制到设备的临时目录，例如 `/home/wlt/epaper-release/`。核对 `release.json` 和其中 `rootfs/etc/pisched/config.json` 的内容。然后在树莓派上，从发布目录执行：

```sh
sudo mkdir -p /boot/firmware/DEBUG
sudo install -d /opt/pisched /etc/pisched /var/log/pisched /home/wlt/IT8951
sudo install -m 0755 rootfs/opt/pisched/pisched.py /opt/pisched/pisched.py
sudo install -m 0755 rootfs/usr/local/bin/my_test_task.sh /usr/local/bin/my_test_task.sh
sudo install -m 0644 rootfs/etc/pisched/config.json /etc/pisched/config.json
sudo install -m 0644 rootfs/etc/systemd/system/pisched.service /etc/systemd/system/pisched.service
sudo install -m 0755 rootfs/home/wlt/IT8951/epd /home/wlt/IT8951/epd
sudo systemctl daemon-reload
```

以上不自动启动服务。验证时可手动执行 `sudo systemctl start pisched.service`；这会运行真实采集、刷新和 RTC 命令，DEBUG 只跳过最后的关机。它不是无副作用的检查。

检查日志及结果：

```sh
systemctl status pisched.service --no-pager
journalctl -u pisched.service -n 50 --no-pager
tail -n 50 /var/log/pisched/pisched.log
tail -n 30 /var/log/pisched/epd.log
tail -n 5 /var/log/pisched/network_metrics.csv
```

确认 PiSugar 服务正常、闹钟设置与读回正确，并安排有人值守的完整唤醒测试后，再考虑删除 DEBUG。现有目录形式的标记可用 `sudo rmdir /boot/firmware/DEBUG` 删除；同时检查 `/boot/DEBUG`。服务已在现有设备启用，新设备如需启用应单独执行 `sudo systemctl enable pisched.service`。本次不提供自动移除 DEBUG 或自动关机脚本。

发布包不覆盖 `/boot/firmware/config.txt`、PiSugar 包管理的 unit 或电源选项。`deploy/boot/` 仅是当前 Pi Zero 2 配置参考，不能直接套用到其他板卡。

## 从设备备份恢复

保留 DEBUG。使用安装前 snapshot 的 `rootfs/` 恢复调度器、任务、JSON、项目 systemd unit 和 `/home/wlt/IT8951/epd`，沿用上面的 `install` 权限；恢复后运行 `sudo systemctl daemon-reload`，核对日志。不要将整个快照根目录直接覆盖到 `/`，也不要默认覆盖启动配置或 PiSugar 包管理文件。

需要完整系统灾难恢复时另做 SD 卡镜像；项目快照只负责本项目相关文件。
