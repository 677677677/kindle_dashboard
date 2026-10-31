# 项目说明

本目录用于设计与实现 Kindle 个人时间 Dashboard。P0 已进入真实快照闭环：当前接入 KOReader、Windows ActivityWatch 和 Android 自动补传/手动导出，其他来源在接入前必须明确显示状态。

## 目录结构

```text
kindle/
├── dashboard/            # Sites/Next 只读展示层，仅消费 snapshot-v1
│   ├── app/              # 800×600 横版 Kindle 页面和灰阶样式
│   └── lib/              # 前端快照契约与格式化
├── src/time_mirror/
│   ├── core/             # 统计日、区间合并与可支配时间计算
│   │   └── allocation.py # 按秒切分、优先级归类与全天闭合
│   ├── collectors/       # 外部数据源只读适配器
│   ├── outlook_ics.py    # 已发布 ICS 的加密地址、循环展开与最小化读取
│   ├── snapshot.py       # 唯一 snapshot-v1 组装与契约校验入口
│   ├── history.py        # 按日候选汇总、覆盖证据与七日趋势
│   ├── runtime.py        # 仓库外同步、原子落盘和只读 HTTP 服务
│   └── __main__.py       # refresh/serve 命令行入口
├── tests/unit/           # 完全合成的时间核心与采集器测试
├── config.example.json   # 无个人秘密的本地配置示例
├── start-dashboard.*     # Windows 单入口启动脚本
├── stop-dashboard.*      # 精确停止本项目 Dashboard 进程组
├── setup-dashboard-autostart.* # 注册正式 Dashboard 登录自启任务
├── setup-outlook-calendar.* # 验证已发布日历并注册每日同步任务
├── sync-outlook-calendar.ps1 # 每日或手动刷新日历缓存
├── refresh-outlook-calendar.cmd # 临时日程变化后的人工刷新入口
├── start-syncthing.ps1   # Syncthing 身份校验与手动启动入口
├── setup-phone-sync.*    # 一次性安装并直接注册 Syncthing 登录任务
├── import-phone-data.*   # 选择、校验并导入 Android ActivityWatch 导出
├── pyproject.toml        # Python 3.12 包与运行依赖
├── AGENTS.md             # 项目边界、目录职责与演进约束
├── PRD.md                # 产品需求、核心口径、信息架构与 MVP 范围
├── TECHNICAL_PLAN.md     # 本机约束、P0 架构、组件边界与分阶段验收
└── assets/
    └── tasks/
        ├── todo.md       # 当前任务进度、审查结论与下一检查点
        └── lessons.md    # 用户反馈沉淀出的可复用产品与工程规则
```

## 职责边界

- `PRD.md`：产品事实源；技术设计不得绕过其中的时间口径、隐私原则与非目标。
- `TECHNICAL_PLAN.md`：P0 技术事实源；编码前确认部署形态、数据边界和验收顺序。
- `assets/tasks/todo.md`：过程状态源；只记录可验证进度与复盘，不承载产品定义。
- `assets/tasks/lessons.md`：纠偏知识源；相关设计开始前应先复习，避免重复犯错。
- `AGENTS.md`：架构镜像；目录、模块职责或数据流发生变化时同步更新。
- `src/time_mirror/core`：唯一的时间推导层；采集器和前端不得各自重算口径。
- `src/time_mirror/collectors`：只把外部事实转成统一事件，不决定“时间如何度过”。
- `src/time_mirror/collectors/koreader.py`：通过严格主机密钥校验的SSH读取远端SQLite只读备份（包含WAL），临时文件退出清理、本地验证后原子替换；不依赖Kindle的SFTP/SCP。
- `src/time_mirror/collectors/gadgetbridge.py`：只读仓库外华为SQLite睡眠起止标记；校验秒级时间、去重裁剪，不读取无关健康字段，不猜测未知阶段格式。
- `src/time_mirror/collectors/activitywatch.py`：只读SQLite仅投影本机App名，再通过本机API反向相交AFK区间；返回起止和App名，窗口标题不得离开ActivityWatch。
- `src/time_mirror/core/allocation.py`：按全部事件边界扫线，每秒只归一类；睡眠和日常补空，计划汇总和剩余量共用区间并集。
- `src/time_mirror/history.py`：保存仓库外版本化日汇总；未获覆盖证据的非零记录可展示但标记不完整，无证据的零值留空。
- `src/time_mirror/collectors/activitywatch_android.py`：统一读取 aw-sync bucket 或手动导出，过滤系统噪声并输出累计、频次和连续段；按包身份识别横轴 App，只传出规范名和起止，包名不得进入快照。
- `src/time_mirror/collectors/outlook_calendar.py`：定义最小日历事实与可信缓存契约；正文、地点、参会人和会议链接不得落盘。
- `src/time_mirror/outlook_ics.py`：下载 Outlook 已发布 ICS、展开循环日程并最小化字段；发布地址只以 DPAPI 密文保存在仓库外。
- `src/time_mirror/snapshot.py`：唯一快照组装层；统一表达事实、未知、缓存和未接入。
- `src/time_mirror/runtime.py`：只在仓库外处理个人数据；失败时保留上一份可信缓存。
- `dashboard`：只读视图；由服务端读取本机 `snapshot.json` 并输出完整 HTML，不直连设备或数据库，Kindle 不依赖客户端 JavaScript。
- `start-dashboard.ps1`：构建并监督 ActivityWatch、快照服务和生产 Dashboard；不得再以开发服务器承担长期运行。
- `setup-dashboard-autostart.ps1`：唯一正式自启安装入口；注册 `TimeMirror-Dashboard` 登录任务并完成页面健康检查。
- `stop-dashboard.ps1`：只按项目命令行和便携工具路径回收 Time Mirror 进程，供重装、重启和人工停止使用。
- `tests/unit`：保护跨午夜、重叠去重、预留扣除、数据过期与 KOReader schema 边界。

## 当前决策

- Dashboard 同时回答“今天如何度过”和“今天还能如何选择”。
- 主活动与设备参与分轨，禁止并发时间重复计数。
- Kindle 阅读属于一等数据源和设备参与信号。
- 首页采用800×600横版，移除此刻和设备明细，保留Kindle采集；全宽单轴、全天环图和七日趋势均服务端输出。
- 全天七类互斥：睡眠、学习、娱乐、阅读、日常、其他、尚未度过；固定安排是另一个计划维度，不与活动扇区相加。
- 手机与电脑同时活跃优先娱乐；游戏按本地名单（初始`tlou-ii.exe`）；微信读书和Kindle连续至少600秒才具备阅读资格，碎片和不同书籍不得拼接。
- 统计日、入睡规划截止与还能安排统一00:00—24:00。睡眠完全以手环为准，不再补默认时长或扣未来假定睡眠；无记录的过去留其他，寝起卡隐藏。只有可识别课程按日程估计学习，普通忙碌事项不全归上课。
- 七日趋势取最近七个已结束统计日，各自除以全天86400秒；手机含微信读书，阅读沿用活动归类，二者可能重叠。缺失断线、不把当前日或无记录当完整零值。
- 手机与电脑统计需保留 App 层语境，Kindle 阅读需保留书名；窗口标题、URL 与消息正文不进入 Dashboard。
- 事实、推测、未知和计划必须在数据模型与视觉表达中可区分。
- 内部分析分类不得直接照搬为前端文案。
- Kindle 是低频刷新的聚合显示端，不承担原始数据采集与重计算。
- 个人运行数据必须位于 Git 仓库之外；仓库内测试数据必须完全合成。
- P0 接受电脑关机时只显示最后一次成功快照，不引入 VPS 或手机中枢。
- 用餐、通勤等预留从剩余时间扣总量；用户要求在横轴另显示预计位置，用完整配置时长从预计截止点倒推，不写入已发生事实或识别率。
- `snapshot-v1.dayAxis.appSegments`承载手机电脑合并累计前五App的单轴显示段；同名跨设备取并集，显示重叠优先手机，显示段至少10分钟。所有短段仍计总量。前端单条全高呈现，不另分设备排，不自行重算排名或统计。
- 用餐和通勤预留按各自预计窗口的剩余分钟递减；睡前90分钟为22:30—24:00。剩余时间与规划截止同为下一零点，私有配置同步迁移。
- 数据依赖保持单向：`采集器 → 时间核心 → snapshot-v1 → Dashboard`。
- 睡眠独立链路：`Gadgetbridge自动导出 → Syncthing HealthData → 只读采集器 → 时间核心`。分类优先采用可识别睡眠阶段，阶段缺失或不支持时采用完整寝起时段并按自然日裁剪，页面注明可能含清醒；寝起展示按醒来日选入完整session。无记录不补估算，健康目录与ActivityWatch目录不得混用。
- LinkedIn和手机ChatGPT按应用身份归学习，微信读书满足连续十分钟时与普通电脑并发归阅读；若实际游戏同时活跃，娱乐归该游戏。娱乐构成由同一秒级扫线单一App归因，秒数及展示分钟分别与扇区闭合。
- Android 自动链路保持单向：`手机 ActivityWatch → Syncthing 目录 → aw-sync pull → 本机 ActivityWatch → snapshot-v1`；电脑 bucket 不反向推送到手机。
- Syncthing 自启任务直接执行固定版本的二进制并显式传入配置/数据目录，允许电池供电和电源切换后继续运行；已有证书与设备 ID 不得重复生成。
- Dashboard 使用正式 `TimeMirror-Dashboard` 登录任务独立运行；临时验证任务不得作为交付运行入口。
- 横轴必须保留未来日历占用及紧凑日程摘要；移除大设备列表不等于隐藏来源异常。手机今日无事件且同步端点仍在昨天时，标记`awaiting_today_sync`，不显示为正常已同步零使用。
- Dashboard启动时可请求现有日历任务补读；日历任务仍按日期幂等跳过已完成同步，不进入五分钟设备轮询。
- Outlook 日历每天 07:00 最多自动读取一次，错过时由任务计划程序补跑；临时变更通过人工刷新，不进入五分钟设备轮询。
- 学校租户以条件访问 `53003` 拒绝未注册设备上的 Graph 委托登录，因此 P0 使用用户明确发布的 Outlook ICS；持链接者可读，发布地址必须由 DPAPI 保护且支持在 Outlook 中撤销。
- 加密后的 ICS 地址位于 `%USERPROFILE%\.time-mirror`，日历缓存仍位于 `%LOCALAPPDATA%\TimeMirror`；计划任务必须显式传入两处路径，不能依赖不稳定的任务环境变量。
- 日历只表达计划：忙碌、外出和异地工作占用未来可支配时间，暂定与空闲只展示不扣除；重叠日程取并集。

## 变更日志

- 2026-10-28：统一零点日界和规划截止，取消默认睡眠；单轴累计前五App、娱乐构成分支和真实完整寝起显示，LinkedIn归学习。

- 2026-10-27：接入Gadgetbridge睡眠导出和来源状态；无记录保持估算，真实睡眠尚待首晚记录验收。

- 2026-08-27：建立 PRD v0.1、任务记录与项目文档边界。
- 2026-08-28：更新 PRD v0.2，加入 Kindle 阅读、自然化前端语言、路线图与纠偏记录。
- 2026-08-28：更新 PRD v0.3，加入 App/书名明细，并延后最终可视化方案。
- 2026-08-28：建立 P0 技术计划，记录本机环境、本地优先架构、隐私边界与实施顺序。
- 2026-08-28：建立 P0 页面、时间核心和 KOReader 只读适配器，工程正式进入实现阶段。
- 2026-10-21：建立真实 snapshot-v1、仓库外运行时和单入口启动链路；页面移除全部合成活动数据。
- 2026-10-21：接入 ActivityWatch 本机聚合接口；便携运行时位于仓库外，窗口标题在采集端即被排除。
- 2026-10-21：接入 Android ActivityWatch 手动导出，增加一键导入、缓存状态、App 汇总和最长连续段；原始 JSON 保持在仓库外。
- 2026-10-21：Kindle 实机暴露现代 JavaScript 不兼容后，首页改为服务端渲染完整快照，客户端脚本降级为非必要增强。
- 2026-10-22：增加 Syncthing 登录自启、aw-sync 定时拉取和自动 bucket 优先读取；手动 JSON 保留为故障回退。
- 2026-10-22：修复自启身份漂移；任务改为直接启动 Syncthing，并用运行时设备 ID 完成身份验收。
- 2026-10-22：完成最终设备配对与真实数据验收；同步 bucket 新鲜度兼容 `metadata.end`，自动链路正式闭环。
- 2026-10-22：修正剩余时间口径；预留按预计完成时间逐项释放，并增加 90 分钟睡前准备。
- 2026-10-23：Dashboard 切换为生产构建和正式登录自启任务，补充精确停止入口并移除临时验证任务依赖。
- 2026-10-23：接入 Outlook 已发布 ICS，完成 DPAPI 地址保护、每日 07:00 补跑任务、日历占用计算与真实页面验收。
- 2026-10-24：修复 Syncthing 在电池供电时被任务计划程序挂起的问题；自启任务现允许电池运行并补启动。
- 2026-10-24：预留改为按预计窗口的剩余分钟递减；临近午餐、通勤或睡前准备截止时不再扣除完整时长。
- 2026-10-26：原单轴内添加未来匿名预留、过去 rednote/bilibili 前台使用、全天刻度和当前时刻线；最短手机展示段为 10 分钟，原统计口径保持不变。
- 2026-10-27：接入横版全天分配、App活跃区间与仓库外七日汇总；固定窗口按并集扣除，历史未获全天覆盖证据时保持空态。
- 2026-10-27：修复横版遗漏日程显示，补充手机待同步状态和启动时日历补读；手机离线与电脑读取故障分开定位。

