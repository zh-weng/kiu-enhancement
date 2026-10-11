# Azahar / Gateway 金手指整理

适用版本只有一个：Kid Icarus: Uprising USA v0，TitleID
`0004000000030100`，权威 `code.bin` SHA-256：
`2718aa2077a29560208c4e499288e5fecc4446d7adec95efb91ebf1b19eda679`。

生成命令：

```sh
python run_kiu.py export-cheats
```

输出：`local/cheats/0004000000030100.txt`。在 Azahar 的 Cheats 界面逐项添加，
或复制到该模拟器用户数据目录的 `cheats/` 下；目录位置随平台/配置不同。
安装项目后也可使用 `kiu-enhance export-cheats` 或 `python -m kiu export-cheats`。

只分享已接受的 60 FPS 与 16:9 / 21:9 宽屏（含恢复码，不含实验项）时使用：

```sh
python run_kiu.py export-cheats -- --basic --output local/cheats-basic/0004000000030100.txt --manifest local/cheats-basic/patches.json
```

基础版恰好五项：60 FPS 启用/恢复、两种宽屏启用、宽屏恢复。补丁和字节检查保持不变。

## 已整理的项目

| 功能 | 能否单独作为金手指 | 启动项 | 取消/恢复项 |
|---|---:|---|---|
| 真 60 FPS | 是 | `60 FPS - Enable` | `60 FPS - Restore 30 FPS` |
| 宽屏 16:9 | 是 | `Widescreen 16-9 - Enable` | `Widescreen - Restore 5-3` |
| 宽屏 21:9 | 是 | `Widescreen 21-9 - Enable` | `Widescreen - Restore 5-3` |
| 菜单下屏原生移到上屏 | 是，但当前为实验项；二维铺满未完成 | `Menu Bottom-to-Top Native Route - Enable` | `Menu Bottom-to-Top Native Route - Restore` |
| Native Mouse 客机 hook | **否，只有 hook** | `Native Mouse Guest Hooks - Enable` | `Native Mouse Guest Hooks - Restore` |
| 陆战俯仰范围 60° | 是，但仍是实验项 | `Ground Pitch Limit 60deg - Enable EXPERIMENTAL` | `Ground Pitch Limit - Restore` |

Native Mouse 那组只能安装陆战 rotation-request 与空战 reticle 两个客机
hook；Raw Input、自动判断战斗类型和高频 mailbox 更新仍需运行
`python run_kiu.py aim`（当前仅 Windows preview）。单独勾选该金手指不会产生鼠标输入。

## 正确的启动与取消方式

Azahar 取消勾选金手指时**不会自动恢复已经写入的内存**，因此每个功能都有
Restore 项：

1. 启动：只勾选对应的 `Enable`。
2. 取消：先取消勾选 `Enable`。
3. 勾选对应的 `Restore`，等待约一秒。
4. 再取消勾选 `Restore`。

不要同时开启 16:9 和 21:9。切换比例时先 Restore，再启动另一比例。
菜单原生路由应与目标比例的 Widescreen 项一起启用；Widescreen 会修正被路由页面中的
透视 3D 内容。模拟器显示区域的比例需要与补丁一致；工具不会覆盖全局布局配置。多数菜单仍按原 320 宽
版面绘制，右侧/两侧会留黑；重定向期间物理下屏还可能保留上一帧。
所以这项可以启动和恢复，但暂不算生产完成。

若读档后 60 FPS 或代码 hook 失效，执行一次上述 Restore → Enable 流程；这会
强制改变代码字节并让 Azahar 丢弃相应 JIT cache。文件中另有
`All Core Patches - Restore`，用于一次恢复 60 FPS、宽屏、菜单原生路由和 Native
Mouse 客机 hook；实验性俯仰补丁仍使用自己的 Restore。

全部固定写入点、原字节、替换字节与 build hash 记录在
`manifests/gateway_cheats.json`，由 `src/kiu/export_cheats.py` 从当前模块生成。
金手指不能提供宿主输入；hook、时间、投影与菜单的技术原理集中在 [知识库](KNOWLEDGE.md)。
