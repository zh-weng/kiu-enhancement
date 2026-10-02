# 开发与验证

Python 3.11+，从仓库根目录执行：

```sh
python -m pip install -e .
python -m unittest discover -s tests
```

纹理开发工具另需 `python -m pip install -e ".[assets]"`；运行时鼠标不依赖 numpy/Pillow。
运行包位于 `src/kiu/`；安装后推荐 `kiu-enhance` 或 `python -m kiu`，
未安装时源码入口为 `python run_kiu.py`。
研究工具位于 `tools/dev/`，用模块方式运行，不直接执行旧目录下的脚本。

## 无 ROM 测试

hook 生成、反汇编契约、FakeGdb 安装/恢复/拒绝未知字节、ABI 和打包测试不需要游戏。
只有私人原始镜像逐字节检查可以在缺少镜像时跳过；不得将核心测试整体跳过。
Windows 专有后端测试在其他平台明确跳过。CI 和本机离线测试不能替代模拟器实机验收。
完整生成器测试使用 macOS Intel；Apple Silicon 的 Keystone 依赖另需验证，
无第三方依赖的核心测试不代表该架构完整补丁生成或输入支持。

## 可选的私人镜像检查

```sh
python -m tools.dev.prepare_kiu /path/to/your/decrypted-game.cci
python run_kiu.py verify-build local/extracted/exefs_code.bin
python run_kiu.py export-cheats -- --verify-code local/extracted/exefs_code.bin
python -m unittest discover -s tests
```

输出在忽略的 `local/`，不覆盖 `manifests/title.json`。
只支持已确认构建的 NoCrypto CCI；不提供解密能力，也不支持压缩 ExeFS code。

只读分析示例：

```sh
python -m tools.dev.inspect_arm_static --range 0x00703fe0 0x00704008
python -m tools.dev.find_arm_mem_refs --help
python -m tools.dev.armscan --help
python -m tools.dev.extract_kiu_textures --help
```

格式规则、扫描工具局限、原生瞄准链和修改验证原则集中在 [知识库](KNOWLEDGE.md)。
新增 backend 保持 guest-VA 读写、counts、物理像素坐标和关闭/失焦语义；
guest ABI 变更必须升级版本、指纹与兼容测试。
每个修复需要确定性回归，并与同状态同输入的 Azahar baseline 比较最早分歧。

## 生成发行候选

```sh
python tools/build_release.py --check
python tools/build_release.py
```

输出在 `local/releases/`。验证白名单、依赖、包内知识库、Enable/Restore 金手指及校验清单。
在不含 `local/` 的干净目录安装并运行测试，不依赖研究机环境或私人路径。
发行物不捆绑模拟器、不改用户全局配置，不含游戏素材、代码、存档或私人研究。
Windows 独立可执行文件暂未实现；当前交付形态是源码包 + 生成的金手指。
核对 [当前状态](../STATUS.md) 的 preview/experimental 标签，打包成功不能写成产品验收完成。
GitHub 推送或创建 Release 需用户另行授权。
