# 分发形态

当前选择：官方 Azahar + 轻量控制工具，不要求模拟器 fork。
发行候选是源码 preview 与可单独使用的金手指；不包含游戏或模拟器。
独立可执行文件、自动启动 Azahar、独立 profile 管理尚未实现。

## 运行与注入

安装后推荐 `kiu-enhance` 或 `python -m kiu`；未安装的源码入口为 `python run_kiu.py`。
GDB 负责验证、安装和恢复客体 hook；实时鼠标写入使用独立数据通道。
当前 Windows 后端实现输入、窗口查询与 process-memory transport。
用户自行启动游戏和启用调试接口；工具不覆盖游戏文件或全局配置。

游戏机制、补丁站点、mailbox ABI、注入顺序、JIT 注意事项与后续 BPS/RPC 候选，
统一见 [知识库](KNOWLEDGE.md#7-注入与跨平台边界)。
完整支持范围见 [当前状态](../STATUS.md)，使用步骤见 [README](../README.md)。

## 源码边界

- `src/kiu/`：正式运行包与统一 CLI，包含平台无关瞄准核心、协议、导出及 GDB 通道。
- `src/kiu/patches/`：当前核心补丁；`experimental/`：菜单路由与俯仰实验，不默认安装。
- `src/kiu/backends/windows/`：延迟加载的 Windows 宿主实现。
- `tools/dev/`：提取、资产解析及只读 ARM 分析，不混进用户运行入口。
- `tools/build_release.py`：白名单打包；`manifests/`：公开兼容与补丁元数据。

Linux/macOS 可替换宿主后端而不改变 guest hook 与 ABI；两平台鼠标后端尚未实现。
不能把平台无关接口或 CI 配置当作三平台原生输入已可用的证明。

## 发布边界

运行包与开发工具使用同一份源码，不增加后台服务、账号或复杂 GUI。
发布包按白名单构建，不压缩整个目录或携带 `.git` 与私人 `local/` 内容。
MIT 仅覆盖项目代码；第三方依赖、Azahar 与 Nintendo 内容保留各自权利。
本地整理和生成源包不代表已推送或已发布 GitHub Release。
