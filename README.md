# Log Collector

Windows 上的 MobaXterm 会话日志采集工具，v0.1.4。

## 使用

1. 首次使用，在 MobaXterm 的 **Settings → Configuration → Terminal** 中开启 **Log terminal output to the following directory**，选择保存目录，并重新连接目标会话。如果该会话设置覆盖了全局设置，也需要在该会话的 Terminal settings 中开启日志。
2. 打开 Log Collector，按住 **按住拖到终端**，拖到要采集的会话文字区域，松开绑定。
3. 目标显示“准备就绪”后，点击 **开始采集**。
4. 点击 **停止并保存**。日志默认存到用户文档下的 `LogCollector` 目录，可更改保存位置。

主界面保留目标会话、开始/停止按钮和保存位置；帮助与版本号位于顶部，界面参照 cmd_send 的字体、细边框和紧凑标题栏。无需在工具中选择原始日志目录、日志文件或输入会话名称。点击顶部版本号打开更新弹窗，自动检查 GitHub Releases。有新版本时显示“升级并重启”；下载显示百分比、已下载大小和总大小，校验 SHA256 后替换并重启。失败可重试，下载或检查期间不会关闭弹窗。

## 实时统计

采集时每 300 毫秒显示行数、字节数、时长和最近写入时间。没有新增数据时显示等待会话日志写入；停止后保留最终统计，再次开始清零。

行数按 CR/LF 统计，CRLF 只算一个换行；非空末行先计作一行，并显示“末行写入中”，继续写入或补全换行不会重复计数。文本行可能是空行或业务事件的一部分，因此不是业务事件的精确条数。统计来自本次实际保存的新增字节；MobaXterm 尚未写入日志文件的缓冲内容不会更新计数。

## 采集方式与边界

- 绑定 MobaXterm 的具体 MoTTY 终端窗口（CMoTTY/PuTTY），使用窗口句柄、进程创建时间和宿主身份校验。切换标签页不会重定向到当前显示的会话。
- 从所选 MoTTY 进程打开的文件句柄中查找日志。只接受一个候选日志；不扫描共享 MobaXterm 进程、其他会话、保存的登录信息或任意日志目录。
- 该适配器仍依赖 **MobaXterm 内置日志输出**，并不是从窗口画面直接获取完整输出。不使用 OCR、周期复制终端或抓取滚屏文字。
- Windows 文件句柄枚举可能遗漏文件；一些 MobaXterm 版本/会话的日志也可能由共享宿主维护。此时会明确显示无法自动关联，而不是猜选其他会话。当前版本没有手动文件关联回退。
- 开始时记录日志当前末尾，排除旧内容；停止时固定末尾边界，保存到单独日志文件。MobaXterm 尚未写入文件的缓冲内容不在采集范围内，日志可能包含终端控制字符。
- 目标关闭、文件截断或替换时停止采集并保留已写入内容。目标权限较高时，应以相同权限启动工具。
- 不持久化 HWND/PID；工具重新启动后重新绑定，避免句柄复用误绑。

## Windows 启动兼容性

支持 Windows 10/11 x64，不支持 Windows 7/8。依赖版本固定；由 Windows Server 2022 构建。显式打包应用所需的 VC DLL，排除构建机的 ucrtbase.dll / APISet 系统 DLL，使用系统本身的对应库。

单文件 `LogCollector.exe` 改为原生 x64 .NET Framework 启动器（Windows 10/11 自带所需框架），内含完整目录版运行文件。首次运行解包至 `%LOCALAPPDATA%/LogCollector/runtime/<包的 SHA256>`，后续复用；关键运行文件缺失时重新提取。Python 和 Qt 从该固定目录加载，避免旧单文件包每次解压到 `%TEMP%/_MEI...` 的路径。它不会修改系统 DLL、关闭安全软件或要求安装 Python。

启动器在进入 Python 前检查运行库加载，异常写入 `%LOCALAPPDATA%/LogCollector/startup-error.txt` 并显示诊断位置。这改变了导致此前错误的临时解包方式；仅凭报错截图仍无法确定用户电脑上的确切缺失依赖或文件拦截原因，不能保证消除所有机器上的 DLL 错误。

发布前验证原生启动器及目录版能在不含 Python 的 PATH 中启动，覆盖中文路径、中文缓存目录、TEMP 不是可用目录、关键缓存 DLL 缺失后的修复；报告写入 Actions 产物 startup-verification.json。升级辅助程序另验证实际替换、备份和重启，包含中文、单引号、% 和 ! 路径。

`LogCollector-Portable.zip` 是完整目录版：解压后运行其中 EXE，保留 `_internal`。目录版可检查版本，升级需要下载完整便携包。软件内“升级并重启”用于发布的单文件启动器；升级保留 `.exe.bak`。旧 v0.1.3 及以前的更新器可将程序替换为新版启动器，但若旧版无法启动，需要先手动下载新版。

## 验证状态

文件增量采集、停止边界、会话身份、候选日志歧义和 Qt 界面状态有自动测试。Windows 构建流程会运行同一套测试及 Win32 API 基础检查。尚未进行真实 MobaXterm 会话的端到端验证，自动日志关联仍需在实际 Windows 环境确认。

## 开发与发布

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
python app.py
```

每次推送 main，GitHub Actions 在 Windows 上测试并从 LogCollector.spec 打包目录版，再生成内嵌运行文件的原生单文件 `LogCollector.exe`，通过实际 EXE 启动测试后上传构建产物。`update.py` 中的 VERSION 尚未发布时，流程创建同名版本标签并发布 EXE、便携 ZIP 与 `SHA256SUMS.txt`。已有版本的资产不会被覆盖。发布下一版时更新 VERSION 后推送 main。

仓库 Actions 需要允许流程声明的 `contents: write`。EXE 应放在有写权限的目录，以便软件内升级。升级保留 `.exe.bak` 备份；源码运行模式不支持原地 EXE 更新。
