# 第三方源码

## Waveshare IT8951

`src/display/IT8951/lib/` 和 Makefile 使用 Waveshare IT8951 驱动，驱动包地址为：

https://files.waveshare.com/upload/8/80/IT8951_20200319_Release.7z

各文件保留作者和版权说明，使用时遵守对应授权条款。`singlepage/` 为本项目的页面入口。

## bcm2835 1.60

`vendor/bcm2835-1.60.tar.gz` 为构建所需的源码依赖，下载地址为：

http://www.airspayce.com/mikem/bcm2835/bcm2835-1.60.tar.gz

SHA-256：`333e7ceee895e910c29098074773ee86bcab4a82c2af0cf083c4533767e52d27`。

源码包内包含 `COPYING` 和各文件授权说明，使用时应一并保留。Docker 构建直接使用本地源码包。

## 中文字体、FreeType 与 yyjson

中文渲染使用 [Noto CJK](https://github.com/notofonts/noto-cjk) 的简体中文黑体及宋体 Regular、Medium。字体遵循随包附带的 SIL Open Font License；文件位于 `src/display/IT8951/assets/fonts/`。保留完整授权，版本和 SHA-256 记录在 `assets/dependencies.json`。

[FreeType](https://freetype.org/) 通过系统软件包安装开发/运行库，实际版本记录于构建环境。

[yyjson](https://github.com/ibireme/yyjson) 以固定版本源码编入显示程序，位于 `src/display/IT8951/lib/json/`，保留 MIT 授权；上游版本与文件 SHA-256 同样记录在依赖清单。

## 基础环境

Docker 使用 Debian 13 ARM64 基础镜像，并通过 APT 安装编译工具。基础镜像 digest 记录在 `docker/Dockerfile`，实际工具版本记录于每次构建产物目录的 `build-info.txt`。
