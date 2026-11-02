# 桌面承载层

```text
desktop/
├── skins/TimeMirror/     # 四个独立 Rainmeter WebView，只有布局与本机页面地址
│   ├── Remaining/        # 左上剩余时间
│   ├── Overview/         # 右上睡眠与概况
│   ├── Allocation/       # 右侧分配与娱乐构成
│   └── Axis/             # 底部全天时间轴
├── install-widgets.ps1   # 校验官方插件、备份、用户级安装与保存布局
├── start-widgets.ps1     # 登录时等页面就绪，再激活皮肤，避免启动空白
├── restart-widgets.ps1   # 仅恢复本组皮肤，不重启或修改采集系统
├── restore-backup.ps1    # 恢复安装前的 Rainmeter 配置
└── README.md             # 来源、边界、恢复与验证说明
```

依赖单向：`snapshot-v1 → dashboard/widgets → 本目录 skins → Rainmeter/WebView2`。这里不读取快照文件、数据库、日历或设备，不实现统计。实际配置和备份位于用户配置目录，禁止回写进仓库。

四块各用一个 WebView measure，页面只允许本机地址；正式版插件会注入 RainmeterAPI，禁止加载外部网页或 iframe。安装脚本将物理布局按 `Scale / DpiScale` 转成 Rainmeter 单位，写入整数尺寸与 UTF-16LE 配置，避免变量、公式和编码造成零尺寸。网页缩放单独可配置并需实测 viewport。

登录任务直接引用仓库脚本，显式使用 `%USERPROFILE%\.time-mirror\desktop` 作为运行记录目录；不得复制脚本到交互进程专属 AppData 后假定计划任务可见。

2026-11-02：新增四块森林色桌面承载配置、官方发布校验和可恢复安装入口；实机后将底部轴调整为基准 Y805、高70，保留人物鞋部。
