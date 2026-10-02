# 分发形态

主要交付是 Windows 独立 `KIU Launcher` 的 onedir ZIP：可执行文件、`_internal`、
当前知识与说明、兼容元数据及第三方许可。用户不需要安装 Python，也不捆绑 Azahar 或游戏。
源码 ZIP、wheel、CLI 和金手指作为附属工具保留，不以维护模拟器 fork 为前提。

## 会话与注入

GUI 负责选择文件、增强选项和自己的本地偏好。
`launcher_core.py` 先只读验证完整游戏构建，再以命令行端口参数启动用户提供的 Azahar。
等待 game/scheduler 就绪，验证端口属于本次进程，并校验补丁字节后安装增强。
鼠标在战斗与焦点条件满足时自动启用；GDB 安装与实时 mailbox 写入分离。
停止/关闭启动器时只恢复本次安装的补丁，不关闭 Azahar，不覆盖未知字节或已有补丁。

当前 Windows 程序使用 Tk GUI 和延迟加载宿主后端，不增加后台服务或跨平台 GUI 框架。
代码安装、JIT、mailbox、游戏机制和生命周期限制统一见 [知识库](KNOWLEDGE.md)。
完整支持范围见 [当前状态](../STATUS.md)，玩家步骤见 [README](../README.md)。

## 源码与冻结边界

- `src/kiu/`：Launcher、CLI、只读构建验证及平台无关核心/协议。
- `src/kiu/patches/`：当前核心增强；`experimental/`：菜单/俯仰实验，不进入玩家 Launcher。
- `src/kiu/backends/windows/`：Windows 输入、窗口与数据通道。
- `tools/dev/`：提取、资产和 ARM 分析；`tools/build_launcher.py`：玩家包构建。

冻结构建预先生成项目自写 ARM hook，并与 canonical 清单精确比对；运行时仍核对 build hash、
Capstone 契约和客体字节。Keystone 仅用于构建，不把其运行库塞进玩家包。
客体 hook 与 mailbox ABI 不变；Linux/macOS 后端与 Launcher 尚未实现，不宣称三平台可用。

## 发布边界

构建结果与缓存在忽略的 `local/`。玩家包只采集新生成的 onedir 与公开文件白名单，
源包另用白名单构建，不压缩整个项目、私人研究或 `.git`。
项目代码是 MIT，Python/Tcl/Tk、Capstone 和冻结工具的声明随包保留。
本地构建不代表已发布 GitHub Release；推送与发布仍需用户授权。
