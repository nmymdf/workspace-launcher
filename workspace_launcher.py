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
import random
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
import tkinter.font as tkfont

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except Exception:  # 沒有拖放套件時，其他功能照常
    HAS_DND = False

APP_NAME = "工作清單啟動器"
APP_VERSION = "v0.2"
AUTHOR = "作者：ArchieKUO"
GOLD = "#B8912A"
IS_WIN = sys.platform == "win32"

TYPES = ["App", "資料夾", "檔案", "網址", "YouTube最新", "音樂資料夾"]
NOTILE_TYPES = {"音樂資料夾"}   # 開了就好，不參與排版
NOTILE = -1
EXCLUDE = set()                 # 本工具自己的視窗，找視窗時要略過
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
    cfg.setdefault("music_shuffle", True)
    cfg.setdefault("music_sub", True)
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
            if i.get("checked") and i.get("type") not in NOTILE_TYPES
            and effective_screen(lst, i) == screen]


def screens_in_use(lst):
    s = sorted({effective_screen(lst, i) for i in lst["items"]
                if i.get("checked") and i.get("type") not in NOTILE_TYPES})
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
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ArchieKUO.WorkspaceLauncher")
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
            if int(h) in EXCLUDE:
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


def wait_new(before, timeout=15.0, pred=None, settle=1.0, exclude=()):
    t0 = time.time()
    while time.time() - t0 < timeout:
        new = [w for w in list_windows() if w[0] not in before and w[0] not in exclude and (pred is None or pred(w))]
        if new:
            time.sleep(settle)
            new = [w for w in list_windows() if w[0] not in before and w[0] not in exclude and (pred is None or pred(w))]
            if new:
                return max(new, key=lambda w: win_area(w[0]))[0]
        time.sleep(0.3)
    return None


def set_titlebar_dark(hwnd, dark):
    if not IS_WIN or not hwnd:
        return
    try:
        v = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):
            if dwmapi.DwmSetWindowAttribute(P(hwnd), attr, ctypes.byref(v), 4) == 0:
                break
    except Exception:
        pass


_LNK_CACHE = {}


def resolve_lnk(p):
    """捷徑 → 目標路徑（有快取，PowerShell 每次要半秒）。"""
    if p in _LNK_CACHE:
        return _LNK_CACHE[p]
    out = ""
    if IS_WIN:
        try:
            ps = "(New-Object -ComObject WScript.Shell).CreateShortcut('%s').TargetPath" % p.replace("'", "''")
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                                 text=True, timeout=10, creationflags=0x08000000).stdout.strip()
        except Exception:
            out = ""
    _LNK_CACHE[p] = out
    return out


def read_url_file(p):
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip().lower().startswith("url="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def fix_shortcut(t, p):
    """捷徑指到資料夾就當資料夾、指到網址就當網址；其他維持原樣。"""
    pl = p.lower()
    if pl.endswith(".lnk"):
        tgt = resolve_lnk(os.path.expandvars(p))
        if tgt and os.path.isdir(tgt):
            return "資料夾", tgt
        if tgt and re.match(r"https?://", tgt, re.I):
            return "網址", tgt
    elif pl.endswith(".url") and t in ("App", "檔案", ""):
        u = read_url_file(os.path.expandvars(p))
        if u:
            return "網址", u
    return t, p


def clean_keyword(s):
    s = re.sub(r"\s*[-–—]\s*(捷徑|shortcut)\s*$", "", s.strip(), flags=re.I)
    s = re.sub(r"\.lnk$", "", s, flags=re.I)
    return s.strip()


def item_keys(item):
    """找視窗用的關鍵字：項目名稱（去掉「- 捷徑」）和路徑的檔名。"""
    keys = []
    n = clean_keyword(item.get("name", ""))
    if n:
        keys.append(n)
    p = item.get("path", "")
    if p:
        stem = clean_keyword(os.path.splitext(re.split(r"[\\/]", p.rstrip("\\/"))[-1])[0])
        if stem and stem.lower() not in [k.lower() for k in keys]:
            keys.append(stem)
    return keys


def title_score(title, key):
    t, k = title.casefold(), key.casefold()
    if not k:
        return 0
    if t == k:
        return 3
    if len(k) >= 2 and (t.startswith(k) or t.endswith(k)):
        return 2
    if len(k) >= 3 and re.search(r"(?<![0-9a-z])%s(?![0-9a-z])" % re.escape(k), t):
        return 1
    return 0


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


def find_existing(item, exclude=()):
    """找這個項目目前已經開著的視窗（不會開新的）。"""
    t, p = fix_shortcut(item.get("type"), os.path.expandvars(item.get("path", "")))
    wins = [w for w in list_windows() if w[0] not in exclude]
    if t == "資料夾":
        name = re.split(r"[\\/]", p.rstrip("\\/"))[-1] or p
        hits = [w for w in wins if w[2] == "CabinetWClass" and w[1].lower() in (name.lower(), p.lower())]
        if hits:
            return hits[0][0]
    if t == "App":
        exe = p
        if p.lower().endswith(".lnk"):
            exe = resolve_lnk(p) or p
        base = os.path.basename(os.path.normcase(os.path.normpath(exe)))
        if base.endswith(".exe"):
            hits = [w for w in wins if os.path.basename(os.path.normcase(proc_exe(w[3]))) == base]
            if hits:
                return max(hits, key=lambda w: win_area(w[0]))[0]
    keys = item_keys(item)
    best, best_s = None, 0
    for w in wins:
        s = max([title_score(w[1], k) for k in keys] or [0])
        if t == "資料夾" and w[2] != "CabinetWClass":
            s = 0
        if s > best_s or (s == best_s and s > 0 and win_area(w[0]) > win_area(best)):
            best, best_s = w[0], s
    return best


def open_url(url, exclude=()):
    before = snapshot()
    exe = default_browser_exe()
    name = os.path.basename(exe).lower()
    if exe and name in ("chrome.exe", "msedge.exe", "brave.exe", "vivaldi.exe", "opera.exe"):
        subprocess.Popen([exe, "--new-window", url])
    elif exe and name == "firefox.exe":
        subprocess.Popen([exe, "-new-window", url])
    else:
        os.startfile(url)
    return wait_new(before, 15, exclude=exclude)


# ---- YouTube 頻道最新影片（用公開的 RSS，不需要金鑰）----
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9", "Cookie": "CONSENT=YES+1; SOCS=CAI"}


def http_get(url, timeout=12):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


CHANNEL_PATTERNS = [r'rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"',
                    r'<meta itemprop="(?:channelId|identifier)" content="(UC[\w-]{22})"',
                    r'"externalId":"(UC[\w-]{22})"', r'"channelId":"(UC[\w-]{22})"']


def channel_id_from_html(html):
    for pat in CHANNEL_PATTERNS:
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return ""


def resolve_channel_id(url):
    m = re.search(r"/channel/(UC[\w-]{22})", url)
    if m:
        return m.group(1)
    return channel_id_from_html(http_get(url))


def parse_feed(xml_text):
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    root = ET.fromstring(xml_text)
    out = []
    for e in root.findall("a:entry", ns):
        out.append({"id": e.findtext("yt:videoId", default="", namespaces=ns),
                    "title": e.findtext("a:title", default="", namespaces=ns)})
    return [v for v in out if v["id"]]


def latest_video_url(item):
    """回傳 (最新影片網址, 頻道id)。網址若是播放清單就用播放清單的最新一支。"""
    url = item["path"]
    m = re.search(r"[?&]list=([\w-]+)", url)
    if m:
        feed = "https://www.youtube.com/feeds/videos.xml?playlist_id=" + m.group(1)
        cid = item.get("cid", "")
    else:
        cid = item.get("cid") or resolve_channel_id(url)
        if not cid:
            raise RuntimeError("找不到這個頻道")
        feed = "https://www.youtube.com/feeds/videos.xml?channel_id=" + cid
    vids = parse_feed(http_get(feed))
    if not vids:
        raise RuntimeError("這個頻道沒有影片")
    return "https://www.youtube.com/watch?v=" + vids[0]["id"], cid


# ---- 音樂資料夾：做成播放清單交給預設播放器 ----
AUDIO_EXT = (".mp3", ".m4a", ".flac", ".wav", ".wma", ".aac", ".ogg", ".opus")


def music_files(folder, sub=True):
    out = []
    if sub:
        for r, _, fs in os.walk(folder):
            out += [os.path.join(r, f) for f in fs if f.lower().endswith(AUDIO_EXT)]
    else:
        out = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(AUDIO_EXT)]
    return sorted(out)


def build_playlist(files, shuffle=True, dest=None):
    files = list(files)
    if shuffle:
        random.shuffle(files)
    dest = dest or os.path.join(tempfile.gettempdir(), "WorkspaceLauncher", "music.m3u")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write("#EXTM3U\n" + "\n".join(files) + "\n")
    return dest


def open_item(item, exclude=(), opts=None, on_cid=None):
    """開啟項目並回傳它的視窗；已經開著的會直接沿用；音樂回傳 NOTILE（不排版）。"""
    if not IS_WIN:
        return None
    opts = opts or {}
    t, p = fix_shortcut(item.get("type"), os.path.expandvars(item.get("path", "")))
    it = dict(item, type=t, path=p)
    if t == "音樂資料夾":
        files = music_files(p, opts.get("music_sub", True))
        if not files:
            raise RuntimeError("資料夾裡沒有音樂檔")
        os.startfile(build_playlist(files, opts.get("music_shuffle", True)))
        return NOTILE
    if t in ("網址", "YouTube最新"):
        url = p
        if t == "YouTube最新":
            url, cid = latest_video_url(it)
            if on_cid and cid and cid != item.get("cid"):
                on_cid(cid)
        return open_url(url, exclude)
    ex = find_existing(it, exclude)
    if ex:
        return ex
    before = snapshot()
    os.startfile(p)
    if t == "資料夾":
        return wait_new(before, 12, pred=lambda w: w[2] == "CabinetWClass", settle=0.6, exclude=exclude)
    return wait_new(before, 20 if t == "App" else 15, exclude=exclude) or find_existing(it, exclude)


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
    if pl.endswith((".lnk", ".url")):
        t, _ = fix_shortcut("App", p)
        return t if t in ("資料夾", "網址") else "App"
    if os.path.isdir(os.path.expandvars(p)):
        return "資料夾"
    if pl.endswith((".exe", ".bat", ".cmd", ".com")):
        return "App"
    return "檔案"


def norm_path(p, t=""):
    p = p.strip().strip('"').strip()
    if t == "YouTube最新" and p.startswith("@"):
        p = "https://www.youtube.com/" + p
    if (t in ("網址", "YouTube最新") or (not t and p.lower().startswith("www."))) and p and "://" not in p:
        p = "https://" + p
    return p


def default_name(t, p):
    if t == "網址":
        m = re.match(r"https?://([^/]+)", p)
        return m.group(1) if m else p
    if t == "YouTube最新":
        m = re.search(r"youtube\.com/(?:@|c/|user/|channel/)?([^/?&]+)", p)
        return (m.group(1).lstrip("@") if m else p) + " 最新"
    base = re.split(r"[\\/]", p.rstrip("\\/"))[-1] or p
    return base if t in ("資料夾", "音樂資料夾") else os.path.splitext(base)[0] or base


def validate(t, p):
    if not p:
        return "路徑不能空白"
    if t == "網址":
        return "" if re.match(r"https?://\S+", p) else "網址要以 http:// 或 https:// 開頭"
    if t == "YouTube最新":
        if re.match(r"https?://\S+", p) and ("youtube.com" in p or "youtu.be" in p):
            return ""
        return "請填 YouTube 頻道網址，例如 https://www.youtube.com/@NBCNews"
    pe = os.path.expandvars(p)
    if os.path.exists(pe) or (t == "App" and shutil.which(pe)):
        if t in ("資料夾", "音樂資料夾") and not os.path.isdir(pe):
            return "這不是資料夾"
        return ""
    return "找不到這個路徑"


def item_error(it):
    if it.get("kind") == "self":
        return ""
    return validate(it.get("type", ""), it.get("path", ""))


# ----------------------------------------------------------------------------
# 外觀：跟隨 Windows 深色／淺色與強調色
# ----------------------------------------------------------------------------


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def blend(a, b, t):
    ra, rb = hex_rgb(a), hex_rgb(b)
    return "#%02X%02X%02X" % tuple(int(ra[i] * (1 - t) + rb[i] * t) for i in range(3))


def lum(h):
    r, g, b = hex_rgb(h)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def system_dark():
    if not IS_WIN:
        return os.environ.get("WL_DARK") == "1"
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


def system_accent():
    if not IS_WIN:
        return None
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM")
        v = winreg.QueryValueEx(k, "AccentColor")[0]
        return "#%02X%02X%02X" % (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)
    except Exception:
        return None


def make_palette(dark, accent=None):
    if dark:
        P = dict(bg="#1E1E1E", surf="#2A2A2A", surf2="#363636", bd="#404040", tx="#F2F2F2", sub="#B3B3B3",
                 mut="#8A8A8A", gold="#D9B450", danger="#FF7B72", cell="#2F2F2F", logo="#1F2A44")
    else:
        P = dict(bg="#FFFFFF", surf="#F5F5F3", surf2="#EAEAE6", bd="#DCDCD7", tx="#1F1F1E", sub="#66665F",
                 mut="#9A9A94", gold="#B8912A", danger="#C62828", cell="#F3F3F1", logo="#1F2A44")
    ac = accent or ("#5AA2FF" if dark else "#2F6FDE")
    if dark and lum(ac) < 0.45:
        ac = blend(ac, "#FFFFFF", 0.4)
    if not dark and lum(ac) > 0.75:
        ac = blend(ac, "#000000", 0.35)
    P["dark"] = dark
    P["ac"] = ac
    P["ac_bg"] = blend(P["bg"], ac, 0.24 if dark else 0.14)
    P["ac_hover"] = blend(ac, "#FFFFFF" if dark else "#000000", 0.12)
    P["on_ac"] = "#FFFFFF" if lum(ac) < 0.6 else "#111111"
    return P


GLYPH = {"play": "", "arrange": "", "move": "", "trash": "", "gear": "",
         "refresh": "", "compact": "", "expand": "", "back": ""}
LOGO_COLORS = ["#4C8DF6", "#E0B03A", "#3FB68B", "#8A93A6"]


def rrect(c, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
           x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return c.create_polygon(pts, smooth=True, **kw)


def resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


# ----------------------------------------------------------------------------
# 小元件
# ----------------------------------------------------------------------------


class Tip:
    def __init__(self, app):
        self.app, self.win, self.job = app, None, None

    def schedule(self, x, y, text, delay=550):
        self.cancel()
        self.job = self.app.root.after(delay, lambda: self.show(x, y, text))

    def show(self, x, y, text):
        self.cancel()
        P = self.app.pal
        w = tk.Toplevel(self.app.root)
        w.wm_overrideredirect(True)
        try:
            w.attributes("-topmost", True)
        except Exception:
            pass
        tk.Label(w, text=text, bg=P["surf2"], fg=P["tx"], font=self.app.font_small, padx=8, pady=4,
                 highlightbackground=P["bd"], highlightthickness=1, justify="left").pack()
        w.wm_geometry("+%d+%d" % (x + 12, y + 18))
        self.win = w

    def cancel(self):
        if self.job:
            try:
                self.app.root.after_cancel(self.job)
            except Exception:
                pass
            self.job = None
        if self.win:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None


class FlatButton(tk.Canvas):
    """圓角扁平按鈕（可帶 Windows 圖示字型）。"""

    def __init__(self, parent, app, text="", glyph="", tip="", command=None, primary=False, bg=None, minw=0):
        P = app.pal
        self.app, self.command, self.primary, self.tip = app, command, primary, tip
        self.icon = GLYPH.get(glyph, "") if (glyph and app.icon_font) else ""
        self.label = text or ("" if self.icon else tip)
        pad, gap = int(11 * app.scale), int(6 * app.scale)
        self.wic = app.icon_font.measure(self.icon) if self.icon else 0
        self.wtx = app.font_small.measure(self.label) if self.label else 0
        self.gap = gap if (self.icon and self.label) else 0
        w = max(minw, pad * 2 + self.wic + self.gap + self.wtx)
        h = int(30 * app.scale)
        super().__init__(parent, width=w, height=h, bg=bg or P["bg"], highlightthickness=0, cursor="hand2")
        self.hover = False
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)
        self.bind("<ButtonRelease-1>", self.on_click)
        self.draw()

    def draw(self):
        self.delete("all")
        P, sc = self.app.pal, self.app.scale
        w, h = int(self["width"]), int(self["height"])
        if self.primary:
            fill = P["ac_hover"] if self.hover else P["ac"]
            ol, fg = fill, P["on_ac"]
        else:
            fill = P["surf2"] if self.hover else P["surf"]
            ol, fg = P["bd"], P["tx"]
        rrect(self, 1, 1, w - 1, h - 1, int(8 * sc), fill=fill, outline=ol)
        x = (w - (self.wic + self.gap + self.wtx)) / 2.0
        if self.icon:
            self.create_text(x, h / 2, text=self.icon, font=self.app.icon_font, fill=fg, anchor="w")
            x += self.wic + self.gap
        if self.label:
            self.create_text(x, h / 2, text=self.label, font=self.app.font_small, fill=fg, anchor="w")

    def on_enter(self, e):
        self.hover = True
        self.draw()
        if self.tip and self.icon and not self.label:
            self.app.tip.schedule(e.x_root, e.y_root, self.tip)

    def on_leave(self, e):
        self.hover = False
        self.draw()
        self.app.tip.cancel()

    def on_click(self, e):
        self.app.tip.cancel()
        if 0 <= e.x <= int(self["width"]) and 0 <= e.y <= int(self["height"]) and self.command:
            self.command()


# ----------------------------------------------------------------------------
# 預覽（可操作：拖格子互換、拖邊界調大小；跟著視窗大小縮放）
# ----------------------------------------------------------------------------

PAD = 8


class Preview(tk.Canvas):
    def __init__(self, parent, app, screen):
        P = app.pal
        super().__init__(parent, bg=P["bg"], highlightthickness=0, width=60, height=int(110 * app.scale))
        self.app, self.screen = app, screen
        self.W, self.H = 100, 80
        self.tmp = None      # 拖邊界時的暫時排版
        self.drag = None     # ('cell', id, target) 或 ('edge', orient, pos, orig)
        self.bind("<Configure>", self.on_conf)
        self.bind("<Motion>", self.on_motion)
        self.bind("<ButtonPress-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<ButtonRelease-1>", self.on_release)

    def on_conf(self, e):
        self.W, self.H = max(40, e.width - 2 * PAD), max(40, e.height - 2 * PAD)
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
        P, sc = self.app.pal, self.app.scale
        lst = self.app.cur_list()
        names = {i["id"]: i for i in lst["items"]}
        rects = self.rects()
        rrect(self, 1, 1, PAD * 2 + self.W - 1, PAD * 2 + self.H - 1, int(10 * sc), fill=P["surf"], outline=P["bd"])
        target = self.drag[2] if self.drag and self.drag[0] == "cell" else None
        for k, (x, y, w, h) in rects.items():
            x0, y0 = self.px(x, y)
            x1, y1 = x0 + w * self.W, y0 + h * self.H
            sel = (k == self.app.sel_id)
            fill = P["ac_bg"] if (sel or k == SELF_ID) else P["cell"]
            outline = P["ac"] if sel else P["bd"]
            if target == k and self.drag[1] != k:
                fill, outline = P["surf2"], P["gold"]
            rrect(self, x0 + 2, y0 + 2, x1 - 2, y1 - 2, int(6 * sc), fill=fill, outline=outline, width=2 if sel else 1)
            self.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=names[k]["name"],
                             width=max(20, x1 - x0 - 12), font=self.app.font_small,
                             fill=P["ac"] if sel else P["tx"])
        for o, pos, a, b in edge_segments(rects):
            if o == "v":
                cx, cy = self.px(pos, (a + b) / 2)
                rrect(self, cx - 2, cy - 11, cx + 2, cy + 11, 2, fill=P["mut"], outline="")
            else:
                cx, cy = self.px((a + b) / 2, pos)
                rrect(self, cx - 11, cy - 2, cx + 11, cy + 2, 2, fill=P["mut"], outline="")

    def on_motion(self, e):
        edge = self.edge_at(e.x, e.y)
        self.config(cursor=("sb_h_double_arrow" if edge[0] == "v" else "sb_v_double_arrow") if edge else "")

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
            v = (e.x - PAD) / float(self.W) if o == "v" else (e.y - PAD) / float(self.H)
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

COLS = ("grip", "chk", "name", "type", "path")
EDITABLE = ["name", "type", "path"]


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
        self.status_text = "就緒"
        self.draft = {"type": "", "name": "", "path": "", "screen": 0}
        self.draft_err = ""
        self.dark = system_dark()
        self.accent = system_accent()
        self.pal = make_palette(self.dark, self.accent)
        root.title("%s %s" % (APP_NAME, APP_VERSION))
        root.geometry(self.cfg.get("geometry") or "760x600")
        root.minsize(400, 90)
        self.set_icon()
        self.setup_fonts()
        self.tip = Tip(self)
        self.setup_style()
        self.build_ui()
        self.refresh_all()
        self.set_compact(self.cfg.get("compact", False), initial=True)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind_all("<ButtonPress-1>", self.on_any_click, add="+")
        root.after(300, self.after_mapped)
        root.after(3000, self.check_theme)
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
        self.status_text = text
        try:
            self.status.config(text=text)
        except Exception:
            pass

    def on_close(self):
        if not self.cfg.get("compact"):
            self.cfg["geometry"] = self.root.geometry()
        self.save()
        self.root.destroy()

    def set_icon(self):
        try:
            if IS_WIN:
                self.root.iconbitmap(default=resource_path("app.ico"))
        except Exception:
            pass

    def after_mapped(self):
        try:
            hw = own_hwnd(self.root)
            if hw:
                EXCLUDE.add(hw)
                set_titlebar_dark(hw, self.dark)
        except Exception:
            pass

    def check_theme(self):
        try:
            d, a = system_dark(), system_accent()
            if (d, a) != (self.dark, self.accent):
                self.dark, self.accent = d, a
                self.pal = make_palette(d, a)
                self.rebuild_ui()
                self.after_mapped()
        finally:
            self.root.after(3000, self.check_theme)

    def rebuild_ui(self):
        self.end_edit(False)
        for w in self.root.winfo_children():
            w.destroy()
        self.tip = Tip(self)
        self.hdr_cb = None
        self.setup_style()
        self.build_ui()
        self.refresh_all()
        self.set_status(self.status_text)
        self.set_compact(self.cfg.get("compact", False), initial=True)

    # ---- 字型與樣式 ----
    def setup_fonts(self):
        r = self.root
        base = tkfont.nametofont("TkDefaultFont").actual("family")
        fam = "Microsoft JhengHei UI" if IS_WIN else base
        self.font = tkfont.Font(root=r, family=fam, size=10)
        self.font_small = tkfont.Font(root=r, family=fam, size=9)
        self.font_bold = tkfont.Font(root=r, family=fam, size=10, weight="bold")
        self.font_italic = tkfont.Font(root=r, family=fam, size=9, slant="italic")
        self.scale = max(1.0, r.winfo_fpixels("1i") / 96.0)
        self.icon_font = None
        fams = set(tkfont.families(r))
        for cand in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
            if cand in fams:
                self.icon_font = tkfont.Font(root=r, family=cand, size=11)
                break

    def setup_style(self):
        P = self.pal
        r = self.root
        r.configure(bg=P["bg"])
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("Treeview", rowheight=int(30 * self.scale), font=self.font, borderwidth=0,
                     background=P["bg"], fieldbackground=P["bg"], foreground=P["tx"])
        st.configure("Treeview.Heading", font=self.font_small, foreground=P["mut"], background=P["bg"],
                     relief="flat", borderwidth=0)
        st.map("Treeview.Heading", background=[("active", P["bg"])])
        st.map("Treeview", background=[("selected", P["ac_bg"])], foreground=[("selected", P["tx"])])
        st.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        for name in ("TEntry", "TCombobox"):
            st.configure(name, fieldbackground=P["bg"], foreground=P["tx"], insertcolor=P["tx"],
                         bordercolor=P["ac"], lightcolor=P["ac"], darkcolor=P["ac"], padding=4)
        st.configure("TCombobox", background=P["surf2"], arrowcolor=P["tx"])
        st.map("TCombobox", fieldbackground=[("readonly", P["bg"])], foreground=[("readonly", P["tx"])],
               selectbackground=[("readonly", P["bg"])], selectforeground=[("readonly", P["tx"])])
        st.configure("Vertical.TScrollbar", background=P["surf2"], troughcolor=P["bg"], bordercolor=P["bg"],
                     arrowcolor=P["mut"], lightcolor=P["surf2"], darkcolor=P["surf2"], relief="flat")
        st.configure("TCheckbutton", background=P["bg"], foreground=P["tx"], font=self.font)
        st.map("TCheckbutton", background=[("active", P["bg"])])
        r.option_add("*TCombobox*Listbox.background", P["bg"])
        r.option_add("*TCombobox*Listbox.foreground", P["tx"])
        r.option_add("*TCombobox*Listbox.selectBackground", P["ac"])
        r.option_add("*TCombobox*Listbox.selectForeground", P["on_ac"])
        r.option_add("*TCombobox*Listbox.font", self.font)
        # 滑鼠滾輪絕對不能改到下拉欄位的值；↑↓ 也不能直接換值
        r.bind_class("TCombobox", "<MouseWheel>", lambda e: "break")
        r.bind_class("TCombobox", "<Button-4>", lambda e: "break")
        r.bind_class("TCombobox", "<Button-5>", lambda e: "break")
        r.bind_class("TCombobox", "<Up>", lambda e: "break")
        r.bind_class("TCombobox", "<Down>", self.combo_post)

    def combo_post(self, e):
        try:
            e.widget.tk.call("ttk::combobox::Post", str(e.widget))
        except Exception:
            pass
        return "break"

    # ---- 畫面 ----
    def build_ui(self):
        P, r, sc = self.pal, self.root, self.scale
        self.bottom = tk.Frame(r, bg=P["bg"])
        self.bottom.pack(side="bottom", fill="x")
        tk.Frame(self.bottom, height=1, bg=P["bd"]).pack(fill="x")
        row = tk.Frame(self.bottom, bg=P["bg"])
        row.pack(fill="x")
        self.status = tk.Label(row, text=self.status_text, anchor="w", bg=P["bg"], fg=P["mut"], font=self.font_small)
        self.status.pack(side="left", padx=12, pady=5)
        tk.Label(row, text="%s　%s" % (AUTHOR, APP_VERSION), bg=P["bg"], fg=P["gold"],
                 font=self.font_italic).pack(side="right", padx=12)

        self.tb = tk.Frame(r, bg=P["bg"])
        self.tb.pack(side="top", fill="x")
        self.logo(self.tb).pack(side="left", padx=(12, 8), pady=8)
        tk.Label(self.tb, text="工作清單", bg=P["bg"], fg=P["tx"], font=self.font_bold).pack(side="left")
        FlatButton(self.tb, self, text="全部開啟並排列", glyph="play", tip="全部開啟並排列", command=self.run_all,
                   primary=True).pack(side="right", padx=(3, 12), pady=8)
        for glyph, tip, cmd in (("gear", "設定", self.show_settings), ("compact", "精簡模式", lambda: self.set_compact(True)),
                                ("trash", "刪除勾選", self.delete_checked), ("move", "移到其他清單", self.move_menu),
                                ("arrange", "整理現有視窗", self.arrange_now)):
            b = FlatButton(self.tb, self, glyph=glyph, tip=tip, command=cmd)
            b.pack(side="right", padx=3, pady=8)
            if glyph == "move":
                self.btn_move = b
        self.tb_line = tk.Frame(r, height=1, bg=P["bd"])
        self.tb_line.pack(side="top", fill="x")

        self.cbar = tk.Frame(r, bg=P["bg"])
        self.cname = tk.Label(self.cbar, text="", bg=P["bg"], fg=P["tx"], font=self.font_bold)
        self.cname.pack(side="left", padx=12, pady=10)
        FlatButton(self.cbar, self, glyph="expand", tip="展開", command=lambda: self.set_compact(False)).pack(side="right", padx=(3, 12))
        FlatButton(self.cbar, self, text="全部開啟並排列", glyph="play", tip="全部開啟並排列", command=self.run_all,
                   primary=True).pack(side="right", padx=3)

        self.main = tk.Frame(r, bg=P["bg"])
        self.main.pack(side="top", fill="both", expand=True)
        self.body = tk.Frame(self.main, bg=P["bg"])
        self.body.pack(fill="both", expand=True)

        left = tk.Frame(self.body, width=int(116 * sc), bg=P["bg"])
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self.lists = ttk.Treeview(left, columns=("grip", "name"), show="tree", selectmode="browse")
        self.lists.column("#0", width=0, stretch=False)
        self.lists.column("grip", width=int(22 * sc), stretch=False, anchor="center")
        self.lists.column("name", width=int(90 * sc), anchor="w")
        self.lists.pack(fill="both", expand=True, padx=4, pady=6)
        self.lists.tag_configure("muted", foreground=P["mut"])
        self.lists.tag_configure("drop", background=P["ac_bg"])
        self.lists.bind("<ButtonPress-1>", lambda e: self.on_press("lists", e))
        self.lists.bind("<B1-Motion>", lambda e: self.on_motion("lists", e))
        self.lists.bind("<ButtonRelease-1>", lambda e: self.on_release("lists", e))
        tk.Frame(self.body, width=1, bg=P["bd"]).pack(side="left", fill="y")

        right = tk.Frame(self.body, bg=P["bg"])
        right.pack(side="left", fill="both", expand=True)

        self.bot = tk.Frame(right, bg=P["bg"])
        self.bot.pack(side="bottom", fill="x", padx=10, pady=(4, 10))
        self.bot.columnconfigure(0, weight=1)
        self.pv_holder = tk.Frame(self.bot, height=int(152 * sc), bg=P["bg"])
        self.pv_holder.grid(row=0, column=0, sticky="nsew")
        self.pv_holder.pack_propagate(False)
        cw = int(176 * sc)
        ctl = tk.Frame(self.bot, bg=P["surf"], width=cw, height=int(152 * sc), highlightbackground=P["bd"],
                       highlightthickness=1)
        ctl.grid(row=0, column=1, sticky="ns", padx=(10, 0))
        ctl.pack_propagate(False)
        r1 = tk.Frame(ctl, bg=P["surf"])
        r1.pack(fill="x", padx=12, pady=(10, 2))
        tk.Label(r1, text="開啟於", bg=P["surf"], fg=P["sub"], font=self.font_small).pack(side="left")
        self.lbl_screen = tk.Label(r1, text="", bg=P["surf"], fg=P["ac"], font=self.font_bold, cursor="hand2")
        self.lbl_screen.pack(side="right")
        self.lbl_screen.bind("<Button-1>", self.edit_list_screen)
        r2 = tk.Frame(ctl, bg=P["surf"])
        r2.pack(fill="x", padx=12, pady=(0, 8))
        tk.Label(r2, text="排版", bg=P["surf"], fg=P["sub"], font=self.font_small).pack(side="left")
        self.lbl_layout = tk.Label(r2, text="", bg=P["surf"], fg=P["tx"], font=self.font_bold)
        self.lbl_layout.pack(side="right")
        FlatButton(ctl, self, text="還原自動平分", glyph="refresh", tip="還原自動平分", command=self.restore_auto,
                   bg=P["surf"], minw=cw - 24).pack(padx=12, pady=2)
        FlatButton(ctl, self, text="刪除此清單", glyph="trash", tip="刪除此清單", command=self.delete_list,
                   bg=P["surf"], minw=cw - 24).pack(padx=12, pady=2)

        tf = tk.Frame(right, bg=P["bg"])
        tf.pack(side="top", fill="both", expand=True, padx=(10, 4), pady=(8, 0))
        tf.columnconfigure(0, weight=1)
        tf.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(tf, columns=COLS, show="headings", selectmode="browse")
        for c, t, w, a, stretch in [("grip", "", 26, "center", False), ("chk", "", 32, "center", False),
                                    ("name", "名稱", 130, "w", True), ("type", "類型", 104, "w", False),
                                    ("path", "路徑／網址", 190, "w", True)]:
            self.tree.heading(c, text=t, anchor="w")
            self.tree.column(c, width=int(w * sc), anchor=a, stretch=stretch)
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.tag_configure("error", foreground=P["danger"])
        self.tree.tag_configure("blank", foreground=P["mut"])
        self.tree.tag_configure("self", foreground=P["mut"])
        self.tree.bind("<ButtonPress-1>", lambda e: self.on_press("items", e))
        self.tree.bind("<B1-Motion>", lambda e: self.on_motion("items", e))
        self.tree.bind("<ButtonRelease-1>", lambda e: self.on_release("items", e))
        self.tree.bind("<Button-3>", self.on_rclick)
        self.tree.bind("<Motion>", self.on_hover)
        self.tree.bind("<Leave>", lambda e: self.tip.cancel())
        self.tree.bind("<Alt-Up>", lambda e: self.nudge(-1))
        self.tree.bind("<Alt-Down>", lambda e: self.nudge(1))
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.droplines = {"items": tk.Frame(self.tree, bg=P["ac"], height=2),
                          "lists": tk.Frame(self.lists, bg=P["ac"], height=2)}
        if HAS_DND:
            try:
                self.tree.drop_target_register(DND_FILES)
                self.tree.dnd_bind("<<Drop>>", self.on_drop_files)
            except Exception:
                pass

        # 設定
        self.settings = tk.Frame(self.main, bg=P["bg"])
        self.var_auto = tk.BooleanVar(value=self.cfg.get("autostart", False))
        self.var_shuffle = tk.BooleanVar(value=self.cfg.get("music_shuffle", True))
        self.var_sub = tk.BooleanVar(value=self.cfg.get("music_sub", True))
        tk.Label(self.settings, text="設定", bg=P["bg"], fg=P["tx"], font=self.font_bold).pack(anchor="w", padx=16, pady=(14, 8))
        ttk.Checkbutton(self.settings, text="開機時自動啟動工具（預設關閉，勾選立刻生效，取消即移除）",
                        variable=self.var_auto, command=self.on_autostart).pack(anchor="w", padx=16, pady=4)
        row = tk.Frame(self.settings, bg=P["bg"])
        row.pack(anchor="w", padx=16, pady=6)
        tk.Label(row, text="開機後自動執行清單：", bg=P["bg"], fg=P["tx"], font=self.font).pack(side="left")
        self.cb_autorun = ttk.Combobox(row, state="readonly", width=22)
        self.cb_autorun.pack(side="left")
        self.cb_autorun.bind("<<ComboboxSelected>>", self.on_autorun)
        ttk.Checkbutton(self.settings, text="音樂資料夾：隨機播放", variable=self.var_shuffle,
                        command=self.on_music_opts).pack(anchor="w", padx=16, pady=4)
        ttk.Checkbutton(self.settings, text="音樂資料夾：包含子資料夾", variable=self.var_sub,
                        command=self.on_music_opts).pack(anchor="w", padx=16, pady=4)
        FlatButton(self.settings, self, text="返回", glyph="back", tip="返回", command=self.hide_settings).pack(anchor="w", padx=16, pady=12)

    def logo(self, parent):
        P = self.pal
        s = int(24 * self.scale)
        c = tk.Canvas(parent, width=s, height=s, bg=P["bg"], highlightthickness=0)
        rrect(c, 0, 0, s, s, int(6 * self.scale), fill=P["logo"], outline="")
        pad, gap = s * 0.2, s * 0.08
        cell = (s - 2 * pad - gap) / 2.0
        for i, col in enumerate(LOGO_COLORS):
            x = pad + (i % 2) * (cell + gap)
            y = pad + (i // 2) * (cell + gap)
            rrect(c, x, y, x + cell, y + cell, 2, fill=col, outline="")
        return c

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
            return ("•", "☑" if it.get("checked") else "☐", it["name"], "App", "（固定第一列，不能刪除）")
        tag = "  · 螢幕%d" % it["screen"] if it.get("screen") else ""
        return ("⋮⋮", "☑" if it.get("checked") else "☐", it["name"] + tag, it["type"], it["path"])

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
        return ("", "", d["name"], d["type"], path)

    def refresh_row(self, iid):
        it = self.find_item(iid)
        if it and iid in self.tree.get_children():
            self.tree.item(iid, values=self.row_values(it), tags=self.item_tags(it))

    def refresh_blank_row(self):
        if NEW in self.tree.get_children():
            self.tree.item(NEW, values=self.blank_values(), tags=("error",) if self.draft_err else ("blank",))

    def refresh_preview(self):
        P = self.pal
        for w in self.pv_holder.winfo_children():
            w.destroy()
        for s in screens_in_use(self.cur_list()):
            box = tk.Frame(self.pv_holder, bg=P["bg"])
            box.pack(side="left", fill="both", expand=True, padx=(0, 8))
            tk.Label(box, text="螢幕%d" % s, bg=P["bg"], fg=P["sub"], font=self.font_small).pack(anchor="w")
            Preview(box, self, s).pack(fill="both", expand=True)

    def previews(self):
        return [c for b in self.pv_holder.winfo_children() for c in b.winfo_children() if isinstance(c, Preview)]

    def select_item(self, iid):
        self.sel_id = iid
        if iid in self.tree.get_children():
            self.tree.selection_set(iid)
            self.tree.see(iid)

    def on_select(self, e):
        sel = self.tree.selection()
        if sel and sel[0] != NEW and sel[0] != self.sel_id:
            self.sel_id = sel[0]
            for p in self.previews():
                p.draw()

    # ---- 滑鼠：表格與清單 ----
    def tree_of(self, kind):
        return self.tree if kind == "items" else self.lists

    def col_name(self, kind, colid):
        try:
            n = int(colid.replace("#", ""))
        except ValueError:
            return ""
        cols = COLS if kind == "items" else ("", "grip", "name")
        idx = n - 1 if kind == "items" else n
        return cols[idx] if 0 <= idx < len(cols) else ""

    def on_press(self, kind, e):
        t = self.tree_of(kind)
        self.tip.cancel()
        self.end_edit(True)
        row = t.identify_row(e.y)
        if t.identify_region(e.x, e.y) not in ("cell", "tree") or not row:
            return
        col = self.col_name(kind, t.identify_column(e.x))
        if kind == "items":
            if row == NEW:
                if col in EDITABLE:
                    self.begin_edit(NEW, col)
                return "break"
            self.select_item(row)
            it = self.find_item(row)
            if col == "chk":
                it["checked"] = not it.get("checked")
                if it["type"] in NOTILE_TYPES:
                    pass
                else:
                    self.cur_list()["layout"] = "auto" if self.cur_list().get("layout") != "custom" else "auto"
                    self.cur_list()["rects"] = {}
                self.save()
                self.refresh_row(row)
                self.refresh_header()
                self.refresh_preview()
                return "break"
            if col == "grip" and it.get("kind") != "self":
                self.drag = {"kind": kind, "row": row, "y0": e.y, "on": False}
                return "break"
            if it.get("kind") != "self" and col in EDITABLE:
                self.begin_edit(row, col)
                return "break"
            return None
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

    def switch_list(self, lid):
        if lid == self.cfg["current"] or lid == NEWLIST:
            return
        self.cfg["current"] = lid
        self.sel_id = SELF_ID
        self.save()
        self.refresh_all()

    def on_hover(self, e):
        self.tip.cancel()
        if self.drag:
            return
        t = self.tree
        row = t.identify_row(e.y)
        if not row or row == NEW:
            return
        col = self.col_name("items", t.identify_column(e.x))
        if col not in ("name", "path"):
            return
        text = str(t.set(row, col))
        if self.font.measure(text) > int(t.column(col, "width")) - 14:
            self.tip.schedule(e.x_root, e.y_root, text)

    def on_rclick(self, e):
        row = self.tree.identify_row(e.y)
        it = self.find_item(row) if row and row != NEW else None
        if not it or it.get("kind") == "self":
            return
        self.select_item(row)
        P = self.pal
        m = tk.Menu(self.root, tearoff=0, bg=P["surf"], fg=P["tx"], activebackground=P["ac"], activeforeground=P["on_ac"])
        var = tk.IntVar(value=it.get("screen", 0))
        for v, label in ((0, "此項開在：跟隨清單"), (1, "此項開在：螢幕1"), (2, "此項開在：螢幕2")):
            m.add_radiobutton(label=label, variable=var, value=v, command=lambda v=v: self.set_item_screen(row, v))
        m.tk_popup(e.x_root, e.y_root)

    def set_item_screen(self, iid, v):
        it = self.find_item(iid)
        if not it:
            return
        it["screen"] = v
        l = self.cur_list()
        l["layout"] = "auto"
        l["rects"] = {}
        self.save()
        self.refresh_row(iid)
        self.refresh_header()
        self.refresh_preview()

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
            t, p = fix_shortcut(t, p)
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
            return self.draft[col]
        return self.find_item(iid)[col]

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
        if col == "type":
            w_ = ttk.Combobox(self.tree, state="readonly", values=TYPES, font=self.font)
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
            self.begin_edit(NEW, "name")
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
            if rows[ri] != NEW:
                self.select_item(rows[ri])
            self.begin_edit(rows[ri], EDITABLE[ci])

    def commit_value(self, iid, col, text):
        self.just_saved = False
        if iid == NEW:
            d = self.draft
            if col == "type":
                d["type"] = text
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
        reset = False
        if col == "type":
            reset = (text in NOTILE_TYPES) != (it["type"] in NOTILE_TYPES)
            it["type"] = text
        elif col == "path":
            p = norm_path(text, it["type"])
            t2, p2 = fix_shortcut(it["type"], p)
            reset = (t2 in NOTILE_TYPES) != (it["type"] in NOTILE_TYPES)
            it["type"], it["path"] = t2, p2
            it.pop("cid", None)
        else:
            it["name"] = text.strip() or default_name(it["type"], it["path"])
        if reset:
            l = self.cur_list()
            l["layout"] = "auto"
            l["rects"] = {}
        self.save()
        self.refresh_row(iid)
        err = item_error(it)
        self.set_status(err and "「%s」%s" % (it["name"], err) or "已儲存")
        self.refresh_header()
        self.refresh_preview()

    def try_save_draft(self):
        d = self.draft
        if not d["path"]:
            return
        path = norm_path(d["path"], d["type"])
        t = d["type"] or guess_type(path)
        t, path = fix_shortcut(t, norm_path(path, t))
        err = validate(t, path)
        if err:
            self.draft_err = err
            self.set_status("空白列：%s（沒有儲存）" % err)
            return
        self.add_item(t, d["name"], path, 0)
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
        self.close_hdr_cb()
        lbl = self.lbl_screen
        cb = ttk.Combobox(lbl.master, state="readonly", values=[SCREEN_LABEL[1], SCREEN_LABEL[2]], width=7, font=self.font)
        cb.set(lbl.cget("text"))
        cb.place(in_=lbl, relx=1.0, x=0, y=0, anchor="ne", relheight=1.0)
        cb.focus_set()
        self.hdr_cb = cb

        def pick(ev=None):
            l = self.cur_list()
            l["screen"] = SCREEN_FROM[cb.get()]
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
        P = self.pal
        m = tk.Menu(self.root, tearoff=0, bg=P["surf"], fg=P["tx"], activebackground=P["ac"], activeforeground=P["on_ac"])
        for l in others:
            m.add_command(label="移到「%s」" % l["name"], command=lambda x=l["id"]: self.transfer(ids, x, False))
        m.add_separator()
        for l in others:
            m.add_command(label="複製到「%s」" % l["name"], command=lambda x=l["id"]: self.transfer(ids, x, True))
        b = self.btn_move
        m.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    # ---- 執行與排列 ----
    def music_opts(self):
        return {"music_shuffle": self.cfg.get("music_shuffle", True), "music_sub": self.cfg.get("music_sub", True)}

    def place_one(self, lst, iid, hwnd):
        it = next((i for i in lst["items"] if i["id"] == iid), None)
        if not it or not hwnd:
            return
        s = effective_screen(lst, it)
        r = get_rects(lst, s).get(iid)
        if not r:
            return
        m = monitor_for(s)
        x, y, w, h = r
        try:
            place_window(hwnd, m["x"] + int(round(x * m["w"])), m["y"] + int(round(y * m["h"])),
                         int(round(w * m["w"])), int(round(h * m["h"])))
        except Exception:
            pass

    def run_all(self):
        if self.busy:
            return
        l = self.cur_list()
        items = [dict(i) for i in l["items"] if i.get("checked") and i.get("kind") != "self"]
        self.busy = True
        self.set_status("開啟中…")
        me = next((i for i in l["items"] if i["id"] == SELF_ID), None)
        if me and me.get("checked"):
            self.place_one(l, SELF_ID, own_hwnd(self.root))
        opts = self.music_opts()
        used0 = set(EXCLUDE)
        q = queue.Queue()

        def work():
            used = set(used0)
            pending, failed = [], []
            for it in items:
                q.put(("status", "開啟：%s…" % it["name"]))
                try:
                    h = open_item(it, used, opts, lambda cid, i=it: q.put(("cid", i["id"], cid)))
                except Exception as ex:
                    failed.append("%s（%s）" % (it["name"], ex))
                    continue
                if h == NOTILE:
                    continue
                if h:
                    used.add(h)
                    q.put(("placed", it["id"], h))
                else:
                    pending.append(it)
            t0 = time.time()
            while pending and time.time() - t0 < 60:
                q.put(("status", "等待視窗出現：%s…" % "、".join(p["name"] for p in pending)))
                time.sleep(1.5)
                for it in list(pending):
                    h = find_existing(it, used)
                    if h:
                        used.add(h)
                        pending.remove(it)
                        q.put(("placed", it["id"], h))
            q.put(("done", [p["name"] for p in pending], failed))

        def poll():
            try:
                while True:
                    msg = q.get_nowait()
                    if msg[0] == "status":
                        self.set_status(msg[1])
                    elif msg[0] == "placed":
                        self.place_one(l, msg[1], msg[2])
                    elif msg[0] == "cid":
                        for i in l["items"]:
                            if i["id"] == msg[1]:
                                i["cid"] = msg[2]
                        self.save()
                    else:
                        self.finish_run(msg[1], msg[2])
                        return
            except queue.Empty:
                pass
            self.root.after(150, poll)

        threading.Thread(target=work, daemon=True).start()
        self.root.after(150, poll)

    def finish_run(self, pending, failed):
        self.busy = False
        msg = "完成：已開啟並排列"
        if pending:
            msg += "；等了一分鐘還沒看到視窗：" + "、".join(pending)
        if failed:
            msg += "；開啟失敗：" + "、".join(failed)
        self.set_status(msg)

    def arrange_now(self):
        l = self.cur_list()
        skipped = []
        used = set(EXCLUDE)
        for it in l["items"]:
            if it.get("kind") == "self":
                if it.get("checked"):
                    self.place_one(l, SELF_ID, own_hwnd(self.root))
                continue
            if not it.get("checked") or it["type"] in NOTILE_TYPES:
                continue
            h = find_existing(it, used) if it["type"] not in ("網址", "YouTube最新") else None
            if h:
                used.add(h)
                self.place_one(l, it["id"], h)
            else:
                skipped.append(it["name"])
        msg = "已整理現有視窗"
        if skipped:
            msg += "；沒找到已開啟的視窗：" + "、".join(skipped)
        self.set_status(msg)

    # ---- 精簡模式／設定 ----
    def set_compact(self, flag, initial=False):
        self.end_edit(False)
        if flag:
            if not initial:
                self.cfg["geometry"] = self.root.geometry()
            self.tb.pack_forget()
            self.tb_line.pack_forget()
            self.main.pack_forget()
            self.cbar.pack(side="top", fill="x")
            if not initial:
                self.root.geometry("%dx%d" % (min(max(self.root.winfo_width(), 400), 560), int(96 * self.scale)))
        else:
            self.cbar.pack_forget()
            self.tb.pack(side="top", fill="x")
            self.tb_line.pack(side="top", fill="x")
            self.main.pack(side="top", fill="both", expand=True)
            if not initial:
                self.root.geometry(self.cfg.get("geometry") or "760x600")
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

    def on_music_opts(self):
        self.cfg["music_shuffle"] = bool(self.var_shuffle.get())
        self.cfg["music_sub"] = bool(self.var_sub.get())
        self.save()


def main():
    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    App(root, autostart_run="--autostart" in sys.argv)
    root.mainloop()


if __name__ == "__main__":
    main()
