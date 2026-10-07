# 分发形态

主要交付是 Windows 独立 `KIU Launcher` 的 onedir ZIP：可执行文件、`_internal`、
当前知识与说明、兼容元数据及第三方许可。用户不需要安装 Python，也不捆绑 Azahar 或游戏。
源码 ZIP、wheel、CLI 和金手指作为附属工具保留，不以维护模拟器 fork 为前提。
Linux/Steam Deck preview 使用源码与用户目录中的 Python venv，不修改 SteamOS 系统分区。
桌面入口为 `scripts/start-linux.sh`；Steam Gaming Mode/Big Picture 入口为
`scripts/start-steamdeck.sh` → `python -m kiu.linux_play`，直接读取已保存偏好，只打开 Azahar。

## 会话与注入

GUI 负责选择文件、增强选项和自己的本地偏好。
`launcher_core.py` 先只读验证完整游戏构建，经首次 GUI 确认后可持久保存显示 preset，
再以命令行端口参数启动用户提供的 Azahar。默认 SingleScreen + `-f`，
自动比例使用主显示器物理宽高；指定比例以共享上屏伸展/留边保持比例，关闭下屏伸展。
Custom 几何保留，双屏可选路径仍修改上屏宽度。性能/键位/存档不变。
必须先关闭全部 Azahar，避免 Qt 以其内存设置覆盖文件。Stop/退出不写回配置快照。
等待 game/scheduler 就绪，验证端口属于本次进程，并校验补丁字节后安装增强。
Linux AppImage 可保留父进程并启动实际模拟器子进程；必须验证其 `/proc` 父链与启动时间，
再固定 GDB owner 的 PID/启动时间，不能仅凭进程名接受子进程或重用的 PID。
鼠标在战斗与焦点条件满足时自动启用；GDB 安装与实时 mailbox 写入分离。
空战由物理 render canvas + 已保存 preset 的上屏矩形映射；SingleScreen 必须匹配预设全屏尺寸，未知/裁切时关 gate，
不以整个主窗或桌面作替代。Windows 只识别 OpenGL OwnDC child；Linux 只接受唯一、PID 匹配的全屏窗口。
Gamescope 使用同一 compositor 的控制 display 确认键盘/鼠标焦点，并验证全屏 canvas；无法证明时关 gate。
Linux 通过 `QT_QPA_PLATFORM=xcb` 使用 X11/XWayland，不支持原生 Qt Wayland 窗口。
保留 Azahar 原生上下屏切换，不注入替代快捷键；切下屏前手动暂停鼠标（Windows F4，Linux F12），
因为原生 swap 不保存 INI、当前不跟踪其内存状态。
菜单路由是另一个 opt-in 实验开关。Stop 先等待 marker=0 且所有 guest PC 不在菜单 caves，再恢复 owned menu，随后恢复 owned 宽屏。
停止/关闭启动器时只恢复本次安装的补丁，不关闭 Azahar，不覆盖未知字节或已有补丁。
GDB 断开后 listener 暂时未重开时，恢复路径最多等待 2 秒；不同 owner/启动时间仍立即拒绝。

桌面 Launcher 使用 Tk GUI 和延迟加载宿主后端，不增加后台服务或新的 GUI 框架。
Linux 两个启动脚本默认以 `XDG_CONFIG_HOME` 指向项目 `local/profile/config`，隔离现有 Azahar 配置。
headless 入口不显示 Tk、不自动授予显示配置权限；启用 preset 时必须已有当前版本的授权偏好。
SIGINT/SIGTERM 请求停止输入并恢复 owned 补丁，不终止 Azahar，也不回滚保存的 preset。
代码安装、JIT、mailbox、游戏机制和生命周期限制统一见 [知识库](KNOWLEDGE.md)。
完整支持范围见 [当前状态](../STATUS.md)，玩家步骤见 [README](../README.md)。

## 源码与冻结边界

- `src/kiu/`：Launcher、CLI、只读构建验证及平台无关核心/协议；`azahar_display.py` 负责定点 INI 编辑和矩形数学。
- `src/kiu/patches/`：当前核心增强；`experimental/`：菜单实验可选，俯仰实验不进入玩家 Launcher。
- `src/kiu/backends/windows/`：Windows 输入、窗口、配置路径与数据通道；DLL 均在实际调用后端时才加载。
- `src/kiu/backends/linux/`：XI2 相对鼠标、X11/Gamescope 焦点与全屏窗口、配置路径、进程身份及 `process_vm_readv/writev` 数据通道。
- `src/kiu/linux_play.py`：Steam headless 入口；复用 Launcher 验证、安装与恢复生命周期，不另建注入路径。
- `tools/dev/`：提取、资产和 ARM 分析；`tools/build_launcher.py`：玩家包构建。

冻结构建预先生成项目自写 ARM hook，并与 canonical 清单精确比对；运行时仍核对 build hash、
Capstone 契约和客体字节。Keystone 仅用于构建，不把其运行库塞进玩家包。
客体 hook 与 mailbox ABI 1 不变。Linux 同样用新鲜 GDB nonce 验证宿主映射，只开放两个 mailbox 的实时数据范围；
需由 Launcher 启动模拟器以满足正常进程内存权限，不提升权限或修改 ptrace 策略。
Linux 是 preview，ABI 1 读写竞态与 savestate 恢复限制仍存在；macOS 后端尚未实现。
已实现适配器不等于物理设备输入或实战体验已验收，实际验证范围以 [当前状态](../STATUS.md) 为准。

## 发布边界

构建结果与缓存在忽略的 `local/`。玩家包只采集新生成的 onedir 与公开文件白名单，
源包另用白名单构建，不压缩整个项目、私人研究或 `.git`。
项目代码是 MIT，Python/Tcl/Tk、Capstone 和冻结工具的声明随包保留。
本地构建不代表已发布 GitHub Release；推送与发布仍需用户授权。
