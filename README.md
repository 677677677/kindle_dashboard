# Time Mirror

面向 Kindle 的个人时间 Dashboard，汇总设备使用、阅读、日程与睡眠，展示一天的分配和剩余可安排时间。

## 运行

在 Windows 上运行 `setup-dashboard-autostart.cmd`，或使用 `start-dashboard.cmd` 启动。配置参考 `config.example.json`；Android、Outlook、KOReader 与手环数据源需要分别配置。个人数据存于本机用户目录，不包含在仓库内。

页面默认地址为 `http://127.0.0.1:3000/`。Kindle 访问需要电脑和 Kindle 处于可互访的网络。

## 目录

- `src/time_mirror`：采集、时间归类和快照。
- `dashboard`：面向电子墨水屏的只读界面。
- `tests/unit`：使用合成数据的测试。
- `PRD.md`、`TECHNICAL_PLAN.md`：产品与技术说明。

本公开版本不包含私人运行数据、密钥、截图或个人操作记录。

