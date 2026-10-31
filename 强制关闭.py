"""可拖动的 Windows 浮窗：关闭下方窗口、结束进程、管理常规自启动。"""
import ctypes
import json
import os
import shutil
import sys
import time
import tkinter as tk
import uuid
import winreg
from pathlib import Path
from tkinter import messagebox

import psutil
import win32api
import win32com.client
import win32con
import win32gui
import win32process


# Preserve the old data folder so previously disabled startup entries remain restorable.
APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "窗口速关"
BACKUP_FILE = APP_DIR / "disabled-startup.json"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_KEYS = [(winreg.HKEY_CURRENT_USER, "HKCU", RUN_KEY),
            (winreg.HKEY_LOCAL_MACHINE, "HKLM", RUN_KEY),
            (winreg.HKEY_LOCAL_MACHINE, "HKLM", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run")]
IGNORED_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
GA_ROOT = 2
GW_HWNDNEXT = 2
WM_CLOSE = 0x0010
CARD_WIDTH, CARD_HEIGHT = 264, 56
HIT_SIZE, HIT_INSET = 48, 8


def card_position(center_x, center_y, work):
    left, top, right, bottom = work
    x = min(max(center_x - 32, left + 4), right - CARD_WIDTH - 4)
    below, above = center_y + 38, center_y - 38 - CARD_HEIGHT
    y = below if below + CARD_HEIGHT <= bottom - 4 else above
    return x, min(max(y, top + 4), bottom - CARD_HEIGHT - 4)


def is_admin():
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def elevated_launch():
    if is_admin() or "--no-elevate" in sys.argv:
        return False
    flags = "" if getattr(sys, "frozen", False) else f'"{Path(__file__).resolve()}"'
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, flags, None, 1)
    return result > 32


def top_level(hwnd):
    return ctypes.windll.user32.GetAncestor(hwnd, GA_ROOT)


def cloaked(hwnd):
    value = ctypes.c_int(0)
    try:
        result = ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(value), ctypes.sizeof(value))
        return result == 0 and value.value != 0
    except (AttributeError, OSError):
        return False


def find_underlay(overlay_hwnd, x, y):
    hwnd = win32gui.GetWindow(overlay_hwnd, GW_HWNDNEXT)
    while hwnd:
        if win32gui.IsWindowVisible(hwnd) and not win32gui.IsIconic(hwnd) and not cloaked(hwnd):
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            if left <= x < right and top <= y < bottom and win32gui.GetClassName(hwnd) not in IGNORED_CLASSES:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid and pid != os.getpid():
                    try:
                        process = psutil.Process(pid)
                        return {"hwnd": hwnd, "pid": pid, "created": process.create_time(),
                                "name": process.name(), "path": process.exe(), "title": win32gui.GetWindowText(hwnd)}
                    except psutil.AccessDenied:
                        return {"hwnd": hwnd, "pid": pid, "created": None,
                                "name": f"PID {pid}", "path": "", "title": win32gui.GetWindowText(hwnd)}
                    except psutil.NoSuchProcess:
                        pass
        hwnd = win32gui.GetWindow(hwnd, GW_HWNDNEXT)
    return None


def still_same(target):
    hwnd, pid = target["hwnd"], target["pid"]
    if not win32gui.IsWindow(hwnd) or win32process.GetWindowThreadProcessId(hwnd)[1] != pid:
        return False
    if target["created"] is not None:
        try:
            return psutil.Process(pid).create_time() == target["created"]
        except psutil.Error:
            return False
    return True


def startup_folders():
    return [Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup",
            Path(os.environ["PROGRAMDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"]


def matches_exe(command, exe):
    # shortcut: only direct executable-path matches; launcher wrappers need manual startup management.
    return bool(exe and exe.casefold() in os.path.expandvars(str(command)).casefold())


def startup_entries(exe):
    if not exe:
        return []
    entries = []
    for hive, hive_name, subkey in RUN_KEYS:
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
                for index in range(winreg.QueryInfoKey(key)[1]):
                    name, value, kind = winreg.EnumValue(key, index)
                    if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and matches_exe(value, exe):
                        entries.append({"kind": "registry", "hive": hive_name, "subkey": subkey,
                                        "name": name, "value": value, "value_type": kind,
                                        "label": f"{hive_name} Run · {name}"})
        except OSError:
            pass
    shell = win32com.client.Dispatch("WScript.Shell")
    for folder in startup_folders():
        if not folder.is_dir():
            continue
        for item in folder.iterdir():
            try:
                target = shell.CreateShortcut(str(item)).TargetPath if item.suffix.lower() == ".lnk" else str(item)
                if matches_exe(target, exe):
                    entries.append({"kind": "file", "path": str(item),
                                    "label": f"启动文件夹 · {item.name}"})
            except (OSError, AttributeError):
                pass
    return entries


def read_backups():
    if not BACKUP_FILE.exists():
        return []
    return json.loads(BACKUP_FILE.read_text(encoding="utf-8"))


def write_backups(records):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    temp = BACKUP_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(BACKUP_FILE)


def disable_startup(entry):
    records = read_backups()
    record = dict(entry, id=uuid.uuid4().hex, disabled_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    if entry["kind"] == "file":
        destination = APP_DIR / "disabled-shortcuts" / f'{record["id"]}_{Path(entry["path"]).name}'
        destination.parent.mkdir(parents=True, exist_ok=True)
        record["stored_path"] = str(destination)
    write_backups(records + [record])
    try:
        if entry["kind"] == "registry":
            hive = winreg.HKEY_CURRENT_USER if entry["hive"] == "HKCU" else winreg.HKEY_LOCAL_MACHINE
            with winreg.OpenKey(hive, entry["subkey"], 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
                current, kind = winreg.QueryValueEx(key, entry["name"])
                if current != entry["value"] or kind != entry["value_type"]:
                    raise RuntimeError("启动项已变化，请重新打开菜单。")
                winreg.DeleteValue(key, entry["name"])
        else:
            shutil.move(entry["path"], record["stored_path"])
    except Exception:
        write_backups(records)
        raise


def restore_startup(record):
    records = read_backups()
    if record["kind"] == "registry":
        hive = winreg.HKEY_CURRENT_USER if record["hive"] == "HKCU" else winreg.HKEY_LOCAL_MACHINE
        with winreg.OpenKey(hive, record["subkey"], 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
            try:
                winreg.QueryValueEx(key, record["name"])
                raise RuntimeError("原启动项名称已被占用。")
            except FileNotFoundError:
                pass
            winreg.SetValueEx(key, record["name"], 0, record["value_type"], record["value"])
    else:
        destination = Path(record["path"])
        if destination.exists():
            raise RuntimeError("原位置已有同名文件。")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(record["stored_path"], destination)
    write_backups([item for item in records if item["id"] != record["id"]])


class WindowCloser:
    KEY = "#010203"
    PROBE_X = 32
    PROBE_Y = 32

    def __init__(self, root):
        self.root = root
        self.root.title("强制关闭")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=self.KEY)
        self.root.attributes("-transparentcolor", self.KEY)
        work = win32api.GetMonitorInfo(win32api.MonitorFromPoint(
            (0, 0), win32con.MONITOR_DEFAULTTOPRIMARY))["Work"]
        start_x, start_y = (work[0] + work[2] - 64) // 2, (work[1] + work[3] - 64) // 2
        self.root.geometry(f"64x64+{start_x}+{start_y}")
        icon = Path(__file__).with_name("强制关闭-准星.ico")
        if icon.exists():
            self.root.iconbitmap(str(icon))
        self.target = None
        self.drag_start = None
        self.notice_until = 0
        self.notice = ""
        self.menu_open = False
        self.hover = False
        self.flash_until = 0
        self.top_job = None
        self.refresh_job = None
        self.show_details = tk.BooleanVar(value=False)
        self.canvas = tk.Canvas(root, bg=self.KEY, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.move)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        self.canvas.bind("<Button-3>", self.menu)
        self.canvas.bind("<Enter>", self.enter)
        self.canvas.bind("<Leave>", self.leave)
        self.card = tk.Toplevel(root)
        self.card.overrideredirect(True)
        self.card.attributes("-topmost", True)
        self.card_canvas = tk.Canvas(self.card, width=CARD_WIDTH, height=CARD_HEIGHT,
                                     bg="#17243c", highlightthickness=0, bd=0)
        self.card_canvas.pack()
        self.card_canvas.bind("<Button-3>", self.menu)
        self.card.withdraw()
        # shortcut: this invisible hit area also catches nearby clicks; shrink it if it blocks adjacent controls.
        self.hitbox = tk.Toplevel(root)
        self.hitbox.overrideredirect(True)
        self.hitbox.configure(bg=self.KEY)
        self.hitbox.attributes("-alpha", 0.01)
        self.hitbox.attributes("-topmost", True)
        self.hitbox.geometry(f"{HIT_SIZE}x{HIT_SIZE}+{start_x + HIT_INSET}+{start_y + HIT_INSET}")
        for event_name, handler in (("<ButtonPress-1>", self.press), ("<B1-Motion>", self.move),
                                    ("<ButtonRelease-1>", self.release), ("<Double-Button-1>", self.double_click),
                                    ("<Button-3>", self.menu), ("<Enter>", self.enter), ("<Leave>", self.leave)):
            self.hitbox.bind(event_name, handler)
        self.canvas.bind("<Double-Button-1>", self.double_click)
        self.root.update_idletasks()
        self.hwnd = top_level(root.winfo_id())
        self.hitbox_hwnd = top_level(self.hitbox.winfo_id())
        self.card_hwnd = top_level(self.card.winfo_id())
        self.resize()
        self.keep_on_top()
        self.refresh()

    def resize(self):
        if self.show_details.get():
            self.card.deiconify()
            self.position_card()
        else:
            self.card.withdraw()
        self.draw()
        self.raise_top()

    def position_hitbox(self):
        self.hitbox.geometry(f"{HIT_SIZE}x{HIT_SIZE}+{self.root.winfo_x() + HIT_INSET}+{self.root.winfo_y() + HIT_INSET}")

    def position_card(self, x=None, y=None):
        x = self.root.winfo_x() if x is None else x
        y = self.root.winfo_y() if y is None else y
        work = win32api.GetMonitorInfo(win32api.MonitorFromPoint((x + 32, y + 32),
                                      win32con.MONITOR_DEFAULTTONEAREST))["Work"]
        card_x, card_y = card_position(x + 32, y + 32, work)
        win32gui.SetWindowPos(self.card_hwnd, 0, card_x, card_y, CARD_WIDTH, CARD_HEIGHT,
                              win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)

    def raise_top(self):
        if not self.menu_open:
            try:
                win32gui.SetWindowPos(self.hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                      win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
                win32gui.SetWindowPos(self.hitbox_hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                      win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
                if self.show_details.get():
                    win32gui.SetWindowPos(self.card_hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                          win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
            except OSError:
                pass

    def keep_on_top(self):
        self.raise_top()
        self.top_job = self.root.after(500, self.keep_on_top)

    def enter(self, _event):
        self.hover = True
        self.draw()

    def leave(self, _event):
        self.hover = False
        self.draw()

    def draw(self):
        c = self.canvas
        c.delete("all")
        accent = ("#ef4444" if time.time() < self.flash_until else
                  "#22c55e" if self.drag_start and self.drag_start[4] else
                  "#facc15" if self.hover else "#3b82f6")
        x, y = self.PROBE_X, self.PROBE_Y
        c.create_line(x - 14, y, x - 5, y, fill=accent, width=2)
        c.create_line(x + 5, y, x + 14, y, fill=accent, width=2)
        c.create_line(x, y - 14, x, y - 5, fill=accent, width=2)
        c.create_line(x, y + 5, x, y + 14, fill=accent, width=2)
        c.create_oval(x - 2, y - 2, x + 2, y + 2, fill=accent, outline=accent)
        if self.show_details.get():
            c = self.card_canvas
            c.delete("all")
            c.create_rectangle(1, 1, CARD_WIDTH - 1, CARD_HEIGHT - 1,
                               fill="#17243c", outline="#354766", width=1)
            if time.time() < self.notice_until:
                line1, line2 = self.notice[:35], "拖动定位 · 双击关闭 · 右键更多"
            elif self.target:
                line1 = f'{self.target["name"]}  ·  PID {self.target["pid"]}'
                line2 = (self.target["title"] or "无标题窗口")[:34]
            else:
                line1, line2 = "下方没有可操作窗口", "把准心对准需要处理的窗口"
            c.create_text(11, 17, text=line1, fill="#e8ecff", anchor="w",
                          font=("Microsoft YaHei UI", 9, "bold"))
            c.create_text(11, 40, text=line2, fill="#aab2ca", anchor="w",
                          font=("Microsoft YaHei UI", 8))

    def target_below(self):
        x = self.root.winfo_rootx() + self.PROBE_X
        y = self.root.winfo_rooty() + self.PROBE_Y
        try:
            return find_underlay(self.hwnd, x, y)
        except Exception:
            return None

    def refresh(self):
        self.target = self.target_below()
        self.draw()
        self.refresh_job = self.root.after(220, self.refresh)

    def tell(self, text):
        self.notice, self.notice_until = text, time.time() + 3
        self.draw()

    def press(self, event):
        self.drag_start = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y(), False)
        self.hover = True
        self.draw()

    def move(self, event):
        if self.drag_start:
            x, y, left, top, _ = self.drag_start
            if abs(event.x_root - x) + abs(event.y_root - y) > 5:
                self.drag_start = (x, y, left, top, True)
            next_x, next_y = left + event.x_root - x, top + event.y_root - y
            flags = win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
            win32gui.SetWindowPos(self.hwnd, 0, next_x, next_y, 0, 0, flags)
            win32gui.SetWindowPos(self.hitbox_hwnd, 0, next_x + HIT_INSET, next_y + HIT_INSET, 0, 0, flags)
            if self.show_details.get():
                self.position_card(next_x, next_y)
            self.draw()

    def release(self, _event):
        if not self.drag_start:
            return
        self.drag_start = None
        self.draw()

    def double_click(self, _event):
        self.drag_start = None
        self.flash_until = time.time() + 0.7
        self.draw()
        self.close_window(self.target_below())

    def close_window(self, target):
        if not target or not still_same(target):
            self.tell("窗口已变化，请重新定位")
            return
        try:
            win32gui.PostMessage(target["hwnd"], WM_CLOSE, 0, 0)
            self.tell(f'已请求关闭 {target["name"]}')
        except OSError:
            self.tell("关闭请求未送达，可尝试管理员权限")

    def kill_process(self, target):
        if not target or not still_same(target):
            self.tell("进程已变化，请重新定位")
            return
        if target["pid"] in (0, 4, os.getpid()):
            self.tell("不允许结束该系统进程")
            return
        try:
            psutil.Process(target["pid"]).kill()
            self.tell(f'已结束 {target["name"]} · PID {target["pid"]}')
        except (psutil.Error, OSError) as error:
            messagebox.showerror("结束进程失败", str(error), parent=self.root)

    def disable(self, entry):
        try:
            disable_startup(entry)
            self.tell(f'已关闭自启动：{entry["name"] if "name" in entry else Path(entry["path"]).name}')
        except (OSError, RuntimeError, ValueError) as error:
            messagebox.showerror("关闭自启动失败", str(error), parent=self.root)

    def restore(self, record):
        try:
            restore_startup(record)
            self.tell("已恢复自启动")
        except (OSError, RuntimeError, ValueError) as error:
            messagebox.showerror("恢复失败", str(error), parent=self.root)

    def menu(self, event):
        target = self.target_below()
        menu = tk.Menu(self.root, tearoff=False)
        if target:
            menu.add_command(label=f'{target["name"]}  ·  PID {target["pid"]}', state="disabled")
            menu.add_command(label=(target["title"] or "无标题窗口")[:50], state="disabled")
            menu.add_separator()
            menu.add_command(label="关闭准心下的窗口", command=lambda t=target: self.close_window(t))
            menu.add_command(label="强制结束进程（未保存内容会丢失）", command=lambda t=target: self.kill_process(t))
            startup = tk.Menu(menu, tearoff=False)
            try:
                entries = startup_entries(target["path"])
                for entry in entries:
                    startup.add_command(label=entry["label"], command=lambda e=entry: self.disable(e))
                if not entries:
                    startup.add_command(label="未找到常规启动项", state="disabled")
            except Exception as error:
                startup.add_command(label=f"读取失败：{error}", state="disabled")
            menu.add_cascade(label="关闭该进程的自启动", menu=startup)
        else:
            menu.add_command(label="下方没有可操作窗口", state="disabled")
        try:
            records = read_backups()
            if records:
                restore_menu = tk.Menu(menu, tearoff=False)
                for record in records[-20:]:
                    restore_menu.add_command(label=record["label"], command=lambda r=record: self.restore(r))
                menu.add_cascade(label="恢复已禁用的自启动", menu=restore_menu)
        except (OSError, ValueError):
            pass
        menu.add_separator()
        menu.add_checkbutton(label="显示详情卡片", variable=self.show_details, command=self.resize)
        menu.add_command(label="管理员权限" if is_admin() else "普通权限（重启后提权）",
                         state="disabled" if is_admin() else "normal", command=self.restart_admin)
        menu.add_command(label="退出工具", command=self.close)
        self.menu_open = True
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
            self.menu_open = False
            self.raise_top()

    def restart_admin(self):
        if elevated_launch():
            self.close()
        else:
            self.tell("未获得管理员权限")

    def close(self):
        for job in (self.top_job, self.refresh_job):
            if job:
                self.root.after_cancel(job)
        self.root.destroy()


if __name__ == "__main__":
    if not elevated_launch():
        window = tk.Tk()
        WindowCloser(window)
        window.mainloop()
