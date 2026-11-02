# TimeMirror Windows 桌面组件

四块透明页面共享现有 Dashboard 的 React 图表与 `snapshot-v1`，不触碰设备采集或私人数据库。森林暖白、浅金与苔绿适合现有森林壁纸，中间人物保持留白。

## 依赖与来源

- Rainmeter 4.5 或更新版本，Windows 10 1803 / Windows 11。
- 已安装 Microsoft Edge WebView2 Evergreen Runtime。
- 第三方插件：[NSTechBytes/WebView2](https://github.com/NSTechBytes/WebView2)，[正式版 v1.0.0](https://github.com/NSTechBytes/WebView2/releases/tag/v1.0.0)。
- 官方 ZIP SHA-256：`f1b207eb61cfd3487cab39be3b176310af464104eb9f385357617b4fbf23e80c`。

插件来自维护者项目，并非 Rainmeter 官方内置功能。安装入口校验 ZIP 和所选位数的 DLL，只写用户目录。正式版会向网页注入 RainmeterAPI，因此仅加载本项目的 `127.0.0.1` 页面，不引入外部页面或 iframe。

## 安装与恢复

已有 Dashboard 应先运行在 `http://127.0.0.1:3000/`，快照只读接口为 `http://127.0.0.1:8765/snapshot.json`。在 Windows PowerShell 中执行本目录 `install-widgets.ps1`；也可传入已下载的官方 DLL：`-LocalPlugin <DLL路径>`。脚本会核实位数与哈希，停止 Rainmeter 保存配置，备份，再安装并启动。

脚本按主屏物理尺寸计算 `Scale`，再除以系统 `DpiScale` 转成 Rainmeter 坐标和尺寸，并输出整数与 UTF-16LE 配置。`-ScreenWidth`、`-ScreenHeight`、`-Scale`、`-DpiScale` 可指定已核实参数；`-WebZoomFactor` 可覆盖单独的网页缩放，安装后必须实测 CSS viewport，避免 Windows DPI 重复放大。布局基准为 1649×928：剩余 `(355,35)`，概况 `(1100,40)`，分配 `(1100,230)`，全天轴 `(355,805)`、尺寸 `980×70`。底部轴压低并收窄，保留中央人物鞋部。

Rainmeter 主配置中，四块使用 `AlwaysOnTop=-2`（On desktop）、`SavePosition=1`、`Draggable=0` 和 `ClickThrough=1`；插件 measure 另设 `Clickthrough=1`。普通窗口应覆盖组件；桌面应显示组件在 Wallpaper Engine 上方。实际层级、透明和系统缩放仍必须本机截图确认。

登录时保留 Rainmeter 的启动文件夹快捷方式；安装时仅在缺失时创建。另注册正式 `TimeMirror-Desktop-Widgets` 登录任务，隐藏等待 `/widgets/remaining` 返回成功后，重新激活四块皮肤，避免先启动 Rainmeter 时 WebView 导航失败。等待上限 15 分钟，失败后由任务计划程序每 2 分钟重试、最多 3 次；任务允许电池供电。后台 Dashboard 继续由现有正式登录任务运行。页面加载后按 30 秒轮询自动恢复快照接口，不需要手动刷新。

- `restart-widgets.ps1`：请求登录恢复任务重新等待并激活这四块，不停采集、不重启 Kindle 页面。
- `restore-backup.ps1 -BackupPath <安装时输出的备份目录>`：恢复安装前的 Rainmeter 配置与已有文件；新增文件保留但不激活。
- 本机备份及安装记录位于 `%USERPROFILE%\.time-mirror\desktop`，不进入 Git。登录任务直接引用仓库中的启动脚本，显式传入此目录，避免交互进程与任务计划程序看到不同 AppData 文件视图。

## 验证边界

安装后检查四块真实数据及最后生成时间，源端缓存不能被 HTTP 成功掩盖。停止或缺失接口时应展示不可用或保留缓存标签，跨日旧快照不得称为今天。检查字体、图例、环图与时间轴均不溢出；切换普通窗口及“显示桌面”后确认层级；重启 Rainmeter 后确认位置、穿透与自动刷新恢复。

只有实际截图与数据读取完成，才可记录本机部署通过。安装程序返回成功仅表示文件与配置已部署。
