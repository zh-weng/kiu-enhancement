# 当前状态

更新：2026-10-06。以实际源码与测试为依据；本轮 Launcher 整理和发布检查不代表新增模拟器战斗验收。

唯一支持 USA v0 / `0004000000030100` / `CTR-P-AKDE`，完整 CCI 与代码 SHA256 见 `manifests/title.json`。

| 功能 | 状态 | 当前实现 |
| --- | --- | --- |
| 真 60 FPS | 已有模拟器运行验收，用户接受 | scheduler 2→0，两个时间通道乘 0.5 |
| 战斗宽屏 | 已有模拟器验收；16:9 / 21:9 | 透视 X 修正，普通下屏不变；不直接改正交 HUD，显示层仍会拉伸二维内容 |
| Windows Launcher | preview，已做基础运行时验收 | 验证自备 CCI、启动 Azahar、检查所属端口、安装与 owned 恢复 |
| 单屏全屏 / aspect 同步 | preview，新增实机验收待补 | 默认 SingleScreen + -f；自动匹配主显示器比例，指定比例留边；双屏 Custom 可选 |
| 原生鼠标 | Windows preview，完整回归待补 | 陆战 rotation request、空战 reticle；Raw Input + process-memory |
| 菜单移上屏 | Launcher 可选实验开关，默认关闭 | 游戏 render-pass 路由；二维铺满和触控未完成；停止前检查 pass/cave 执行边界 |
| 俯仰修改 | 实验，不进入 Launcher | 下游 limiter 仍需确认 |
| Linux Launcher 与鼠标 | Steam Deck Desktop / Gaming Mode 源码 preview | XI2 + process_vm + Gamescope；Steam 直接启动、全屏与焦点、安装和 owned 恢复已跑通，物理战斗输入待用户验收 |
| macOS Launcher 与鼠标 | 尚未实现 | 平台无关核心、ABI 和后端契约已保留 |

## 玩家入口

主要交付为 `kiu-launcher-windows-preview.zip`，解压整个目录后运行 `KIU Launcher.exe`。
首次选择 Azahar 与自己的精确已解密 CCI，选择增强后「开始游戏」；进入战斗且有焦点时自动瞄准。
Windows F4 / Linux F12 暂停鼠标；「停止并恢复增强」只恢复本次安装的补丁，不关闭 Azahar。
CLI、源码 ZIP、wheel 和金手指留作附属工具，详见 [README](README.md) 与 [开发说明](docs/DEVELOPMENT.md)。

本轮已验证 GUI 与冻结 EXE 自检，以及指定构建上的补丁安装、鼠标菜单待机、停止清理。
停止后 FPS/宽屏/两组鼠标 hook 全部核对为 stock，Azahar 保持运行。
冷启动参数和 scheduler 60→30 过渡已分别验证并补回归；尚未重新验收完整选关、战斗或长时间输入。
新增 aspect preset、权限链、比例冲突与坐标失效已做离线回归；只读识别了当前 OpenGL 渲染窗口。
尚未实际保存新 preset 并验收 5:3/16:9/21:9 游戏画面及空战鼠标，不将这些检查算作新增战斗验收。

Steam Deck 已部署源码 Launcher 和并存的官方 Azahar 2126.1.1；原 2123.2 保留。
自备 CCI 的完整 source/code hash 验证通过，默认 Auto 得到 16:10 / 1280×800。
真实运行核对 fps/wide/aim/pin 安装、nonce 内存定位、XI2 输入启动及菜单待机；
停止后四项均核对为 stock，模拟器继续运行，原 EmuDeck 配置 hash 不变。
Linux 后端的断线重连空档已补有界等待和确定性回归；399 个本地测试通过，Deck 无 ROM 测试另有 Windows-only 跳过项。
新增 Steam 游戏模式入口直接启动 headless session，不弹 Tk。实际 Gamescope 的 `gamescope-wl`、游戏 display 与控制 display 已识别；
焦点匹配、1280×800 Vulkan 全屏 canvas、XI2 2.2 相对 XTEST 位移输入已核对，停止后四项补丁核对为 stock。
Steam Input 需为该入口选择 Gamepad with Mouse Trackpad；建议 R4=F12、L4=本 profile 的 Ctrl+Tab。
尚未验证实际鼠标/触摸板战斗、长时间运行、读档或多显示器；合成位移测试不是物理触控板验收。
独立配置和启动方式见 README 的 Linux 部分；这些基础检查不算新增战斗验收。

## 已知限制

- 鼠标 mailbox 的 host 读改写与 guest 清零仍存在竞争，不保证零丢包或严格一次消费。
- 读即时存档可能改变映射、带回 hook 与打开的 gate；尚无自动重连，读档前停止增强。
- Launcher 不重新接管已停止的会话；再次开始前自行关闭原 Azahar。未知/混合状态应完整重启游戏。
- Stop 只恢复本次 owned 补丁；已有补丁保持不变，CLI helper 单独退出也不等于全部恢复。
- Windows Launcher 根据保存的 SingleScreen 留边或 Custom 几何映射真实 OpenGL render child；Linux 仅支持已验证的 fullscreen X11/XWayland canvas（含 Vulkan）。未知或尺寸变化时空战待机。未同步也不回退桌面；陆战不依赖区域。
- 显示 preset 需首次确认、关闭全部 Azahar 后保存。默认单屏全屏的伸展/留边是共享字段；保留既有 Custom 几何。双屏选项仍改 Custom 上屏宽度。Stop 不撤销 preset。
- 独立 CLI 的默认桌面映射与旧 viewport side-by-side 假设仍保留，不等同于 Launcher 新路径。
- 同步模式只接受 3D=Off，不修改 3D 设置。复用 Azahar 所配置的上下屏切换（本次 Deck profile 为 Ctrl+Tab），但无法自动识别其内存 swap 状态；切下屏前用鼠标暂停键（Windows F4 / Linux F12）、回上屏后恢复。换布局/旋转/3D 或显示器前停止并重启。
- 新模拟器版本、关卡、DPI/多显示器、长时间输入和读档需要独立运行验收。
- 不同时使用同一补丁的持续金手指、其他 GDB 客户端或 Launcher 会话。

## 分发与后续

玩家包为新生成的 onedir 加当前文档、知识、清单和许可；不捆绑 Azahar、游戏或私人设置。
固定 hook 在构建时预生成并核对清单，冻结程序不带 Keystone 运行库，guest ABI 不变。
公开源码只发布 `main`；研究分支、私人 `local/` 数据和完整 `.git` 不发布。玩家二进制仍为本地发行候选，未创建远程 Release。
有效技术结论统一在 [知识库](docs/KNOWLEDGE.md)。

后续先完成 Launcher 全流程、鼠标协议时序与读档恢复验收，再验证跨平台后端。
