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
    '未知阶段': 'Unknown stage', '编辑内容': 'Edit content', '唤醒计划': 'Wake schedule',
    '设备管理导航': 'Device management navigation', '管理地址': 'Management address',
    '屏幕、连接与下一次唤醒，一眼掌握。': 'Your display, connection and next wake, at a glance.',
    '以设备确认的闹钟为准': 'Based on the confirmed device alarm',
    '设备最近一次连接服务器': 'The most recent server check-in',
    '当前配置': 'CONFIGURED DISPLAY', '本轮运行': 'THIS CYCLE', '设备运行状态': 'Device status',
    '预览显示当前配置，实际刷新结果以设备上报为准。':
        'Preview of the saved content. Actual display results come from device reports.',
    '查看本轮与历史报告': 'Explore cycle reports', '查看缓存详情': 'View cache details',
    '调整计划': 'Edit schedule', '设备下次报到时生效': 'Applies at the next check-in',
    '基本信息': 'THE BASICS', '信息行': 'DISPLAY ROWS', '留给重要的信息。': 'Room for what matters.',
    '最多六行，空白行不会显示在屏幕上。': 'Up to six rows. Empty rows stay off the screen.',
    '维护选项': 'MAINTENANCE', '调试说明': 'About remote debug',
    '实时预览': 'LIVE PREVIEW', '屏幕上的样子': 'On your screen',
    '预览随输入更新；保存后将在设备下次报到时生效。':
        'The preview follows your edits. Saved changes arrive at the next check-in.',
    '内容与唤醒计划独立保存，可放心调整。': 'Content and wake schedules are saved separately.',
    '让屏幕按时醒来。': 'Wake on your schedule.',
    '按自己的节奏，安排每一天。': 'A daily rhythm that works for you.',
    '每日安排': 'DAILY ROUTINE', '按指定时区，每天重复执行。': 'Repeats every day in your chosen time zone.',
    '每日时间按此时区解释。': 'Daily times use this time zone.',
    '已保存计划预览': 'SAVED SCHEDULE', '下一次': 'Next',
    '保存新计划后更新预览。': 'Save your changes to update this preview.',
    '等待第一份计划': 'Your first schedule starts here',
    '每日规则，离线可用': 'Ready for offline days',
    '设备会缓存每日规则，断网后仍可继续安排唤醒。':
        'The device caches the daily schedule so it can keep arranging wakes offline.',
    '计划如何生效': 'How your schedule works', '时区、缓存与实际闹钟': 'Time zones, offline cache and alarms',
    '时区与夏令时': 'Time zones & daylight saving', '离线缓存': 'Offline cache', '实际闹钟': 'The device alarm',
    '设备状态以报告为依据；“计划休眠”不代表已确认实际断电。':
        'Device state is based on reports; scheduled sleep does not confirm electrical power-off.',
    '返回设备概览': 'Device overview', '报告历史': 'Report history',
    '报告历史 · 保留 30 天': 'Report history · 30 days',
    '每次报到，都有记录。': 'Every check-in tells a story.',
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
    '设备洞察': 'Device insights', '看见每一轮变化。': 'A clearer view of every cycle.',
    '管理历史数据': 'Manage history', '区间概览': 'Range overview',
    '最近电量': 'Latest battery', '区间电量变化': 'Battery change',
    '典型运行耗时': 'Typical cycle time', '以运行周期耗时的中位数计算': 'Median reported cycle duration',
    '等待电量样本': 'Awaiting a battery sample', '等待历史数据': 'Awaiting history',
    '至少两个电量样本后可比较': 'Needs at least two battery samples',
    '正在整理历史数据…': 'Loading history…',
    '历史数据加载失败，请重试。': 'History could not load. Please retry.',
    '重新加载': 'Retry', '这个时间范围还没有报告。': 'No reports in this time range.',
    '此指标暂无有效样本。': 'No valid samples for this metric yet.',
    '移动指针查看样本，或聚焦图表后使用左右方向键。':
        'Hover to inspect samples, or focus the chart and use the arrow keys.',
    '电池电量': 'Battery level', '电池电压': 'Battery voltage', 'PiSugar 温度': 'PiSugar temperature',
    '运行耗时': 'Cycle duration', '异常周期': 'Cycles with faults', '原始报告': 'Raw reports',
    '个百分点': 'pp', '小时': 'hours', '分钟': 'minutes', '秒': 'seconds', '天': 'days',
    '聚合间隔': 'Bucket width', '次': 'cycles', '份': 'reports', '个样本': 'samples',
    '区间内没有上报异常': 'No faults reported in this range',
    '有异常上报的周期': 'cycles with reported faults',
    '数据覆盖': 'Coverage', '等待第一份报告': 'Awaiting the first report',
    '图表已按时间聚合；数值显示有效样本平均值。':
        'Time buckets show the average of valid samples.',
    '每个点代表一次运行周期；缺失指标保留为空。':
        'Each point is a cycle. Missing readings remain gaps.',
    '选中样本': 'Selected sample', '报到与异常分布': 'Check-ins & faults',
    '绿色表示运行周期，橙色表示其中有异常上报的周期。':
        'Green bars show cycles; orange shows cycles with reported faults.',
    '趋势分析': 'TRENDS', '电量与设备状态': 'Battery & device health',
    '图表时间范围': 'Chart time range', '图表指标': 'Chart metric',
    '近 {days} 天': '{days} days', '全部': 'All time', '最新样本': 'Latest sample',
    '电量曲线反映电池百分比变化；充电和电量校准也会影响读数。尚未采集电流，不能换算为实际 mAh 或 Wh 耗电。':
        'The battery curve shows percentage changes, including charging and gauge calibration. Current is not measured, so actual energy use in mAh or Wh is unavailable.',
    '图表需要 JavaScript；下方仍可查看报告明细。':
        'Charts need JavaScript. Report details remain available below.',
    '运行节奏': 'ACTIVITY', '查看图表数据': 'View chart data',
    '数据管理': 'DATA MANAGEMENT', '让记录按你的方式保留。': 'Keep history on your terms.',
    '当前保留策略': 'Current policy', '永久保留': 'Keep forever',
    '保留 {days} 天': 'Keep {days} days', '永久保留（默认）': 'Keep forever (default)',
    '有限保留期会自动清理过期报告。删除前会显示影响范围，并再次确认。设备配置和当前状态会保留。':
        'A limited retention period automatically removes older reports. Review the affected records before confirming. Device settings and current state are preserved.',
    '历史保留期限': 'History retention', '自定义天数': 'Custom period',
    '自定义天数（1–36500）': 'Custom days (1–36500)', '预览并保存': 'Review & save',
    '批量删除某日期之前的记录': 'Delete records before a date', '预览删除': 'Review deletion',
    '逐次记录': 'CYCLE LOG', '同一次运行的报到、最终报告与补充结果合并展示。':
        'Check-in, final report and outcome are grouped into one cycle.',
    '选择本页': 'Select this page', '预览删除选中记录': 'Review selected deletion',
    '选择运行周期 {event_id}': 'Select cycle {event_id}',
    '有异常': 'Fault reported', '已完成': 'Completed', '仅报到': 'Check-in only',
    '查看运行详情': 'Cycle details',
    '设备下一次报到后，这里会显示运行记录和指标。':
        'Cycle records and metrics appear after the next check-in.',
    '确认历史数据操作': 'Confirm history changes', '返回报告历史': 'Report history',
    '操作预览': 'REVIEW CHANGES',
    '这些历史记录将被永久删除，无法恢复。设备配置和当前状态会保留。':
        'These history records will be permanently deleted and cannot be recovered. Device settings and current state are preserved.',
    '此次操作不会删除现有历史报告。': 'This change will not delete existing reports.',
    '确认删除并保存': 'Delete & save policy', '确认永久删除': 'Permanently delete', '确认保存': 'Confirm & save', '确认操作': 'Confirm changes',
    '确认已失效，请返回历史页面重新预览。': 'This confirmation expired. Return to history and review again.',
    '保留天数须为 1–36500 的整数，或选择永久保留。':
        'Choose forever or a whole number of days from 1 to 36500.',
    '请选择有效的删除截止日期。': 'Choose a valid deletion cutoff date.',
    '请选择 1–50 个运行周期。': 'Select between 1 and 50 cycles.',
    '保留设置已保存，过期历史已清理。': 'Retention saved. Expired history has been removed.',
    '所选历史已删除，设备当前状态和配置保持不变。':
        'Selected history deleted. Current device state and settings are preserved.',
    '确认修改保留期限': 'Confirm retention policy',
    '历史将永久保留，不再自动清理。': 'History will be kept forever without automatic cleanup.',
    '历史将保留最近 {days} 天；此前收到的报告将立即清理，之后自动按此期限清理。':
        'Keep the most recent {days} days. Older reports will be deleted now, and future cleanup will follow this policy.',
    '确认按日期删除': 'Confirm deletion by date',
    '删除 {date}（{zone}）当天开始之前收到的报告，不包含当天。':
        'Delete reports received before the start of {date} in {zone}; the selected day is excluded.',
    '确认删除所选周期': 'Confirm selected cycle deletion',
    '将删除所选运行周期的全部历史报告。': 'All historical reports for the selected cycles will be deleted.',
    '此操作无法撤销。设备当前状态、屏幕内容及调试设置会保留。':
        'This cannot be undone. Current device state, screen content and debug settings are preserved.',
    '浏览器本地时区': 'Browser local time zone', '当地时间': 'Local time',
    '需启用 JavaScript 显示本地时间': 'Enable JavaScript to show local time',
    '无法识别浏览器时区': 'Browser time zone unavailable',
    '时间按浏览器识别的当地时区显示。': 'Times follow the local time zone detected by your browser.',
    '按浏览器当地日期的开始时间删除，不包含当天。':
        'Delete before the start of the selected local date; that day is excluded.',
    '实际删除截止时间': 'Deletion cutoff',
    '无法识别浏览器时区，请启用 JavaScript 并刷新页面。':
        'Your browser time zone is unavailable. Enable JavaScript and reload the page.',
    '该日期在所选时区不存在，请选择其他日期。':
        'This date does not exist in the selected time zone. Choose another date.',
    '耗电与续航估算': 'Battery use & runtime estimate', '能耗预测': 'BATTERY OUTLOOK',
    '每天耗多少，还能用多久。': 'Daily drain. Days ahead.',
    '历史趋势估算': 'Estimated from history', '估计日耗电': 'Estimated daily drain',
    '个百分点/天': 'pp/day', '相当于每天减少的电量百分比': 'Battery percentage points used per day',
    '预计剩余续航': 'Estimated runtime left', '计算至 {percent}% 保护电量': 'Until the {percent}% battery reserve',
    '预计到达保护电量': 'Estimated reserve reached', '按当前放电趋势推算': 'At the current discharge trend',
    '最近有效电量样本': 'Latest valid battery sample',
    '根据最近的连续放电趋势估算': 'Based on the latest continuous discharge trend',
    '还需要更多电量样本和观察时间，才能给出可靠估算。':
        'More battery samples and observation time are needed for a useful estimate.',
    '尚未观察到足够的电量下降；可能正在外接供电或电量读数尚未变化。':
        'No clear battery drop yet. External power or an unchanged battery reading may explain this.',
    '电量回升，等待新的稳定放电样本。':
        'Battery level rose. Waiting for a new, steady discharge trend.',
    '最近的电量样本已过时，等待设备再次报到后更新续航。':
        'The latest battery sample is too old. Runtime will update after the next check-in.',
    '采样间隔较长，等待新的连续放电数据。':
        'Samples are too far apart. Waiting for a new continuous discharge trend.',
    '按历史趋势推算，已到保护电量，请检查最新电量。':
        'The trend projects that the battery reserve has been reached. Check the latest battery reading.',
    '近 {window} 天内 {count} 个样本，覆盖 {hours} 小时。':
        '{count} samples over {hours} hours, from the last {window} days.',
    '期间电量下降 {drop} 个百分点。': 'Battery dropped {drop} percentage points over this period.',
    '电压依据：{first} → {last} V，{count} 个有效样本。':
        'Voltage evidence: {first} → {last} V, {count} valid samples.',
    '独立采用最近 7 天的有效电量样本，不随图表范围改变。':
        'Uses valid battery samples from the last 7 days, independently of the chart range.',
    '这是基于历史电量变化的推算，充电、电量校准和使用频率变化都会影响结果。':
        'This projection follows battery history. Charging, gauge calibration and changes in use can affect it.',
    '断网后仍可安排唤醒': 'WAKE SCHEDULE · OFFLINE READY',
    '每日唤醒计划': 'Daily wake schedule',
    '服务器下发未来 24 小时内的全部时间，且至少五次。设备缓存完整每日计划，断网或缓存时间用完后仍可继续计算下一次唤醒。':
        'The server sends every wake time in the next 24 hours, with at least five occurrences. The device caches the complete daily schedule to keep scheduling wakes offline, even after those dates run out.',
    '计划时区（IANA）': 'Schedule time zone (IANA)',
    '每日 HH:MM 按此时区解释；下方具体日期按浏览器当地时区显示。夏令时不存在的时刻跳过，重复时刻只执行第一次。':
        'Daily HH:MM entries use this zone; dates below use your browser’s local zone. Daylight-saving gaps are skipped and repeated times run only on the first occurrence.',
    '每日唤醒时间': 'Daily wake times',
    '使用 24 小时 HH:MM 格式，以换行、空格或逗号分隔。保存此表不会修改屏幕内容或远程调试开关。':
        'Use 24-hour HH:MM times, separated by newlines, spaces or commas. Saving this schedule preserves screen content and the remote debug setting.',
    '保存唤醒计划': 'Save wake schedule', '接下来五次唤醒': 'Next five wakes',
    '每天 {count} 次': '{count} wakes per day',
    '本次可下载 {count} 个未来时间，覆盖至': '{count} future wake times available, through',
    '尚未配置。升级后的设备首次报到时，会以设备当前完整每日时间表初始化；之后以此页面保存的计划为准。':
        'No schedule saved yet. The upgraded device’s first check-in seeds its complete daily schedule; subsequent check-ins use the schedule saved here.',
    '此处展示可下载的计划，并不代表设备已设置 RTC。实际闹钟与缓存状态请查看设备概览；PiSugar 故障仍可能导致无法唤醒。':
        'This preview shows the downloadable plan. Check the device overview for the actual RTC confirmation and cache status. A PiSugar fault can still prevent waking.',
    '唤醒计划与离线缓存': 'Wake schedule & offline cache', '服务器每日计划': 'Server daily schedule',
    '设备本轮计划来源': 'Schedule used this cycle', '服务器下载': 'Downloaded from server',
    '本地缓存': 'Local cache', '设备本地计划': 'Device local schedule', '尚未上报': 'Not reported yet',
    '本轮具体时间覆盖至': 'This cycle’s dated plan through', '本轮具体时间数量': 'Dated occurrences this cycle',
    '计划本地保存': 'Schedule saved locally', '已持久保存': 'Saved to disk',
    '本轮未持久保存': 'Not saved to disk this cycle',
    '具体时间用完后，设备仍会按缓存的完整每日计划计算。RTC 设置是否成功以本轮闹钟确认状态为准。':
        'After the dated occurrences run out, the device still calculates wakes from its cached daily schedule. The current cycle’s RTC confirmation shows whether the alarm was set.',
    '尚未配置': 'Not configured',
    '唤醒时区必须是有效的 IANA 时区。': 'Enter a valid IANA time zone.',
    '请填写 1 至 1440 个每日唤醒时间。': 'Enter 1 to 1440 daily wake times.',
    '唤醒时间须使用 HH:MM 格式，范围为 00:00 至 23:59。': 'Use HH:MM wake times from 00:00 to 23:59.',
    '每日唤醒时间不能重复。': 'Daily wake times must be unique.',
    '唤醒计划无效，请检查时区和时间表。': 'Check the wake schedule’s time zone and times.',
    '唤醒计划已保存，将在设备下次成功报到时下载并缓存。':
        'Wake schedule saved. The device will download and cache it at its next successful check-in.',
}


def get_language():
    value = request.cookies.get(LANGUAGE_COOKIE) if has_request_context() else None
    return value if value in LANGUAGES else 'zh'


def t(message, **values):
    translated = ENGLISH.get(message, message) if get_language() == 'en' else message
    return translated.format(**values) if values else translated
