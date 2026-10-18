import os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tkinter as tk
import workspace_launcher as w

tmp = tempfile.mkdtemp()
cfgf = os.path.join(tmp, "c.json")
fdir = os.path.join(tmp, "專案"); os.makedirs(fdir)
fx = os.path.join(tmp, "月報.xlsx"); open(fx, "w").close()

root = tk.Tk(); root.geometry("1000x660")
app = w.App(root, cfg_file=cfgf)
root.update()

def pump(n=5):
    for _ in range(n):
        root.update_idletasks(); root.update()

def type_into(iid, col, text, move=1):
    app.begin_edit(iid, col); pump()
    assert app.edit, "沒有開啟編輯"
    ww = app.edit["w"]
    if isinstance(ww, tk.ttk.Combobox):
        ww.set(text)
    else:
        ww.delete(0, "end"); ww.insert(0, text)
    app.finish_edit(move); pump()

rows = list(app.tree.get_children())
assert rows == ["self", w.NEW], rows
print("初始列 ok")

# 空白列：填路徑 → 自動猜類型並儲存
type_into(w.NEW, "path", fdir)
items = app.cur_list()["items"]
assert len(items) == 2 and items[1]["type"] == "資料夾" and items[1]["name"] == "專案", items
assert app.draft["path"] == "" and app.edit and app.edit["iid"] == w.NEW and app.edit["col"] == "name", app.edit
app.end_edit(False)
print("空白列自動存檔 ok")

# 錯誤：標紅不存
type_into(w.NEW, "path", "/no/such/file.txt", move=0)
assert len(app.cur_list()["items"]) == 2 and app.draft_err and "error" in app.tree.item(w.NEW, "tags")
print("錯誤標紅不存 ok")
app.draft = {"type": "", "name": "", "path": "", "screen": 0}; app.draft_err = ""; app.refresh_items()

# 網址
type_into(w.NEW, "type", "網址", move=0)
type_into(w.NEW, "path", "www.example.com", move=0)
assert app.cur_list()["items"][-1]["path"] == "https://www.example.com", app.cur_list()["items"][-1]
app.end_edit(False)
# 檔案（用拖入的方式）
class Ev: data = "{%s}" % fx; action = "copy"
app.on_drop_files(Ev()); pump()
assert app.cur_list()["items"][-1]["type"] == "檔案" and app.cur_list()["items"][-1]["name"] == "月報"
print("網址／拖入檔案 ok")

# Tab 導覽：改第二列名稱後往右到「路徑」
fid = app.cur_list()["items"][1]["id"]
type_into(fid, "name", "專案資料", move=1)
assert app.edit and app.edit["iid"] == fid and app.edit["col"] == "type", app.edit
app.end_edit(False)
assert app.find_item(fid)["name"] == "專案資料"
print("Enter/Tab 跳下一格 ok")

# 下拉：滾輪與↑↓不能改值；↓才打開
app.begin_edit(fid, "type"); pump()
cb = app.edit["w"]; cb.set("資料夾")
cb.focus_force(); pump()
for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>", "<Up>"):
    try:
        cb.event_generate(seq, delta=-120)
    except tk.TclError:
        pass
    pump()
assert cb.get() == "資料夾", cb.get()
app.end_edit(False)
print("滾輪／↑ 不改值 ok")

# 勾選＝執行；刪除只走右鍵（delete_item），刪前要確認
import tkinter.messagebox as mb
asked = []
mb.askyesno = lambda *a, **k: (asked.append(a) or True)
app.tree.update_idletasks()
bb = app.tree.bbox(fid, "chk")
app.tree.event_generate("<ButtonPress-1>", x=bb[0] + 5, y=bb[1] + 5); pump()
app.tree.event_generate("<ButtonRelease-1>", x=bb[0] + 5, y=bb[1] + 5); pump()
assert app.find_item(fid)["checked"] is False
app.find_item(fid)["checked"] = True
assert not hasattr(app, "delete_checked")
n0 = len(app.cur_list()["items"])
app.delete_item("self")
assert len(app.cur_list()["items"]) == n0, "本工具不能刪"
app.delete_item(fid)
assert asked and "月報" not in asked[-1][1] and len(app.cur_list()["items"]) == n0 - 1 and app.find_item(fid) is None
print("右鍵刪除單項要確認 ok")
app.cur_list()["items"] = [i for i in app.cur_list()["items"] if i["id"] == "self"]; app.refresh_all(); pump()

# 再補幾個項目，測拖曳排序／Alt+↑↓／換清單
for p in (fdir, fx):
    t = w.guess_type(p); app.add_item(t, w.default_name(t, p), p, 0)
app.refresh_all(); pump()
ids = [i["id"] for i in app.cur_list()["items"]]
app.select_item(ids[2]); app.nudge(-1)
assert [i["id"] for i in app.cur_list()["items"]][1] == ids[2]
app.nudge(-1)   # 已在最上面（本工具是固定第一列）
assert app.cur_list()["items"][0]["id"] == "self"
print("Alt+↑↓ ok")

l2 = w.new_list("寫程式"); app.cfg["lists"].append(l2); app.refresh_all()
app.transfer([ids[1]], l2["id"], copy_=True)
assert len(l2["items"]) == 2 and any(i["id"] == ids[1] for i in app.cur_list()["items"])
app.transfer([ids[1]], l2["id"], copy_=False)
assert not any(i["id"] == ids[1] for i in app.cur_list()["items"]) and len(l2["items"]) == 3
print("清單間搬移／複製 ok")

# 預覽：拖邊界、換格子
app.add_item("網址", "入口", "https://portal.example.com", 0); app.refresh_all(); pump()
pvs = [c for b in app.pv_holder.winfo_children() for c in b.winfo_children() if isinstance(c, w.Preview)]
assert len(pvs) == 1
pv = pvs[0]
r = w.get_rects(app.cur_list(), 1)
assert len(r) == 3, r
x = int(pv.px(0.6, 0.5)[0]); y = int(pv.px(0.6, 0.5)[1])
assert pv.edge_at(x, y) and pv.edge_at(x, y)[0] == "v"
pv.event_generate("<ButtonPress-1>", x=x, y=y); pump()
nx = int(pv.px(0.5, 0.5)[0])
pv.event_generate("<B1-Motion>", x=nx, y=y); pump()
pv.event_generate("<ButtonRelease-1>", x=nx, y=y); pump()
assert app.cur_list()["layout"] == "custom"
r2 = w.get_rects(app.cur_list(), 1)
assert abs(r2["self"][2] - 0.5) < 1e-6, r2
pvs = [c for b in app.pv_holder.winfo_children() for c in b.winfo_children() if isinstance(c, w.Preview)]
pv = pvs[0]
a = pv.px(0.25, 0.5); b = pv.px(0.75, 0.25)
pv.event_generate("<ButtonPress-1>", x=int(a[0]), y=int(a[1])); pump()
pv.event_generate("<B1-Motion>", x=int(b[0]), y=int(b[1])); pump()
pv.event_generate("<ButtonRelease-1>", x=int(b[0]), y=int(b[1])); pump()
r3 = w.get_rects(app.cur_list(), 1)
assert r3["self"] != r2["self"], (r2, r3)
app.restore_auto()
assert app.cur_list()["layout"] == "auto" and w.get_rects(app.cur_list(), 1)["self"] == (0, 0, 0.6, 1.0)
print("預覽拖邊界／換格子／還原 ok")

# 新類型與音樂不排版
m = w.new_list("音樂測試"); m["items"].append({"id": "mu", "kind": "item", "type": "音樂資料夾", "name": "音樂", "path": fdir, "screen": 0, "checked": True})
assert w.group_ids(m, 1) == ["self"] and "mu" not in w.get_rects(m, 1)
assert w.validate("YouTube最新", "https://www.youtube.com/@NBCNews") == ""
print("新類型 ok")

# 例外螢幕 → 兩個預覽
it = app.cur_list()["items"][1]; it["screen"] = 2; app.save(); app.refresh_preview(); pump()
pvs = [c for b in app.pv_holder.winfo_children() for c in b.winfo_children() if isinstance(c, w.Preview)]
assert [p.screen for p in pvs] == [1, 2]
app.refresh_items(); assert "· 螢幕2" in app.tree.set(it["id"], "name")
it["screen"] = 0; app.refresh_preview()
print("雙螢幕預覽 ok")

# 執行：換成假的開啟／排版函式，檢查排到螢幕2 的座標
placed = {}
w.open_item = lambda item, exclude=(), opts=None, on_cid=None, info=None: abs(hash(item["id"])) % 100000 + 10
w.place_window = lambda h, x, y, wd, ht: placed.__setitem__(h, (x, y, wd, ht))
w.own_hwnd = lambda r: 1
app.cur_list()["screen"] = 2; app.cur_list()["layout"] = "auto"; app.refresh_all(); pump()
app.run_all()
for _ in range(100):
    pump(); time.sleep(0.05)
    if not app.busy: break
assert not app.busy and len(placed) == 3, placed
assert placed[1] == (1920, 0, 1152, 1040), placed[1]
assert all(v[0] >= 1920 for v in placed.values())
print("全部開啟並排列（螢幕2 座標）ok", app.status.cget("text"))

# 全部關閉：只關本工具開的、打勾的；關完最後關自己
pump(5)
lid = app.cur_list()["id"]
assert len(app.opened.get(lid, {})) == 2, app.opened   # 螢幕2清單：3 個項目有 2 個（本工具自己不算）
closed = []
w.window_alive = lambda h: h not in closed
w.close_window = lambda h: closed.append(h)
quit_ = []
app.on_close = lambda: quit_.append(1)
mb.askyesno = lambda *a, **k: (asked.append(a) or True)
tg = app.close_targets(); assert len(tg) == 2
first = tg[0][0]; app.find_item(first)["checked"] = False
assert len(app.close_targets()) == 1          # 沒打勾的不關
app.find_item(first)["checked"] = True
app.close_all(); pump(); time.sleep(2.7); pump(10)
assert len(closed) == 2 and quit_ == [1], (closed, quit_)
asked_text = asked[-1][1]
assert "確定嗎" in asked_text
# 有視窗關不掉 → 不關本工具
closed.clear(); quit_.clear()
w.window_alive = lambda h: True
app.close_all(); pump(); time.sleep(2.7); pump(10)
assert quit_ == [] and "沒關掉" in app.status.cget("text")
w.window_alive = lambda h: h not in closed
print("全部關閉 ok")

# 再按一次全部開啟：網址視窗還開著就不再開
opens = []
w.open_item = lambda item, exclude=(), opts=None, on_cid=None, info=None: (opens.append(item["id"]) or abs(hash(item["id"])) % 100000 + 10)
w.window_alive = lambda h: True
app.cur_list()["layout"] = "auto"
urlid = [i["id"] for i in app.cur_list()["items"] if i["type"] == "網址"][0]
app.opened[lid][urlid] = 777
app.run_all()
for _ in range(100):
    pump(); time.sleep(0.05)
    if not app.busy: break
assert urlid not in opens and opens, opens
w.window_alive = lambda h: False          # 被關掉了 → 會重開
opens.clear(); app.run_all()
for _ in range(100):
    pump(); time.sleep(0.05)
    if not app.busy: break
assert urlid in opens
print("重複開啟不疊加 ok")

# 精簡模式與設定
for geo in ("300x200", "240x130", "500x420", "1000x660"):
    root.geometry(geo); pump(8)
    assert app.main.winfo_ismapped() and app.btn_run.winfo_ismapped(), geo
root.geometry("300x200"); pump(8)
assert not app.left.winfo_ismapped() and not app.bot.winfo_ismapped() and app.cname.winfo_manager() == "pack"
root.geometry("1000x660"); pump(8)
assert app.left.winfo_ismapped() and app.bot.winfo_ismapped() and app.ctl.winfo_ismapped() and app.cname.winfo_manager() == ""
print("視窗縮放自動收 ok")

# 顯示桌面
w.minimize_others = lambda ex=(): [11, 12]
got = []
w.restore_windows = lambda hs: got.extend(hs)
app.toggle_desk(); assert app.hidden_wins == [11, 12]
app.toggle_desk(); assert app.hidden_wins == [] and got == [11, 12]
print("顯示桌面 ok")

# 搬完檢查：實際位置不對就重搬
calls = []
w.place_window = lambda h, *r: calls.append(r)
w.window_rect = lambda h: (0, 0, 500, 500)
app.verify_place(5, (0, 0, 800, 600), 3); pump(); time.sleep(0.5); pump()
assert len(calls) >= 1
n = len(calls); w.window_rect = lambda h: (0, 0, 800, 600)
app.verify_place(5, (0, 0, 800, 600), 3); assert len(calls) == n
print("搬完檢查 ok")
app.show_settings(); pump(); assert app.settings.winfo_ismapped() and app.var_auto.get() is False
app.hide_settings(); pump(); assert app.body.winfo_ismapped()
print("精簡／設定 ok")

# 清單新增、改名、刪除
n = len(app.cfg["lists"])
app.begin_list_edit(w.NEWLIST); pump()
e = [c for c in app.lists.winfo_children() if isinstance(c, tk.ttk.Entry)][0]
e.insert(0, "月底結帳"); e.event_generate("<Return>"); pump()
assert len(app.cfg["lists"]) == n + 1 and app.cur_list()["name"] == "月底結帳"
app.delete_list(); assert len(app.cfg["lists"]) == n
print("清單新增／刪除 ok")

root.destroy()
print("ALL UI TESTS PASSED")
