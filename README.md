# KIU Enhancements

Kid Icarus: Uprising 的 Azahar 增强工具：真 60 FPS、透视宽屏，以及预览中的原生鼠标瞄准。
使用官方 Azahar，不捆绑游戏或修改版模拟器。源码许可证为 MIT。

只支持 **USA v0**：TitleID `0004000000030100`，产品码 `CTR-P-AKDE`。
不支持其他地区、更新版本或未知修改后的代码。准确 hash 见 [构建清单](manifests/title.json)。

| 功能 | 当前支持 |
| --- | --- |
| 60 FPS、16:9 / 21:9 战斗宽屏 | 已有实机验收；可以单独使用金手指 |
| 原生鼠标 | Windows preview；陆战转镜头，空战控制准星 |
| 菜单下屏移上屏 | 实验开关；二维铺满与触控联动未完成 |
| Linux / macOS | 平台无关核心与接口已定义；鼠标后端尚未实现，实机兼容待验收 |

## 快速开始

Python 3.11+，先在项目目录安装依赖：

```sh
python -m pip install -e .
python kiu.py --help
```

只有 60 FPS / 宽屏需求：

```sh
python kiu.py export-cheats
```

输出在 `local/cheats/`，启用、取消和恢复方式见 [金手指说明](docs/GATEWAY_CHEATS.md)。
这条命令不需要 ROM，也不会改 Azahar 配置。

### 运行时控制工具

用户自行安装 Azahar 并启动自己的游戏。启用 Azahar 的 GDB stub，端口默认 `24689`；
不要把调试端口暴露到不可信网络。不要同时运行其他 GDB 客户端或相同补丁的持续金手指。
至少等到游戏完成加载再安装补丁；60 FPS 需要 scheduler 对象已经存在。

推荐先用自己提取的代码确认完整构建 hash：

```sh
python kiu.py verify-build /path/to/your/code.bin
python kiu.py status
python kiu.py apply --aspect 1.7777777778
```

`apply` 默认只安装 60 FPS 和 16:9。21:9 使用 `--aspect 2.3333333333`。
运行时仍逐点校验原字节/已安装字节，拒绝混合状态；这些局部指纹不代替整个代码 SHA256。
工具不会自动拉伸窗口或覆盖用户的布局配置，Azahar 显示区域比例需与补丁匹配。

Windows 原生鼠标（进入战斗后）：

```sh
python kiu.py aim
python kiu.py aim -- --map relative --sensitivity 0.0025
```

`aim` 自动安装客体鼠标 hook，不自动安装 FPS/宽屏。F4 开关输入；Esc 停止 helper。
默认陆战使用相对 Raw Input，空战映射桌面光标位置。更多参数：`python kiu.py aim -- --help`。
退出只关闭输入，hook 仍保留；停止 helper 后恢复：

```sh
python kiu.py restore
```

菜单实验须显式 `python kiu.py apply --menu-route`；不建议作为普通用户默认配置。
实验俯仰补丁不在统一入口默认安装或恢复范围内。

## 限制与跨平台边界

鼠标仍是预览：陆战 mailbox 有并发读改写竞争，读即时存档后也没有自动重锚/重连。
读档前停止 helper，读档后重新启动；未知状态应完整重启游戏，不强行覆盖。
当前 viewport 映射仅基于原始 side-by-side 布局，不能当作任意 custom layout 的自动识别。
这次仓库整理没有重新完成所有模拟器版本、关卡、DPI 和长时间输入验收。

客体 hook、little-endian mailbox ABI、GDB 安装和补丁生成不依赖宿主系统。
宿主的输入、窗口信息、数据通道通过 [aim_protocol.py](tools/aim_protocol.py) 定义契约。
Linux/macOS 后续可接平台适配器或经验证的 Azahar RPC 数据通道，不改变现有客体 ABI；
目前不声称两平台鼠标功能可用。更多说明见 [架构与注入方案](docs/ARCHITECTURE.md)。

## 开发与分发

```sh
python -m unittest discover -s tests
python tools/build_release.py --check
python tools/build_release.py
```

发布脚本只按白名单收集源码，并生成金手指及校验清单。输出在忽略的 `local/releases/`。
**不要直接压缩整个项目目录**：`local/` 可能包含用户游戏、研究内存、存档和本机配置。
当前开发分支为 `codex/development`；整理前源码和当时未提交的源码修改保留在
仅本地的 `codex/research-before-cleanup`。开发分支使用独立的干净根提交，不携带研究历史；
旧实验与个人信息只在本地研究分支追溯，不加入发行分支或当前源代码包。
本地研究数据仍保留原来的 `local/` 路径并被忽略，不进入 Git。
获准发布时只推送开发分支；不要使用 `git push --all` 或 `git push --mirror`，不要发布研究分支。

当前有效研究结论统一从 [知识入口](docs/KNOWLEDGE.md) 查阅；
另见 [开发说明](docs/DEVELOPMENT.md)、[整理记录](docs/CLEANUP.md)、[当前状态](STATUS.md)。
此仓库不包含 ROM、游戏素材、提取后的 code.bin、存档或 Azahar 二进制；
MIT 只覆盖本项目代码，不改变 Nintendo、Azahar 或依赖库的权利与许可证。
