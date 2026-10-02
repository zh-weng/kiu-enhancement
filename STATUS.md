# 当前状态

更新：2026-10-02。以实际源码与测试为依据；本轮 Launcher 整理不代表新增模拟器战斗验收。

唯一支持 USA v0 / `0004000000030100` / `CTR-P-AKDE`，完整 CCI 与代码 SHA256 见 `manifests/title.json`。

| 功能 | 状态 | 当前实现 |
| --- | --- | --- |
| 真 60 FPS | 已有模拟器运行验收，用户接受 | scheduler 2→0，两个时间通道乘 0.5 |
| 战斗宽屏 | 已有模拟器验收；16:9 / 21:9 | 透视 X 修正，普通下屏与正交 HUD 不缩放 |
| Windows Launcher | preview，已做基础运行时验收 | 验证自备 CCI、启动 Azahar、检查所属端口、安装与 owned 恢复 |
| 原生鼠标 | Windows preview，完整回归待补 | 陆战 rotation request、空战 reticle；Raw Input + process-memory |
| 菜单移上屏 | 实验，不进入 Launcher | 游戏 render-pass 路由；二维铺满和触控未完成 |
| 俯仰修改 | 实验，不进入 Launcher | 下游 limiter 仍需确认 |
| Linux/macOS Launcher 与鼠标 | 尚未实现 | 平台无关核心、ABI 和后端契约已保留 |

## 玩家入口

主要交付为 `kiu-launcher-windows-preview.zip`，解压整个目录后运行 `KIU Launcher.exe`。
首次选择 Azahar 与自己的精确已解密 CCI，选择增强后「开始游戏」；进入战斗且有焦点时自动瞄准。
F4 暂停鼠标；「停止并恢复增强」只恢复本次安装的补丁，不关闭 Azahar。
CLI、源码 ZIP、wheel 和金手指留作附属工具，详见 [README](README.md) 与 [开发说明](docs/DEVELOPMENT.md)。

本轮已验证 GUI 与冻结 EXE 自检，以及指定构建上的补丁安装、鼠标菜单待机、停止清理。
停止后 FPS/宽屏/两组鼠标 hook 全部核对为 stock，Azahar 保持运行。
冷启动参数和 scheduler 60→30 过渡已分别验证并补回归；尚未重新验收完整选关、战斗或长时间输入。

## 已知限制

- 鼠标 mailbox 的 host 读改写与 guest 清零仍存在竞争，不保证零丢包或严格一次消费。
- 读即时存档可能改变映射、带回 hook 与打开的 gate；尚无自动重连，读档前停止增强。
- Launcher 不重新接管已停止的会话；再次开始前自行关闭原 Azahar。未知/混合状态应完整重启游戏。
- Stop 只恢复本次 owned 补丁；已有补丁保持不变，CLI helper 单独退出也不等于全部恢复。
- 空战默认映射桌面；viewport 模式仅支持原始 side-by-side 假设，不支持任意 custom layout。
- 显示布局需与投影比例匹配，Launcher 不直接改 Azahar 布局配置。
- 新模拟器版本、关卡、DPI/多显示器、长时间输入和读档需要独立运行验收。
- 不同时使用同一补丁的持续金手指、其他 GDB 客户端或 Launcher 会话。

## 分发与后续

玩家包为新生成的 onedir 加当前文档、知识、清单和许可；不捆绑 Azahar、游戏或私人设置。
固定 hook 在构建时预生成并核对清单，冻结程序不带 Keystone 运行库，guest ABI 不变。
当前仅为本地发行候选，尚未推送 GitHub 或创建远程 Release。
有效技术结论统一在 [知识库](docs/KNOWLEDGE.md)。

后续先完成 Launcher 全流程、鼠标协议时序与读档恢复验收，再验证跨平台后端。
