# -*- coding: utf-8 -*-
"""工作清單啟動器 - 一次開啟一組 App／資料夾／檔案／網址，並把視窗整齊排好。

作者：ArchieKUO
"""
import os
import sys
import json
import re
import time
import math
import copy
import uuid
import shutil
import threading
import queue
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except Exception:  # 沒有拖放套件時，其他功能照常
    HAS_DND = False

APP_NAME = "工作清單啟動器"
APP_VERSION = "v0.1"
AUTHOR = "作者：ArchieKUO"
GOLD = "#B8912A"
IS_WIN = sys.platform == "win32"

TYPES = ["App", "資料夾", "檔案", "網址"]
SCREEN_LABEL = {0: "跟隨清單", 1: "螢幕1", 2: "螢幕2"}
SCREEN_FROM = {v: k for k, v in SCREEN_LABEL.items()}
NEW = "__new__"          # 項目表最下面的空白輸入列
NEWLIST = "__newlist__"  # 清單最下面的「＋ 新增清單」
SELF_ID = "self"

# ----------------------------------------------------------------------------
# 設定檔
# ----------------------------------------------------------------------------


def new_id():
    return uuid.uuid4().hex[:8]


def self_item():
    return {"id": SELF_ID, "kind": "self", "type": "App", "name": "工作清單（本工具）",
            "path": "", "screen": 0, "checked": True}


def new_list(name):
    return {"id": new_id(), "name": name, "screen": 1, "layout": "auto",
            "items": [self_item()], "rects": {}}


def cfg_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "WorkspaceLauncher")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "config.json")


def load_config(path=None):
    path = path or cfg_path()
    cfg = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = None
    if not isinstance(cfg, dict) or not cfg.get("lists"):
        first = new_list("早上上班")
        cfg = {"lists": [first], "current": first["id"], "compact": False,
               "autostart": False, "autorun": "", "geometry": ""}
    for lst in cfg["lists"]:
        lst.setdefault("screen", 1)
        lst.setdefault("layout", "auto")
        lst.setdefault("rects", {})
        lst.setdefault("items", [])
        if not any(i.get("id") == SELF_ID for i in lst["items"]):
            lst["items"].insert(0, self_item())
        for it in lst["items"]:
            it.setdefault("checked", True)
            it.setdefault("screen", 0)
    cfg.setdefault("compact", False)
    cfg.setdefault("autostart", False)
    cfg.setdefault("autorun", "")
    cfg.setdefault("geometry", "")
    if cfg.get("current") not in [l["id"] for l in cfg["lists"]]:
        cfg["current"] = cfg["lists"][0]["id"]
    return cfg


def save_config(cfg, path=None):
    path = path or cfg_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ----------------------------------------------------------------------------
# 排版計算（座標一律是 0~1 的比例：x, y, w, h）
# ----------------------------------------------------------------------------


def auto_rects(n):
    if n <= 0:
        return []
    if n == 1:
        return [(0.0, 0.0, 1.0, 1.0)]
    if n == 2:
        return [(0.0, 0.0, 0.5, 1.0), (0.5, 0.0, 0.5, 1.0)]
    if n == 3:
        return [(0.0, 0.0, 0.6, 1.0), (0.6, 0.0, 0.4, 0.5), (0.6, 0.5, 0.4, 0.5)]
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    res = []
    left = n
    for r in range(rows):
        k = min(cols, left)
        left -= k
        for c in range(k):
            res.append((c / k, r / rows, 1.0 / k, 1.0 / rows))
    return res


def effective_screen(lst, item):
    return item.get("screen") or lst.get("screen", 1)


def group_ids(lst, screen):
    return [i["id"] for i in lst["items"]
            if i.get("checked") and effective_screen(lst, i) == screen]


def screens_in_use(lst):
    s = sorted({effective_screen(lst, i) for i in lst["items"] if i.get("checked")})
    return s or [lst.get("screen", 1)]


def get_rects(lst, screen):
    """回傳 {項目id: (x,y,w,h)}；自訂排版只有在項目組成沒變時才沿用。"""
    ids = group_ids(lst, screen)
    stored = lst.get("rects", {}).get(str(screen))
    if lst.get("layout") == "custom" and stored and set(stored) == set(ids):
        return {i: tuple(stored[i]) for i in ids}
    return dict(zip(ids, auto_rects(len(ids))))


SNAPS = [0.25, 1 / 3, 0.4, 0.5, 0.6, 2 / 3, 0.75]
EPS = 1e-3


def snap(v):
    for s in SNAPS:
        if abs(v - s) < 0.025:
            return s
    return round(v, 3)


def edge_segments(rects):
    """找出相鄰格子之間的邊界線段：('v', x, y0, y1) 或 ('h', y, x0, x1)。"""
    segs = []
    items = list(rects.values())
    for a in items:
        for b in items:
            if a is b:
                continue
            if abs(a[0] + a[2] - b[0]) < EPS:
                y0, y1 = max(a[1], b[1]), min(a[1] + a[3], b[1] + b[3])
                if y1 - y0 > EPS:
                    segs.append(("v", b[0], y0, y1))
            if abs(a[1] + a[3] - b[1]) < EPS:
                x0, x1 = max(a[0], b[0]), min(a[0] + a[2], b[0] + b[2])
                if x1 - x0 > EPS:
                    segs.append(("h", b[1], x0, x1))
    return segs


def move_edge(rects, orient, pos, newpos, minsize=0.12):
    """把邊界線從 pos 拖到 newpos；任何格子小於 minsize 就回傳 None。"""
    out = {}
    for k, (x, y, w, h) in rects.items():
        if orient == "v":
            if abs(x + w - pos) < EPS:
                w = newpos - x
            elif abs(x - pos) < EPS:
                w = (x + w) - newpos
                x = newpos
        else:
            if abs(y + h - pos) < EPS:
                h = newpos - y
            elif abs(y - pos) < EPS:
                h = (y + h) - newpos
                y = newpos
        if w < minsize or h < minsize:
            return None
        out[k] = (x, y, w, h)
    return out


# ----------------------------------------------------------------------------
# Windows 視窗操作
# ----------------------------------------------------------------------------

FAKE_MONITORS = [dict(x=0, y=0, w=1920, h=1040, primary=True),
                 dict(x=1920, y=0, w=1920, h=1040, primary=False)]

if IS_WIN:
    import ctypes
    import ctypes.wintypes as wt
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    P = ctypes.c_void_p
    user32.IsWindowVisible.argtypes = [P]
    user32.GetWindow.argtypes = [P, ctypes.c_uint]
    user32.GetWindow.restype = P
    user32.GetWindowLongW.argtypes = [P, ctypes.c_int]
    user32.GetWindowTextLengthW.argtypes = [P]
    user32.GetWindowTextW.argtypes = [P, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetClassNameW.argtypes = [P, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [P, ctypes.c_void_p]
    user32.IsIconic.argtypes = [P]
    user32.IsZoomed.argtypes = [P]
    user32.ShowWindow.argtypes = [P, ctypes.c_int]
    user32.SetWindowPos.argtypes = [P, P, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_uint]
    user32.GetWindowRect.argtypes = [P, ctypes.c_void_p]
    user32.GetAncestor.argtypes = [P, ctypes.c_uint]
    user32.GetAncestor.restype = P
    user32.GetMonitorInfoW.argtypes = [P, ctypes.c_void_p]
    dwmapi.DwmGetWindowAttribute.argtypes = [P, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
    kernel32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
    kernel32.OpenProcess.restype = P
    kernel32.CloseHandle.argtypes = [P]
    kernel32.QueryFullProcessImageNameW.argtypes = [P, ctypes.c_uint, ctypes.c_wchar_p,
                                                    ctypes.c_void_p]
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, P, P)
    MONITORENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, P, P, P, P)

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                    ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


def get_monitors():
    """螢幕工作區（已扣掉工作列），主螢幕在前，其餘由左到右。"""
    if not IS_WIN:
        return FAKE_MONITORS
    mons = []

    def cb(hm, hdc, rc, lp):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(mi)
        user32.GetMonitorInfoW(hm, ctypes.byref(mi))
        r = mi.rcWork
        mons.append(dict(x=r.left, y=r.top, w=r.right - r.left, h=r.bottom - r.top,
                         primary=bool(mi.dwFlags & 1)))
        return 1

    user32.EnumDisplayMonitors.argtypes = [P, P, MONITORENUMPROC, P]
    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), None)
    mons.sort(key=lambda m: (not m["primary"], m["x"]))
    return mons or FAKE_MONITORS


def monitor_for(screen):
    mons = get_monitors()
    return mons[screen - 1] if 0 < screen <= len(mons) else mons[0]


def list_windows():
    """回傳 [(hwnd, 標題, 類別, pid)]：只含看得到、有標題的頂層視窗。"""
    if not IS_WIN:
        return []
    res = []

    def cb(h, lp):
        try:
            if not user32.IsWindowVisible(h) or user32.GetWindow(h, 4):
                return 1
            ex = user32.GetWindowLongW(h, -20)
            if (ex & 0x80) and not (ex & 0x40000):
                return 1
            cloaked = ctypes.c_uint(0)
            dwmapi.DwmGetWindowAttribute(h, 14, ctypes.byref(cloaked), 4)
            if cloaked.value:
                return 1
            n = user32.GetWindowTextLengthW(h)
            if n == 0:
                return 1
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(h, buf, n + 1)
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(h, cls, 256)
            pid = ctypes.c_uint(0)
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            res.append((int(h), buf.value, cls.value, pid.value))
        except Exception:
            pass
        return 1

    user32.EnumWindows.argtypes = [WNDENUMPROC, P]
    user32.EnumWindows(WNDENUMPROC(cb), None)
    return res


def proc_exe(pid):
    if not IS_WIN:
        return ""
    h = kernel32.OpenProcess(0x1000, 0, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_uint(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
    finally:
        kernel32.CloseHandle(h)
    return ""


def win_area(hwnd):
    if not IS_WIN:
        return 0
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return max(0, r.right - r.left) * max(0, r.bottom - r.top)


def frame_adjust(hwnd):
    """Windows 10/11 視窗有看不見的邊框，量出來才能貼齊。"""
    r, e = wt.RECT(), wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    if dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(e), ctypes.sizeof(e)) != 0:
        return 0, 0, 0, 0
    return e.left - r.left, e.top - r.top, r.right - e.right, r.bottom - e.bottom


def place_window(hwnd, x, y, w, h):
    if not IS_WIN:
        return
    if user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, 9)
        time.sleep(0.08)
    dl, dt, dr, db = frame_adjust(hwnd)
    user32.SetWindowPos(hwnd, None, x - dl, y - dt, w + dl + dr, h + dt + db,
                        0x0004 | 0x0010 | 0x0040)


def own_hwnd(root):
    if not IS_WIN:
        return 0
    return int(user32.GetAncestor(root.winfo_id(), 2) or 0)


def snapshot():
    return {w[0] for w in list_windows()}


def wait_new(before, timeout=15.0, pred=None, settle=1.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        new = [w for w in list_windows() if w[0] not in before and (pred is None or pred(w))]
        if new:
            time.sleep(settle)
            new = [w for w in list_windows() if w[0] not in before and (pred is None or pred(w))]
            if new:
                return max(new, key=lambda w: win_area(w[0]))[0]
        time.sleep(0.3)
    return None


def resolve_lnk(p):
    try:
        ps = "(New-Object -ComObject WScript.Shell).CreateShortcut('%s').TargetPath" % p.replace("'", "''")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                             text=True, timeout=10, creationflags=0x08000000).stdout.strip()
        return out
    except Exception:
        return ""


def default_browser_exe():
    if not IS_WIN:
        return ""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice")
        prog = winreg.QueryValueEx(k, "ProgId")[0]
        k2 = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog + r"\shell\open\command")
        cmd = winreg.QueryValueEx(k2, "")[0]
        m = re.match(r'\s*"?([^"]+?\.exe)', cmd, re.I)
        return m.group(1) if m else ""
    except Exception:
        return ""


def find_existing(item):
    """找這個項目目前已經開著的視窗（不會開新的）。"""
    t, p = item.get("type"), os.path.expandvars(item.get("path", ""))
    wins = list_windows()
    if t == "App":
        exe = p
        if p.lower().endswith(".lnk"):
            exe = resolve_lnk(p) or p
        exe = os.path.normcase(os.path.normpath(exe))
        base = os.path.basename(exe)
        hits = [w for w in wins if os.path.basename(os.path.normcase(proc_exe(w[3]))) == base]
        return max(hits, key=lambda w: win_area(w[0]))[0] if hits else None
    if t == "資料夾":
        name = os.path.basename(p.rstrip("\\/")) or p
        hits = [w for w in wins if w[2] == "CabinetWClass" and w[1].lower() in (name.lower(), p.lower())]
        return hits[0][0] if hits else None
    if t == "檔案":
        stem = os.path.splitext(os.path.basename(p))[0].lower()
        hits = [w for w in wins if stem and stem in w[1].lower()]
        return hits[0][0] if hits else None
    return None


def open_item(item):
    """開啟項目並回傳它的視窗；已經開著的會直接沿用。"""
    if not IS_WIN:
        return None
    t, p = item.get("type"), os.path.expandvars(item.get("path", ""))
    if t == "網址":
        before = snapshot()
        exe = default_browser_exe()
        name = os.path.basename(exe).lower()
        if exe and name in ("chrome.exe", "msedge.exe", "brave.exe", "vivaldi.exe", "opera.exe"):
            subprocess.Popen([exe, "--new-window", p])
        elif exe and name == "firefox.exe":
            subprocess.Popen([exe, "-new-window", p])
        else:
            os.startfile(p)
        return wait_new(before, 15)
    ex = find_existing(item)
    if ex:
        return ex
    before = snapshot()
    os.startfile(p)
    if t == "資料夾":
        return wait_new(before, 12, pred=lambda w: w[2] == "CabinetWClass", settle=0.6)
    return wait_new(before, 20 if t == "App" else 15) or find_existing(item)


def set_autostart(on):
    if not IS_WIN:
        return
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                         r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
    try:
        if on:
            if getattr(sys, "frozen", False):
                cmd = '"%s" --autostart' % sys.executable
            else:
                cmd = '"%s" "%s" --autostart' % (sys.executable, os.path.abspath(__file__))
            winreg.SetValueEx(key, "WorkspaceLauncher", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, "WorkspaceLauncher")
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(key)


# ----------------------------------------------------------------------------
# 項目輔助
# ----------------------------------------------------------------------------


def guess_type(p):
    pl = p.lower()
    if pl.startswith(("http://", "https://", "www.")):
        return "網址"
    if os.path.isdir(os.path.expandvars(p)):
        return "資料夾"
    if pl.endswith((".exe", ".lnk", ".bat", ".cmd", ".com")):
        return "App"
    return "檔案"


def norm_path(p, t=""):
    p = p.strip().strip('"').strip()
    if (t == "網址" or (not t and p.lower().startswith("www."))) and p and "://" not in p:
        p = "https://" + p
    return p


def default_name(t, p):
    if t == "網址":
        m = re.match(r"https?://([^/]+)", p)
        return m.group(1) if m else p
    base = re.split(r"[\\/]", p.rstrip("\\/"))[-1] or p
    return base if t == "資料夾" else os.path.splitext(base)[0] or base


def validate(t, p):
    if not p:
        return "路徑不能空白"
    if t == "網址":
        return "" if re.match(r"https?://\S+", p) else "網址要以 http:// 或 https:// 開頭"
    pe = os.path.expandvars(p)
    if os.path.exists(pe) or (t == "App" and shutil.which(pe)):
        if t == "資料夾" and not os.path.isdir(pe):
            return "這不是資料夾"
        return ""
    return "找不到這個路徑"


def item_error(it):
    if it.get("kind") == "self":
        return ""
    return validate(it.get("type", ""), it.get("path", ""))


# ----------------------------------------------------------------------------
# 預覽（可操作：拖格子互換、拖邊界調大小）
# ----------------------------------------------------------------------------

PAD = 6


class Preview(tk.Canvas):
    def __init__(self, parent, app, screen, aspect, height=150):
        w = int(min(height * aspect, 420))
        super().__init__(parent, width=w, height=height, bg="#FFFFFF", highlightthickness=1,
                         highlightbackground="#8A8A85")
        self.app, self.screen = app, screen
        self.W, self.H = w - 2 * PAD, height - 2 * PAD
        self.tmp = None      # 拖邊界時的暫時排版
        self.drag = None     # ('cell', id, target) 或 ('edge', orient, pos, orig)
        self.bind("<Motion>", self.on_motion)
        self.bind("<ButtonPress-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<ButtonRelease-1>", self.on_release)
        self.draw()

    def rects(self):
        return self.tmp or get_rects(self.app.cur_list(), self.screen)

    def px(self, x, y):
        return PAD + x * self.W, PAD + y * self.H

    def cell_at(self, ex, ey):
        for k, (x, y, w, h) in self.rects().items():
            x0, y0 = self.px(x, y)
            if x0 <= ex <= x0 + w * self.W and y0 <= ey <= y0 + h * self.H:
                return k
        return None

    def edge_at(self, ex, ey):
        for o, pos, a, b in edge_segments(self.rects()):
            if o == "v":
                px, py0 = self.px(pos, a)
                _, py1 = self.px(pos, b)
                if abs(ex - px) <= 5 and py0 - 3 <= ey <= py1 + 3:
                    return o, pos
            else:
                px0, py = self.px(a, pos)
                px1, _ = self.px(b, pos)
                if abs(ey - py) <= 5 and px0 - 3 <= ex <= px1 + 3:
                    return o, pos
        return None

    def draw(self):
        self.delete("all")
        lst = self.app.cur_list()
        names = {i["id"]: i for i in lst["items"]}
        rects = self.rects()
        target = self.drag[2] if self.drag and self.drag[0] == "cell" else None
        for k, (x, y, w, h) in rects.items():
            x0, y0 = self.px(x, y)
            x1, y1 = x0 + w * self.W, y0 + h * self.H
            sel = (k == self.app.sel_id)
            fill = "#E6F1FB" if (sel or k == SELF_ID) else "#F4F4F2"
            outline = "#185FA5" if sel else "#9A9A95"
            if target == k and self.drag[1] != k:
                fill, outline = "#FAEEDA", "#BA7517"
            self.create_rectangle(x0 + 2, y0 + 2, x1 - 2, y1 - 2, fill=fill, outline=outline,
                                  width=2 if sel else 1)
            self.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=names[k]["name"],
                             width=max(20, x1 - x0 - 12), font=self.app.font_small,
                             fill="#2C2C2A")
        for o, pos, a, b in edge_segments(rects):
            if o == "v":
                cx, cy = self.px(pos, (a + b) / 2)
                self.create_rectangle(cx - 2, cy - 12, cx + 2, cy + 12, fill="#888780", outline="")
            else:
                cx, cy = self.px((a + b) / 2, pos)
                self.create_rectangle(cx - 12, cy - 2, cx + 12, cy + 2, fill="#888780", outline="")

    def on_motion(self, e):
        edge = self.edge_at(e.x, e.y)
        self.config(cursor=("sb_h_double_arrow" if edge[0] == "v" else "sb_v_double_arrow")
                    if edge else "")

    def on_press(self, e):
        edge = self.edge_at(e.x, e.y)
        if edge:
            self.drag = ("edge", edge[0], edge[1], dict(get_rects(self.app.cur_list(), self.screen)))
            return
        k = self.cell_at(e.x, e.y)
        if k:
            self.app.select_item(k)
            self.drag = ("cell", k, None)
            self.draw()

    def on_drag(self, e):
        if not self.drag:
            return
        if self.drag[0] == "edge":
            _, o, pos, orig = self.drag
            v = (e.x - PAD) / self.W if o == "v" else (e.y - PAD) / self.H
            new = move_edge(orig, o, pos, snap(min(max(v, 0.05), 0.95)))
            if new:
                self.tmp = new
                self.draw()
        else:
            self.drag = ("cell", self.drag[1], self.cell_at(e.x, e.y))
            self.draw()

    def on_release(self, e):
        d, self.drag = self.drag, None
        if not d:
            return
        if d[0] == "edge":
            if self.tmp:
                tmp, self.tmp = self.tmp, None
                self.app.set_custom(self.screen, tmp)
            return
        target = d[2]
        if target and target != d[1]:
            r = dict(get_rects(self.app.cur_list(), self.screen))
            r[d[1]], r[target] = r[target], r[d[1]]
            self.app.set_custom(self.screen, r)
        else:
            self.draw()


# ----------------------------------------------------------------------------
# 主程式
# ----------------------------------------------------------------------------

COLS = ("grip", "chk", "type", "name", "path", "screen")
EDITABLE = ["type", "name", "path", "screen"]


class App:
    def __init__(self, root, autostart_run=False, cfg_file=None):
        self.root = root
        self.cfg_file = cfg_file
        self.cfg = load_config(cfg_file)
        self.edit = None
        self.drag = None
        self.sel_id = SELF_ID
        self.busy = False
        self.just_saved = False
        self.hdr_cb = None
        self.draft = {"type": "", "name": "", "path": "", "screen": 0}
        self.draft_err = ""
        root.title("%s %s" % (APP_NAME, APP_VERSION))
        root.geometry(self.cfg.get("geometry") or "1000x660")
        root.minsize(380, 70)
        self.setup_style()
        self.build_ui()
        self.refresh_all()
        self.set_compact(self.cfg.get("compact", False), initial=True)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind_all("<ButtonPress-1>", self.on_any_click, add="+")
        if autostart_run and self.cfg.get("autostart") and self.cfg.get("autorun"):
            for l in self.cfg["lists"]:
                if l["id"] == self.cfg["autorun"]:
                    self.cfg["current"] = l["id"]
                    self.refresh_all()
                    root.after(1500, self.run_all)

    # ---- 基本 ----
    def cur_list(self):
        for l in self.cfg["lists"]:
            if l["id"] == self.cfg["current"]:
                return l
        return self.cfg["lists"][0]

    def find_item(self, iid):
        for it in self.cur_list()["items"]:
            if it["id"] == iid:
                return it
        return None

    def save(self):
        try:
            save_config(self.cfg, self.cfg_file)
        except Exception as ex:
            self.set_status("存檔失敗：%s" % ex)

    def set_status(self, text):
        try:
            self.status.config(text=text)
        except Exception:
            pass

    def on_close(self):
        if not self.cfg.get("compact"):
            self.cfg["geometry"] = self.root.geometry()
        self.save()
        self.root.destroy()

    def setup_style(self):
        fam = "Microsoft JhengHei UI" if IS_WIN else "TkDefaultFont"
        self.font = (fam, 10)
        self.font_small = (fam, 9)
        self.font_bold = (fam, 10, "bold")
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        self.root.option_add("*Frame.background", "#FFFFFF")
        self.root.option_add("*Label.background", "#FFFFFF")
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("Treeview", rowheight=int(28 * self.scale), font=self.font, borderwidth=0)
        st.configure("Treeview.Heading", font=self.font_small, foreground="#6B6B66",
                     background="#F4F4F2", relief="flat")
        st.configure("TButton", font=self.font_small, padding=(8, 3))
        st.configure("TCheckbutton", font=self.font)
        st.configure("TCombobox", font=self.font)
        st.map("Treeview", background=[("selected", "#E6F1FB")],
               foreground=[("selected", "#0C447C")])
        # 滑鼠滾輪絕對不能改到下拉欄位的值；↑↓ 也不能直接換值
        self.root.bind_class("TCombobox", "<MouseWheel>", lambda e: "break")
        self.root.bind_class("TCombobox", "<Button-4>", lambda e: "break")
        self.root.bind_class("TCombobox", "<Button-5>", lambda e: "break")
        self.root.bind_class("TCombobox", "<Up>", lambda e: "break")
        self.root.bind_class("TCombobox", "<Down>", self.combo_post)

    def combo_post(self, e):
        try:
            e.widget.tk.call("ttk::combobox::Post", str(e.widget))
        except Exception:
            pass
        return "break"

    # ---- 畫面 ----
    def build_ui(self):
        r = self.root
        self.bottom = tk.Frame(r, bg="#F4F4F2")
        self.bottom.pack(side="bottom", fill="x")
        self.status = tk.Label(self.bottom, text="就緒", anchor="w", bg="#F4F4F2", fg="#6B6B66",
                               font=self.font_small)
        self.status.pack(side="left", padx=10, pady=4)
        tk.Label(self.bottom, text="%s　%s" % (AUTHOR, APP_VERSION), bg="#F4F4F2", fg=GOLD,
                 font=(self.font[0], 9, "italic")).pack(side="right", padx=10)

        self.tb = tk.Frame(r, bg="#F4F4F2")
        self.tb.pack(side="top", fill="x")
        tk.Label(self.tb, text=APP_NAME, bg="#F4F4F2", font=self.font_bold).pack(side="left", padx=10, pady=6)
        btns = [("全部開啟並排列", self.run_all), ("整理現有視窗", self.arrange_now),
                ("移到其他清單", self.move_menu), ("刪除勾選", self.delete_checked)]
        for text, cmd in btns:
            b = ttk.Button(self.tb, text=text, command=cmd)
            b.pack(side="right", padx=3, pady=5)
            if text == "移到其他清單":
                self.btn_move = b
        ttk.Button(self.tb, text="設定", command=self.show_settings).pack(side="right", padx=3)
        ttk.Button(self.tb, text="精簡", command=lambda: self.set_compact(True)).pack(side="right", padx=3)

        self.cbar = tk.Frame(r, bg="#F4F4F2")
        self.cname = tk.Label(self.cbar, text="", bg="#F4F4F2", font=self.font_bold)
        self.cname.pack(side="left", padx=10, pady=8)
        ttk.Button(self.cbar, text="展開", command=lambda: self.set_compact(False)).pack(side="right", padx=6)
        ttk.Button(self.cbar, text="全部開啟並排列", command=self.run_all).pack(side="right", padx=3)

        self.main = tk.Frame(r)
        self.main.pack(side="top", fill="both", expand=True)
        self.body = tk.Frame(self.main)
        self.body.pack(fill="both", expand=True)

        # 左：清單
        left = tk.Frame(self.body, width=170)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self.lists = ttk.Treeview(left, columns=("grip", "name"), show="tree", selectmode="browse")
        self.lists.column("#0", width=0, stretch=False)
        self.lists.column("grip", width=24, stretch=False, anchor="center")
        self.lists.column("name", width=140, anchor="w")
        self.lists.pack(fill="both", expand=True)
        self.lists.tag_configure("muted", foreground="#888780")
        self.lists.tag_configure("drop", background="#FAEEDA")
        self.lists.bind("<ButtonPress-1>", lambda e: self.on_press("lists", e))
        self.lists.bind("<B1-Motion>", lambda e: self.on_motion("lists", e))
        self.lists.bind("<ButtonRelease-1>", lambda e: self.on_release("lists", e))
        tk.Frame(self.body, width=1, bg="#D3D1C7").pack(side="left", fill="y")

        # 右
        right = tk.Frame(self.body)
        right.pack(side="left", fill="both", expand=True)
        hd = tk.Frame(right, bg="#FFFFFF")
        hd.pack(fill="x")
        tk.Label(hd, text="開啟於：", bg="#FFFFFF", fg="#6B6B66", font=self.font_small).pack(side="left", padx=(10, 0), pady=6)
        self.lbl_screen = tk.Label(hd, text="", bg="#FFFFFF", font=self.font_bold, cursor="hand2")
        self.lbl_screen.pack(side="left")
        self.lbl_screen.bind("<Button-1>", self.edit_list_screen)
        tk.Label(hd, text="　排版：", bg="#FFFFFF", fg="#6B6B66", font=self.font_small).pack(side="left")
        self.lbl_layout = tk.Label(hd, text="", bg="#FFFFFF", font=self.font_bold)
        self.lbl_layout.pack(side="left")
        ttk.Button(hd, text="刪除此清單", command=self.delete_list).pack(side="right", padx=8)
        ttk.Button(hd, text="還原自動平分", command=self.restore_auto).pack(side="right")

        self.pv_holder = tk.Frame(right, height=int(170 * self.scale))
        self.pv_holder.pack(side="bottom", fill="x", padx=10, pady=(4, 8))
        self.pv_label = tk.Label(right, text="排版預覽（拖格子互換、拖邊界調大小）", anchor="w",
                                 fg="#6B6B66", font=self.font_small)
        self.pv_label.pack(side="bottom", fill="x", padx=10)

        self.tree = ttk.Treeview(right, columns=COLS, show="headings", selectmode="browse")
        for c, t, w, a in [("grip", "", 26, "center"), ("chk", "", 34, "center"), ("type", "類型", 70, "w"),
                           ("name", "名稱", 150, "w"), ("path", "路徑／網址", 330, "w"),
                           ("screen", "螢幕", 84, "w")]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=a, stretch=(c == "path"))
        self.tree.pack(side="top", fill="both", expand=True)
        self.tree.tag_configure("error", foreground="#C62828")
        self.tree.tag_configure("blank", foreground="#888780")
        self.tree.tag_configure("self", background="#F4F4F2", foreground="#5F5E5A")
        self.tree.bind("<ButtonPress-1>", lambda e: self.on_press("items", e))
        self.tree.bind("<B1-Motion>", lambda e: self.on_motion("items", e))
        self.tree.bind("<ButtonRelease-1>", lambda e: self.on_release("items", e))
        self.tree.bind("<Alt-Up>", lambda e: self.nudge(-1))
        self.tree.bind("<Alt-Down>", lambda e: self.nudge(1))
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.droplines = {"items": tk.Frame(self.tree, bg="#185FA5", height=2),
                          "lists": tk.Frame(self.lists, bg="#185FA5", height=2)}
        if HAS_DND:
            try:
                self.tree.drop_target_register(DND_FILES)
                self.tree.dnd_bind("<<Drop>>", self.on_drop_files)
            except Exception:
                pass

        # 設定
        self.settings = tk.Frame(self.main)
        self.var_auto = tk.BooleanVar(value=self.cfg.get("autostart", False))
        tk.Label(self.settings, text="設定", font=self.font_bold).pack(anchor="w", padx=16, pady=(14, 8))
        ttk.Checkbutton(self.settings, text="開機時自動啟動工具（預設關閉，勾選立刻生效，取消即移除）",
                        variable=self.var_auto, command=self.on_autostart).pack(anchor="w", padx=16, pady=4)
        row = tk.Frame(self.settings)
        row.pack(anchor="w", padx=16, pady=8)
        tk.Label(row, text="開機後自動執行清單：", font=self.font).pack(side="left")
        self.cb_autorun = ttk.Combobox(row, state="readonly", width=24)
        self.cb_autorun.pack(side="left")
        self.cb_autorun.bind("<<ComboboxSelected>>", self.on_autorun)
        ttk.Button(self.settings, text="返回", command=self.hide_settings).pack(anchor="w", padx=16, pady=10)

    # ---- 重新整理 ----
    def refresh_all(self):
        self.refresh_lists()
        self.refresh_header()
        self.refresh_items()
        self.refresh_preview()

    def refresh_lists(self):
        self.lists.delete(*self.lists.get_children())
        for l in self.cfg["lists"]:
            self.lists.insert("", "end", iid=l["id"], values=("⋮⋮", l["name"]))
        self.lists.insert("", "end", iid=NEWLIST, values=("", "＋ 新增清單"), tags=("muted",))
        self.lists.selection_set(self.cfg["current"])
        self.cname.config(text=self.cur_list()["name"])

    def refresh_header(self):
        l = self.cur_list()
        self.lbl_screen.config(text=SCREEN_LABEL[l.get("screen", 1)])
        self.lbl_layout.config(text="自訂" if l.get("layout") == "custom" else "自動")

    def row_values(self, it):
        if it.get("kind") == "self":
            return ("•", "☑" if it.get("checked") else "☐", "App", it["name"], "（固定第一列，不能刪除）", "跟隨清單")
        return ("⋮⋮", "☑" if it.get("checked") else "☐", it["type"], it["name"], it["path"],
                SCREEN_LABEL[it.get("screen", 0)])

    def item_tags(self, it):
        if it.get("kind") == "self":
            return ("self",)
        return ("error",) if item_error(it) else ()

    def refresh_items(self):
        self.end_edit(False)
        self.tree.delete(*self.tree.get_children())
        for it in self.cur_list()["items"]:
            self.tree.insert("", "end", iid=it["id"], values=self.row_values(it), tags=self.item_tags(it))
        self.tree.insert("", "end", iid=NEW, values=self.blank_values(), tags=("error",) if self.draft_err else ("blank",))
        if self.sel_id in self.tree.get_children():
            self.tree.selection_set(self.sel_id)

    def blank_values(self):
        d = self.draft
        path = d["path"] or ("" if d["name"] else "空白輸入列：輸入或把檔案、資料夾拖進來，填完自動存")
        return ("", "", d["type"], d["name"], path, SCREEN_LABEL[d["screen"]])

    def refresh_row(self, iid):
        it = self.find_item(iid)
        if it and iid in self.tree.get_children():
            self.tree.item(iid, values=self.row_values(it), tags=self.item_tags(it))

    def refresh_blank_row(self):
        if NEW in self.tree.get_children():
            self.tree.item(NEW, values=self.blank_values(), tags=("error",) if self.draft_err else ("blank",))

    def refresh_preview(self):
        for w in self.pv_holder.winfo_children():
            w.destroy()
        l = self.cur_list()
        mons = get_monitors()
        for s in screens_in_use(l):
            m = mons[s - 1] if 0 < s <= len(mons) else mons[0]
            box = tk.Frame(self.pv_holder)
            box.pack(side="left", padx=(0, 12))
            tk.Label(box, text="螢幕%d" % s, fg="#6B6B66", font=self.font_small).pack(anchor="w")
            Preview(box, self, s, m["w"] / float(m["h"]), int(150 * self.scale)).pack()

    def select_item(self, iid):
        self.sel_id = iid
        if iid in self.tree.get_children():
            self.tree.selection_set(iid)
            self.tree.see(iid)

    def on_select(self, e):
        sel = self.tree.selection()
        if sel and sel[0] != NEW:
            if sel[0] != self.sel_id:
                self.sel_id = sel[0]
                for w in self.pv_holder.winfo_children():
                    for c in w.winfo_children():
                        if isinstance(c, Preview):
                            c.draw()

    # ---- 滑鼠：表格與清單 ----
    def tree_of(self, kind):
        return self.tree if kind == "items" else self.lists

    def on_press(self, kind, e):
        t = self.tree_of(kind)
        self.end_edit(True)
        row = t.identify_row(e.y)
        if t.identify_region(e.x, e.y) not in ("cell", "tree") or not row:
            return
        col = self.col_name(kind, t.identify_column(e.x))
        if kind == "items":
            self.select_item(row) if row != NEW else None
            if row == NEW:
                self.begin_edit(NEW, col) if col in EDITABLE else None
                return "break"
            it = self.find_item(row)
            if col == "chk":
                it["checked"] = not it.get("checked")
                self.save()
                self.refresh_row(row)
                self.refresh_preview()
                return "break"
            if col == "grip" and it.get("kind") != "self":
                self.drag = {"kind": kind, "row": row, "y0": e.y, "on": False}
                return "break"
            if it.get("kind") != "self" and col in EDITABLE:
                self.begin_edit(row, col)
                return "break"
            return None
        # 清單
        if row == NEWLIST:
            self.begin_list_edit(NEWLIST)
            return "break"
        if col == "grip":
            self.drag = {"kind": kind, "row": row, "y0": e.y, "on": False}
            self.switch_list(row)
            return "break"
        if row == self.cfg["current"]:
            self.begin_list_edit(row)
        else:
            self.switch_list(row)
        return "break"

    def col_name(self, kind, colid):
        try:
            n = int(colid.replace("#", ""))
        except ValueError:
            return ""
        cols = COLS if kind == "items" else ("", "grip", "name")
        idx = n - 1 if kind == "items" else n
        return cols[idx] if 0 <= idx < len(cols) else ""

    def switch_list(self, lid):
        if lid == self.cfg["current"] or lid == NEWLIST:
            return
        self.cfg["current"] = lid
        self.sel_id = SELF_ID
        self.save()
        self.refresh_all()

    def on_motion(self, kind, e):
        d = self.drag
        if not d or d["kind"] != kind:
            return
        if not d["on"] and abs(e.y - d["y0"]) > 4:
            d["on"] = True
        if not d["on"]:
            return
        t = self.tree_of(kind)
        d["target"] = self.drop_index(t, e.y, kind)
        self.show_dropline(t, d["target"], kind)
        d["over"] = None
        for k in self.lists.get_children():
            self.lists.item(k, tags=("muted",) if k == NEWLIST else ())
        if kind == "items":
            rx = e.x_root - self.lists.winfo_rootx()
            ry = e.y_root - self.lists.winfo_rooty()
            if 0 <= rx < self.lists.winfo_width() and 0 <= ry < self.lists.winfo_height():
                over = self.lists.identify_row(ry)
                if over and over not in (NEWLIST, self.cfg["current"]):
                    d["over"] = over
                    self.lists.item(over, tags=("drop",))
                    self.droplines["items"].place_forget()

    def real_rows(self, kind):
        if kind == "items":
            return [r for r in self.tree.get_children() if r != NEW]
        return [r for r in self.lists.get_children() if r != NEWLIST]

    def drop_index(self, t, y, kind):
        rows = self.real_rows(kind)
        lo = 1 if kind == "items" else 0   # 本工具永遠是第一列
        for i, r in enumerate(rows):
            bb = t.bbox(r)
            if bb and y < bb[1] + bb[3] / 2:
                return max(i, lo)
        return len(rows)

    def show_dropline(self, t, idx, kind):
        rows = self.real_rows(kind)
        if not rows:
            return
        if idx < len(rows):
            bb = t.bbox(rows[idx])
            y = bb[1] if bb else 0
        else:
            bb = t.bbox(rows[-1])
            y = (bb[1] + bb[3]) if bb else 0
        self.droplines[kind].place(x=0, y=max(0, y - 1), relwidth=1, height=2)

    def on_release(self, kind, e):
        d, self.drag = self.drag, None
        for f in self.droplines.values():
            f.place_forget()
        for k in self.lists.get_children():
            self.lists.item(k, tags=("muted",) if k == NEWLIST else ())
        if not d or d["kind"] != kind or not d["on"]:
            return
        if kind == "lists":
            ids = [l["id"] for l in self.cfg["lists"]]
            self.cfg["lists"] = self.reorder(self.cfg["lists"], [d["row"]], d["target"])
            self.save()
            self.refresh_lists()
            return
        it = self.find_item(d["row"])
        ids = [d["row"]]
        if it and it.get("checked"):
            ids = [i["id"] for i in self.cur_list()["items"] if i.get("checked") and i.get("kind") != "self"]
        if d.get("over"):
            self.transfer(ids, d["over"], copy_=bool(e.state & 0x4))
            return
        l = self.cur_list()
        l["items"] = self.reorder(l["items"], ids, d["target"])
        self.save()
        self.refresh_items()
        self.refresh_preview()

    @staticmethod
    def reorder(seq, ids, target):
        moving = [x for x in seq if x["id"] in ids]
        rest = [x for x in seq if x["id"] not in ids]
        before = sum(1 for x in seq[:target] if x["id"] not in ids)
        return rest[:before] + moving + rest[before:]

    def nudge(self, delta):
        l = self.cur_list()
        ids = [i["id"] for i in l["items"]]
        if self.sel_id not in ids or self.sel_id == SELF_ID:
            return "break"
        pos = ids.index(self.sel_id)
        new = pos + delta
        if new < 1 or new >= len(ids):
            return "break"
        l["items"][pos], l["items"][new] = l["items"][new], l["items"][pos]
        self.save()
        self.refresh_items()
        self.refresh_preview()
        return "break"

    def transfer(self, ids, list_id, copy_=False):
        dest = next((x for x in self.cfg["lists"] if x["id"] == list_id), None)
        src = self.cur_list()
        if not dest:
            return
        moving = [i for i in src["items"] if i["id"] in ids and i.get("kind") != "self"]
        if not moving:
            return
        for it in moving:
            c = copy.deepcopy(it)
            c["id"] = new_id()
            dest["items"].append(c)
        if not copy_:
            src["items"] = [i for i in src["items"] if i not in moving]
        for l in (src, dest):
            l["layout"] = "auto"
            l["rects"] = {}
        self.save()
        self.refresh_all()
        self.set_status("已%s %d 項到「%s」" % ("複製" if copy_ else "移動", len(moving), dest["name"]))

    # ---- 拖入檔案 ----
    def on_drop_files(self, event):
        for p in self.root.tk.splitlist(event.data):
            t = guess_type(p)
            self.add_item(t, default_name(t, p), norm_path(p, t), 0)
        self.refresh_items()
        self.refresh_preview()
        return event.action

    def add_item(self, t, name, path, screen):
        it = {"id": new_id(), "kind": "item", "type": t, "name": name or default_name(t, path),
              "path": path, "screen": screen, "checked": True}
        l = self.cur_list()
        l["items"].append(it)
        l["layout"] = "auto"
        l["rects"] = {}
        self.save()
        return it

    # ---- Excel 式儲存格編輯 ----
    def current_text(self, iid, col):
        if iid == NEW:
            d = self.draft
            return SCREEN_LABEL[d["screen"]] if col == "screen" else d[col]
        it = self.find_item(iid)
        return SCREEN_LABEL[it.get("screen", 0)] if col == "screen" else it[col]

    def begin_edit(self, iid, col):
        self.end_edit(True)
        if col not in EDITABLE:
            return
        self.tree.see(iid)
        self.tree.update_idletasks()
        bb = self.tree.bbox(iid, col)
        if not bb:
            return
        x, y, w, h = bb
        cur = self.current_text(iid, col)
        if col in ("type", "screen"):
            w_ = ttk.Combobox(self.tree, state="readonly",
                              values=TYPES if col == "type" else [SCREEN_LABEL[i] for i in (0, 1, 2)])
            w_.set(cur)
            w_.bind("<<ComboboxSelected>>", lambda e: w_.selection_clear())
        else:
            w_ = ttk.Entry(self.tree, font=self.font)
            w_.insert(0, cur)
            w_.select_range(0, "end")
            w_.bind("<FocusOut>", lambda e: self.finish_edit(0))
        w_.place(x=x, y=y, width=w, height=h)
        w_.focus_set()
        for seq, mv in (("<Return>", 1), ("<Tab>", 1), ("<Shift-Tab>", -1), ("<ISO_Left_Tab>", -1)):
            w_.bind(seq, lambda e, m=mv: self.finish_edit(m) or "break")
        w_.bind("<Escape>", lambda e: self.end_edit(False) or "break")
        self.edit = {"w": w_, "iid": iid, "col": col}

    def end_edit(self, commit):
        ed = self.edit
        if not ed:
            return
        self.edit = None
        text = ed["w"].get()
        try:
            ed["w"].destroy()
        except Exception:
            pass
        if commit:
            self.commit_value(ed["iid"], ed["col"], text)

    def finish_edit(self, move):
        ed = self.edit
        if not ed:
            return
        iid, col = ed["iid"], ed["col"]
        self.end_edit(True)
        if move:
            self.root.after_idle(lambda: self.goto_next(iid, col, move))
        return "break"

    def goto_next(self, iid, col, move):
        if self.just_saved:
            self.just_saved = False
            self.begin_edit(NEW, "type")
            return
        rows = [r for r in self.tree.get_children() if r == NEW or (self.find_item(r) or {}).get("kind") != "self"]
        if iid not in rows:
            iid = NEW
        ci = EDITABLE.index(col) + move
        ri = rows.index(iid)
        if ci >= len(EDITABLE):
            ci, ri = 0, ri + 1
        elif ci < 0:
            ci, ri = len(EDITABLE) - 1, ri - 1
        if 0 <= ri < len(rows):
            self.select_item(rows[ri]) if rows[ri] != NEW else None
            self.begin_edit(rows[ri], EDITABLE[ci])

    def commit_value(self, iid, col, text):
        self.just_saved = False
        if iid == NEW:
            d = self.draft
            if col == "type":
                d["type"] = text
            elif col == "screen":
                d["screen"] = SCREEN_FROM.get(text, 0)
            elif col == "path":
                d["path"] = norm_path(text, d["type"])
            else:
                d["name"] = text.strip()
            self.draft_err = ""
            self.try_save_draft()
            self.refresh_blank_row()
            return
        it = self.find_item(iid)
        if not it:
            return
        if col == "type":
            it["type"] = text
        elif col == "screen":
            it["screen"] = SCREEN_FROM.get(text, 0)
        elif col == "path":
            it["path"] = norm_path(text, it["type"])
        else:
            it["name"] = text.strip() or default_name(it["type"], it["path"])
        if col in ("screen",):
            it_l = self.cur_list()
            it_l["layout"] = "auto"
            it_l["rects"] = {}
        self.save()
        self.refresh_row(iid)
        err = item_error(it)
        self.set_status(err and "「%s」%s" % (it["name"], err) or "已儲存")
        if col in ("name", "screen"):
            self.refresh_preview()
            self.refresh_header()

    def try_save_draft(self):
        d = self.draft
        if not d["path"]:
            return
        path = norm_path(d["path"], d["type"])
        t = d["type"] or guess_type(path)
        path = norm_path(path, t)
        err = validate(t, path)
        if err:
            self.draft_err = err
            self.set_status("空白列：%s（沒有儲存）" % err)
            return
        self.add_item(t, d["name"], path, d["screen"])
        self.draft = {"type": "", "name": "", "path": "", "screen": 0}
        self.draft_err = ""
        self.refresh_items()
        self.refresh_preview()
        self.set_status("已新增")
        self.just_saved = True

    # ---- 清單：改名／新增／刪除 ----
    def begin_list_edit(self, lid):
        self.end_edit(True)
        bb = self.lists.bbox(lid, "name")
        if not bb:
            return
        x, y, w, h = bb
        cur = "" if lid == NEWLIST else next(l["name"] for l in self.cfg["lists"] if l["id"] == lid)
        e = ttk.Entry(self.lists, font=self.font)
        e.insert(0, cur)
        e.select_range(0, "end")
        e.place(x=x, y=y, width=w, height=h)
        e.focus_set()
        done = {"v": False}

        def commit(ev=None):
            if done["v"]:
                return
            done["v"] = True
            name = e.get().strip()
            e.destroy()
            if not name:
                return
            if lid == NEWLIST:
                nl = new_list(name)
                self.cfg["lists"].append(nl)
                self.cfg["current"] = nl["id"]
                self.sel_id = SELF_ID
            else:
                next(l for l in self.cfg["lists"] if l["id"] == lid)["name"] = name
            self.save()
            self.refresh_all()

        e.bind("<Return>", commit)
        e.bind("<FocusOut>", commit)
        e.bind("<Escape>", lambda ev: (done.__setitem__("v", True), e.destroy()))

    def delete_list(self):
        if len(self.cfg["lists"]) <= 1:
            messagebox.showinfo(APP_NAME, "至少要保留一份清單。")
            return
        l = self.cur_list()
        if not messagebox.askyesno(APP_NAME, "確定刪除清單「%s」？" % l["name"]):
            return
        self.cfg["lists"].remove(l)
        self.cfg["current"] = self.cfg["lists"][0]["id"]
        self.sel_id = SELF_ID
        self.save()
        self.refresh_all()

    # ---- 開啟於／排版 ----
    def edit_list_screen(self, e):
        self.end_edit(True)
        lbl = self.lbl_screen
        cb = ttk.Combobox(lbl.master, state="readonly", values=[SCREEN_LABEL[1], SCREEN_LABEL[2]], width=8)
        cb.set(lbl.cget("text"))
        cb.place(in_=lbl, x=0, y=0, relheight=1)
        cb.focus_set()
        self.hdr_cb = cb

        def pick(ev=None):
            self.cur_list()["screen"] = SCREEN_FROM[cb.get()]
            l = self.cur_list()
            l["layout"] = "auto"
            l["rects"] = {}
            self.close_hdr_cb()
            self.save()
            self.refresh_header()
            self.refresh_preview()

        cb.bind("<<ComboboxSelected>>", pick)
        cb.bind("<Escape>", lambda ev: self.close_hdr_cb())

    def close_hdr_cb(self):
        if self.hdr_cb is not None:
            try:
                self.hdr_cb.destroy()
            except Exception:
                pass
            self.hdr_cb = None

    def on_any_click(self, e):
        if self.hdr_cb is not None and not str(e.widget).startswith(str(self.hdr_cb)):
            self.close_hdr_cb()

    def restore_auto(self):
        l = self.cur_list()
        l["layout"] = "auto"
        l["rects"] = {}
        self.save()
        self.refresh_header()
        self.refresh_preview()
        self.set_status("已還原自動平分")

    def set_custom(self, screen, rects):
        l = self.cur_list()
        l["layout"] = "custom"
        l.setdefault("rects", {})[str(screen)] = {k: list(v) for k, v in rects.items()}
        self.save()
        self.refresh_header()
        self.refresh_preview()

    # ---- 批次動作 ----
    def checked_ids(self):
        return [i["id"] for i in self.cur_list()["items"] if i.get("checked") and i.get("kind") != "self"]

    def delete_checked(self):
        ids = self.checked_ids()
        if not ids:
            self.set_status("先勾選要刪除的項目")
            return
        if not messagebox.askyesno(APP_NAME, "確定刪除勾選的 %d 個項目？" % len(ids)):
            return
        l = self.cur_list()
        l["items"] = [i for i in l["items"] if i["id"] not in ids]
        l["layout"] = "auto"
        l["rects"] = {}
        self.save()
        self.refresh_all()
        self.set_status("已刪除 %d 個項目" % len(ids))

    def move_menu(self):
        ids = self.checked_ids()
        others = [l for l in self.cfg["lists"] if l["id"] != self.cfg["current"]]
        if not ids or not others:
            self.set_status("先勾選項目，且要有另一份清單")
            return
        m = tk.Menu(self.root, tearoff=0)
        for l in others:
            m.add_command(label="移到「%s」" % l["name"], command=lambda x=l["id"]: self.transfer(ids, x, False))
        m.add_separator()
        for l in others:
            m.add_command(label="複製到「%s」" % l["name"], command=lambda x=l["id"]: self.transfer(ids, x, True))
        b = self.btn_move
        m.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    # ---- 執行與排列 ----
    def run_all(self):
        if self.busy:
            return
        l = self.cur_list()
        items = [dict(i) for i in l["items"] if i.get("checked") and i.get("kind") != "self"]
        self.busy = True
        self.set_status("開啟中…")

        q = queue.Queue()

        def work():
            found, skipped = {}, []
            for it in items:
                q.put(("status", "開啟：%s…" % it["name"]))
                try:
                    h = open_item(it)
                except Exception:
                    h = None
                if h:
                    found[it["id"]] = h
                else:
                    skipped.append(it["name"])
            q.put(("done", (found, skipped)))

        def poll():
            try:
                while True:
                    kind, val = q.get_nowait()
                    if kind == "status":
                        self.set_status(val)
                    else:
                        self.finish_run(l, val[0], val[1])
                        return
            except queue.Empty:
                pass
            self.root.after(120, poll)

        threading.Thread(target=work, daemon=True).start()
        self.root.after(120, poll)

    def finish_run(self, lst, found, skipped):
        self.busy = False
        self.tile_list(lst, found)
        msg = "完成：已開啟並排列"
        if skipped:
            msg += "；沒找到視窗，只開啟未排版：" + "、".join(skipped)
        self.set_status(msg)

    def arrange_now(self):
        l = self.cur_list()
        found, skipped = {}, []
        for it in l["items"]:
            if not it.get("checked") or it.get("kind") == "self":
                continue
            h = find_existing(it)
            if h:
                found[it["id"]] = h
            else:
                skipped.append(it["name"])
        self.tile_list(l, found)
        msg = "已整理現有視窗"
        if skipped:
            msg += "；沒找到已開啟的視窗：" + "、".join(skipped)
        self.set_status(msg)

    def tile_list(self, lst, found):
        found = dict(found)
        found[SELF_ID] = own_hwnd(self.root)
        for s in screens_in_use(lst):
            m = monitor_for(s)
            for iid, (x, y, w, h) in get_rects(lst, s).items():
                hw = found.get(iid)
                if hw:
                    try:
                        place_window(hw, m["x"] + int(round(x * m["w"])), m["y"] + int(round(y * m["h"])),
                                     int(round(w * m["w"])), int(round(h * m["h"])))
                    except Exception:
                        pass

    # ---- 精簡模式／設定 ----
    def set_compact(self, flag, initial=False):
        self.end_edit(False)
        if flag:
            if not initial:
                self.cfg["geometry"] = self.root.geometry()
            self.tb.pack_forget()
            self.main.pack_forget()
            self.cbar.pack(side="top", fill="x")
            self.root.geometry("%dx%d" % (min(max(self.root.winfo_width(), 380), 520), 86))
        else:
            self.cbar.pack_forget()
            self.tb.pack(side="top", fill="x")
            self.main.pack(side="top", fill="both", expand=True)
            self.root.geometry(self.cfg.get("geometry") or "1000x660")
        self.cfg["compact"] = bool(flag)
        self.save()

    def show_settings(self):
        self.end_edit(False)
        self.body.pack_forget()
        self.var_auto.set(self.cfg.get("autostart", False))
        names = ["不執行"] + [l["name"] for l in self.cfg["lists"]]
        self.cb_autorun.config(values=names)
        cur = next((l["name"] for l in self.cfg["lists"] if l["id"] == self.cfg.get("autorun")), "不執行")
        self.cb_autorun.set(cur)
        self.settings.pack(fill="both", expand=True)

    def hide_settings(self):
        self.settings.pack_forget()
        self.body.pack(fill="both", expand=True)

    def on_autostart(self):
        on = bool(self.var_auto.get())
        try:
            set_autostart(on)
            self.cfg["autostart"] = on
            self.save()
            self.set_status("已設定開機自動啟動" if on else "已取消開機自動啟動")
        except Exception as ex:
            self.var_auto.set(not on)
            self.set_status("設定失敗：%s" % ex)

    def on_autorun(self, e=None):
        name = self.cb_autorun.get()
        self.cfg["autorun"] = next((l["id"] for l in self.cfg["lists"] if l["name"] == name), "")
        self.save()
        self.cb_autorun.selection_clear()


def main():
    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    App(root, autostart_run="--autostart" in sys.argv)
    root.mainloop()


if __name__ == "__main__":
    main()
