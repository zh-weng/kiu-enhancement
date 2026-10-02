"""Small desktop launcher; GUI and preferences are separate from game I/O."""
from __future__ import annotations

import argparse
import json
import math
import os
import queue
import sys
import tempfile
from pathlib import Path

from .launcher_core import LauncherOptions, LauncherSession, validate_options


ASPECTS = {"原始 5:3": None, "16:9": 16 / 9, "21:9": 21 / 9}
DEFAULTS = {"azahar": "", "game": "", "fps": True, "aspect": "16:9",
            "mouse": True, "sensitivity": 0.0025}
MOUSE_STATUS = {"connecting": "正在连接鼠标 hook…", "anchoring": "正在定位鼠标内存（首次可能约 30 秒）…",
                "waiting": "增强已安装，等待进入战斗…", "active": "鼠标瞄准已启用 · F4 可暂停",
                "off": "鼠标已暂停 · F4 可重新启用", "inactive": "鼠标待机：游戏失焦或战斗未活动",
                "complete": "鼠标输入已停止"}


def settings_path() -> Path:
    """Only local user preferences; never emulator configuration or telemetry."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "KIUEnhancements/launcher.json"


def checked_preferences(data: object) -> dict:
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("不支持的启动器设置格式")
    result = dict(DEFAULTS)
    for key in ("azahar", "game"):
        if key in data:
            if not isinstance(data[key], str) or "\0" in data[key]:
                raise ValueError("无效的文件路径")
            result[key] = data[key]
    for key in ("fps", "mouse"):
        if key in data:
            if type(data[key]) is not bool:
                raise ValueError("无效的开关设置")
            result[key] = data[key]
    if data.get("aspect", result["aspect"]) not in ASPECTS:
        raise ValueError("不支持的宽屏比例")
    result["aspect"] = data.get("aspect", result["aspect"])
    sensitivity = data.get("sensitivity", result["sensitivity"])
    if type(sensitivity) not in (int, float) or not math.isfinite(sensitivity) or not 0 < sensitivity <= 1:
        raise ValueError("鼠标灵敏度应在 0 到 1 之间")
    result["sensitivity"] = float(sensitivity)
    return result


def load_preferences(path: Path) -> dict:
    return checked_preferences(json.loads(path.read_text(encoding="utf-8")))


def save_preferences(path: Path, values: dict) -> None:
    """Atomically replace our own preferences, never expose them in releases."""
    data = checked_preferences({"version": 1, **values})
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".launcher-", suffix=".json", delete=False) as handle:
        pending = Path(handle.name)
        json.dump({"version": 1, **data}, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    try:
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


class LauncherApp:
    def __init__(self, root, tk, ttk, preferences: Path):
        self.root, self.tk, self.ttk = root, tk, ttk
        self.preferences = preferences
        self.events = queue.Queue()
        self.session = None
        self.closing = False
        self.was_alive = False
        values = dict(DEFAULTS)
        warning = ""
        try:
            values = load_preferences(preferences)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as exc:
            warning = f"无法读取上次设置，将使用默认值：{exc}"

        root.title("KIU Launcher — Windows Preview")
        root.geometry("700x510")
        root.minsize(640, 470)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        if sys.platform == "win32":
            ttk.Style(root).theme_use("vista")
        frame = ttk.Frame(root, padding=22)
        frame.grid(sticky="nsew")
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Kid Icarus: Uprising", font=("Segoe UI", 20, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, text="官方 Azahar + USA v0 · 不修改 ROM，不覆盖模拟器配置").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 20))
        self.azahar = tk.StringVar(value=values["azahar"])
        self.game = tk.StringVar(value=values["game"])
        self.editable = []
        for row, label, variable, kind in ((2, "Azahar", self.azahar, "azahar"),
                                           (3, "游戏文件", self.game, "game")):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10))
            entry = ttk.Entry(frame, textvariable=variable)
            entry.grid(row=row, column=1, sticky="ew", pady=6)
            button = ttk.Button(frame, text="选择…", command=lambda k=kind: self.pick_file(k))
            button.grid(row=row, column=2, padx=(8, 0))
            self.editable.extend((entry, button))

        options = ttk.LabelFrame(frame, text="增强选项", padding=12)
        options.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(14, 12))
        self.fps = tk.BooleanVar(value=values["fps"])
        self.mouse = tk.BooleanVar(value=values["mouse"])
        self.aspect = tk.StringVar(value=values["aspect"])
        self.sensitivity = tk.StringVar(value=f"{values['sensitivity']:g}")
        fps = ttk.Checkbutton(options, text="真 60 FPS", variable=self.fps)
        fps.grid(row=0, column=0, sticky="w", padx=(0, 18))
        mouse = ttk.Checkbutton(options, text="原生鼠标（preview）", variable=self.mouse)
        mouse.grid(row=0, column=1, columnspan=2, sticky="w")
        ttk.Label(options, text="画面比例").grid(row=1, column=0, sticky="w", pady=(12, 0))
        aspect = ttk.Combobox(options, values=tuple(ASPECTS), state="readonly",
                              textvariable=self.aspect, width=15)
        aspect.grid(row=1, column=1, sticky="w", pady=(12, 0))
        ttk.Label(options, text="灵敏度").grid(row=1, column=2, padx=(20, 8), pady=(12, 0))
        sensitivity = ttk.Entry(options, textvariable=self.sensitivity, width=10)
        sensitivity.grid(row=1, column=3, pady=(12, 0))
        self.editable.extend((fps, mouse, aspect, sensitivity))

        actions = ttk.Frame(frame)
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        self.start_button = ttk.Button(actions, text="开始游戏", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="停止并恢复增强", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=10)
        self.status = tk.StringVar(value="首次选择文件，以后直接开始。")
        ttk.Label(frame, textvariable=self.status, wraplength=640).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(2, 8))
        self.log = tk.Text(frame, height=5, wrap="word", state="disabled", relief="flat",
                           font=("Segoe UI", 9))
        self.log.grid(row=7, column=0, columnspan=3, sticky="nsew")
        frame.rowconfigure(7, weight=1)
        ttk.Label(frame, text="进入战斗且窗口有焦点时自动瞄准；F4 暂停。即时读档前先停止增强。\n"
                  "宽屏比例须与 Azahar 显示布局匹配；菜单实验默认不启用。", wraplength=640).grid(
            row=8, column=0, columnspan=3, sticky="w", pady=(12, 0))
        root.protocol("WM_DELETE_WINDOW", self.close)
        if warning:
            self.report(warning)
        root.after(100, self.poll)

    def report(self, message: str):
        message = MOUSE_STATUS.get(message, message)
        self.status.set(message)
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 100:
            self.log.delete("1.0", "2.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def pick_file(self, kind: str):
        from tkinter import filedialog
        variable = self.azahar if kind == "azahar" else self.game
        filename = filedialog.askopenfilename(parent=self.root,
            title="选择 Azahar" if kind == "azahar" else "选择自己提供的已解密 USA v0 CCI",
            filetypes=[("Azahar", "*.exe"), ("所有文件", "*")] if kind == "azahar" else
                      [("3DS CCI 游戏", "*.cci *.3ds"), ("所有文件", "*")])
        if filename:
            variable.set(filename)

    def set_busy(self, busy: bool):
        for widget in self.editable:
            widget.configure(state="disabled" if busy else
                "readonly" if isinstance(widget, self.ttk.Combobox) else "normal")
        self.start_button.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")

    def start(self):
        from tkinter import messagebox
        if self.session is not None and self.session.alive:
            return
        if self.session is not None and self.session.process is not None and self.session.process.poll() is None:
            messagebox.showinfo("游戏仍在运行", "增强已停止，但 Azahar 仍在运行。\n关闭这次游戏后，再从启动器开始。", parent=self.root)
            return
        try:
            values = checked_preferences({"version": 1, "azahar": self.azahar.get().strip(),
                "game": self.game.get().strip(), "fps": self.fps.get(), "mouse": self.mouse.get(),
                "aspect": self.aspect.get(), "sensitivity": float(self.sensitivity.get())})
            if not values["azahar"] or not values["game"]:
                raise ValueError("请先选择 Azahar 和自己的游戏文件。")
            options = LauncherOptions(azahar=Path(values["azahar"]), game=Path(values["game"]),
                fps=values["fps"], aspect=ASPECTS[values["aspect"]], mouse=values["mouse"],
                sensitivity=values["sensitivity"])
            validate_options(options)
            session = LauncherSession(options, self.events.put)
            session.start()
            self.session = session
            self.was_alive = True
            self.set_busy(True)
            try:
                save_preferences(self.preferences, values)
            except OSError as exc:
                self.report(f"本次可以运行，但未能保存设置：{exc}")
        except (OSError, ValueError, RuntimeError) as exc:
            messagebox.showerror("无法开始", str(exc), parent=self.root)

    def stop(self):
        if self.session is not None and self.session.alive:
            self.session.stop(restore=True)
            self.stop_button.configure(state="disabled")
            self.report("正在停止输入并恢复本次安装的增强…定位内存阶段可能需要稍等。")

    def close(self):
        self.closing = True
        if self.session is not None and self.session.alive:
            self.stop()
        else:
            self.root.destroy()

    def poll(self):
        while True:
            try:
                self.report(str(self.events.get_nowait()))
            except queue.Empty:
                break
        alive = self.session is not None and self.session.alive
        if self.was_alive and not alive:
            self.was_alive = False
            self.set_busy(False)
            if self.session.error:
                self.report("增强失败：" + self.session.error)
        if self.closing and not alive:
            self.root.destroy()
            return
        self.root.after(100, self.poll)


def prepare_tk():
    """Keep Conda's DLL search handle alive; no global environment edits."""
    if sys.platform == "win32" and not getattr(sys, "frozen", False):
        directory = Path(sys.prefix) / "Library/bin"
        if directory.is_dir():
            return os.add_dll_directory(str(directory))
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, help="local preference file (also useful for development QA)")
    parser.add_argument("--self-test", action="store_true", help="check bundled GUI and patch dependencies without a game")
    args = parser.parse_args(argv)
    dll_directory = prepare_tk()
    import tkinter as tk
    from tkinter import ttk
    root = tk.Tk()
    app = LauncherApp(root, tk, ttk, args.settings or settings_path())
    if args.self_test:
        root.withdraw()
        from .patches import patch_aim_request, pin_reticle
        patch_aim_request.assemble_hook()
        pin_reticle.assemble_hook()
        pin_reticle.assemble_late_hook()
        root.update_idletasks()
        root.destroy()
    else:
        root.mainloop()
    if dll_directory is not None:
        dll_directory.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
