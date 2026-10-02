# KIU Enhancements

Kid Icarus: Uprising 的 Azahar 增强工具：真 60 FPS、透视宽屏，以及预览中的原生鼠标瞄准。
配合官方 Azahar 使用，不捆绑游戏或修改版模拟器。项目代码采用 MIT。

只支持 **USA v0**：TitleID `0004000000030100`、产品码 `CTR-P-AKDE`。
其他地区、更新或未知修改后的代码不兼容，准确 hash 见 [构建清单](manifests/title.json)。

| 功能 | 支持情况 |
| --- | --- |
| 真 60 FPS、16:9 / 21:9 战斗宽屏 | 已有模拟器运行验收；可以仅用金手指 |
| 原生鼠标 | Windows preview；陆战转镜头，空战控制准星 |
| 菜单下屏移上屏 | 实验；无拉伸铺满与触控联动未完成 |
| Linux/macOS 鼠标 | 尚未实现；已保留平台无关核心与后端接口 |

## 安装

需要 Python 3.11+。在项目目录执行：

```sh
python -m pip install -e .
python run_kiu.py --help
```

安装后推荐使用 `kiu-enhance` 或 `python -m kiu`；源码目录可用 `python run_kiu.py`，参数相同。

## 仅使用 60 FPS / 宽屏金手指

```sh
python run_kiu.py export-cheats
```

文件输出到 `local/cheats/`。这一步不需要 ROM，也不改 Azahar 配置。
在 Azahar 添加所需 Enable 项；取消时要单独执行 Restore，不能只取消勾选。
具体项目和操作见 [金手指说明](docs/GATEWAY_CHEATS.md)。

## 运行时增强

自行启动 Azahar 和游戏，启用 GDB stub（默认端口 `24689`）。
至少等游戏完成加载；不要同时运行其他 GDB 客户端或相同补丁的持续金手指。
调试端口不要暴露到不可信网络。

推荐先检查自己提取的完整代码，再安装增强：

```sh
python run_kiu.py verify-build /path/to/your/code.bin
python run_kiu.py status
python run_kiu.py apply --aspect 1.7777777778
```

默认 `apply` 安装 60 FPS 和 16:9；21:9 用 `--aspect 2.3333333333`。
运行时逐点校验补丁字节，未知或混合状态会拒绝覆盖；局部指纹不代替完整 SHA256。
Azahar 显示区域比例应与补丁匹配，工具不会替你拉伸窗口或覆盖布局配置。

### Windows 原生鼠标

进入战斗后运行：

```sh
python run_kiu.py aim
```

F4 开关瞄准，Esc 停止 helper。`aim` 自动安装鼠标 hook，不自动安装 FPS/宽屏。
陆战用相对 Raw Input，空战默认映射桌面光标；参数见 `python run_kiu.py aim -- --help`。
退出只关闭输入，hook 仍保留。停止 helper 后可完整恢复核心增强：

```sh
python run_kiu.py restore
```

鼠标仍有 mailbox 时序竞争，读即时存档后没有自动重连。
读档前停止 helper，之后重新启动；混合状态应完整重启游戏，不强制覆盖。
当前 `--map viewport` 只适用于原始 side-by-side 布局，不能自动识别任意 custom layout。

菜单实验须显式 `python run_kiu.py apply --menu-route`，不作为默认稳定功能。
实验俯仰补丁不在统一入口的默认安装/恢复范围内。

## 更多说明

- [当前状态与限制](STATUS.md)
- [集中知识库：游戏机制、hook、ABI、格式与验证方法](docs/KNOWLEDGE.md)
- [分发形态](docs/ARCHITECTURE.md) · [开发与测试](docs/DEVELOPMENT.md)

源码包用白名单生成，不要压缩整个工作目录：私人 `local/` 内容不属于发行物。
仓库不包含 ROM、游戏素材、提取代码、存档或 Azahar 二进制。
MIT 只覆盖项目代码，不改变 Nintendo、Azahar 或依赖库的权利与许可证。
