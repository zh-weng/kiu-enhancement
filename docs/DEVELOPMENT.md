# 开发和验证

使用 Python 3.11+：`python -m pip install -e .`。
纹理开发工具另需 `python -m pip install -e ".[assets]"`；运行时鼠标不依赖 numpy/Pillow。

## 无 ROM 测试

`python -m unittest discover -s tests`。
hook 生成、反汇编契约、FakeGdb 安装/恢复/拒绝未知字节、协议和打包测试不需要游戏。
只有原始游戏镜像逐字节比对在缺少私人镜像时跳过；不得将核心测试整体跳过。
CI 配置运行 Windows/Linux/macOS；Windows 专有后端测试在其他平台明确跳过。
配置了 CI 并不等于三平台已经实机验收，发布前应查看实际 CI 和运行结果。
完整生成器测试暂用 macOS Intel runner，Apple Silicon 另测无第三方依赖的核心。
Keystone 的 Apple Silicon 原生依赖尚未验证，不以 Intel 测试宣称 ARM64 已兼容。
runner 架构见 [GitHub 官方列表](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)。

## 私人镜像校验

```sh
python tools/prepare_kiu.py /path/to/your/decrypted-game.cci
python kiu.py verify-build local/extracted/exefs_code.bin
python tools/export_gateway_cheats.py --verify-code local/extracted/exefs_code.bin
python -m unittest discover -s tests
```

提取器输出在 `local/extracted/`，用户 manifest 在 `local/title.json`，不覆盖公开兼容清单。
仅验证过 USA v0 的 NoCrypto dump；压缩 ExeFS `.code` 明确不支持，不能误套资产 LZ11。

静态工具：`inspect_arm_static.py`、`find_arm_mem_refs.py`、`armscan.py`。
`--image` 可指定私人镜像；默认仅查 `local/extracted/exefs_code.bin`。
资产工具：`kiu_assets.py`、`extract_kiu_textures.py`、`lz11.py`。
研究输出不得提交，不能用合成 fixture 测试代替实机行为验收。

## 修改原则

每处 code patch 必须先比较原字节，记录地址、原字节、替换字节、构建 hash。
安装顺序：cave → 关闭状态 mailbox → redirect；恢复顺序：断 redirect → 清 cave。
代码写入使用可暂停 guest、处理 JIT 的通道；实时数据写入不得当作代码安装替代。
禁止未知状态的强制覆盖。补丁组合需要检查 cave 范围不重叠。

ABI v1 是现有布局，并不提供原子传递保证。协议演进先加兼容测试，再升级 hook/version。
平台后端不公开 native handle、指针宽度或 OS 坐标类型。
新增后端须保持 guest-VA exact read/write、输入 counts 语义、关闭/失焦/异常清理语义一致。

## 发布前

检查 `STATUS.md` 的 preview / experimental 标签；不要把源码打包成功称为完整产品验收。
用 `build_release.py --check` 和正常打包验证允许文件清单。
发行物不捆绑模拟器、不改用户全局配置，不包含任何游戏派生大文件。
Windows 可执行文件打包暂未实现；目前正式产物是源码包 + 生成的金手指。
此轮本机测试 141/141 通过；实际源码发布包在无 ROM 目录通过 137 项，另 4 项私人镜像检查按设计跳过。
源包隐私白名单和 SHA256 清单核验通过；三平台 CI 尚需发布到 GitHub 后实际运行。
