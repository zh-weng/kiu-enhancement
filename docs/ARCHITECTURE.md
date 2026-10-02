# 分发形态与注入方案

本文只记录产品形态和宿主接入决策。游戏机制、补丁站点与 mailbox 语义统一见
[知识入口](KNOWLEDGE.md)；支持状态见 [STATUS](../STATUS.md)，操作见 [README](../README.md)。
官方实现参考固定于 Azahar `2126.1.2`，不代表本项目已完成该版本的实机验收。

## 现有形态：官方 Azahar + companion

当前使用官方 Azahar 和一个轻量 Windows companion，不要求维护模拟器 fork。
统一入口是 `kiu.py` / `kiu-enhance`；用户自行启动游戏并启用 GDB stub。
工具不自动启动 Azahar，也不覆盖用户布局、全局配置或游戏文件。

安装与实时输入分离：

1. 通过 GDB 检查原字节、已安装字节及 cave，拒绝未知或混合状态。
2. 暂停 guest，安装代码并初始化关闭状态的输入数据，读回验证后恢复运行。
3. Windows `GuestMemory` 使用 nonce 验证对应的宿主内存映射。
4. 输入循环经独立数据通道写 mailbox；停止 helper 后可显式恢复 hook。

GDB 用于代码安装、检查和恢复，不承担高频鼠标传输。
官方 GDB 写内存后清理 CPU 指令缓存；这也是代码修改不能简单改用宿主内存写入的原因。
[官方 GDB/JIT 缓存处理](https://github.com/azahar-emu/azahar/blob/2126.1.2/src/core/gdbstub/gdbstub.cpp)。

现有数据通道使用 Windows `ReadProcessMemory` / `WriteProcessMemory`。
其已验证映射只适用于目标 mailbox 区域，不是整个 guest 地址空间的通用映射。
退出关闭输入不等于删除全部 hook；读档后的重连、协议竞争和验收缺口见
[当前限制](../STATUS.md#已知的发布限制)，不在此重复游戏链路细节。

## 分发选择

首发以源码 preview、轻量 companion 和独立金手指文件为主。
Python 与未来打包入口复用同一实现，不增加后台服务、账号或复杂 GUI。
独立可执行文件和自动启动/profile 管理仍是后续交付工作，不是当前已完成能力。

60 FPS / 宽屏可仅使用金手指；金手指本身不能采集宿主鼠标。
同一补丁只能由一个管理者控制，启用与恢复需明确区分。
详见 [金手指说明](GATEWAY_CHEATS.md)，不把菜单实验纳入默认稳定配置。

源码和发布包不包含 ROM、提取代码、素材、存档、派生游戏二进制或 Azahar。
项目代码使用 MIT，第三方依赖保留各自许可证。

## Linux / macOS 后端边界

客体 hook 与 ABI 不依赖宿主系统。宿主契约集中在 [aim_protocol.py](../tools/aim_protocol.py)：

- `InputSource`：相对设备输入及输入源生命周期。
- `WindowContext`：焦点与物理像素 viewport。
- `MemoryTransport`：经验证的 guest 数据读写。

平台适配器延迟加载，公共帮助、构建校验和金手指导出不导入 Windows 后端。
Linux/macOS 可替换输入、窗口和传输实现；不得改变既有 ABI 语义来掩盖平台差异。
ABI 变更必须升级版本并补兼容性测试，完整契约见 [知识入口](KNOWLEDGE.md)。
两平台后端尚未实现；Wayland 输入、macOS 权限、DPI 和读档恢复需要分别验收。

## 后续候选：加载时 BPS + 官方 RPC

加载时 `code.bps` 可承载固定 hook，避免每次运行都注入相同代码。
Azahar 加载器支持 ExeFS BPS，格式实现校验源和目标 CRC；仍应额外验证受支持构建 SHA256。
[官方加载器](https://github.com/azahar-emu/azahar/blob/2126.1.2/src/core/file_sys/ncch_container.cpp)、
[BPS 校验实现](https://github.com/azahar-emu/azahar/blob/2126.1.2/src/core/file_sys/patch.cpp)。
本项目尚无 BPS 交付流程；加载时代码补丁也不能替代运行时数据初始化和生命周期管理。

官方 RPC 已有进程选择、guest 内存读写及 UDP 传输，可评估为跨平台数据通道。
[RPC 实现](https://github.com/azahar-emu/azahar/blob/2126.1.2/src/core/rpc/rpc_server.cpp)、
[UDP 传输](https://github.com/azahar-emu/azahar/blob/2126.1.2/src/core/rpc/udp_server.cpp)。
本项目尚未实现该后端，延迟、丢包、帧率与读档重连必须先测，不能视为等价替换。
RPC 不会自动完成跨平台鼠标采集；调试服务也不应暴露到不可信网络。

3GX 加载的是 guest 插件，不是宿主 Raw Input 扩展接口，因此不是现有 companion 的直接替代。
[官方 3GX loader](https://github.com/azahar-emu/azahar/blob/2126.1.2/src/core/hle/service/plgldr/plgldr.cpp)。
若以后分发修改版 Azahar，应另行处理其 GPL 要求；[Azahar 许可](https://github.com/azahar-emu/azahar/blob/2126.1.2/license.txt)。

## 公开历史边界

`codex/development` 使用独立干净根提交，只保留当前源码、知识与必要文档。
研究分支和个人研究记录仅保留本地；私人研究数据留在忽略的 `local/`。
获准发布时只推开发分支，不使用 `git push --all` / `--mirror`，不分发完整 `.git`。
发布包始终用白名单构建，不压缩整个工作目录。
