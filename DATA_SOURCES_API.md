# Time Mirror 数据来源与接口文档

> 依据当前仓库实现整理，版本：2026-10-31。文中的路径是默认值或示例；实际配置以运行时 `config.json` 为准。示例数据均为虚构值，不包含个人记录。

## 1. 总体链路

```text
手机 ActivityWatch ─ Syncthing ─ aw-sync pull ─┐
电脑 ActivityWatch ─ SQLite + 本机 HTTP ───────┤
Kindle KOReader ───── SSH + SQLite 备份 ───────┤
华为手环 ─ Gadgetbridge ─ Syncthing ─ SQLite ─┼─ 时间核心 ─ snapshot-v1 ─ Dashboard
Outlook 日历 ───── 已发布 ICS + 本地缓存 ──────┘
```

电脑是目前的数据汇总端：后台通常每 300 秒刷新一次，但实际耗时、设备连接和各源的独立刷新频率会影响更新时间。电脑关机期间不会生成新快照；本地缓存及其他设备各自已经记录的数据不会因此自动变成实时更新。不同来源时间采用带时区的时间戳，统计日为本地 00:00—次日 00:00。

默认运行目录：`%LOCALAPPDATA%\TimeMirror`；默认私有配置：该目录下 `config.json`，配置字段示例见 `config.example.json`。运行数据、日历地址、数据库、密钥不应提交 Git。

## 2. 手机应用使用：Android ActivityWatch

**用途**：获取手机前台 App 的时间区间、累计使用时间和使用次数；识别微信读书、LinkedIn、ChatGPT 等。

**链路**：手机 ActivityWatch → 手机上的同步文件夹 → Syncthing → 电脑 `ActivityWatchSync` → 本机 `aw-sync sync --mode pull` → 本机 ActivityWatch → Time Mirror。

- 自动导入由每次快照刷新触发；Syncthing 实际何时传到电脑取决于两端连接和运行状态。
- 本机 ActivityWatch 默认地址：`http://127.0.0.1:5600`。
- `GET /api/0/buckets/`：查找名称以 `aw-watcher-android` 开头、含 `-synced-from-`、类型为 `currentwindow` 的 bucket；重复候选取更新时间最新者。
- `GET /api/0/buckets/{bucket_id}/events?start={带时区时间}&end={带时区时间}`：获取所选区间的事件。
- 输入事件主要使用 `timestamp`（带时区）、`duration`（秒）、`data.app`、`data.package`；不合规事件会被跳过。内部会使用包名识别 App，公开快照只保留规范名称和起止时间，不输出包名。
- 手机读取不可用或没有同步 bucket 时，可回退读取手动导出 JSON；默认位置 `imports/activitywatch-android.json`。手动导出必须包含类型为 `currentwindow` 的 `buckets.aw-watcher-android.events`。
- 桌面、系统设置及配置的忽略包会过滤；累计时间取可见事件的区间并集。微信读书需要满足连续 10 分钟阅读门槛；其与普通电脑重叠归阅读，与名单内电脑游戏重叠归娱乐。手机总时长与互斥分类可以不同，不能直接相加。
- 手机事件未覆盖当天、且来源仍停在昨天时返回 `awaiting_today_sync`；它表示当天数据尚未收到，不能证明当天没使用手机。

配置入口：`android.enabled`、`syncEnabled`、`syncDirectory`、`syncExecutable`、`exportPath`、`staleAfterMinutes`、`ignorePackages`。实现：`src/time_mirror/collectors/activitywatch_android.py`，同步编排：`src/time_mirror/runtime.py`。

## 3. 电脑应用使用：Windows ActivityWatch

**用途**：获取电脑的前台应用及非空闲时间；不会把窗口标题、网页地址或内容传给 Dashboard。

**读取方式**：

1. 只读 ActivityWatch Rust SQLite 的 `buckets`、`events`，仅投影 `data.app`，选取同主机的 `aw-watcher-window_*` 与 `aw-watcher-afk_*`。
2. 请求 `POST http://127.0.0.1:5600/api/0/query/`，将各 App 的窗口时间与 AFK 的 `status=not-afk` 时间相交；返回侧只接受 `data={"status":"not-afk"}`。
3. 输出内部 `AppInterval(name, interval)` 与 `AppSummary(name, seconds)`，区间按需要裁剪、合并。

默认数据库：`%LOCALAPPDATA%\activitywatch\aw-server-rust\sqlite.db`；可用 `activitywatch.databasePath` 覆盖，HTTP 地址由 `activitywatch.baseUrl` 配置。未运行、缺少数据库或 bucket 为 `not_connected`；返回结构异常为 `error`。电脑开机不等于活跃：须有前台 App 且落在非 AFK 区间。程序不会推断窗口内容的用途。

实现：`src/time_mirror/collectors/activitywatch.py`。ActivityWatch 自身可能保存窗口标题；本项目采集器不将标题输出。

## 4. Kindle 阅读：KOReader

**用途**：读取 KOReader 本地书籍的阅读页记录、书名、作者及阅读时间。

**协议**：电脑通过 SSH 公钥连接 Kindle，默认用户 `root`、端口 `2222`；实际地址及密钥由 `kindle.hosts`、`port`、`user`、`privateKey` 指定。严格校验主机密钥，可用 `hostKeyAlias` 指向已信任的主机身份；禁止自动信任未知设备。

**源数据库**：默认 `/mnt/us/koreader/settings/statistics.sqlite3`，由 `kindle.statisticsDatabase` 配置。不是 HTTP 接口。读取时在 Kindle 上用 SQLite 只读 `.backup` 生成临时备份并经 SSH 输出，包含已提交 WAL；电脑端做完整性与表结构校验后原子替换 `imports/koreader-statistics.sqlite3`。失败保留先前可信快照。

必需字段：

- `book`：`id`、`title`、`authors`。
- `page_stat_data`：`id_book`、`start_time`（Unix 秒）、`duration`（秒）。通过 `id_book = book.id` 关联，仅取正时长并与查询窗口相交的记录。

内部输出 `ReadingEvent(book_id, title, authors, interval)`，同一本书的相邻或重叠区间合并。**当前阅读分类需要连续区间至少 600 秒**；页记录存在间隔时，累计阅读时间可能非零而饼图阅读仍为零，不应据此推断用户没有读书。设备不可达时可能继续显示缓存，需结合 `collectedAt`、`latestEventAt` 判断。

实现：`src/time_mirror/collectors/koreader.py`；本机缓存可能含书名与阅读记录，应视为私人数据。

## 5. 手环睡眠：Gadgetbridge

**用途**：读取华为手环睡眠，显示完整寝起时段并参与全天分配。

**链路**：手环 → Gadgetbridge 同步 → Gadgetbridge 数据库自动导出 → Syncthing → 电脑 `HealthData/Gadgetbridge.db` → Time Mirror 只读 SQLite。自动获取手环记录、自动导出数据库、Syncthing 同步是三个独立步骤；任一环节延迟都会影响电脑。`gadgetbridge.enabled` 控制启用，`databasePath` 可覆盖默认路径。

数据表及字段：

- `HUAWEI_ACTIVITY_SAMPLE`：`TIMESTAMP`、`OTHER_TIMESTAMP`（Unix 秒）、`SOURCE`、`RAW_KIND`、`DEVICE_ID`、`USER_ID`。当前识别 `SOURCE` 为 10 或 13，`RAW_KIND` 为 6、7、`0x5656`、`0x5659` 的睡眠起止记录；去重、合并并裁剪到统计日。
- `HUAWEI_SLEEP_STATS_SAMPLE`：`TIMESTAMP`、`WAKEUP_TIME`（Unix 毫秒）、`DEVICE_ID`、`USER_ID`。完整寝起展示按醒来日期选择，保留跨午夜的完整起点。

分类优先使用可识别睡眠区间；阶段缺失或不支持时，用有效完整寝起时段参与分类，并按自然日裁剪。寝起跨度可能包含夜间清醒，不等同于净睡眠时长；分类还会遵循活动优先级避免重复计时。无数据不虚构睡眠。

状态代码包括 `missing_export`、`invalid_sleep_export`、`no_sleep_record`、`sleep_session_span`、`stale_export`。文件修改时间超过 36 小时会标记缓存；`sleep_session_span` 表示使用完整寝起跨度回退，不能据此断言全部时间均处于睡眠状态。实现：`src/time_mirror/collectors/gadgetbridge.py`、`src/time_mirror/runtime.py`。

## 6. 日程：Outlook 已发布 ICS

**用途**：读取日历计划，显示日程并将忙碌时段纳入未来安排计算。此路线不使用 Microsoft Graph，也不绕过组织账户访问策略。

**协议**：`GET` 用户在 Outlook 中发布的 HTTPS iCalendar 地址；当前只允许 `outlook.office365.com`、`outlook.office.com` 主机，下载上限 5 MiB、超时 30 秒。发布链接是持有者可读凭据，须保密，不放入代码、日志或公开仓库；在 Windows 上由 DPAPI 加密后保存在 `%USERPROFILE%\.time-mirror`。

解析 ICS 的事件、循环和例外，缓存只保留 `id`、`subject`、`startAt`、`endAt`、`showAs`、`isAllDay`。地点、正文、参会人、会议链接不写入缓存；私密事件标题替换为“私人日程”。取消事件不纳入。

默认缓存：`%LOCALAPPDATA%\TimeMirror\imports\outlook-calendar.json`；schema 为 `outlook-calendar-v1`，包含 `collectedAt`、`coverageStart`、`coverageEnd`、`events`。采集窗口为当前统计日和下一日。`busy`、`oof`、`workingElsewhere` 阻挡可安排时间；`free`、`tentative` 和未知状态不扣除；重叠区间只计一次。

正式任务每天 07:00 尝试同步，错过时由任务计划程序补跑；可通过 `refresh-outlook-calendar.cmd` 手动刷新。每五分钟快照刷新只读取缓存，不反复下载 ICS。缓存缺失、覆盖不足或非当天采集会分别表现为 `missing_cache`、`coverage_mismatch`、`daily_cache` 等状态。实现：`src/time_mirror/outlook_ics.py`、`collectors/outlook_calendar.py`。

## 7. 内部快照接口：提供给 Dashboard

本机运行时默认仅监听 `127.0.0.1:8765`，为只读服务：

- `GET /health`：返回文本 `ok`，仅证明 HTTP 处理器可响应，**不证明各数据源正常**。
- `GET /snapshot.json`：返回当前 `snapshot-v1` JSON；`Cache-Control: no-store`。生成失败时不得把旧缓存误称实时结果。
- `GET /snapshot.js`：返回同一快照的 JavaScript 包装，供兼容用途；Kindle 页面本身采用服务端渲染，不依赖浏览器执行 JavaScript。

典型结构（删去了非关键字段；所有数字仅为示例）：

```json
{
  "schemaVersion": "snapshot-v1",
  "timezone": "Asia/Shanghai",
  "statisticalDate": "2026-01-02",
  "generatedAt": "2026-01-02T12:00:00+08:00",
  "sources": {
    "phone": {"status": "ok", "collectedAt": "2026-01-02T11:55:00+08:00"},
    "health": {"status": "cached", "errorCode": "no_sleep_record"}
  },
  "allocation": {"totalSeconds": 86400, "categories": []},
  "sleepSummary": null,
  "devices": [],
  "calendarEvents": [],
  "history": {"version": "history-v1", "days": []}
}
```

常见来源状态：`ok` 正常、`cached` 缓存或数据不新、`unavailable` 未取得、`error` 数据或解析错误、`not_connected` 未接入。状态必须与 `errorCode`、采集时间和具体来源一起解释。详细字段以 `dashboard/lib/snapshot.ts` 和 `src/time_mirror/snapshot.py` 为准；上述示例不是完整响应。

Web 页面通常由 `http://127.0.0.1:3000/` 提供；端口 8765 是电脑本机内部接口，不能把 127.0.0.1 当作手机或 Kindle 的电脑地址。向局域网暴露页面或新增接口时，应先考虑认证、访问范围和个人数据保护。

## 8. 核对与排障顺序

1. **先看源头**：手机 ActivityWatch 有使用记录；KOReader 统计已启用；Gadgetbridge 有相应睡眠；Outlook 有日程。
2. **再看传输**：手机 Syncthing 与电脑设备连接、相应文件夹已共享，Gadgetbridge 数据库已自动导出且时间更新；Kindle SSH 可达且主机身份可信。
3. **看电脑缓存和来源状态**：区分没有事件、文件过期、设备离线、格式错误；不要上传或公开原始数据库及日历链接。
4. **看分类口径**：跨午夜按自然日切割；设备同时发生按分类优先级归一类；10 分钟门槛与数据缺失都会影响阅读显示。
5. **看快照和页面**：确认 `generatedAt`、`sources` 和 `allocation`；`/health` 成功不足以证明数据已同步。

快捷检查：电脑浏览器打开 `http://127.0.0.1:8765/health`、`http://127.0.0.1:8765/snapshot.json` 和 `http://127.0.0.1:3000/`。快照含个人信息，只在可信设备上查看。

