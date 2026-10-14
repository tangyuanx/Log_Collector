# Log Collector

Windows 上的 MobaXterm 会话日志采集工具，v0.1.2。

## 使用

1. 首次使用，在 MobaXterm 的 **Settings → Configuration → Terminal** 中开启 **Log terminal output to the following directory**，选择保存目录，并重新连接目标会话。如果该会话设置覆盖了全局设置，也需要在该会话的 Terminal settings 中开启日志。
2. 打开 Log Collector，按住 **按住拖到终端**，拖到要采集的会话文字区域，松开绑定。
3. 目标显示“准备就绪”后，点击 **开始采集**。
4. 点击 **停止并保存**。日志默认存到用户文档下的 `LogCollector` 目录，可更改保存位置。

主界面保留目标会话、开始/停止按钮和保存位置；帮助与检查更新位于顶部，界面参照 cmd_send 的字体、细边框和紧凑标题栏。无需在工具中选择原始日志目录、日志文件或输入会话名称。“检查更新”从 GitHub Releases 下载单文件 EXE，校验 SHA256 后替换并重启。

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

发布前验证实际单文件 EXE 和目录版能在不含 Python 的 PATH 中启动；另验证中文程序路径和中文临时目录。报告写入 Actions 构建产物的 startup-verification.json。启动测试经过 Python DLL 加载和 Qt windows 平台插件，并确认窗口可见。

如果单文件版仍报告临时目录中的 DLL 加载失败，请使用发布页的 LogCollector-Portable.zip：完整解压后运行其中的 EXE，保留旁边的 _internal 目录。便携包不需要在启动时解包到临时目录；不能把里面的 EXE 单独拿出来运行。便携目录版可检查更新，安装新版时需要下载完整便携包并解压到新目录。

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

每次推送 main，GitHub Actions 在 Windows 上测试并从 LogCollector.spec 打包单文件 `LogCollector.exe` 以及目录版，通过实际 EXE 启动测试后上传构建产物。`update.py` 中的 VERSION 尚未发布时，流程创建同名版本标签并发布 EXE、便携 ZIP 与 `SHA256SUMS.txt`。已有版本的资产不会被覆盖。发布下一版时更新 VERSION 后推送 main。

仓库 Actions 需要允许流程声明的 `contents: write`。EXE 应放在有写权限的目录，以便软件内升级。升级保留 `.exe.bak` 备份；源码运行模式不支持原地 EXE 更新。
