# 开发与验证

Python 3.11+，从仓库根目录执行：

```sh
python -m pip install -e .
python -m unittest discover -s tests
python run_launcher.py
```

安装后 GUI 入口为 `kiu-launcher`，CLI 为 `kiu-enhance` 或 `python -m kiu`。
未安装时源码 CLI 用 `python run_kiu.py`。运行包位于 `src/kiu/`，
研究工具在 `tools/dev/`，用 `python -m tools.dev.NAME`，不直接执行单文件。
纹理开发另需 `python -m pip install -e ".[assets]"`；玩家 EXE 不需要这些环境。

## 无游戏测试与 GUI 自检

安装/恢复/拒绝未知字节、hook、冻结 payload、ABI、构建验证与打包测试不需要 ROM。
只有私人原始镜像逐字节检查在缺少镜像时跳过，不得跳过核心回归。
Windows 专有后端测试在其他平台明确跳过；CI 与离线测试不能代替模拟器战斗验收。
macOS Intel 的生成器测试不表示 Apple Silicon 的 Keystone 或原生输入已经兼容。

```sh
python run_launcher.py --self-test --settings local/launcher-qa/preferences.json
```

GUI 自检构造界面并验证 hook 依赖，不启动游戏；QA 设置使用本地路径，不加载真实用户偏好。
格式规则、扫描工具局限、原生瞄准链与回归要求统一见 [知识库](KNOWLEDGE.md)。

## 生成 Windows 玩家包

在 Windows 构建；固定 PyInstaller 版本，不修改已有 Python 环境：

```sh
python -m pip install --target local/build-tools pyinstaller==6.22.3
python tools/build_launcher.py --check
python tools/build_launcher.py
```

输出 `local/releases/kiu-launcher-windows-preview.zip`。
spec、缓存和 generated hook payload 在 `local/build/launcher/`，不提交。
构建从 ARM 源生成五个固定 hook（含可选菜单路由），与 canonical 清单比对；冻结运行不依赖 Keystone。
脚本检查 Tcl/Tk、Capstone、许可与发行边界，再以独立 QA 设置运行 EXE 自检后打包。
不包含游戏、模拟器、个人设置或研究目录，不能把打包成功称为战斗验收完成。
CI 的手动 `build_windows_launcher` 开关可执行同一流程，不自动创建 Release。

## 附属源码与私人校验

```sh
python -m tools.dev.prepare_kiu /path/to/your/decrypted-game.cci
python run_kiu.py verify-build local/extracted/exefs_code.bin
python run_kiu.py export-cheats -- --verify-code local/extracted/exefs_code.bin
python tools/build_release.py --check
python tools/build_release.py
```

提取输出只在忽略的 `local/`，不覆盖公开清单；仅支持已确认 NoCrypto 构建，不提供解密能力。
源码发行物需在无 `local/` 的干净目录安装测试；源码包与 wheel 是开发者附属，不代替玩家 EXE。
每个修复仍需确定性回归与相同状态/输入的 Azahar baseline。
ABI 改动必须升级版本、指纹与兼容测试。GitHub 推送和 Release 需另行授权。
