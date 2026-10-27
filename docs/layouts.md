# 中文显示与在线版式

“编辑内容”保存同一份标题、状态和最多六条信息；“版式”决定它们在屏幕上的位置。支持 UTF-8 中文、英文、数字和中文标点。标题/状态/名称/内容分别限制为 40/24/20/48 个 Unicode 字符，内容允许换行。

## 编辑与应用

1. 在“版式”选择纸刊、极简或已保存的自定义版式。
2. 内置模板可以直接应用；需要修改时点击“复制为自定义版式”。
3. 在画布或组件菜单选择文字区域，拖动移动，右下角调整区域大小。属性面板可以精确设置坐标、尺寸、字体、字号、行距、对齐和灰度。方向键移动 1 像素，Shift 加方向键移动 10 像素；提供撤销/重做。
4. 文字可以绑定标题、状态、第 1–6 条信息的名称/内容、日期、更新时间、电量，也可以填写固定文字。支持添加分隔线，每个版式最多 32 个组件。
5. “保存草稿”写入服务器，重启后仍保留，不改变已应用版式。草稿文字溢出或缺字可以保存，但必须修正标记后才能应用。
6. “应用到设备”先保存当前修改，再发布完整版式快照。设备下次正常唤醒取得；没有实时推送、额外唤醒或后台轮询。已应用版式之后的草稿修改不会自动下发。

切换版式保留内容。编辑内容时也会检查当前版式是否容纳新文字；不合适时保留表单内容并提示调整。当前使用的版式须先切换后才能删除。并发编辑版本冲突会拒绝覆盖，保留当前窗口的编辑内容。

## 预览与状态

画布图像直接来自设备共用的 C 渲染器；浏览器仅覆盖选中框和拖动手柄。版式编辑器使用示例时间和满电读数，避免编辑过程受采样值影响；普通内容预览使用设备最近一次可信报告。两者都不是实体屏幕截图。

文字按实际字宽换行，区域放不下时提示调整，不自动缩字或截断发布。日期和电量等动态字段应用前还会验证最长代表值以及 1872/1856 两种有效宽度。运行时任何文字、字体或格式异常都不提交无效帧。报告中的下载内容版本不代表刷新完成；只有 `metrics.applied_content_version` 与发布版本相同才显示已完成刷新。

## 数据与接口

版式文档为 `{schema_version:1,id,revision,elements}`，坐标使用 1872×1404 参考画布。每个组件包含 `id,type,x,y,width,height`，以及 `binding,text,prefix,suffix,font,weight,size,line_height,align,gray`。类型为 `text` 或 `line`；字体为 `sans/serif`，字重为 `400/500`，字号 16–160，行距 1–2，灰度 0（黑）至 15（白）。文字绑定为 `title/status/clock/date/battery/rows.0.label/rows.0.value` 等，空绑定使用固定文字。分隔线绑定非空信息时可随信息隐藏。

管理接口均要求管理员会话，POST 必须提交 `X-CSRF-Token`：

| 路径 | 行为 |
| --- | --- |
| `GET /devices/<id>/layouts` | 编辑器及已保存版式 |
| `POST /devices/<id>/layouts` | 按 `source_id,name` 复制版式 |
| `POST /devices/<id>/layouts/<layout_id>` | 按 `revision,name,layout` 保存；`action=delete` 删除 |
| `POST /devices/<id>/layouts/<layout_id>/apply` | 按 `revision` 应用已保存快照 |
| `POST /devices/<id>/layout-preview` | 输入 `layout`，返回 `png_base64,issues`，不保存 |

设备 check-in 的 `display.layout` 包含完整已应用文档；内容或已应用版式变化递增 `content_version`。草稿版本与设备显示版本独立；冲突返回 409。

## 字体与构建

FreeType 绘制固定版本 Noto Sans SC / Noto Serif SC Regular、Medium，输出 4 bpp。字体及 yyjson 解析器版本、下载源和 SHA-256 位于 `src/display/IT8951/assets/dependencies.json`，授权随文件保存。日常请求只下发小型 JSON，字体随发布包安装一次。

修改渲染代码后执行 `make build-preview`、`make check`、`make check-display`、`make image`、`make build-display`，并重建管理服务镜像。设备运行需要 `libfreetype6`，发布包必须同时包含新程序、`display_model.py`、字体和源码。
