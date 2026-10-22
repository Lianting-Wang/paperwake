"""Two management UI languages; device content and protocol stay independent."""
from flask import has_request_context, request

LANGUAGE_COOKIE = 'epaper_language'
LANGUAGES = ('zh', 'en')

# Chinese source messages also provide the default when no preference is saved.
ENGLISH = {
    '电子纸管理': 'E-paper control', '电子纸': 'E-paper', '管理中心': 'Control panel',
    '界面语言': 'Interface language', '退出登录': 'Sign out',
    '电子纸设备管理': 'Your screen, connected.',
    '修改将在设备下次报到时生效。': 'Changes arrive at the next check-in.',
    '设备概览': 'Device overview', '页面刷新时间': 'Last refreshed', '刷新状态': 'Refresh status',
    '1 台设备': '1 device', '{count} 台设备': '{count} devices', '电子纸设备': 'E-paper device',
    '当前配置预览；设备实际使用的版本和刷新结果见右侧状态。':
        'Configured content; actual content version and display results are reported separately.',
    '最近报到': 'Last contact', '尚未连接': 'Not yet connected', '电量': 'Battery',
    'Wi-Fi 信号': 'Wi-Fi signal', '请求耗时': 'HTTP round trip', '屏幕刷新': 'Display result',
    '已上报内容版本': 'Reported content version', '未知': 'Unknown', '唤醒闹钟': 'Wake-up alarm',
    '已确认': 'Confirmed', '尚未确认': 'Not confirmed', '下次唤醒': 'Next wake-up',
    '远程调试': 'Remote debug', '已开启，下次报到生效': 'Enabled for the next check-in',
    '已关闭，下次报到生效': 'Disabled for the next check-in', '电源计划': 'Power intent',
    '尚未确认实际断电': 'Actual power-off is not confirmed', '设备上报的异常': 'Reported failures',
    '未知阶段': 'Unknown stage', '编辑内容与调试': 'Edit content & debug',
    '查看报告历史': 'View report history', '管理地址': 'Management address',
    '设备状态以报告为依据；“计划休眠”不代表已确认实际断电。':
        'Device state is based on reports; scheduled sleep does not confirm electrical power-off.',
    '返回设备概览': 'Device overview', '报告历史': 'Report history',
    '报告历史 · 保留 30 天': 'Report history · 30 days',
    '每次报到，都有记录。': 'Every check-in tells a story.',
    '以下接收时间均为服务器 UTC 时间。': 'Received timestamps below are server UTC time.',
    '原始报告（JSON，保留设备字段与诊断信息）': 'Raw report (JSON with original fields and diagnostics)',
    '暂未收到报告。': 'No reports received yet.', '较新记录': 'Newer', '较早记录': 'Older',
    '第 {number} 页': 'Page {number}', '登录': 'Sign in',
    '设备状态，一目了然': 'Your device, at a glance', '连接设备，': 'A quiet place',
    '掌握每次更新。': 'to stay connected.',
    '登录后可编辑屏幕内容、设置远程调试，并查看设备状态。':
        'Sign in to edit screen content, set remote debug and view device status.',
    '用户名': 'Username', '密码': 'Password', '编辑屏幕': 'Edit screen',
    '内容与调试设置': 'Content & debug settings', '设置你的电子纸。': 'Make it yours.',
    '内容版本': 'Content version', '屏幕显示内容': 'On the screen',
    '电子纸目前仅支持英文、数字和可打印 ASCII 符号；未使用的信息行请留空。':
        'The screen supports English, numbers and printable ASCII. Leave unused rows empty.',
    '标题': 'Title', '状态': 'Status', '名称': 'Label', '内容': 'Value',
    '最多 {count} 个字符': 'Up to {count} characters',
    '名称 · 最多 20 个字符': 'Label · up to 20 characters',
    '内容 · 最多 48 个字符': 'Value · up to 48 characters',
    '第 {number} 行名称': 'Row {number} label', '第 {number} 行内容': 'Row {number} value',
    '要展示的信息（英文或数字）': 'Information to display (English or numbers)',
    '维护时保持开机': 'Keep awake for maintenance',
    '开启后，设备下次成功报到并完成本轮工作后将保持开机。每轮只获取一次此设置。':
        'When enabled, the next successful check-in keeps the device awake after its work. This setting is fetched once per cycle.',
    '本地 DEBUG 文件始终允许留机。若本轮无法连接服务器，远程设置不会生效；本轮已取得“开启”后，后续上报失败仍会留机。':
        'A local DEBUG file always keeps the device awake. Remote debug requires a successful check-in this cycle; once enabled, later upload failures still allow it to stay awake.',
    '保存设置': 'Save settings', '取消': 'Cancel',
    '屏幕内容必须包含标题、状态和信息行。': 'Display must contain title, status and rows.',
    '{field}须包含 {minimum}–{maximum} 个字符。': '{field} must contain {minimum}–{maximum} characters.',
    '{field}仅支持英文、数字和可打印 ASCII 符号。': '{field} accepts English, numbers and printable ASCII only.',
    '最多可设置六条显示信息。': 'Use at most six display rows.',
    '每条显示信息都需要名称和内容。': 'Each display row needs a label and value.',
    '表单已失效，请刷新页面后重新提交。': 'The form expired. Reload the page and try again.',
    '用户名或密码不正确。': 'The username or password is incorrect.',
    '设置已保存，将在设备下次报到时生效。': 'Saved. Settings apply at the next check-in.',
    '等待设备首次报到': 'Awaiting first check-in', '维护中 · 保持开机': 'Maintenance · staying awake',
    '维护中 · 本地调试': 'Maintenance · local debug',
    '周期未完成 · 未收到最终报告': 'Cycle incomplete · no final report',
    '设备失联 · 超过预计报到时间': 'Missing check-in', '设备上报异常': 'Device reported a fault',
    '计划休眠': 'Scheduled sleep', '下次报到时间未知': 'Next check-in time unknown',
    '设备报到': 'Check-in', '最终报告': 'Final report', '补充结果': 'Outcome',
    '保持开机': 'Stay awake', '准备关机': 'Power off', '采集任务': 'Collection task',
    '电源管理': 'Power management', '时钟同步': 'Time synchronization',
    '服务器连接': 'Server connection', '报告上传': 'Report upload', '异常通知': 'Failure notification',
    '报告队列': 'Report queue', '关机': 'Shutdown', '状态保存': 'State persistence',
    '异常画面': 'Failure display', '运行周期': 'Cycle', '成功': 'Success', '失败': 'Failed',
    '已跳过': 'Skipped', '等待处理': 'Pending', '正在请求': 'Requesting', '已请求': 'Requested',
    '调试留机': 'Debug hold', '失败（退出码 {code}）': 'Failed (exit code {code})',
    '请求无效': 'Invalid request', '请检查填写的内容或链接，然后重试。': 'Check the form or link and try again.',
    '需要身份验证': 'Authentication required', '请登录后重试。': 'Sign in and try again.',
    '无权访问': 'Access denied', '当前账号没有访问此页面的权限。': 'This account cannot access this page.',
    '页面不存在': 'Page not found', '未找到此页面或设备，请返回概览。': 'This page or device was not found. Return to the overview.',
    '不支持此操作': 'Unsupported operation', '请通过页面中的按钮提交操作。': 'Use the buttons on the page to submit an operation.',
    '提交内容过大': 'Request too large', '请减少提交内容后重试。': 'Reduce the submitted content and try again.',
    '服务器错误': 'Server error', '服务暂时无法处理请求，请稍后重试。': 'The server cannot process this request. Try again later.',
    '请求失败': 'Request failed', '请稍后重试。': 'Try again later.',
    '不支持此语言。': 'This language is not supported.',
}


def get_language():
    value = request.cookies.get(LANGUAGE_COOKIE) if has_request_context() else None
    return value if value in LANGUAGES else 'zh'


def t(message, **values):
    translated = ENGLISH.get(message, message) if get_language() == 'en' else message
    return translated.format(**values) if values else translated
