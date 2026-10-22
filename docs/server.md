# 电子纸常驻服务

服务保存设备报到、最终报告、远程 debug 和屏幕内容，提供需要登录的中英文管理页面，默认中文。右上角“中文 / English”在登录前后均可切换；选择通过浏览器 Cookie 保留一年，刷新、登录与退出后仍保留。页面导航、状态、表单、错误提示随语言切换；下发到电子纸的内容仍仅支持英文及可打印 ASCII，历史原始 JSON 保留设备字段与诊断信息。当前测试地址为 `http://192.168.76.109:8088`。设备仍然每次唤醒运行一轮；网页保存后，下一次成功报到才会取得修改。

## 本机运行

在仓库根目录安装服务端依赖并准备本地配置：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r src/server/requirements.txt
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

## Docker / NAS 迁移

```sh
docker compose -f compose.server.yaml up -d --build
docker compose -f compose.server.yaml logs --tail 50 epaper-server
```

容器使用 Gunicorn，并将 SQLite 数据保存在命名卷 `epaper-server-data` 的 `/data/server.sqlite3`。不要用 `down -v` 删除需要保留的数据。默认发布到主机所有网卡的 8088；可通过 `SERVER_BIND`、`SERVER_PORT` 改变绑定。迁移时停止旧服务，备份并恢复整个数据卷，再在新主机启动；SQLite WAL 和 SHM 文件应与数据库一起处理，或在停机后确认已 checkpoint。服务器启动不会重置 debug、展示内容或历史；`DEVICE_TOKEN` 修改会更新该设备认证。

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
  "local_debug": false
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

- `alarm_confirmed=true` 且 `time_trusted=true` 时，使用 `next_run` 推算下次报到；超过十分钟仍未报到显示失联。
- `debug_hold=true` 或 `shutdown.status=debug` 显示维护留机，不产生周期失联告警。
- `failures` 非空显示设备故障；新轮次成功最终报告清除当前故障，历史仍保留。
- 缺少可信下一次唤醒时间显示未知。关机意图和 HTTP 上传成功均不能证明实体断电。
- 历史默认保留 30 天，随请求清理；当前设备状态和管理设置单独保留；去重所需的事件/阶段 ID 也保留，不保留过期报告正文。收到 check-in 后十分钟缺少最终报告会标记周期未完成。管理页面手动刷新，不持续轮询设备，不发送外部通知。

管理登录使用密码哈希、八小时会话、HttpOnly/SameSite Cookie 和 CSRF 表单保护；所有改变设置及登出的请求为 POST。API 不接受管理员会话作为认证。日志不记录请求体或设备 token。建议不把局域网设备 API 端口直接暴露到公网。

## 验证

```sh
.venv/bin/python -m unittest discover -s tests -p test_server.py -v
```

测试使用真实临时 SQLite 和 Flask HTTP 测试客户端，覆盖认证、CSRF、内容限制、幂等与补报顺序、故障恢复、失联与维护状态、重启持久化、30 天保留和 HTTP/HTTPS Cookie。`GET /healthz` 只检查进程与数据库可读，可供容器健康检查使用，不表示设备在线。
