# Kindle 个人时间镜子 P0 技术计划

> 版本：v0.2<br>
> 更新：2026-10-21<br>
> 状态：真实 KOReader 快照闭环已完成，其他数据源按阶段接入

## 1. 已确认环境

### 1.1 开发电脑

- 系统：Windows 11 家庭中文版 25H2，构建 26200.9168，64 位；
- 设备：ASUS TUF Gaming A15 FA507RM；
- 处理器：AMD Ryzen 7 6800H；
- 内存：约 16 GB；
- 可用运行时：Python 3.12、Node.js 24、Git、OpenSSH；
- 使用特征：电脑不会每天长期开机，没有 NAS、树莓派等常在线设备。

项目固定使用 `py -3.12` 创建虚拟环境，不使用 PATH 中 MSYS2 提供的 Python 3.14，降低第三方包兼容风险。

### 1.2 Kindle

- Kindle 10，固件 5.16.8，已越狱；
- 已安装 KOReader；
- 支持 Wi-Fi SSH；
- Kindle 浏览器可以访问局域网网页；
- USB 只能充电，电脑完全无法识别，不能作为部署或恢复通道；
- 主要使用 KOReader 阅读本地导入书籍。

KOReader 的阅读统计插件将数据保存在设置目录下的 `statistics.sqlite3`，数据库包含书名、开始时间和阅读持续时间等字段，可以作为 P0 的只读数据源。实现依据：[KOReader statistics 插件源码](https://github.com/koreader/koreader/blob/master/plugins/statistics.koplugin/main.lua)。

### 1.3 手机、日历与身体数据

- 手机允许安装 APK、授予应用使用情况权限和后台运行权限；
- Android `UsageStatsManager` 可以按包名查询应用使用统计和前台事件，但需要用户授予 `PACKAGE_USAGE_STATS` 特殊权限。实现依据：[Android UsageStatsManager](https://developer.android.com/reference/android/app/usage/UsageStatsManager)；
- 日历主要使用 Outlook；账号实际属于 组织租户，Graph 委托登录被条件访问策略 `53003` 拒绝；
- P0 改用用户在 Outlook Web 明确发布的 ICS，只保留标题、开始/结束、忙闲状态与全天标记；地点、正文、参会人和会议链接在采集边界丢弃；
- 华为手环 9 的睡眠、运动接入置于 P0 尾部单独验证，不阻塞主链路。

## 2. 个人默认时间配置

首版使用以下默认值，并保持可在本地配置文件中修改：

```text
计划入睡时间       01:00
午餐预留           60 分钟（预计窗口截至 14:00）
晚餐预留           90 分钟（预计窗口截至 21:00）
去程通勤           30 分钟（预计窗口截至 10:00）
返程通勤           30 分钟（预计窗口截至 20:00）
睡前准备           90 分钟（预计窗口截至次日 01:00）
默认运动预留       0 分钟
默认休息预留       0 分钟
时区               Asia/Shanghai
```

用餐与通勤的完成时间是保守估算，只决定从该预计窗口中还有多少未来分钟需要从剩余总量扣除，不生成虚构的事实时间线；后续允许根据当天日历和实际记录覆盖。例如 13:59 时，预计截至 14:00 的 60 分钟午餐只扣 1 分钟。

2026-10-26 展示扩展：`core.reserve_window` 从完整 `reservePlan` 生成预计窗口，快照裁至当前时刻之后；`pending_reserves` 继续用于剩余总量，两者不可混用。Android 采集器按包身份输出不含包名的 rednote/bilibili 连续段。快照新增可选 `dayAxis`（`reserves`、`phone`、`minimumPhoneMinutes`），在裁剪至统计日和快照时刻后对手机段应用 10 分钟阈值。前端将过去手机使用、未来预留叠加到原单轴，不重算业务总量。

### 2.1 Kindle SSH 当前验证结果

- `<局域网地址>:2222` TCP 连接正常；
- 服务端为 KOReader 自带的 `dropbear_2026.92`；
- 当前仅允许公钥认证；
- 工作区此前临时登录并完成只读盘点，确认设备为 Kindle 5.16.8、KOReader v2026.07.1，可用空间约 1.9 GiB，且 `statistics.sqlite3` 存在；
- 已安装项目专用 RSA-3072 公钥 `kindle_dashboard_rsa`，无人值守仅密钥认证复验通过；
- Kindle 授权文件当前只保留 `kindle-dashboard-rsa@local`，未使用的 Ed25519 授权已移除；
- 私钥位于 Windows 用户 SSH 目录且在 Git 仓库之外，不得复制、提交或发送。

## 3. 核心架构判断

### 3.1 P0 采用本地优先，而非立即购买常在线服务

已确认先在 Windows 电脑运行本地服务，验证数据模型和 Kindle 页面是否真正有价值：

```text
KOReader（Wi-Fi SSH）──→ 仓库外 SQLite 快照 ─┐
ActivityWatch / Android / Outlook ICS ───────────┼→ 时间核心 → snapshot-v1 JSON
                                             ↓
                                  本机只读 HTTP → Kindle 浏览器
```

电脑关机时：

- 不产生电脑使用数据，因此电脑采集不存在漏记；
- 手机和 Outlook 在电脑关机时不刷新；手机保留本地缓冲，Outlook 保留上次可信缓存，电脑下次开机后补传或补读；
- Kindle 保留最后一次成功生成的页面，并明确显示“数据更新至……”；
- KOReader 阅读数据留在 Kindle 数据库中，电脑下次在线时再增量读取；
- 不声称离线页面反映实时状态。

这个阶段不承诺 24 小时实时刷新。它先回答两个问题：采集结果是否可信，Dashboard 是否值得每天看。

### 3.2 何时引入常在线中枢

如果 P0 证明有价值，且用户要求“电脑关机时手机、日历和 Kindle 仍持续同步”，再在以下两条路线中选择：

1. **Android 手机中枢**：不产生云成本，个人数据留在本地；但需要常驻前台服务、固定局域网入口，并验证 HyperOS 杀后台问题；
2. **低配 VPS 中枢**：在线稳定、各设备容易补传；但增加费用、运维、网络可达性和数据出网风险。

P0 本地原型会保持采集器与服务 API 解耦，因此这个选择不阻塞核心时间模型和第一张 Dashboard。它必须在实现 Android 补传与常态化部署前确定。

若选择 VPS，它是一项新的成本与隐私决策，不能被私有 GitHub 替代。届时需要单独确认：

- 是否接受个人聚合数据离开本地网络；
- VPS 地区、费用和网络可达性；
- 传输认证、加密备份和保留周期；
- Kindle 只读快照凭据与其他写入凭据隔离。

## 4. P0 组件边界

### 4.1 `core`

- 定义统一时间事件模型；
- 合并主活动、设备参与、身体状态和计划；
- 计算已度过时间、剩余可支配时间和连续空档；
- 保留来源、推断状态和数据更新时间；
- 不负责采集具体平台数据。

### 4.2 `collector_pc`

- 通过 ActivityWatch 读取当前 Windows 活跃应用和 AFK 信息；
- ActivityWatch 官方 watcher 可记录活跃窗口和应用，并使用 AFK watcher 判断人在不在电脑前。实现依据：[ActivityWatch Watchers](https://docs.activitywatch.net/en/latest/watchers.html)；
- 适配器只保留应用名、起止时间和活跃状态；
- 窗口标题、浏览器 URL、文档路径不得进入统一数据库。

### 4.3 `collector_koreader`

- 通过 Wi-Fi SSH 只读检查 KOReader 版本、统计插件状态和数据库路径；
- 复制数据库快照后在电脑端查询，避免直接锁定 Kindle 正在写入的 SQLite 文件；
- 按书名和时间增量导入阅读区间；
- 首次部署前备份所有将修改的 Kindle 文件；
- USB 无法恢复，因此 P0 不修改系统启动链、不替换系统组件。

### 4.4 `collector_outlook`

- 对上层暴露统一的只读日历事件接口；
- 只读取用户明确发布的 Outlook ICS，不绕过学校租户条件访问；
- 优先只读取开始时间、结束时间、忙闲状态及最小必要标题；
- 会议标题默认只在本地用于展示，不写日志、不进入 Git。

### 4.5 `collector_android`

- 手机 ActivityWatch 将 bucket 写入 SAF 目录，Syncthing 只负责把该目录补传到电脑；
- 每次快照刷新前由 `aw-sync sync --mode pull` 导入本机 ActivityWatch，禁止把电脑 bucket 反向推送到手机；
- 采集器优先查询 `-synced-from-` Android bucket，无自动数据时回退到手动导入的 `aw-watcher-android` JSON；
- 电脑端计算应用名、累计活跃时长、独立会话和最长连续段；
- 桌面、采集器等系统噪声同时从 App 排名和总时长中排除；额外排除项放在仓库外配置的 `ignorePackages`；
- 导出文件超过 `staleAfterMinutes` 后标记为缓存，不伪装成实时数据；
- 手机端仍依赖 ActivityWatch/HyperOS 的后台存活；需验收自启动、后台运行和省电白名单；
- 不采集通知正文、聊天对象、输入内容和精确位置。

### 4.6 `dashboard`

- 使用 Sites/Next 与简单 CSS；服务端读取本机 `snapshot-v1` 并把完整内容写入首屏 HTML；
- 首版提供桌面浏览器预览和 Kindle 局域网页面；
- 页面带生成时间，数据过期时降级为历史快照；
- P0 不冻结饼图、堆叠图等最终视觉形式；
- Kindle 不依赖客户端 JavaScript；灰度 PNG 仅作为 HTML/CSS 也无法兼容时的最后备选。

### 4.7 `storage`

- P0 使用 SQLite；
- 个人运行数据放在 Git 仓库之外；
- 仓库测试只使用人工合成数据；
- 统一事件按来源 ID 幂等写入，允许设备离线后补传；
- 原始数据和派生结果分表，规则变化后可以重新计算。

## 5. 计划目录结构

```text
kindle/
├── dashboard/                     # Sites/Next Kindle 只读页面
│   ├── app/                       # 页面布局与灰阶样式
│   └── lib/snapshot.ts            # snapshot-v1 前端契约与状态格式化
├── src/time_mirror/
│   ├── core/                      # 时间模型、合并规则与可支配时间计算
│   ├── collectors/                # PC、KOReader、Outlook、Android 适配边界
│   ├── snapshot.py                # snapshot-v1 唯一组装入口
│   ├── runtime.py                 # 同步、原子落盘与本机只读 HTTP
│   └── __main__.py                # refresh/serve 命令行入口
├── tests/
│   ├── unit/                 # 时间计算与适配器单元测试
│   └── fixtures/             # 完全合成的数据
├── config.example.json            # 无个人信息的配置模板
├── start-dashboard.cmd            # Windows 双击启动入口
├── start-dashboard.ps1            # 生产构建、ActivityWatch、快照与页面监督
├── stop-dashboard.*               # 精确停止本项目进程组
├── setup-dashboard-autostart.*    # 注册并验证 Dashboard 登录自启任务
├── setup-outlook-calendar.*       # 验证已发布日历并注册每日 07:00 任务
├── sync-outlook-calendar.ps1      # 幂等读取 ICS 日历与刷新快照
├── refresh-outlook-calendar.cmd   # 手动强制刷新日历
├── .gitignore                # 排除秘密、数据库、导出和渲染结果
├── PRD.md
└── TECHNICAL_PLAN.md
```

实际运行数据建议位于仓库外的独立目录：

```text
%LOCALAPPDATA%\TimeMirror\
├── config.json
├── imports/                       # KOReader 只读缓存
│   └── outlook-calendar.json      # 无正文/参会人的日历基本字段缓存
├── web/                           # 原子生成的 snapshot.json/js
├── logs/
├── tools/                         # 可回退的 ActivityWatch 便携版
└── database/                      # 后续统一事件库预留

%USERPROFILE%\.time-mirror\
└── secrets\outlook-ics-url.bin    # 当前 Windows 用户 DPAPI 加密的发布地址
```

## 6. 实施顺序与验收

### 阶段 0：只读验证 Kindle 链路

- 使用用户已有的 SSH 连接方式读取系统和 KOReader 基本信息；
- 确认 `statistics.sqlite3` 存在且包含书籍记录；
- 验证经 Wi-Fi 复制小型测试文件与数据库快照；
- 不修改 Kindle 文件。

验收：能够在电脑端只读得到至少一本书名和一个阅读统计区间。

### 阶段 1：项目骨架与合成数据

- 建立 Python 3.12 项目、配置模板、`.gitignore` 和测试框架；
- 实现统一事件模型、SQLite schema 和可支配时间计算；
- 使用合成数据生成第一张 HTML Dashboard。

验收：测试覆盖重叠日程、跨午夜、固定 01:00 入睡、用餐/通勤扣除和离线旧数据提示。

### 阶段 2：Windows App 与 Outlook

- 接入 ActivityWatch，只保留应用名和活跃时间；
- 使用 Outlook 已发布 ICS 接入未来日程，并在采集边界删除非必要字段；
- 生成第一张包含真实电脑 App 与未来日程的页面。

验收：应用总时长、最长使用段和高频 App 可由原始事件重算，日历重叠不重复扣除。

### 阶段 3：Kindle 展示与 KOReader 增量同步

- 在 Kindle 浏览器验证页面兼容性；
- 增量导入 KOReader 书名和阅读区间；
- 增加离线快照与最后更新时间。

验收：Kindle 上能看到实际书名、阅读时长和数据更新时间；电脑关机后仍保留最后页面。

### 阶段 4：Android App 使用

- 已完成 ActivityWatch Android 手动导出的真实数据桥接；
- 已提供 `import-phone-data.cmd` 文件选择、结构校验、仓库外原子复制与快照刷新；
- 已在电脑端实现 Syncthing 登录自启、aw-sync 只拉取导入和自动 bucket 优先读取；
- 待手机设备配对后验证 Xiaomi HyperOS 后台运行和跨离线区间补传。

当前验收：重复导入同一导出结果稳定，原始包名与 Activity 类名不进入快照。最终验收仍要求断网和电脑关机期间的手机事件在恢复连接后不丢失、不重复。

### 阶段 5：华为手环可行性验证

- 先验证现有华为健康数据能否通过 Health Connect、导出或兼容工具读取；
- 只接入睡眠区间和运动摘要；
- 失败不阻塞 P0 其他功能交付。

## 7. GitHub 决策

### 7.1 为什么可能建立私有仓库

私有 GitHub 用于：

- 异地备份源码，避免电脑损坏导致项目历史丢失；
- 保存可回滚的提交记录；
- 使用 Issue、PR 和 CI 检查代码；
- 未来换电脑或跨设备继续开发。

选择私有而非公开，是因为源码和配置结构可能间接暴露生活规律、设备名称、局域网结构和集成服务。

### 7.2 它不是什么

- GitHub 不是 Dashboard 的运行服务器；
- 私有仓库不是个人数据备份；
- 私有仓库也不是秘密保险箱；
- Kindle、手机和电脑不需要通过 GitHub 同步时间数据。

即使仓库未来意外公开，也不应泄露真实活动、日历、书名、健康数据、IP、令牌和 SSH 密钥。

### 7.3 当前建议

当前继续使用本地 Git。完成项目骨架、运行目录隔离、`.gitignore` 和秘密检查后，再由用户决定是否创建私有 GitHub 仓库。

仓库只保存“程序如何处理数据”；真实数据保存在仓库之外，并使用另一套加密备份方案。

## 8. 已确认的启动决策

P0 接受“电脑关机时显示最后快照”，暂不购买 VPS，也不把手机作为常在线中枢。

Outlook 已确认是 组织租户账号；用户无应用注册权限，且 Graph 登录被条件访问 `53003` 拒绝。P0 不要求管理员介入，改用用户明确发布的 Outlook ICS。

## 9. 当前实现检查点

- 已完成 600×800 灰阶首屏；页面只读取真实 `snapshot-v1`，不再包含合成 App、会议或活动；
- 已实现 01:00 统计日、重叠区间并集、预留扣除、未来时长截断和不确定连续空档表达；
- KOReader 快照在替换前执行 SQLite 完整性与 schema 校验，失败时保留上一份可信缓存；
- 当前真实缓存可读取最近书名；Kindle 离线时明确显示缓存时间，当天无阅读记录时不伪造时长；
- 已部署官方 ActivityWatch 0.14.1 便携版；启动脚本按需拉起本机服务与 watcher，并强制 `--exclude-title`；
- ActivityWatch 只读适配器仅向 `127.0.0.1` 请求 AFK 过滤后的 App 聚合，不查询或落盘窗口标题；首次启动后只从新事件开始累计，不伪造历史电脑时长；
- 手机已通过 ActivityWatch Android 接入；页面显示真实总时长、大块 App、最长连续段和频繁打开次数，自动 bucket 优先，手动导出作为回退；
- Outlook ICS 已完成真实接入、每日任务和页面验收；手环仍显示“未接入”；
- `start-dashboard.cmd` 负责生成生产构建、启动只读运行时和局域网页面；首页由电脑服务端读取快照并输出完整 HTML，电子墨水端无需执行现代 JavaScript。
- `setup-dashboard-autostart.cmd` 注册正式 `TimeMirror-Dashboard` 登录任务；任务不依赖 Codex 会话，异常退出最多自动重启三次。
- `stop-dashboard.cmd` 按固定命令行和便携工具目录停止本项目进程，用于人工停止或重新安装，不影响其他 Node/Python 程序。

## 10. 手机自动补传与手动回退

自动链路：

1. 手机 ActivityWatch 把本地 bucket 写入 `ActivityWatchSync`；
2. Syncthing 在两台设备在线时补传到 `%LOCALAPPDATA%\TimeMirror\ActivityWatchSync`；
3. 电脑登录后由 `TimeMirror-Syncthing` 计划任务自动启动 Syncthing；
4. `TimeMirror-Dashboard` 计划任务构建并启动 ActivityWatch、快照服务和生产 Dashboard；
5. Dashboard 每 5 分钟刷新前执行一次只拉取模式的 `aw-sync`，再从本机 ActivityWatch 读取 Android bucket；
6. 同步失败不删除历史 bucket，也不会覆盖最后一份可信快照。

首次电脑设置依次运行 `setup-phone-sync.cmd` 和 `setup-dashboard-autostart.cmd`。手机添加脚本显示的电脑设备 ID，并将 ActivityWatch 同步文件夹共享给电脑；电脑接受文件夹时路径必须选择 `%LOCALAPPDATA%\TimeMirror\ActivityWatchSync`。

手动回退：

1. 在手机 ActivityWatch 页面导出 `aw-watcher-android` 数据；不要选择仅含解锁次数的 `aw-watcher-android-unlock`。
2. 在电脑双击 `import-phone-data.cmd`，选择导出的 JSON。
3. 脚本校验 bucket 类型后，将原始文件原子复制到 `%LOCALAPPDATA%\TimeMirror\imports\activitywatch-android.json`，并立即刷新 Dashboard 快照。
4. 导入脚本和 Git 只保存处理规则；原始手机记录、包名与 Activity 类名不会进入仓库或前端快照。

电脑关机时手机仍持续在 ActivityWatch 本地记录；两台设备再次在线且 Dashboard 启动后，遗漏数据会自动补传。若 Syncthing 暂时不可用，仍可按上述手动流程更新数据。

## 11. Outlook 日历每日同步

数据链路：

1. 用户在 Outlook Web 发布默认日历并选择“可查看标题和位置”；发布地址属于持有者密钥，可随时在 Outlook 中撤销；
2. 程序只把发布地址以 Windows DPAPI 密文保存到 `%USERPROFILE%\.time-mirror`，不在 Git、日志、任务参数或快照中写入明文链接；
3. Python 下载 ICS 后展开循环日程，只保留匿名 ID、标题、开始/结束、忙闲状态与全天标记；地点、正文、参会人和会议链接在进入缓存前丢弃；
4. 最小缓存原子写入 `%LOCALAPPDATA%\TimeMirror\imports\outlook-calendar.json`，覆盖当前及下一统计日，保证次日 01:00—07:00 仍有数据；
5. `TimeMirror-Outlook-Calendar` 每天 07:00 运行，`StartWhenAvailable` 负责关机补跑，同日重复触发由缓存日期幂等跳过；任务显式传入运行目录与密钥目录，避免计划任务和交互进程的 `AppData` 视图差异；
6. `refresh-outlook-calendar.cmd` 只用于临时日程变化后的人工强制刷新。

计算口径：`busy`、`oof` 和 `workingElsewhere` 从未来可支配时间扣除；`tentative` 与 `free` 只展示。所有占用区间先合并再扣除，日历计划不得写成已经发生的事实。

## 12. 横版全天账本与历史

`App区间／全部手机可见App会话／KOReader页事件 → snapshot设备事实适配 → core.allocation扫线 → allocation → SSR SVG`。电脑窗口标题不进入Python：只读SQLite先投影App名，API返回AFK一侧的相交区间；Rust查询字符串保留Unicode原文，不能把内层设备名写成字面`\\u`转义。SQLite连接显式关闭。

`allocation-v1`包含总秒数、七类秒数及展示百分比、估算秒数、观测与未知量、固定安排并集分钟、未来预留并集分钟。游戏名单和课程关键词位于仓库外`classification`配置，示例见`config.example.json`。阅读先按同一来源连续段、裁至统计日和当前时刻后判断600秒资格，再按重叠优先级分配；KOReader页事件先按书归并，不同书不拼门槛。

`history.py`将最近30天的候选日结果写入运行目录`history/daily-summary.json`，保留算法版本、规则指纹、各来源可用性与更新时间。`history-v1.days`输出最近七个已结束日期，不跳过缺日。两个比例的分母均为86400秒，空值不画线，可信的零才画0。

现有ActivityWatch元数据与手机unlock事件无法自动证明全天完整，默认候选不认证为完整趋势点。未来若有明确的全天证据，可放在同目录`coverage.json`，格式为`{schemaVersion:"history-coverage-v1",days:{"YYYY-MM-DD":{phone:{startAt,endAt,method},computer:{...},koreader:{...}}}}`；时间必须带时区并覆盖该统计日，method只接受`user-confirmed`或`collector-attested`。手机趋势要求手机证据，阅读趋势还要求电脑和Kindle等冲突依赖可用。程序不会自行制造这些认证。

2026-10-29：实际非零历史候选可显示，明确标注可能不完整；没有全天证明的零值仍留空。首次写入前创建历史目录，缓存写入失败仍保留本次可展示结果。手机ChatGPT与LinkedIn归学习，游戏并发仍归娱乐。

历史读取或写入失败不清空已保留候选；规则指纹变化不沿用旧口径。历史数据同样留在Git仓库外。正式界面缺失时显示空态，不绘制预览中的演示曲线。

## 13. Gadgetbridge睡眠导出

使用独立receiveonly目录`%LOCALAPPDATA%\TimeMirror\HealthData`接收手机自动导出的`Gadgetbridge.db`。私有配置`gadgetbridge.enabled`和`databasePath`由正式任务身份最小合并，保留现有同步身份及其它秘密。无需Health Connect或Google Drive。

采集器以SQLite只读模式校验完整性和表结构，仅投影`HUAWEI_ACTIVITY_SAMPLE`的睡眠起止、设备和用户身份。SOURCE为10或13，RAW_KIND为6、7、0x5656或0x5659；起止使用Unix秒，只取起始标记，合并重叠并裁至统计日。未来或无效时间、身份歧义明确失败。新版阶段表有记录而当前窗口没有可读旧式区间时，返回不支持，禁止推测采样间隔或静默零值。

`sources.health`记录导出时间、最新睡眠事件、错误原因及是否仍含夜间估算；空睡眠记录为`cached/no_sleep_record`，450分钟估算保留。夜间实测区间与既定夜间窗口相交时替代该日夜间估算；仅白天小睡不清除夜间估算。设备活动优先于实测睡眠，实测睡眠优先于课程计划；已实测醒来后释放预计睡眠预留，分类和剩余计算共用同一窗口。首晚是否成功同步仍须真实记录验收。

## 14. 来源可见性与启动补读

KOReader取库改为SSH执行远端`sqlite3 -readonly ... .backup`，读取唯一临时文件的二进制流后清理；直接拷贝主库可能漏掉WAL提交，现代SCP又依赖Kindle没有的SFTP。`kindle.hostKeyAlias`可将变更后的地址绑定既有可信主机密钥，保持StrictHostKeyChecking，不自动接受未知设备。取库失败继续保留上一份已验证快照。

### 2026-10-28 当前口径（替代先前01:00日界和默认睡眠）

统计日、规划入睡截止与还能安排统一零点。`classification.sleepMinutes=0`，没有手环数据时不补450分钟，不从未来扣睡眠；90分钟睡前预留迁移至22:30—24:00。设备、日历查询、历史候选和日期均按自然日重算，算法版本及dayBoundary指纹阻止混用旧结果。

`allocation.entertainmentBreakdown`由分类扫线归因，手机与电脑重叠不重复，LinkedIn计学习（实际游戏并发归游戏）。`dayAxis.appSegments`先按同名跨设备区间并集累计选前五，再筛连续十分钟；单轴冲突优先手机，短段不丢统计。`sleepSummary`仅来自华为完整睡眠统计表TIMESTAMP/WAKEUP_TIME（毫秒），按醒来日选择最长主session；无记录null，前端整块隐藏。分类sleepIntervals仅供核算，不作为寝起展示源。完整寝起跨度不是扣除清醒的净睡眠时长。

2026-10-30：用户同意在睡眠阶段不可读或缺失时使用手环完整寝起时段参与分类，跨日裁到自然日；已识别阶段优先，页面标示可能含清醒。源数据无有效记录或结构错误时不作估算。

2026-10-31：微信读书连续满足十分钟阅读条件时，与普通电脑活动重叠归阅读；与电脑游戏重叠归游戏娱乐。其余设备并发优先级不变。

今日查询为空且Android源端点早于当前统计日时，`PhoneResult.error_code`及快照源状态使用`awaiting_today_sync`，即使尚未超过通用24小时阈值也标为缓存。历史窗口不套用这个今日判断；新事件补到后自动恢复正常。此状态只说明今天未收到，不能据此判断手机具体离线原因。

横版页面保留未来日历条和原图例行中的日程摘要；页脚展示手机待同步时间和日历缓存状态。Dashboard启动时请求既有`TimeMirror-Outlook-Calendar`任务补读，任务按已有日期门禁幂等处理；失败继续使用缓存，不阻止页面启动，也不提高日历轮询频率。

