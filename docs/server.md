# 电子纸常驻服务

服务保存设备报到、最终报告、远程 debug、屏幕内容和每日唤醒计划，提供需要登录的中英文管理页面，默认中文。右上角“中文 / English”在登录前后均可切换；选择通过浏览器 Cookie 保留一年，刷新、登录与退出后仍保留。页面导航、状态、表单、错误提示随语言切换；下发到电子纸的内容仍仅支持英文及可打印 ASCII，历史原始 JSON 保留设备字段与诊断信息。当前测试地址为 `http://192.168.76.109:8088`。设备仍然每次唤醒运行一轮；网页保存后，下一次成功报到才会取得修改。

登录后，页眉下方提供“设备概览 / 编辑内容 / 唤醒计划 / 报告历史”四个标签，滚动页面时固定在顶部。内容与远程调试在 `/devices/<device_id>/settings` 编辑，每日唤醒计划在独立的 `/devices/<device_id>/wake-plan` 页面编辑；保存成功或校验失败均留在对应页面，两种设置独立保存。

概览通过关键指标、屏幕配置预览和运行状态分区呈现设备信息，缓存详情可展开查看。内容编辑提供实时预览及六条编号信息行，输入会发送到只读的预览接口，只有点击保存后才会更新设备配置。唤醒页并列展示每日规则与已保存计划的未来五次时间，修改表单后需保存才能更新计划预览；时区、离线缓存和 RTC 说明收在可展开区域。三页沿用报告历史的配色、卡片和间距，窄屏自动改为单列。

屏幕预览采用电子纸实际的灰色标题区、左侧六行信息、右侧 TIME / BATTERY / STATUS 及 PI STATUS 页脚；位图字体、坐标和换行直接来自共用 C 绘图函数，可点击查看原尺寸。TIME 与 BATTERY 使用最近一轮报告中的采样时间和电量，时间按该轮 check-in 上报的设备配置时区显示，不随浏览器或后来编辑的唤醒时区改变；缺少可信时间、设备时区或电量时显示 N/A。它展示当前配置或未保存草稿，不是实体屏幕的实时截图，也不证明设备已应用新内容。

`GET /devices/<device_id>/preview.png` 生成当前配置的 PNG；同一路径的 POST 接收内容表单生成草稿 PNG。两者均需要管理员会话，POST 还需要 CSRF，均不保存草稿、不递增内容版本、不切换 debug，也不调用设备。输入停顿 200 ms 后更新，过期请求会被取消，失败时保留上一张有效预览并显示提示。PNG 响应不缓存到浏览器；没有 JavaScript 时仍可查看已保存配置的预览。

## 本机运行

在仓库根目录安装服务端依赖并准备本地配置：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r src/server/requirements.txt
make build-preview
cp config/server.env.example config/server.local.env
.venv/bin/python -m src.server hash-password
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

编辑 `config/server.local.env`，填写上面生成的密码哈希，以及两次独立生成的 `SECRET_KEY` 和 `DEVICE_TOKEN`。哈希保留单引号，避免 shell 或 Compose 展开 `$`。本地配置、密码和 token 不提交 Git。`DEVICE_ID` 必须与设备配置相同；当前设备为 `epaper-pi-zero2`。设备端只安装 token，不安装管理员密码或会话密钥。

```sh
set -a
. config/server.local.env
set +a
DATABASE=data/runtime/server.sqlite3 .venv/bin/gunicorn --bind 0.0.0.0:8088 --workers 2 --threads 2 --timeout 30 --access-logfile - 'src.server:create_app()'
```

浏览器打开本机地址登录。防火墙需允许树莓派访问 TCP 8088。该进程必须保持运行；主机休眠或退出进程会被设备视为 server 不可达。局域网 HTTP 用于本机测试，不提供传输加密。

本机需要 C 编译器构建预览库。修改 `page_render.c`、相关头文件或字库后，重新执行 `make build-preview` 并重启管理服务；默认预览尺寸为 1872×1404，使用 32 像素传输对齐的设备可在 app 配置中将 `PAGE_PREVIEW_WIDTH` 设为 1856。预览库路径可用 `PAGE_RENDERER_LIBRARY` 指定。源码与已编译库不一致或库缺失时，预览返回 503，不使用另一套过时版式。

## Docker / NAS 迁移

```sh
docker compose -f compose.server.yaml up -d --build
docker compose -f compose.server.yaml logs --tail 50 epaper-server
```

容器使用 Gunicorn，并将 SQLite 数据保存在命名卷 `epaper-server-data` 的 `/data/server.sqlite3`。不要用 `down -v` 删除需要保留的数据。默认发布到主机所有网卡的 8088；可通过 `SERVER_BIND`、`SERVER_PORT` 改变绑定。迁移时停止旧服务，备份并恢复整个数据卷，再在新主机启动；SQLite WAL 和 SHM 文件应与数据库一起处理，或在停机后确认已 checkpoint。服务器启动不会重置 debug、展示内容或历史；`DEVICE_TOKEN` 修改会更新该设备认证。

Docker 构建阶段会编译本机架构的共用绘图库，运行镜像保留源码与字库校验，但不安装编译器或 GPIO/SPI 依赖。后续电子纸风格调整应修改共用绘图文件，并同时构建设备程序和重新构建本服务镜像，网页无需再单独调整屏幕 CSS。

迁移 NAS 时将设备地址改为 NAS 局域网 IP（例如 `http://192.168.76.71:8088`），将 `BASE_URL` 改为管理网页的 HTTPS 域名，并在现有反向代理设置 TLS 和到服务端 8088 的转发。HTTPS `BASE_URL` 会启用 Secure 会话 Cookie；保持 HTTP 时这些 Cookie 不要求 HTTPS。无需 DNS 参与设备局域网访问。`BASE_URL` 只用于显示管理地址和配置 Cookie，不重写设备 API 路径。

## 设备协议

两个接口均使用 `Authorization: Bearer <DEVICE_TOKEN>` 和 JSON 对象；单个请求体上限 256 KiB。设备 token 不能登录管理页，管理员会话也不能代替设备 token。未登记的设备 ID 和错误 token 返回 401。示例：

```json
POST /api/v1/check-in
{
  "device_id": "epaper-pi-zero2",
  "event_id": "unique-boot-event-id",
  "started_at": "2026-10-22T12:00:00+00:00",
  "battery_percent": 80,
  "local_debug": false,
  "wake_schedule": {"timezone": "America/New_York", "schedule": ["08:00", "12:00", "16:00", "18:00"]}
}
```

返回：

```json
{
  "debug": false,
  "content_version": 1,
  "display": {
    "title": "E-PAPER",
    "status": "Ready",
    "rows": [{"label": "Device", "value": "Connected"}]
  }
}
```

新版设备还发送 `wake_schedule` 作为首次计划种子。仅当服务器尚未保存计划时采用，之后设备配置不能覆盖管理页面保存的计划。已保存计划时，check-in 响应额外包含 `wake_plan={version,timezone,schedule,generated_at,occurrences}`，具体时间均为带偏移的 UTC 时间，覆盖未来 24 小时且至少五次；返回完整每日规则供长期离线继续计算。管理页的每日计划通过独立表单保存、单独递增版本，不改动屏幕内容或 debug。旧版设备和未配置的服务器之间不会凭空生成计划。

`started_at` 必须包含 UTC 偏移；事件 ID 在设备每轮运行期间保持不变。只有首次收到某事件的 check-in 才推进当前事件。重试旧 check-in 和补报旧报告不会替换新轮次。显示标题/状态/名称/值只接受可打印 ASCII，长度分别为 40/24/20/48，最多六行；值可以为空，其余字段不可为空。管理页面进行服务端校验，无效修改不会保存。

```json
POST /api/v1/reports
{
  "device_id": "epaper-pi-zero2",
  "event_id": "unique-boot-event-id",
  "phase": "final",
  "finished_at": "2026-10-22T12:00:23+00:00",
  "content_version": 1,
  "metrics": {"display_returncode": 0, "values": {"battery_percent": 80, "wifi_rssi": -48}},
  "failures": [],
  "display": {"status": "ok"},
  "next_run": "2026-10-22T16:00:00+00:00",
  "alarm_confirmed": true,
  "time_trusted": true,
  "debug_hold": false,
  "shutdown_intent": "poweroff",
  "shutdown": {"status": "requesting"}
}
```

成功或重复报告均返回 `{"accepted":true}`。按设备 ID、事件 ID、阶段去重，重复 final/outcome 不覆盖同阶段原报告。关机请求失败后的 outcome 可补充最终故障状态。最终报告只在事件等于当前 check-in 时更新当前状态；初次连接失败产生的报告可在下一轮补报并保留为历史。服务端使用接收时间记录历史和保留期，不以设备时钟决定事件新旧。

## 状态与保留

- 设备概览展示本轮计划来源（server/cache/local）、版本、缓存覆盖范围和实际 RTC 闹钟结果；设置页预览接下来五次及可下载时间数量。预览只表示服务器可下发的计划，设备下一次成功报到才会取得。浏览器本地时间显示不改变明确设置的计划 IANA 时区。
- `alarm_confirmed=true` 且 `time_trusted=true` 时，使用 `next_run` 推算下次报到；超过十分钟仍未报到显示失联。
- `debug_hold=true` 或 `shutdown.status=debug` 显示维护留机，不产生周期失联告警。
- `failures` 非空显示设备故障；新轮次成功最终报告清除当前故障，历史仍保留。
- 缺少可信下一次唤醒时间显示未知。关机意图和 HTTP 上传成功均不能证明实体断电。
- 历史默认永久保留。每台设备的历史页面可选择 3、7、15、30 天或自定义 1–36500 天；保存前预览清理数量，确认后立即按服务器接收时间清理，并在后续请求（含容器健康检查）时持续执行。保留设置保存在 SQLite，重启不重置；选择永久保留停止后续自动清理。
- 历史可勾选周期删除，也可批量删除选定日期之前的报告。截止日期以浏览器识别的 IANA 时区当天开始为界，不包含当天；夏令时按该日期当时的偏移计算，重复午夜取最早一次，不存在的午夜取当日首个有效瞬间，整日被跳过则拒绝操作。预览确认会固定时区和截止时间，之后新收到的报告不在本次手动删除范围。浏览器未提供有效时区时不会默认为 UTC 删除。操作需要管理员登录、CSRF 保护和十分钟内的会话确认，取消预览不会改动数据。
- 清理只移除历史正文；当前状态、屏幕内容、调试设置和去重所需的事件/阶段 ID 单独保留。删除后重传同阶段旧报告不会恢复已删历史，也不会覆盖当前状态。删除无法撤销，永久保留不会恢复此前已被清理的记录。
- 历史页面提供电量、电压、PiSugar 温度、Wi-Fi、连接延迟和运行时长曲线，支持时间范围和指标切换。同一运行周期的 check-in/final/outcome 合并为一个样本，后阶段优先，缺失指标留空；异常按周期统计。时间优先使用可信的设备开始时间，失效时回退服务器接收时间。长期数据按时间桶聚合，图上注明间隔，原始报告仍可逐周期展开查看。
- 电量首末读数差以百分点展示，并不代表实测 mAh、Wh 或电流；充电也会造成读数上升。现有设备没有这些能耗测量数据，实际长期放电趋势需等待正常电池运行积累。
- 概览和历史页显示估计日耗电（百分点/天）、至 10% 保护电量的剩余天数和预计到达时间。独立取最近 7 天的原始去重周期，优先使用可信的 `metrics.timestamp` 采样时间，否则使用有效且可信的 `started_at`；无可信时钟的最新电量会暂停预测。至少 3 个不同采样时刻、跨度 6 小时、净下降 2 个百分点后，按最近连续放电段首末净下降除以实际经过天数估算，避免将小幅读数抖动累加为耗电。
- 回升判断在本段至少有 3 个有效电压样本时优先参考电压，阈值为 0.05 V；缺失或不足时参考电量百分比，阈值为 2 个百分点。各自取最近 3 小时内最多 5 个读数的中间 50% 区间，只有当前区间下沿超过最低滚动中位数对应区间的上沿至少一个阈值，才重新积累放电段；区间重叠时保留历史，不将中位数的短时回弹视为充电。窗口内只剩单个样本时直接使用该读数。
- 明显偏离当前波动区间的孤立高读数，或超过参考区间上沿两倍阈值加参考区间宽度的大幅回升，暂缓预测但不丢弃历史，等后续样本确认或恢复。采样间隔超过 24 小时会重置放电段，最新有效电压间隔超过 24 小时也重新积累电压依据；缺失电压不会丢弃有效电量样本。页面显示本段有效电压的首末值及样本数量。以上是趋势启发式判断，尚未采集实测插电或充电状态。
- 日耗电仍按原始电量首末读数的净下降与实际时间计算，不累计波动，也不将电压线性外推为续航。PiSugar 3 的百分比由平均电压与电池曲线换算，两者并非独立传感器，见 [v2.3.2 官方实现](https://github.com/PiSugar/pisugar-power-manager-rs/blob/v2.3.2/pisugar-core/src/pisugar3.rs#L494-L501)。
- 最新有效读数超过 24 小时、读数平直或数据不足时不输出续航。剩余天数扣除距最近采样已经过去的时间，最小为零。页面显示观察依据和状态说明；这是历史趋势推算，充电、电量校准及使用频率变化会影响结果，不表示已测量电流或能量。
- 所有管理页面的概览、曲线刻度/样本、数据表、记录、预测和删除截止时间通过浏览器 `Intl` 自动识别时区后显示当地时间，并标注时区；原始 ISO 时间保存在 `datetime` 属性和报告 JSON 中，数据库与设备 API 保持原始带偏移时间。无需按浏览器语言猜时区，也不会修改设备唤醒计划。没有 JavaScript 时页面明确提示启用本地时间显示。
- 收到 check-in 后十分钟缺少最终报告会标记周期未完成。管理页面按需加载曲线，手动刷新设备状态，不持续轮询设备，不发送外部通知。

管理登录使用密码哈希、八小时会话、HttpOnly/SameSite Cookie 和 CSRF 表单保护；所有改变设置及登出的请求为 POST。API 不接受管理员会话作为认证。日志不记录请求体或设备 token。建议不把局域网设备 API 端口直接暴露到公网。

## 验证

```sh
.venv/bin/python -m unittest discover -s tests -p test_server.py -v
node --test tests/test_local_time.cjs
```

`make check` 覆盖真实临时 SQLite、Flask HTTP 测试客户端及纯计算验证，包括认证、CSRF、内容限制、幂等与补报顺序、故障恢复、失联与维护状态、重启持久化、永久/限期保留、删除预览与确认、本地日期/DST 边界、曲线数据合并、电池估算和 HTTP/HTTPS Cookie。浏览器时间格式的专项测试使用 Node.js 内置测试器单独运行，不增加服务端运行依赖。`GET /healthz` 只检查进程与数据库可读，可供容器健康检查使用，不表示设备在线。
