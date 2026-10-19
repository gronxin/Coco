export type DisplayLanguage = 'zh' | 'en';

const storageKey = 'codex-token-analyzer-language';

export function savedLanguage(): DisplayLanguage {
  try { return localStorage.getItem(storageKey) === 'en' ? 'en' : 'zh'; }
  catch { return 'zh'; }
}

const en: Record<string, string> = {
  '总览':'Overview','用量总览':'Usage overview','任务明细':'Task details','用量分析':'Usage analysis',
  'Prompt 记录':'Prompt history','任务队列':'Task queue','运行历史':'Run history','设置':'Settings',
  '任务用量详情':'Task usage details','数据与计费设置':'Data and pricing settings',
  'Token 账本':'Token ledger','本地数据 · 离线分析':'Local data · Offline analysis',
  '各模型 Token 用量占比':'Token usage by model',
  '各模型估算金额占比':'Estimated cost by model',
  '按模型划分的 Token 用量饼图':'Pie chart of token usage by model',
  '按模型划分的 API 等价费用饼图':'Pie chart of API-equivalent cost by model',
  '当前筛选范围没有模型用量':'No model usage for these filters',
  '当前筛选范围没有可计价的模型用量；请先在设置中配置模型单价。':'No priced model usage for these filters. Configure model prices in Settings first.',
  '每一笔用量，都有来源。':'Every token has a source.',
  '调用与历史汇总分开记录':'Calls and historical totals are tracked separately',
  '金额按币种分别展示':'Amounts are shown by currency',
  '配置本地日志来源、模型单价与月度订阅。':'Configure local logs, model prices, and monthly subscriptions.',
  '持续监控额度耗尽的原任务，并按额度顺序执行队列任务。':'Monitor conversations that run out of quota and dispatch queued tasks when quota is available.',
  '查看已完成任务；点开标题查看完整记录。':'Review completed tasks. Open a title to see the full record.',
  '按项目和任务回看你发给 Codex 的每条需求。':'Review the prompts you sent to Codex by project and task.',
  '从总量到每次调用，查看 Token 去向与成本依据。':'See token usage and cost from totals down to individual calls.',
  '浅色':'Light','深色':'Dark','本地同步':'Local sync','重新扫描':'Rescan',
  '加载失败：':'Failed to load:','同步异常：':'Sync error:','重试':'Retry',
  '筛选条件':'Filters','快捷时间范围':'Quick date ranges','今天':'Today','本周':'This week',
  '全部时间':'All time','当前 Token 统计周期':'Current Token period',
  '所有已记录数据':'All recorded data','最早记录':'Earliest record','至今':'Present',
  '本月':'This month','近 30 天':'Last 30 days','自定义':'Custom','开始日期':'Start date',
  '结束日期':'End date','工作目录':'Working directory','全部工作目录':'All working directories',
  'Codex 项目':'Codex project','全部 Codex 项目':'All Codex projects',
  '任务窗口':'Conversation','全部任务窗口':'All conversations','模型':'Model','全部模型':'All models',
  '搜索任务':'Search tasks','窗口名称、任务标题或 ID':'Conversation name, task title, or ID',
  '清除':'Clear','正在读取本地账本…':'Loading local ledger…',
  '← 返回任务列表':'← Back to task list','包含子任务用量':'Include subtask usage',
  '任务队列保存在本机 · 派发需 Codex 额度与网络':'The task queue is stored locally · Dispatch requires Codex quota and network access',
  '已完成任务保存在本机 · 点击标题展开详情':'Completed tasks are stored locally · Open a title for details',
  '运行记录保存在本机 · 点击标题展开详情':'Run records are stored locally · Open a title for details',
  '仅展示用户输入 · 从本机 Codex 日志读取 · 不含 Codex 回复':'User input only · Read from local Codex logs · No Codex replies',
  '本地只读日志分析 · 缺失数据标记为未知 · 不同币种不合并':'Read-only local log analysis · Missing data is marked unknown · Currencies stay separate',
  '未知':'Unknown','未定价':'Unpriced','暂无':'None','未设置':'Not set','未配置':'Not configured',
  '当前筛选范围内没有图表数据':'No chart data for these filters',
  '分页':'Pagination','上一页':'Previous','下一页':'Next','页':'page','条':'items',
  '正在更新任务列表…':'Updating task list…','↓ 降序':'↓ Descending','↑ 升序':'↑ Ascending',
  '每页':'Per page','每页任务数':'Tasks per page','全部任务':'All tasks',
  '排序':'Sort','API 成本':'API cost','暂无调用记录':'No calls yet',
  '当前没有需要提示的用量信号':'No usage signals to report',
  '任务窗口 / 项目':'Conversation / project','首条任务：':'First task:',
  '任务':'Task','项目':'Project','轮次':'Turn','调用':'Calls','事件':'Events',
  '时间':'Time','开始':'Started','完成':'Completed','创建':'Created',
  '状态':'Status','类型':'Type','失败':'Failed','正常':'Normal','操作':'Actions',
  '输入':'Input','输出':'Output','缓存输入':'Cached input','非缓存输入':'Uncached input',
  '缓存复用输入':'Reused cached input','缓存命中':'Cache hit','缓存命中率':'Cache hit rate',
  '缓存节省':'Cache savings','缓存写入':'Cache write','可见输出':'Visible output',
  '推理输出':'Reasoning output','Token 总量':'Total tokens','输入 Token':'Input tokens',
  '输入 tokens':'Input tokens','缓存输入量':'Cached input tokens','非缓存输入量':'Uncached input tokens',
  '输出量（含推理）':'Output tokens (including reasoning)',
  'API 等价费用':'API-equivalent cost','API费用':'API cost','订阅分摊':'Subscription allocation',
  '定价覆盖':'Pricing coverage','最近活动':'Latest activity','最近扫描':'Last scan',
  '平均每轮':'Average per turn','失败后重试信号':'Retry signals after failure',
  '近15分钟':'Last 15 minutes','Token/分钟':'Tokens/minute','预计每小时':'Projected hourly',
  '上下文占用':'Context usage','上下文占用率':'Context usage rate',
  '最近压缩':'Latest compaction','服务层':'Service tier',
  '时间未知用量：':'Usage with unknown time:','全数据集时间未知用量：':'Full-dataset usage with unknown time:',
  '轮次与调用':'Turns and calls','个轮次':'turns','核对与诊断':'Reconciliation and diagnostics',
  '没有发现已知的核对异常。':'No known reconciliation issues found.',
  '查看核对数据':'View reconciliation data','日历热力图':'Calendar heatmap',
  '最近 180 个有记录日期':'Latest 180 days with records',
  '24 小时 × 星期':'Hour × weekday','几点使用 Token 最多':'When do you use the most tokens?',
  '本周 vs 上周同期':'This week vs the same period last week','按星期对齐':'Aligned by weekday',
  '每日用量':'Daily usage','已报告 Token 总量':'Reported token total',
  '模型构成':'By model','项目构成':'By project',
  '高用量任务':'High-usage tasks','高用量调用':'High-usage calls','默认每页 10 条':'10 per page by default',
  '可核对的观察':'Verifiable observations','查看相关任务 →':'View related task →',
  '统计依据':'Statistical basis','查看任务 →':'View task →',
  'Codex 使用额度':'Codex usage quota','剩余':'Remaining','已使用':'Used','更新于':'Updated',
  '刷新失败：':'Refresh failed:','刷新中…':'Refreshing…','刷新额度':'Refresh quota',
  '5 小时额度':'5-hour quota','周额度':'Weekly quota','等待刷新':'Awaiting refresh',
  '上周同期':'Same period last week','实际与线性预测':'Actual and linear forecast',
  '额度上限':'Quota limit',
  '重置时间未知':'Reset time unknown','额度读取正常。':'Quota loaded.',
  '尚未取得 Codex 额度数据。':'No Codex quota data yet.',
  '当前显示最后一次成功快照；本次读取失败。':'Showing the last successful snapshot; the latest read failed.',
  '限流事件':'Rate-limit events','账户级明确日志证据':'Explicit account-level log evidence',
  '当前日期范围内':'In the selected date range','次':'times',
  '当前日期范围内没有明确的限流事件':'No explicit rate-limit events in the selected dates',
  '范围':'Scope','每日':'Daily','每月':'Monthly','指标':'Metric','阈值':'Limit',
  '添加预算':'Add budget','正在读取设置…':'Loading settings…',
  '本地日志目录':'Local log directories','保存目录':'Save directories','文件':'Files',
  '数据库：':'Database:','同步诊断':'Sync diagnostics',
  '数据质量中心':'Data quality','诊断总数':'Total diagnostics','数据版本':'Data version',
  'Codex 周额度与燃尽预测':'Codex weekly quota and usage forecast',
  '最后成功：':'Last successful:','本地预算提醒':'Local budget alerts',
  '已超限':'Over limit','删除':'Delete','尚未设置预算。':'No budgets set.',
  '模型价格':'Model prices','模型 / 生效日':'Model / effective date',
  '币种':'Currency','来源':'Source','编辑':'Edit','新增 / 更新单价':'Add / update price',
  '保存单价':'Save price','清空':'Clear','月度订阅分摊':'Monthly subscription allocation',
  '月份':'Month','金额':'Amount','保存订阅':'Save subscription',
  '模型全名':'Full model name','生效日期':'Effective date',
  '输入单价 / 百万':'Input price / million','缓存输入单价 / 百万':'Cached input price / million',
  '输出单价 / 百万':'Output price / million',
  '推理输出（可选，留空同输出）':'Reasoning output (optional; blank uses output price)',
  '缓存写入（可选）':'Cache write (optional)','价格来源 / 依据':'Price source / basis',
  '同输出':'Same as output',
  '移除':'Remove','次调用':'calls','第':'Page','页 · 共':'pages · total',
  '周':'Week','个':'items','· 已用':'· Used','/ 阈值':'/ limit',
  '每个任务最多添加 10 个附件。':'Up to 10 attachments per task.',
  '正在重启服务…':'Restarting service…','服务已重启':'Service restarted',
  '服务未在 30 秒内恢复，请检查本机服务状态':'Service did not recover within 30 seconds. Check its status.',
  '读取中':'Loading','已处理':'Handled',
  '模型或思考深度已不可用，请修改配置':'The model or reasoning effort is no longer available. Update the settings.',
  '已关闭':'Disabled','任务运行中':'Task running',
  '等待 Codex 桌面派发到原对话':'Waiting for Codex desktop to dispatch to the original conversation',
  '等待原任务额度重置并续接':'Waiting for quota reset to resume the original task',
  '部分任务需要处理，其他任务继续派发':'Some tasks need attention; other tasks continue dispatching',
  '需要人工处理':'Manual attention required','待派发':'Awaiting dispatch','队列为空':'Queue empty',
  '监控原对话；仅在明确额度耗尽后自动续接':'Watching original conversation; resume only after confirmed quota exhaustion',
  '等待向指定对话发送后续任务':'Waiting to send a follow-up to the selected conversation',
  '额度已自动刷新，无需发送唤醒消息':'Quota refreshed automatically; no wake-up message was needed',
  '已通过 Codex 桌面原生接口发送；目标对话轮次已完成。':'Sent through the Codex desktop interface; the target turn completed.',
  '时间未知':'Time unknown','未归属轮次':'Unassigned turn',
  '正在从源日志读取本轮请求…':'Loading this turn from source logs…',
  '流程事件筛选':'Flow event filters','输入（非缓存）':'Input (uncached)',
  '输入（缓存量未知）':'Input (cache amount unknown)',
  '每日 Token 用量趋势图':'Daily token usage trend',
  '输入、缓存输入与输出 Token 分布图':'Input, cached input, and output token distribution',
  '按上海时间排序的逐次调用输入 Token 与缓存命中率双轴曲线':'Per-call input tokens and cache-hit rate by Shanghai time',
  '暂无带时间的模型调用，无法绘制逐次曲线':'No timestamped model calls are available for a per-call chart.',
  '缓存命中与节省':'Cache hits and savings','订阅费用分摊':'Subscription cost allocation',
  '输出（细分未知）':'Output (breakdown unknown)','来源未知':'Unknown source',
  '上下文':'Context','用户请求':'User request','模型输出':'Model output',
  '本轮流程事件构成':'Events in this turn',
  '一':'Mon','二':'Tue','三':'Wed','四':'Thu','五':'Fri','六':'Sat','日':'Sun',
  '暂无可比较的周数据':'No weekly comparison available',
  '任务 Token':'Task tokens','单日 Token':'Daily tokens',
  '当前范围未发现符合规则的离群点':'No matching outliers in this range',
  '每日 Token 日历热力图':'Daily token calendar heatmap',
  '当前筛选范围内没有热力图数据':'No heatmap data for these filters',
  '暂无工具索引':'No tool index yet','未加入 Codex 项目':'Not in a Codex project',
  '工作目录未知':'Unknown working directory','该任务尚无可展示轮次':'No turns available for this task',
  '今日':'Today','不可比':'Not comparable','暂无可用预测。':'No forecast available.',
  '预计本周期内不会用完':'Expected to last through this window',
  '当前使用最后成功快照，预测可能已过时。':'Using the last successful snapshot; the forecast may be stale.',
  '已保存设置。':'Settings saved.','使用最后成功快照':'Using last successful snapshot',
};

Object.assign(en, {
  '任务队列':'Task queue','开启队列':'Enable queue','关闭队列':'Disable queue',
  '开启后持续扫描本机未归档的 Codex 主任务。额度可用就继续执行；额度耗尽后在重置时间尝试续接原任务，若仍受限则每 5 分钟再试。关闭后停止监控和后续派发，已启动的任务继续完成。':'When enabled, the queue scans local, unarchived Codex conversations. It resumes work when quota is available, tries again at the reset time after quota runs out, and retries every five minutes if still limited. Disabling stops monitoring and future dispatch; running tasks can finish.',
  '队列':'Queue','项待执行':'pending','待续接':'Waiting to resume','额度更新':'Quota updated',
  '预计重置':'Estimated reset','关闭额度自动唤醒':'Disable quota wake-up',
  '开启额度自动唤醒':'Enable quota wake-up',
  '持续读取五小时额度；若窗口已自动刷新，直接派发排队任务。仅在到点后额度仍未更新时尝试简短唤醒消息。':'Keep reading the five-hour quota. If it refreshes automatically, dispatch queued tasks. Only send a brief wake-up message if it has not refreshed at the expected time.',
  '上次窗口':'Last window','下次唤醒尝试':'Next wake-up attempt',
  '唤醒发送结果待核对：':'Wake-up delivery needs verification:',
  '；队列仍会独立检查额度并派发已有任务。':'; the queue will still check quota and dispatch existing tasks.',
  '队列默认模型':'Default queue model','默认思考深度':'Default reasoning effort',
  '保存模型设置':'Save model settings','重启本机服务':'Restart local service',
  '由系统服务管理，重启后会自动恢复':'Managed by the system service; it will recover after restart',
  '服务和电脑须保持运行、联网与唤醒。额度是账户窗口比例，不能保证单项任务一定完成。':'The service and computer must stay running, online, and awake. Quota is an account window percentage and does not guarantee any task will finish.',
  '添加任务':'Add task','编辑待执行任务':'Edit pending task','选择 Codex 项目':'Choose Codex project',
  '选择最近工作目录':'Choose a recent working directory',
  '这里列出 Codex 左侧导航中保存的本机项目。选择后，再从该项目的对话列表中指定目标。':'These are the local projects saved in the Codex sidebar. Choose one, then select a target conversation.',
  '服务正在运行任务，需在任务结束后重启以完成更新；当前暂时显示最近使用的工作目录，可能与 Codex 项目不同。':'A task is running. Restart the service after it finishes to complete the update. For now, recently used working directories are shown and may differ from Codex projects.',
  '新指令会作为队列任务执行。':'The new prompt will be dispatched as a queued task.',
  'Codex 项目列表暂时无法读取，请手动输入工作目录。':'Codex projects are unavailable. Enter the working directory manually.',
  'Codex 当前没有可用的本机项目，请手动输入工作目录。':'No local Codex projects are available. Enter a working directory manually.',
  '正在读取项目列表…':'Loading projects…','本机工作目录':'Local working directory',
  '选择上方项目，或输入 /Users/…/project':'Choose a project above or enter /Users/…/project',
  '目标任务窗口':'Target conversation','新建 Codex 对话（仅使用所选目录）':'New Codex conversation (selected directory only)',
  '当前已选对话 ·':'Selected conversation ·','· 已归档（不可续接）':'· Archived (cannot resume)',
  '任务窗口暂时无法读取；可新建对话或稍后重试。':'Conversations are unavailable. Create a new one or try again later.',
  '正在读取该项目的任务窗口…':'Loading conversations for this project…',
  '继承队列默认模型':'Use queue default model','思考深度':'Reasoning effort',
  '该模型默认深度':'Model default effort','继承队列默认深度':'Use queue default effort',
  '本项生效配置：':'Effective settings:','请先选择模型':'Select a model first',
  '任务标题':'Task title','完整任务指令':'Full task prompt','添加图片或文件':'Add images or files',
  '可粘贴图片、拖入文件或点击选择。每项最多 10 个文件，每个不超过 25 MB；派发时会把本机文件路径附在指令后，请 Codex 读取。':'Paste an image, drop files, or choose files. Up to 10 files per task, 25 MB each. Local file paths are included in the dispatched prompt for Codex to read.',
  '当前后台尚未更新，待运行中的任务结束后重启本机服务即可使用附件。':'Attachments will be available after running tasks finish and the local service is restarted.',
  '保存任务':'Save task','加入所选对话的后续任务':'Add follow-up to selected conversation',
  '加入队列':'Add to queue','取消编辑':'Cancel editing',
  '这里只保存你主动录入的任务指令；派发前再次核验模型，不会自动降级。':'Only prompts you enter are saved here. The model is checked again before dispatch and is never silently downgraded.',
  '手动指定一个对话（可选）':'Specify a conversation manually (optional)',
  '自动扫描会处理本机未归档的主任务。需要额外监控某个对话时，可从“任务明细”页面 URL 的 task 参数复制 ID；系统只会在明确检测到额度耗尽后续接。':'Automatic scanning covers unarchived local main conversations. To watch another conversation, copy its ID from the task URL on the Task details page. It will resume only after a confirmed quota exhaustion.',
  'Codex 任务 ID':'Codex task ID','监控这个对话':'Watch this conversation',
  '队列与运行中任务':'Queued and running tasks','待执行':'Pending','监控已有对话':'Watching existing conversation',
  '已认领':'Claimed','运行中':'Running','等待额度后续接':'Waiting for quota to resume',
  '正在续接':'Resuming','已交给 Codex':'Sent to Codex','等待原对话派发':'Waiting to dispatch to original conversation',
  '桌面派发中':'Desktop dispatching','已排入原对话':'Queued in original conversation',
  '原对话执行中':'Original conversation running','已完成':'Completed','需要处理':'Needs attention',
  '· 手动监控':'· Manually watched','· 自动发现的原任务':'· Automatically discovered',
  '· 接续指定对话':'· Follow-up to selected conversation',
  '未选模型':'No model selected','默认深度':'Default effort',
  '下次尝试续接：':'Next resume attempt:','；每次检查都会重新读取额度':'; quota is rechecked each time',
  '下次自动重试：':'Next automatic retry:',
  '自动续接尝试':'Automatic resume attempts','次（含未成功的尝试）':'times (including unsuccessful attempts)',
  '目标对话 ID':'Target conversation ID','查看完整任务指令':'View full task prompt',
  '改为新对话':'Use a new conversation','上移':'Move up','下移':'Move down',
  '修改模型':'Change model','核对后重试':'Verify and retry',
  '当前没有待执行或运行中的任务。已完成任务可在“运行历史”页面查看。':'There are no pending or running tasks. See completed tasks in Run history.',
  '监控的原对话':'Watched original conversations',
  '这些对话目前没有排队任务。保留监控记录是为了在原对话明确因额度耗尽中断时自动续接；对话已完成也不会从监控列表消失。':'These conversations have no queued tasks. They remain on the watch list so work can resume after a confirmed quota interruption; completed conversations also remain listed.',
  '监控中 · #':'Watching · #','Codex 对话 ID：':'Codex conversation ID:',
  '查看监控记录':'View monitoring record','历史自动续接尝试':'Historical resume attempts',
  '次，包含未成功建立新轮次的尝试；这不是已完成次数。':'times, including attempts that did not start a new turn; this is not a completion count.',
  '停止监控':'Stop watching',
  '已完成任务':'Completed tasks',
  '已完成与已停止的任务按结束时间从近到远排列。点击标题查看任务指令和运行详情。':'Completed and stopped tasks are sorted by end time, newest first. Open a title for the prompt and run details.',
  '· 结束':'· Ended','暂无历史任务。':'No run history yet.',
  '按完成时间从近到远排列。点击标题查看任务指令和运行详情。':'Sorted by completion time, newest first. Open a title to view the prompt and run details.',
  '项':'items','正在读取运行历史…':'Loading run history…','新建对话':'New conversation',
  '接续指定对话':'Follow-up to selected conversation','自动发现':'Automatically discovered',
  '手动监控':'Manually watched','工作目录：':'Working directory:',
  '· 开始':'· Started','· 完成':'· Completed','轮次 ID：':'Turn ID:',
  '删除记录':'Delete record','暂无已完成任务。':'No completed tasks yet.',
  '我的 Prompt':'My prompts',
  '按项目和任务查看你发给 Codex 的输入。只读取本机用户主任务与已派发队列任务，不展示 Codex 回复。':'Review what you sent to Codex by project and task. Only local main conversations and dispatched queue tasks are read; Codex replies are not shown.',
  '当前开启隐私模式，Prompt 原文不会在此页展示。':'Privacy mode is enabled. Prompt text is hidden on this page.',
  '文本字符':'Text characters','读取 Prompt 失败：':'Failed to read prompts:',
  '正在读取本机 Prompt…':'Loading local prompts…','暂无可读取的用户 Prompt。':'No user prompts available.',
  '个任务 ·':'tasks ·','条 Prompt':'prompts','查找任务或 Prompt':'Find a task or prompt',
  '标题、任务 ID、Prompt 内容':'Title, task ID, or prompt text','条 ·':'items ·',
  '字符':'characters','没有匹配的任务。':'No matching tasks.',
  '条 Prompt ·':'prompts ·','· 队列代发':'· Sent by queue',
  '这条消息只有非文本附件，本地日志没有可展示的文字。':'This message contains only non-text attachments; there is no text to show in local logs.',
  '选择一个任务查看 Prompt。':'Select a task to view its prompts.',
  '本轮工作':'Work in this turn','消耗解释':'Usage explanation',
  '该轮没有足够的明细形成变化判断。':'There is not enough detail to assess changes in this turn.',
  '具体流程仅在打开时从本机源日志读取，不写入分析数据库。':'Detailed steps are read from local source logs only when opened and are not stored in the analysis database.',
  '事件类型':'Event type','全部事件':'All events','执行规划':'Planning','模型推理':'Model reasoning',
  '工具调用':'Tool calls','读取记忆':'Memory reads','协作代理':'Collaborating agents',
  '模型交互':'Model interactions','上下文压缩':'Context compaction',
  '执行状态':'Execution status','全部状态':'All statuses','仅失败':'Failures only',
  '仅成功':'Successes only','筛选中…':'Filtering…','应用流程筛选':'Apply event filters',
  '正在按需读取首批流程事件与调用…':'Loading the first batch of events and calls…',
  '模型调用明细':'Model call details',
  '默认收起，展开后可逐条查看 Token 分项与日志证据':'Collapsed by default. Open to inspect token components and log evidence for each call.',
});

Object.assign(en, {
  '已报告总量':'Reported total','· 分项不完整':'· Incomplete breakdown',
  '已报告总量 · 含历史汇总':'Reported total · Includes historical totals',
  '输入 Token 与输出 Token 的合计。缓存输入属于输入的细分，推理 Token 属于输出的细分，均不会再次相加。':'Input plus output tokens. Cached input is part of input, and reasoning tokens are part of output; neither is counted twice.',
  '模型内部推理使用的输出 Token。它包含在输出总量中，此处单列用于观察构成，不会重复计入总量或费用。':'Output tokens used for model reasoning. They are already included in total output and are shown separately only to explain the breakdown.',
  '缓存输入 ÷ 输入 Token。缓存复用不会减少账本中的 Token 数；节省金额按缓存复用量 ×（普通输入单价 − 缓存输入单价）计算。':'Cached input divided by input tokens. Cache reuse does not reduce recorded tokens; savings use reused tokens times the difference between regular and cached input prices.',
  '复用':'Reused','· 节省':'· Saved','· 覆盖':'· Coverage',
  'Standard API 等价估算':'Standard API-equivalent estimate',
  '按模型、调用日期及已配置单价估算。推理费率留空时继承输出费率；推理作为输出子项只计价一次。':'Estimated from model, call date, and configured prices. When reasoning has no separate price, it uses the output price. Reasoning is charged once as part of output.',
  '月订阅费按照当月本机已记录 Token 比例分配。':'The monthly subscription fee is allocated in proportion to locally recorded tokens for that month.',
  '本月分摊随全月用量变化':'This month’s allocation changes with total monthly usage',
  '未分摊':'Unallocated','API 等价费用构成':'API-equivalent cost breakdown',
  '按调用日期匹配费率':'Rates matched by call date',
  '推理 Token 是输出的子项；未设置独立推理费率时沿用输出费率，不会重复计费。缓存节省按普通输入与缓存输入的价差估算。':'Reasoning tokens are part of output. Without a separate reasoning rate, the output rate applies; costs are not duplicated. Cache savings use the difference between regular and cached input rates.',
  '缓存复用':'Cache reuse','缓存价差节省':'Cache price savings',
  '节省计价覆盖':'Savings pricing coverage','显示列':'Visible columns',
  '当前范围没有可可靠计价的调用':'No calls can be reliably priced in this range',
  '历史汇总':'Historical total','模型调用':'Model call','模型未知':'Unknown model',
  '什么是历史汇总？':'What is a historical total?','什么是模型调用？':'What is a model call?',
  '这不是一次确定的模型调用，而是累计记录与可识别单次调用之间的差额。它可能覆盖一次或多次缺失调用，因此无法还原具体工作。':'This is the difference between a cumulative record and identifiable individual calls. It may cover one or more missing calls, so the exact work cannot be reconstructed.',
  '总量仍计入任务和全局统计，但不计入模型调用次数。输入、缓存输入、输出和推理输出来自累计分项差额；缺失项保持未知。':'The total still counts toward task and overall usage, but not model call count. Token components come from differences between cumulative records; missing values remain unknown.',
  '时间是形成该差额的累计记录时间。费用只有在当时模型价格可靠匹配时才计算。':'The timestamp belongs to the cumulative record that produced the difference. Cost is calculated only when the historical model price can be matched reliably.',
  '日志中带请求身份和单次 usage 的模型请求，可核对输入、缓存输入、输出、推理输出、时间和来源。':'A model request with a request identity and per-call usage in the log. Input, cached input, output, reasoning output, time, and source can be checked.',
  '输入 Token 中由提示缓存复用的部分，已经包含在输入中，不能再加到总量。':'Prompt-cache reuse within input tokens. It is already included in input and must not be added again.',
  '输出 Token 中用于内部推理的细分，已经包含在输出中，不能再次加入总量。':'Reasoning tokens within output. They are already included in output and must not be added again.',
  '证据：':'Evidence:','记录 ID：':'Record ID:',
  '这是累计记录差额形成的历史汇总，不能据此还原为一次模型调用。':'This historical total comes from a difference between cumulative records and cannot be treated as one model call.',
  '查看执行全流程':'View full execution flow',
  '点击展开，独立查看规划、推理、记忆、工具、协作代理、模型交互与结果':'Open to inspect planning, reasoning, memory, tools, agents, model interactions, and results.',
  '个事件':'events',
  '模型输入量包含系统指令、历史对话、记忆和工具结果等上下文；当前日志未提供各部分的 Token 拆分，也不提供内部推理原文。':'Model input includes system instructions, conversation history, memory, tool results, and other context. Current logs do not break out their token counts or include private reasoning text.',
  '结果：':'Result:',
  '识别依据：工具调用访问了 Codex 记忆目录；仅展示脱敏后的文件名和操作，不展示完整命令及文件内容。':'Detected because a tool accessed the Codex memory directory. Only redacted filenames and actions are shown, not full commands or file contents.',
  '协作消息正文与代理产出不在此展开；这里只展示动作、目标、状态和耗时。':'Agent messages and output are not shown here. Only actions, targets, status, and duration appear.',
  '其中推理':'Reasoning within output','合计':'Total','源日志第':'Source log line',
  '行':'line','该轮次没有可展示的流程事件。':'No flow events to show for this turn.',
  '当前明细覆盖':'Current detail coverage','成本与用量异常':'Cost and usage anomalies',
  '中位数 + MAD 规则':'Median + MAD rule',
  '至少 5 个同类样本，同时达到离群阈值且高于中位数 2 倍才标记；这是排查线索，不代表任务存在问题。':'Flagged only with at least five comparable samples, an outlier score above the threshold, and more than twice the median. This is a clue, not proof of a problem.',
  '中位数的':'of median','倍':'times','任务过程指标':'Task process metrics',
  '前 50 个高用量任务':'Top 50 high-usage tasks',
  '重试信号指工具失败后紧接着再次调用同类工具，仅用于定位可能绕路的过程。':'A retry signal is a same-type tool call immediately after a failure. It only helps locate possible detours.',
  '平均每轮 Token':'Average tokens per turn','工具调用':'Tool calls','重试信号':'Retry signals',
  '模型横向用量':'Cross-model usage','仅描述账本统计':'Ledger statistics only',
  '单位任务 Token 和费用受任务难度、上下文及切换模型影响，不能解释为模型质量或真实完成效率。':'Tokens and cost per task depend on difficulty, context, and model switching; they do not measure model quality or real completion efficiency.',
  '任务数':'Tasks','调用数':'Calls','Token/任务':'Tokens/task','Token/调用':'Tokens/call',
  '调用/任务':'Calls/task','API费用/任务':'API cost/task',
  'Codex 工具排行':'Codex tool ranking',
  '关联 Token 仅表示工具之后记录的模型用量，不代表工具造成了这些消耗。':'Associated tokens are model usage recorded after a tool call; they do not show that the tool caused the usage.',
  '工具类别':'Tool category','失败数':'Failures','关联 Token':'Associated tokens',
  '工具调用之后记录的模型调用 Token，仅表示时间上的关联，不代表工具造成了这些消耗。':'Model-call tokens recorded after tool use indicate only a timing relationship, not causation.',
  '子任务用量':'Subtask usage','子任务是 Codex 创建的独立内部线程，不是对话轮次':'Subtasks are separate internal Codex threads, not conversation turns',
  '个 ·':'items ·',
  '展开后可查看子代理、分叉和权限审查任务。日志没有记录父轮次时会明确显示未知。':'Open to view agents, forks, and permission review tasks. Missing parent-turn data is explicitly marked unknown.',
  '所属轮次':'Parent turn','子代理':'Subagent','分叉任务':'Forked task',
  '权限审批':'Permission review','其他内部任务':'Other internal task','内部任务':'Internal task',
  '已归档':'Archived','可查看':'Viewable',
  '日期按 Asia/Shanghai · 每 5 秒增量扫描并在数据版本变化时自动刷新 · Standard API 等价估算':'Dates in Asia/Shanghai · Incremental scan every 5 seconds · Auto-refresh on data changes · Standard API-equivalent estimate',
  '周报 Markdown':'Weekly Markdown report','报告 JSON':'JSON report',
  '最近一次模型输入 Token ÷ 日志记录的模型上下文窗口。缺少窗口数据时显示未知。':'Latest model input tokens divided by the model context window in logs. Unknown if the window is missing.',
  '· 已被当前日期筛选排除，不进入每日分桶、定价或订阅分摊。':'· Excluded by the selected date range and from daily buckets, pricing, and subscription allocation.',
  '· 不进入每日分桶；日期确认前不计入定价与订阅分摊。':'· Excluded from daily buckets, pricing, and subscription allocation until its date is known.',
  '当前单价回算':'At current prices','· 较上期':'· vs previous period',
  '· 缓存命中变化':'· Cache-hit change','观察来自日志统计，不代表已经确定原因。':'Observations come from log statistics and do not establish a cause.',
  '来自本机 Codex 账户，与 Token 日志统计和 API 等价费用分开。':'From the local Codex account, separate from token-log statistics and API-equivalent costs.',
  '日均消耗约':'Average daily use about','个百分点。':'percentage points.',
  '限流是账户级状态，项目和模型筛选不用于归属。':'Rate limiting is account-wide; project and model filters do not assign ownership.',
  '可合并多个本机可读的 Codex 数据目录；每个目录读取 sessions 与 archived_sessions，不修改源日志。':'Multiple readable local Codex data directories can be combined. Sessions and archived sessions are read without changing source logs.',
  'Codex 数据根目录（每行一个）':'Codex data roots (one per line)',
  '来自本机 codex app-server，与 API 等价费用分开显示。':'From the local Codex app-server, separate from API-equivalent costs.',
  '仅在页面保存和展示。':'Saved and shown only on this page.',
  '每百万 Token 计价，精确匹配模型名。未配置模型显示为未定价。':'Prices are per million tokens and matched by exact model name. Unconfigured models appear as unpriced.',
  '按该月完整本地账本 Token 比例分摊，筛选不改变分母。本月为暂估。':'Allocated by the full local ledger’s token share for that month. Filters do not change the denominator. This month is provisional.',
});

const dynamic: Array<[RegExp, (...parts:string[])=>string]> = [
  [/^第 (\d+) \/ (\d+) 页 · 共 (.+) 条$/, (_,$1,$2,$3)=>`Page ${$1} / ${$2} · ${$3} items`],
  [/^第 (\d+) \/ (\d+) 页$/, (_,$1,$2)=>`Page ${$1} / ${$2}`],
  [/^创建 (.+)$/, (_,$1)=>`Created ${$1}`],
  [/^(\d+) 项$/, (_,$1)=>`${$1} items`],
  [/^(\d+) 条$/, (_,$1)=>`${$1} items`],
  [/^(\d+) 字符$/, (_,$1)=>`${$1} characters`],
  [/^(\d+) 条 Prompt$/, (_,$1)=>`${$1} prompts`],
  [/^同步中 (.+)$/, (_,$1)=>`Syncing ${$1}`],
  [/^(.+) 超过 25 MB，未添加。$/, (_,$1)=>`${$1} exceeds 25 MB and was not added.`],
  [/^(.+) 上传失败：(.+)$/, (_,$1,$2)=>`Failed to upload ${$1}: ${$2}`],
  [/^移除 (.+)$/, (_,$1)=>`Remove ${$1}`],
  [/^解释：(.+)$/, (_,$1)=>`Explanation: ${en[$1]||$1}`],
  [/^切换到浅色主题$/, ()=>`Switch to light theme`],
  [/^切换到深色主题$/, ()=>`Switch to dark theme`],
  [/^后续指令将发送到所选对话 (.+)；遇到桌面写入锁时，会由 Codex 桌面派发桥接到同一对话 ID。$/, (_,$1)=>`The next prompt will be sent to conversation ${$1}. If the desktop writer lock is held, the Codex desktop bridge will dispatch to the same conversation ID.`],
  [/^已列出该项目的 (\d+) 个任务窗口；已归档任务仍显示但不可续接。请选择任务，或保持“新建”创建独立任务。$/, (_,$1)=>`${$1} conversations found for this project. Archived conversations are shown but cannot be resumed. Select one or keep “New” to create a separate task.`],
  [/^(\d+) 天 (\d+) 小时后重置$/, (_,$1,$2)=>`Resets in ${$1} days ${$2} hours`],
  [/^(\d+) 小时 (\d+) 分后重置$/, (_,$1,$2)=>`Resets in ${$1} hours ${$2} minutes`],
  [/^(\d+) 小时额度$/, (_,$1)=>`${$1}-hour quota`],
  [/^按当前速度约还能使用 (.+) 天，预计 (.+) 用完。$/, (_,$1,$2)=>`At the current pace, about ${$1} days remain; estimated exhaustion: ${$2}.`],
  [/^按当前速度预计不会在本周期内用完；(.+) 重置。$/, (_,$1)=>`At the current pace, quota should last through this window; resets ${$1}.`],
  [/^预计 (.+) 用完$/, (_,$1)=>`Estimated exhaustion: ${$1}`],
  [/^任务 ([0-9a-f]{8})$/, (_,$1)=>`Task ${$1}`],
  [/^周([一二三四五六日])$/, (_,$1)=>({一:'Mon',二:'Tue',三:'Wed',四:'Thu',五:'Fri',六:'Sat',日:'Sun'} as Record<string,string>)[$1]],
];

function english(value:string):string {
  const core = value.trim();
  const translated = en[core] || dynamic.map(([pattern, format])=>{
    const match = core.match(pattern);
    return match ? format(...match) : '';
  }).find(Boolean);
  return translated ? value.replace(core, translated) : value;
}

export function localizedText(value:string, language:DisplayLanguage):string {
  return language==='en' ? english(value) : value;
}

type Original = {source:string;translated:string};
const originalText = new WeakMap<Text, Original>();
const originalAttributes = new WeakMap<Element, Map<string, Original>>();
const ignored = 'pre, code, textarea, [translate="no"], .queue-item h3, .prompt-picker strong, .prompt-conversation h2, .task-link, .task-heading h2';

function excluded(element:Element|null):boolean {
  return Boolean(element?.closest(ignored));
}

export function displayLanguage(language:DisplayLanguage):()=>void {
  try { localStorage.setItem(storageKey, language); } catch { /* Private browsing can disable storage. */ }
  document.documentElement.lang = language === 'en' ? 'en' : 'zh-CN';
  document.title = 'Coco Codex管家';

  function textNode(node:Text){
    if(excluded(node.parentElement))return;
    const current=node.nodeValue||'';
    const prior=originalText.get(node);
    if(language==='zh'){
      if(prior&&current===prior.translated)node.nodeValue=prior.source;
      originalText.delete(node);
      return;
    }
    if(prior&&current===prior.translated)return;
    const translated=english(current);
    if(translated!==current){originalText.set(node,{source:current,translated});node.nodeValue=translated}
  }

  function attributes(element:Element){
    if(excluded(element))return;
    const originals=originalAttributes.get(element)||new Map<string, Original>();
    for(const name of ['placeholder','title','aria-label']){
      const current=element.getAttribute(name);
      if(current===null)continue;
      const prior=originals.get(name);
      if(language==='zh'){
        if(prior&&current===prior.translated)element.setAttribute(name,prior.source);
        originals.delete(name);
      }else if(!prior||current!==prior.translated){
        const translated=english(current);
        if(translated!==current){originals.set(name,{source:current,translated});element.setAttribute(name,translated)}
      }
    }
    originalAttributes.set(element,originals);
  }

  function walk(node:Node){
    if(node.nodeType===Node.TEXT_NODE){textNode(node as Text);return}
    if(node.nodeType!==Node.ELEMENT_NODE)return;
    const element=node as Element;
    if(excluded(element))return;
    attributes(element);
    for(const child of Array.from(element.childNodes))walk(child);
  }

  walk(document.body);
  if(language==='zh')return ()=>{};
  const observer=new MutationObserver(changes=>{
    for(const change of changes){
      if(change.type==='characterData')walk(change.target);
      else if(change.type==='attributes')attributes(change.target as Element);
      else for(const node of Array.from(change.addedNodes))walk(node);
    }
  });
  observer.observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['placeholder','title','aria-label']});
  return ()=>observer.disconnect();
}
