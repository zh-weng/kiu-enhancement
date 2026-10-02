# 当前状态

最近整理：2026-10-02。以实际工具与测试为依据；没有在此次整理中重新进行模拟器实机验收。

唯一支持构建：USA v0 / `0004000000030100` / `CTR-P-AKDE`。构建 SHA256 见 `manifests/title.json`。

| 功能 | 状态 | 当前实现 |
| --- | --- | --- |
| 真 60 FPS | 已有运行时验收，用户接受 | scheduler interval 2→0，两个时间通道乘 0.5 |
| 战斗宽屏 | 已验收；16:9 / 21:9 | 透视投影 X 修正；普通下屏与正交 HUD 不缩放 |
| 原生鼠标 | Windows preview，完整回归待补 | 陆战 rotation request + 空战 reticle hooks；Raw Input + process-memory |
| 菜单下屏移上屏 | 实验 | 游戏自己的 render-pass 路由；不是 F9 或 framebuffer copy |
| 菜单无拉伸铺满 | 未完成 | 原 320 宽版面仍在，触控坐标未联动，个别 Gear/AR 路径未统一 |
| 俯仰 60° | 实验，不保证最终范围 | 下游还有 limiter，不默认安装 |
| Linux / macOS 鼠标 | 尚未实现 | 平台无关协议与接口已定义，宿主后端待做 |

## 当前入口

安装后推荐 `kiu-enhance` 或 `python -m kiu`；源码入口为 `python run_kiu.py --help`。
默认 `apply` 只安装 60 FPS 和 16:9；鼠标/菜单需要显式选择。
`restore` 恢复当前核心 hook 与菜单路由，不包含实验俯仰补丁。
金手指生成器同时提供 Enable / Restore，见 `docs/GATEWAY_CHEATS.md`。

## 已知的发布限制

- 陆战输入 mailbox 的 host 读改写与 guest 清零存在竞争；不能宣称零丢包或严格一次消费。
- 即时存档可能改变宿主映射及携带打开的 gate；没有自动重连。读档前停掉鼠标，读档后重新启动 helper；遇到混合状态须重启游戏。
- 当前 helper 退出关闭输入，不自动删除 hook。停止 helper 后使用 `restore` 或彻底重启游戏恢复。
- 空战默认映射桌面；`--map viewport` 只适用于原始 side-by-side 布局假设，不支持任意 custom layout。
- 新版本 Azahar、不同平台、DPI/多显示器和长时间输入需单独验收。
- 同一补丁不可同时由反复执行的金手指与控制工具管理。

## 发行候选

正式运行代码在 `src/kiu/`，研究工具在 `tools/dev/`；保留统一 CLI 与模块入口。
公开目录只含当前源码、测试、说明及元数据，技术知识集中在一处。
源包采用白名单，不打包游戏、私人 `local/`、旧报告或整个物理目录。
独立可执行文件、自动启动器与 Linux/macOS 原生输入尚未完成。
当前仅为本地发行候选，没有推送 GitHub 或创建远程 Release。
`main` 是公开候选分支；`codex/development` 用于后续开发，不发布私人研究分支。

当前有效研究结论集中在 [知识入口](docs/KNOWLEDGE.md)；旧报告不作为公开知识来源。

未来主线：先完成鼠标协议时序与读档恢复验收，再验证平台无关 RPC 后端；不以维护 Azahar fork 为前提。
