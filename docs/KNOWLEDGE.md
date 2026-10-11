# KIU 当前知识库

这里集中公开分发需要保留的游戏机制、文件格式、注入设计及验证方法。
仅收录当前工具可查证的结论；动态对象地址、私人日志和已撤回的假设不作为接口。
状态以 `STATUS.md` 为准，操作以 `README.md` 为准；本文件不是实机验收记录。
游戏中的 `FUN_地址` 是分析命名，`A/P/uiMgr/pass` 是对象角色，不是原作者符号。

## 1. 构建身份与地址

唯一支持：Kid Icarus: Uprising USA v0，TitleID `0004000000030100`，产品 `CTR-P-AKDE`。
权威身份在 `manifests/title.json`：

- 原始已解密 CCI SHA256：`98a5fd7708d4d31364953f05183c919093c9a1bac9a0a1b7a97fbb33c063cbc8`。
- 未压缩代码 SHA256：`2718aa2077a29560208c4e499288e5fecc4446d7adec95efb91ebf1b19eda679`。
- 代码镜像大小 `0xC64000`（12,992,512 字节），代码基址 `0x00100000`。

该构建的 `.text` 基址/有效大小为 `0x00100000/0x00B950F0`；
rodata 为 `0x00C96000/0x0009B990`；data 为 `0x00D32000/0x00031C78`。
有效段长度和分页 padding 不同。当前 cave 使用 `.text` 尾部对齐间隙，安装仍逐字节验空。
对象指针和宿主内存地址随会话变化，不能从某次捕获结果硬编码。
相同 TitleID 不等于相同构建；其他地区、更新及修改镜像不自动兼容。

所有代码改动记录地址、原字节、替换字节和构建 hash，见补丁模块和
`manifests/gateway_cheats.json`。下表原字节按文件/内存中的小端字节顺序书写。

| 控制点 | guest 地址 | 原字节 | 对应 cave / 用途 |
| --- | --- | --- | --- |
| 调度 dt | `0x0060E00C` | `408ab0ee` | `0x00C957C0`，dt × 0.5 |
| 全局时间输入 | `0x00607A74` | `080a90ed` | `0x00C957E0`，master × 0.5 |
| VS 投影上传尾部 | `0x008DEC80` | `000095e5` | `0x00C95840`，透视 X 修正 |
| GS 投影上传尾部 | `0x008DECCC` | `000095e5` | `0x00C958A0`，透视 X 修正 |
| 陆战旋转合并调用 | `0x00704004` | `a60801eb` | `0x00C95100`，读取旋转请求 |
| 准星 clamp 调用 | `0x006D2ED4` | `f8feffeb` | `0x00C95F00`，位置接管与观测 |
| 准星平滑之后 | `0x006D2F10` | `d470c4e5` | `0x00C95200`，重申宿主位置 |
| render-pass pre | `0x009167C0` | `541080e2` | `0x00C95640`，菜单目标路由 |
| render-pass post | `0x0091688C` | `541080e2` | `0x00C95700`，resolve 与恢复宽度 |

替换分支由模块按地址计算；不另维护手写副本。菜单还验证原始 12 字节 thunk：
`541080e2060091e80000a0e1`。完整 cave 字节由生成器确定并读回检查。

## 2. 真 60 FPS：解锁调度，同时保持游戏时间

实现：`src/kiu/patches/patch_60fps.py`；回归：`tests/test_patch_60fps.py`。
`*0x00D63824` 指向 scheduler，`+0x50` 的名义值必须是 30；
`+0x54` 的 stock interval 是 2，补丁改为 0，让完整帧管线每周期运行。
这个 scheduler 字段不能孤立写入：只解锁会令仍按 30 Hz 计时的系统加速。

必须同时修正两个时间源：

1. `FUN_0060DFF8` 输入仍是 `1/30 s`；在 `0x0060E00C` 将其存入 `s16` 前乘 0.5。
2. `FUN_00607A4C` 从 time 对象 `0x00E174E4+0x20` 取 master，
   stock 为 2 个 VBlank；在 `0x00607A74` 取值后乘 0.5，再派生公共 delta 通道。

写公共 delta 字段不是持久修复，因为更新函数会重新计算它们。
60 次更新 × 半份 delta，应与 stock 30 次更新 × 整份 delta 推进相同时间。
当前方案不跳 logic/render/post/present 阶段，不靠隔帧跳任务伪造计数器。
安装/恢复同时核对两处指令、两个 cave、scheduler 名义值和 interval。
离线算术和字节回归证明补丁结构；动画、音频及速度正常仍需实机 baseline 对照。

## 3. 宽屏：改变透视，不是拉伸完成帧

实现：`src/kiu/patches/patch_widescreen.py`；回归：`tests/test_patch_widescreen.py`。
两处上传尾部原指令是 `ldr r0,[r5]`；`r0` 指向 PICA uniform command block。
横向系数在 `+0x28`，纵向系数在 `+0x14`，`+0x40` 的精确 `-1.0f` 标识透视。
hook 先检查透视 marker，再以原始 X/Y 比例阈值区分上/下屏分支。

上屏原比例是 400:240（5:3）：横向系数乘 `(5/3)/目标 aspect`。
16:9 对应 `15/16`，21:9 对应 `5/7`；纵向投影保持不变，横向视野扩展。
正交 UI 不走该透视分支，因此普通 HUD 不直接被这个补丁缩放。
这不是改 ROM 纹理，也不是声明改变模拟器内部 framebuffer 分辨率。
模拟器最终显示区域应采用同一 aspect，否则仍会产生显示层拉伸。Launcher 经用户首次确认后
可保存匹配的显示 preset，CLI 补丁本身不改配置。最终宽屏显示会横向拉伸未重排的二维 HUD：
16:9 相对 5:3 约 6.7%，21:9 约 40%；不能把“hook 不改正交投影”说成“最终 HUD 完全不拉伸”。

普通下屏不变。仅在菜单路由标记 `0x00F36820==1` 时，
共享 extension cave `0x00C95800` 对下屏透视乘 `(4/3)/目标 aspect`，
21:9 对应 `4/7`。它只能修正该 pass 的透视内容，不能自动重排二维菜单。

### Launcher 显示 preset

实现：`src/kiu/azahar_display.py` 与 `backends/windows/display_preset.py`。
以官方 Azahar `2126.1.1` 为依据：桌面 `aspect_ratio` 不是有效的配置接口；
[Custom 布局](https://github.com/azahar-emu/azahar/blob/2126.1.1/src/core/frontend/framebuffer_layout.cpp)
直接使用 framebuffer raw pixel 的 x/y/width/height，不按窗口大小或 DPI 自动等比缩放。
[配置读写](https://github.com/azahar-emu/azahar/blob/2126.1.1/src/citra_qt/configuration/config.cpp)
只在全局文件处理 Custom 几何；本游戏的 layout/swap/upright 可单独覆写。
因此 preset 保留既有 top x/y/height 与全部 bottom 几何，仅将 top width 设为 round(height×aspect)，
本游戏选择 layout=6、swap=false、upright=false。5:3/16:9/21:9 在 height=480 时分别是 800/853/1120px。
读取 `default=true` 时用实际编译默认值，不信任旁边存储的旧数值；格式不明/重复键拒绝写入。
Custom 不等于自动铺满；小窗口会裁切，大窗口会留黑。该方案不改变 guest framebuffer 分辨率，
不修复二维 HUD/菜单，也不改变 internal resolution 或其他性能设置。

Launcher 默认改为原生 SingleScreen（layout=1）并以 `-f` 全屏启动；不是菜单 render-pass 注入。
自动比例用主显示器物理宽高，同时设置相同透视 aspect；指定比例则以
`screen_top_stretch=true` 和原生 `screen_top_leftright_padding/screen_top_topbottom_padding`
计算居中留边，保持同一比例，不在 16:10 屏幕硬拉成 16:9。单屏模式保留 Custom 几何、关闭下屏 stretch，
Azahar 原生 Swap 仍可显示原下屏，默认 F9；不改用户快捷键。留边是共享 raw pixel 设置，
不是自适应算法；换显示器、离开全屏或画布尺寸变化时要重新校准，鼠标遇到尺寸变化保持待机。
[官方 SingleFrameLayout](https://github.com/azahar-emu/azahar/blob/2126.1.1/src/core/frontend/framebuffer_layout.cpp)
控制两个 screen 的 enable 位，并分别采用上下屏 stretch 设置。保存后的配置不在 Stop 时恢复。

Windows 配置路径优先采用所选 Azahar 旁的 `user/config`，否则由系统查询 Roaming AppData；
中立层不依赖 Win32，未来平台后端只需提供路径及 physical render rectangle，guest ABI 不变。
`[Layout] render_3d` 的有效 global/title 值必须为 Off；其他立体模式会改变画区，写入前拒绝且不改该设置。
全部 Azahar 关闭后才保存；只编辑目标字段，不重写其余配置。写失败时仅回滚仍匹配本次 replacement 的文件，
成功后是持久 preset，Stop/退出不回滚配置。用户日后改配置不被 Launcher 的旧快照覆盖。
共享上屏宽度会影响其他 Custom 游戏，首次 GUI 确认明确提示；旧 Launcher 偏好不自动授予这项权限。

## 4. 原生鼠标：陆战转相机，空战移准星

实现：`src/kiu/backends/windows/native_fps_aim.py`、
`src/kiu/patches/patch_aim_request.py`、`src/kiu/patches/pin_reticle.py`。
`A` 是准星对象；`P` 是相机 controller。hook 动态记录指针，不采用固定运行时对象地址。

### 陆战调用链

`FUN_006D2ABC(A,P,...)` 读取 `A+0xA4/+0xA8`，限制准星范围，
将越界部分转为 `P+0xD38/+0xD3C` 的旋转请求。
`FUN_00703B68` 在 `0x00703FEC` 用 `vldmia` 取请求，
在 `0x00704004` 调用 `FUN_007462A4` 合并角度，
后者调用 `FUN_006063E8` 写 `P+0x2AC/+0x2B0` 并重建方向和 eye。

当前 hook 接管的是合并调用的参数 `s0/s1`，而不是不停写 controller 字段。
`r1=P+0x278` 由已校验的 setup 指令给出，cave 据此记录当前 `P`。
gate=1 时读取 mailbox yaw/pitch 并清零，同时替换游戏自己的准星边缘请求；
gate=0 时保持原参数，继续原函数。
hook 保持 `s2`（dt）和调用点栈布局，恢复寄存器后 tail-branch 到原 callee；
不能用增加栈帧的嵌套调用破坏原 callee 的栈参数。

reticle hook 提供 mode=1 固定到游戏中心的能力，但当前 driver 地面保持 mode=0 观测，
仅接管旋转请求并抑制原 edge request；不能称为已自动锁定中心。
Raw Input 的相对设备 counts 转成角度请求；默认灵敏度 `0.0025 rad/count`。
当前屏幕方向约定：鼠标右移为负 yaw，请求正 pitch 对应向下看；反转由宿主参数控制。
下游原有动态俯仰限制仍保留，不能把 literal 的 ±80° 上限宣称为实际可视范围。

### 空战调用链

当前空战链没有地面 controller（记录的 `P==0`），镜头沿预定路线运行。
鼠标应控制准星位置，不替换地面旋转请求。
pre hook 在 `0x006D2ED4` 写 `A+0xA4/+0xA8` 后进入原 clamp；
后续 `FUN_006D2820`、`FUN_0081739C` 仍会平滑位置，
late hook 在 `0x006D2F10` 先重放 displaced store，再在 mode=2 时重申宿主位置。
这样准星射线构建前读到的最终位置才跟随宿主，而不是只在平滑前改一次。

mode=2 的 x/y 是 viewport 像素，不是触摸坐标模拟。
Launcher 同步模式将所保存的 top rectangle 加到同 PID 的真实 render canvas 原点，
以物理桌面坐标映射到游戏 width/height。Windows 仅识别唯一 Qt OpenGL OwnDC child；
Vulkan 的 generic Qt child 不能可靠识别，故未知、裁切或最小化时保持空战 gate 关闭，区域恢复后可自动启用。
映射按启动时 preset 建立，不检测运行中手动改变 layout/swap/upright/3D。
原生 F9 上下屏切换不写回 INI，因此不能靠读配置辨别当前显示哪块；切下屏前 F4 暂停瞄准，回上屏后恢复。
其他布局/旋转/3D 或显示器改动前必须停止、关闭并重新开始。
worker 临时设置 per-monitor thread DPI 并在 finally 恢复，光标用 GetPhysicalCursorPos；不修改 Tk/process DPI。
未选择同步时 Launcher 也不使用整块桌面替代未知区域，空战绝对输入待机；陆战不依赖 viewport。
CLI 默认桌面映射仍保留；relative 模式可累计设备 counts，边界外运动会饱和丢弃。
CLI `--map viewport` 仍依赖 stock side-by-side 的 5/9 宽度假设，不能用于任意 custom layout。
auto 依据 reticle 调用活动与记录的 controller 切换 look/point；暂停/菜单不能只靠 `P==0` 判断。

### mailbox ABI 1

定义：`src/kiu/aim_protocol.py`、`manifests/aim_protocol.json`；字段全为小端 u32 / binary32。
地址都是 guest VA。魔数和版本不匹配应停止，不得把宿主 native pointer 放进 ABI。

| mailbox | 地址 / 大小 | 头部及宿主字段 | 客体写入字段 |
| --- | --- | --- | --- |
| ground | `0x00F36100` / `0x20` | `+00` magic `0x4D494158`；`+04` version 1；`+08` gate；`+0C/+10` yaw/pitch（rad） | `+14` frames；`+18` applied；`+1C` controller VA |
| reticle | `0x00F360C0` / `0x40` | `+00` magic `0x54455249`；`+04` version 1；`+08` mode；`+0C/+10` x/y（px） | `+14/+18` A/P；`+1C/+20` width/height；`+24/+28` calls/applied；`+2C/+30` centre；`+34/+38` 平滑后位置；`+3C` late 次数 |

reticle mode：0=只观测，1=中心，2=宿主位置。ground gate：0=游戏，1=宿主接管。
客体当前以 magic 判断有效输入；宿主安装/启动校验 magic 和 version。
此 ABI **不是原子队列**：host 读 pending、累加并写回，与 guest 读取并清零可能竞争。
清零减少旧请求持续作用，但不能证明零丢包或严格一次消费；高发送频率不等于正确协议。
任何字段布局或语义改动都需要新 ABI version、新 hook 指纹和兼容测试。

## 5. 菜单移上屏：render-pass 的目标字段

实现：`src/kiu/experimental/menu_render_pass.py`；回归：`tests/test_menu_render_pass.py`。
`*0x00D636BC` 是 UI manager；`uiMgr+0x140` 是战斗 HUD 指针，**不是屏幕编号**。
代码 `FUN_00A33CB4` 先检查 `uiMgr+0xC0 & 2` 与该指针，
screen=0 调 `FUN_0060D39C/FUN_0060D3EC`，screen=1 转 `FUN_0060D93C`。
当前菜单路由用 HUD 指针为空作门；这不是对所有 overlay 状态都完成认证的通用 UI 枚举。

`FUN_00BA9E14(pass)` 执行 pass 的 pre、callback、post。
pre=`0x009167C0`，post=`0x0091688C`；两者从 `pass+0x54` 加载 context 和 screen。
因此 `BottomRenderPass+0x58` 是这个游戏对象的 **screen 字段**（stock=1），不是函数地址。
callback 指针在 pass `+0x40`；callback `+0x4C/+0x50` 是 viewport 宽/高（stock 320/240）。

hook 以 pass 名的 `Bott`、`omRe` 前两字识别当前 BottomRenderPass，且 HUD 指针为空时：
pre 暂时把 callback width 设为 400，传 screen=0，并打开投影 marker；
post 同样 resolve target 0，关闭 marker，马上把 width 恢复 320。
对象的 `+0x58` 不被永久改写；战斗仍走原下屏 target=1 / width=320。
这是游戏 GPU 的原生目标路由，不是模拟器热键换布局或 CPU framebuffer copy。

路由不等于镜像：物理下屏可能保留旧帧。viewport 400 不等于版面自动从 320 扩展。
透视背景可以结合宽屏投影增加视野；二维节点、图标、背景几何需要各页重排和扩展。
触控仍按原下屏坐标；Gear/AR 等独立上屏路径未统一。
因此无拉伸铺满和可交互单屏菜单尚未完成，不能以标题背景一个成功画面替代全系统验收。

Launcher 现在提供启动前的 opt-in「单屏注入」checkbox，默认关闭；选中才安装这个游戏 hook。
它与 SingleScreen/fullscreen/Swap 三者独立，不把 ACTIVE_MARKER 当用户开关（该字是 pass 内部 pre/post 标记）。
取消本次安装通过 Stop 的 owned 恢复；改变选项需关闭 Azahar 后重新启动，不在任意 pass 中途热切换。
恢复前核对完整 thunk/cave 字节，再要求 marker=0 且所有 guest PC 不在两个 caves；
若正处于 pass 中就先 resume、在有界时间内重试，不满足条件拒绝恢复并提示重启。
菜单先于 owned 宽屏恢复；已有用户安装的菜单 hook 不被认领或卸载。

## 6. 相机实验的边界

### 6.1 俯仰范围

`src/kiu/experimental/patch_pitch_limit.py` 修改 `0x00746650/54` 的 ±1.39626336 rad literal，
并将 `0x00746504/0x00746518` 的条件 VFP move 改成无条件。
只改 literal 不足：每次调用还有动态上下界，literal 只是上限。
修改这些 move 也不证明最后渲染视角达到目标值；下游 limiter 和相机 basis 需独立验证。
该功能不默认安装；近 ±90° 的 look-at basis 可能退化，不作为稳定鼠标功能承诺。
Launcher 提供独立 checkbox 和角度输入（默认输入 60°、上限 89°）；保存选择供 Linux 游戏模式复用。
Linux 游戏模式仍可显式传 `--experimental-pitch-degrees` 覆盖保存值。
沿用完整游戏验证、所启动 PID 绑定和会话 owned 恢复，不增加独立 GDB 注入进程。
会话只接受 stock 或本次指定角度的完整四处字节；不同角度和混合状态拒绝，停止前也核对 owned 字节。
安装/恢复事务逐项 readback、不提前 resume；失败尝试回滚已知字节，不覆盖陌生值。实验选项默认关闭。

### 6.2 陆战停火后的自动回正

静态分析的链路为：`FUN_007DBAB4` 尾调用 `FUN_006D75A8(P+0x98, P+0x3AC)`，
更新射击/瞄准相关标志和计时。相关标志清零后，经过运行时阈值且保持倒计时结束，
写入 `P+0x178=1`。`FUN_00703268` 在没有特殊相机模式时由该标志选择 mode 1
（否则 mode 2）。倒计时有 2.0 秒的装载值，但不能据此把总停火延迟称作固定 2 秒。
准星更新会调用这条状态链；尚未据此证明 `P+0x178` 直接控制准星绘制。

`P+0x704` 的构造函数 `FUN_005BBB84` 绑定 vtable `0x00CF6D94`，
其 `+8` getter `FUN_00B0D3A4` 返回 1，确认该对象对应闲置模式。
主相机在 `0x00704038` 调用该对象的 `FUN_007CAE30`，其中
`FUN_007CAB04` 可自动跟随水平角度；后续插值以辅助状态 `P+0x494` 的默认俯仰为目标，
经 `FUN_006063E8` 发布角度和对应方向向量。闲置模块也会在退出 mode 1 时短暂淡出。

`src/kiu/experimental/patch_no_recenter.py` 仅取消该闲置模块的两处角度发布：

| 地址 | 原始字节（little-endian） | 替换字节 | 用途 |
| --- | --- | --- | --- |
| `0x007CADEC` | `7dedf8eb` | `00f020e3` | 自动水平跟随的 setter 调用 |
| `0x007CAFFC` | `f9ecf8eb` | `00f020e3` | 闲置 yaw/pitch 插值结果的 setter 调用 |

两处原指令均为 `BL 0x006063E8`，替换为 ARM NOP。
构建 SHA256 为 `2718aa2077a29560208c4e499288e5fecc4446d7adec95efb91ebf1b19eda679`。
不修改输入处理、停火计时、相机模式选择、准星可见性或全局 setter；
其他特殊模式及 mode 0 的强制恢复调用保持原字节。
第一处调用后重新读取 yaw 并记录增量，移除写入使该自动增量为零；两处均不依赖 setter 返回值。

默认关闭，可在 Launcher 选中「取消停火自动回正」，Linux 会话也可显式使用 `--experimental-no-recenter`；独立调试入口支持
`python -m kiu.experimental.patch_no_recenter status|apply|revert`。
Steam Deck 启动脚本直接读取 Launcher 保存的选择，不再读取私有 marker；
取消 checkbox 后下次启动使用原版自动回正，不改变独立的俯仰范围选择。
两处必须同时为 stock 或本补丁；构造函数、mode getter、调用参数及默认俯仰加载也需通过指纹。
会话不认领已有补丁，恢复只针对本次安装；两处写入失败会尝试回滚已知字节，绝不覆盖陌生字节。
回归覆盖精确指令、安装/恢复、混合与陌生状态拒绝、失败回滚和启动会话所有权。
当前为静态定位与离线测试通过，**尚无实际战斗停火、重新开火、闪避/剧情与章节切换验收**。

## 7. 注入与跨平台边界

当前主交付是官方 Azahar + Windows 独立 `KIU Launcher`；金手指和 CLI 为附属工具。
`game_build.py` 只读验证 CCI 容器、身份、完整 source/code hash，不提取或写出游戏数据。
`launcher_core.py` 启动所选 Azahar、检查端口属于本次进程，等待 scheduler 就绪后安装增强。
实际启动验证中，官方 Azahar `2126.1.1` 拒绝 `--gdbport`；`-g PORT` 能启动且端口 PID 匹配，
因此 core 使用 `-g`。这不代表所有旧版本均兼容，也不替代完整游戏流程验收。
冷启动观察到非空 scheduler 的 nominal 从 `60` 过渡到 `30`；仅在 FPS 静态字节全部 stock、
其他组件也通过验证时 resume 等待，并只在 nominal `30` 安装；未知值或 mixed 状态仍拒绝。
记录本次安装的 owned 补丁；Stop 关闭输入并按已验证字节恢复 owned 项，不关闭 Azahar。
已有补丁不被重新配置或收归 owned；未知字节不能覆盖。停止的会话不自动重新接管。
进入战斗且有焦点时自动启用输入，F4 只暂停鼠标；即时读档前仍需停止增强。
GDB 负责停 guest、验证原字节与空 cave、写 hook、初始化关闭状态 mailbox、连接 redirect、恢复运行。
当前 helper 高频数据走 Windows `ReadProcessMemory/WriteProcessMemory`，
启动用 GDB 写入 fresh nonce，再验证缓存或扫描宿主映射；不能把这一映射推广到整个 guest 空间。
[官方 GDB WriteMemory](https://github.com/azahar-emu/azahar/blob/2126.1.2/src/core/gdbstub/gdbstub.cpp)
会清 CPU 指令缓存，因此代码安装应走该类通道，高频 mailbox 不宜照用。

平台无关层是 `src/kiu/aim_core.py` 与 `src/kiu/aim_protocol.py`：
`MemoryTransport` 提供精确 guest 数据读写与 close；`InputSource` 提供相对设备 counts 与生命周期；
`WindowContext` 提供焦点、同一物理像素坐标系、viewport 或未知值。
输入回调不做 transport I/O，未知 viewport 不猜布局，接口不公开 Win32 句柄。
Linux 后端现有源码 preview：`src/kiu/backends/linux/`。XI2 提供原始相对 counts，
`process_vm_readv/writev` 只开放 ABI 1 的两块 mailbox，fresh nonce 扫描且拒绝多个命中，
同时校验 PID/starttime。Launcher 必须是模拟器的父进程（或已验证的 AppImage 祖先进程），
不修改 Yama/ptrace 安全策略、不要求 root。启动前先初始化 Xlib，再创建 Tk。
Qt 以 `xcb` 运行于 X11/XWayland；当前只接受唯一同 PID 的 fullscreen client，
再按授权 preset 映射上屏，未知区域不回退桌面。Steam Deck Desktop 与 Gaming Mode 的 Vulkan fullscreen canvas 已读取为 1280×800。
Gamescope 不维护 `_NET_CLIENT_LIST`；仅在 root 的 compositor PID 对应实际 `gamescope` / `gamescope-wl` 时查询 root children。
游戏 display 与 server 0 控制 display 必须属于同一 compositor；同时核对键盘和鼠标 focus display，避免 Steam overlay 期间继续输入。
focus display 的 CARDINAL32 保存 NUL 结尾的显示名，不是 server ID；只解析合法本地 display 前缀。
XI2 必须协商 2.2，libXi 在协商旧协议时会隐藏 raw sourceid；查询设备轴，拒绝绝对触摸/笔输入被误算成陆战相对位移。
Gaming Mode 的相对 XTEST 输入已收为原始 counts，但不代表物理 Deck 触控板或战斗验收。
对应实现依据为 [Gamescope](https://github.com/ValveSoftware/gamescope/blob/master/src/steamcompmgr.cpp) 与
[libXi raw event 解码](https://github.com/mirror/libXi/blob/master/src/XExtInt.c)。
窗口模式与原生 Qt Wayland 尚不支持。Linux 用 F12 暂停瞄准，避开原生 F4 游戏暂停；
Windows 保持 F4，不改模拟器的键位。游戏 hook 与 ABI 1 完全不变。
调试连接断开后端口可能短暂消失；在已绑定进程仍存活且 starttime 不变时最多等待两秒，
其他 PID、身份变化或未知代码仍立即拒绝。旧 2123.2 的 cold-start 调试断开后无法按当前流程重连，
Deck 部署改用并存的官方 2126.1.1，不覆盖原模拟器。操作和独立配置见 README 的 Linux 部分。
macOS 后端仍未实现。
修改宿主后端不应改变 guest ABI。CLI 帮助、导出、hash 校验不应加载 Windows 输入模块。

当前没有完整读档自动恢复。PID 不变、magic/version 相同，都不能证明宿主映射仍有效。
读档前停输入，之后重新验证代码、scheduler、mailbox 和 session anchor；混合状态应重启游戏。
独立 CLI helper 退出只关闭 gate/mode，完整恢复使用 `restore` 或重启；
Launcher 另外管理本次 owned hook 的恢复，两种退出语义不要混淆。
这些边界需要实机验证，不把‘能导入接口’写成‘已跨平台运行’。

Windows 玩家 EXE 使用构建时固定 payload，不依赖 Keystone 运行库：五个自写 hook（含可选菜单路由）由当前 ARM 源生成，
精确比对 canonical cave bytes；冻结加载核对 build hash 并继续运行 Capstone pool 契约。
这不改变 guest 代码、mailbox 或 ABI。生成文件只在 `local/build/`，玩家包不含游戏镜像。
GUI 使用现有 Tk；Linux 为源码 preview，macOS 尚未实现，不增加新的框架承诺。

后续可小范围验证官方 [RPC guest 内存接口](https://github.com/azahar-emu/azahar/blob/2126.1.2/src/core/rpc/rpc_server.cpp)
替代平台专属内存扫描；性能、时序、发行构建可用性与读档恢复尚未验收。
固定代码亦可研究加载时 `mods/<TitleID>/exefs/code.bps`，见
[官方加载器](https://github.com/azahar-emu/azahar/blob/2126.1.2/src/core/file_sys/ncch_container.cpp)。
BPS 不能省略 runtime scheduler 和 mailbox 管理；不分发修改后的完整 code.bin。
上述官方源码固定在 `2126.1.2`，不是声称该模拟器版本已通过本项目全套验收。
调试服务不可暴露给不可信网络；除经授权的 Launcher 显示 preset 外，控制工具不更改用户配置。

## 8. CCI / ExeFS 提取与验证

实现：`tools/dev/prepare_kiu.py`，入口 `python -m tools.dev.prepare_kiu`；
输入是用户提供的已解密 NoCrypto CCI，不是解密器。
NCSD magic 在 `0x100`，分区表从 `0x120` 开始；分区0基址=表中 offset × `0x200`。
表是 8 组交错的 `(offset, size)` 小端 u32，不是两个独立数组；分区0的 size 位于 `0x124`。
NCCH 外层 section offset/size 也是 `0x200` media units。
**ExeFS 内部 entry offset 是字节**：payload=`ExeFS base+0x200+entry offset`，不能再乘单位。
ExeFS entry table 为 10×`0x10`，代码名通常是 `.code`（带点），也识别 `code`。

exheader 从 NCCH `+0x200` 按声明长度 hash（当前构建 `0x400`）；
ExeFS superblock hash 覆盖其首 `0x200`。输出再记录 source/code SHA256 与构建字段。
已知 decrypted dump 的 per-file expected hash 来自密文，因而不匹配；
不能据此宣称解密内容损坏，也不能将这个不匹配当成完整性证明。
当前 verified workflow 不支持 compressed ExeFS code，不能误用资产的 LZ11 解压。
提取结果与机器路径只放 `local/`；公开清单仅保留兼容身份，不携带私人提取报告。

## 9. 资产格式：能够解析什么，不能推断什么

实现：`tools/dev/` 下的 `kiu_assets.py`、`lz11.py`、`extract_kiu_textures.py`；
回归：`tests/test_asset_formats.py`。脚本使用 `python -m tools.dev.<模块名>`。
有效链为 raw DARC / LZ11-wrapped DARC → nested DARC → CGFX DICT/TXOB texture。
DARC file offset 从 archive 起点算，不能再加 data offset；目录是 pre-order subtree 的 exclusive-end index。
CGFX relative offset 从存放该 offset 的字段地址算，不统一从文件头算。
LZ11 距离=`displacement+1`；重叠回溯要从不断增长的输出逐字节复制，并拒绝越界/截断。

像素 decoder 包括 RGBA8/RGB8、RGBA5551/RGB565/RGBA4、LA8/HILO8/L8/A8、
LA4/L4/A4、ETC1/ETC1A4，处理 8×8 tile swizzle，输出 RGBA；`--flip-y` 可改变导出朝向。
记录 mipmap 数但只导出 base image，不表示完整 mip/材质/模型/动画解析器，也不支持重新打包或纹理注入。
合成测试覆盖关键 DARC offset、非法 folder、L8 tile 和 LZ11 literal，不等于每种格式完整实机验收。
纹理工具输入是已提取 RomFS 目录，不从 CCI 解 RomFS；archive 递归有深度上限。
它的 title/hash 标记常量不等于对输入目录来源作 hash 验证。
纹理、raw payload、截图、存档与私人分析输出不可随 MIT 源码发布。

## 10. 陆战摇杆突推与独立冲刺键：静态定位

以下静态结论对应第 1 节的已验证 USA v0 code hash。独立按钮补丁见 10.1。
`FUN_0060F94C` 比较当前摇杆向量与 15 项时间历史，满足条件返回逻辑位 `0x800`，
否则返回 0。历史位于输入处理对象 `+0xB8`，每项为 age/x/y、stride `0x0C`，
环形索引为 `+0x16C`。它不是只按当前幅度切换走/跑，也不是简单的速度导数比较。

配置 getter `FUN_00B499D8` 根据配置对象 `+0x37C == 1` 选择另一组常量；
这个配置项的玩家界面含义尚未确认，不能据此给它命名。

| getter 结果 | 回看时间（游戏输入 dt） | 当前幅度至少 | 历史低幅度至多 |
| --- | --- | --- | --- |
| false | 约 33.33 ms | 0.925（平方阈值 0.855625） | 0.35（平方阈值 0.1225） |
| true | 约 50 ms | 0.95（平方阈值 0.9025） | 0.25（平方阈值 0.0625） |

幅度使用游戏内部向量单位。当前幅度达到高阈值后，时间窗内任一历史样本
低于低阈值，或历史方向与当前方向的归一化点积 `< 0.707`（约转向超过 45°），
即可产生事件。时间窗比较包含约 `0.00001526 s` 容差。
`0x0060FAB8` 的原字节 `028ba0e3` 为 `MOV r8,#0x800`；这里没有进行替换。

`FUN_0060FDA0` 在 `0x0060FEEC` 调用检测器（原字节 `96feffeb`），将结果并入逻辑输入。
`0x0060FFA8..0x0060FFBC` 计算 held/pressed/released，pressed 为 `(old XOR new) AND new`。
因此检测结果即使连续数次为真，也只有从 0 到 1 的边沿产生新的 pressed；
旧低幅度/旧方向移出时间窗后，保持相同方向的满幅输入不会持续生成新的冲刺事件。
这不代表已开始的冲刺会随输入位清零立即取消。

已接通的陆战输入链：`FUN_00A275BC` 拷贝逻辑 held/pressed/released 至帧输入 `+4/+8/+0C`；
`FUN_0060A15C` 将帧输入送入陆战环形队列，`FUN_00610468` 在其非零模式分支
将 pressed `0x800` 写成队列项 `+0x12`。零模式分支改取 held `0x01000000`，
不能假定所有控制模式都使用相同位。
`FUN_004E928C` 在 `0x004E92F0` 读取队列项 `+0x12`，检查可执行条件、方向及当前状态后，
在 `0x004E98B0` 调用 `FUN_004E6EE8`；后者经 `FUN_004D17A0` 和 `FUN_0036BFF8`
进入游戏类型名为 `StDashStart` 的状态，并初始化方向/参数。该路径还保留原有的数值扣减。
这些地址为分析命名，不是已认证的公开 hook 接口。

因此独立按键具有可行的原生入口：保留摇杆方向与普通移动，opt-in 时抑制自动突推，
按键按下边沿送入同一冲刺请求，让游戏继续管理冲刺状态和结束。
`FUN_006100C4` 已存在 `1 << mappingSlot` 的物理键到逻辑动作映射，
但尚未证明设置界面允许选择动作 11；不能宣称已能直接用菜单绑定。
10.1 的实现选用独立 Debug 输入并统一两个队列模式；仍需实机验证长按不重复、松开再按、转向、体力和状态限制。
“按住跑、松开停”是额外的状态控制要求，不由一次 pressed 事件自动实现。

### 10.1 独立按钮 dash（实验）

`experimental/patch_button_dash.py` 不增设宿主 mailbox，而是读取 Azahar HID Debug 位
`0x1000`；该输入独立于常规 ABXY、肩键与摇杆。使用时须将指定宿主按键绑定到
Azahar `button_debug`，再将 Deck 的专用背键映射到该宿主按键。

| 地址 | 原始字节 | 替换字节 | 用途 |
| --- | --- | --- | --- |
| `0x0060FEEC` | `96feffeb005085e100f020e3010000ea` | `000097e5010a10e3025bc5e3025b8513` | 不再调用摇杆 flick 检测；清除原 dash 位，再从 Debug held 生成 `0x800` |
| `0x006104DC` | `041091e5011401e2211ca0e1` | `081091e5021b01e2a115a0e1` | 零模式队列也读取 logical pressed `0x800`，不再采用 held `0x01000000` |

构建 SHA256 为 `2718aa2077a29560208c4e499288e5fecc4446d7adec95efb91ebf1b19eda679`。
总计替换 28 字节，无代码洞，不改变鼠标 ABI、模拟幅度、速度、体力或状态转移。
原版 edge 计算保留：按下请求一次，长按不重复，松开再按才有新请求；已有 dash
仍由游戏结束，不是松键立即停止。两种队列模式通过相同 pressed 请求，方向与限制保留。
默认关闭，Launcher 提供「独立按键冲刺」checkbox，桌面与 Steam 游戏模式共用保存设置；
Linux 入口保留 `--experimental-button-dash` 显式覆盖。运行时核对两个完整区域及上下文指纹，拒绝混合状态，
失败回滚已知写入，Launcher 仅恢复 owned 补丁。离线回归执行实际解码的输入/edge/队列指令，
覆盖按住、松开再按、其他输入保留及两种控制模式；实际战斗行为仍须验收。

### 移动速度：状态倍率与加减速

`FUN_004E44A4` 是普通移动与 dash 状态共同调用的速度计算路径。
以下使用分析用名称：controller 为 C，当前 motion 为 M，`W=*(C+0x38)`，
`Q=FUN_00510AD8(*(C+0x30), ...)`。W 和 Q 不是同一对象。
`0x004E4758` 的调用原字节为 `22d000eb`；第三参数实际是 W，不能只信
Ghidra 丢失浮点返回/参数的伪 C。

`FUN_005187E8` 返回的目标速度包含 `M+0x18`、条件性修正与状态倍率：
`StWalk`（enum 6）读取 `W+0x74`，`StDash`（enum 8）及 `StDashEnd`（enum 9）
读取 `W+0x78`；`StDashStart`（enum 7）不进入这两个倍率分支。
`0x005188B0` 原字节 `1e8a94ed`、`0x005188E0` 原字节 `1d8a94ed`
分别是这两组倍率的浮点读取。不能把这些倍率字段直接叫作绝对速度。

普通移动确实保留摇杆幅度，而非只用归一化方向。`FUN_004EAF0C` 从输入
队列项 `+8/+0x0C` 的 x/y 计算模长，将接近满幅的值视为 1，并输出方向。
`FUN_004BD660` 在 `0x004BD690`（原字节 `9fb000eb`）将 `StWalk+0x54`
作为 M 传给 `FUN_004E9914`。后者在模拟输入更新分支以 `Q+0x44` 为幅度分界，
低段在 `Q+0x50/+0x5C` 之间插值，高段在 `Q+0x68` 与经修正的 `Q+0x74`
之间插值；插值比例限制在 [0,1]。`0x004E9C58`（原字节 `060a84ed`）
将结果写入 `M+0x18`，再供上述目标速度计算使用。因此不是幅度无关，
也不是最终速度简单正比于原始摇杆幅度；曲线具体数值仍需读取运行时参数。

目标还受 `30.0 * *(float*)(C+0xE8)` 上限约束；此字段来源/运行时数值未核实。
当前速度读取 `M+0x1C`，加速分支为 `min(target, current + Q[+0x80] * dt)`；
制动分支使用 `Q+0x8C` 与 `FUN_00519A94`、`FUN_00549A18` 返回的修正量，
并限制不越过目标。最终方向乘以速度写入 `C+0x54/+0x58/+0x5C`。
`FUN_004E7E84` 还根据实际速度与阈值选择普通移动的慢/快动画，
所以视觉上的走/跑不能直接当成两档固定速度。

另在 `FUN_0065B290` 找到 `moveSpeed_`、`runSpeed_`（代码默认 150.0）和
`runRateMax_`（默认约 1.2）；尚未闭合它们到上述玩家路径的取值链。
这些默认值不证明当前武器的走/跑/dash 数值，也不证明跑速为走速的 1.2 倍。
目前没有读取当前战斗的运行时速度，没有安装速度补丁。

## 11. 如何复查与继续研究

`python -m unittest discover -s tests` 应在无 ROM checkout 运行；私人 code image 存在时增加原字节/hash检查。
`python run_kiu.py verify-build local/extracted/exefs_code.bin` 验证用户自己的完整 code hash。
`python run_kiu.py export-cheats -- --verify-code local/extracted/exefs_code.bin` 可同时验证导出元数据。
金手指只负责 guest 改动，不能读取宿主鼠标；取消勾选不会自动撤销内存写入，应单独执行 Restore。
同一补丁不能同时由循环金手指与 companion 管理，未知/混合字节要拒绝覆盖。

`python -m tools.dev.inspect_arm_static` 和 `python -m tools.dev.find_arm_mem_refs`
先验代码 hash，再输出原字节与候选。
ARM PC-relative 地址按 `instruction VA+8` 计算；前者 direct-call 搜索只枚举 unconditional ARM BL。
后者独立解码每个 word，可能把 literal 解成指令；相同 displacement 不证明同一对象或实际执行。
`python -m tools.dev.armscan` 跟直接分支但不是完整 CFG：
不自验构建、有限窗口、不追 indirect/jumptable 或进入 callees。
线性反汇编可能跨函数/数据、漏多出口；不匹配的 push/pop 是警报，不是唯一原因的证明。

修复必须比较相同输入与状态的 Azahar baseline，找最早分歧而非只解释最终画面。
鼠标要对等总 counts 的慢拖/快甩、空陆战切换、失焦、暂停、退出、读档作确定性回归；
菜单要逐页检查目标、viewport、投影、二维几何及触控；60 FPS 要比较墙钟、游戏时间和音频。
离线测试、字节/hash 验证与实机行为验收分别记录，不以测试数量代替功能结论。
