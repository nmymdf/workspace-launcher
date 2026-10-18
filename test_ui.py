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
assert app.draft["path"] == "" and app.edit and app.edit["iid"] == w.NEW and app.edit["col"] == "type", app.edit
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
assert app.edit and app.edit["iid"] == fid and app.edit["col"] == "path", app.edit
app.end_edit(False)
assert app.find_item(fid)["name"] == "專案資料"
print("Enter/Tab 跳下一格 ok")

# 下拉：滾輪與↑↓不能改值；↓才打開
app.begin_edit(fid, "screen"); pump()
cb = app.edit["w"]; cb.set("跟隨清單")
cb.focus_force(); pump()
for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>", "<Up>"):
    try:
        cb.event_generate(seq, delta=-120)
    except tk.TclError:
        pass
    pump()
assert cb.get() == "跟隨清單", cb.get()
app.end_edit(False)
print("滾輪／↑ 不改值 ok")

# 勾選與刪除前確認（把確認視窗換成自動回答）
import tkinter.messagebox as mb
asked = []
mb.askyesno = lambda *a, **k: (asked.append(a) or True)
app.tree.update_idletasks()
bb = app.tree.bbox(fid, "chk")
app.tree.event_generate("<ButtonPress-1>", x=bb[0] + 5, y=bb[1] + 5); pump()
app.tree.event_generate("<ButtonRelease-1>", x=bb[0] + 5, y=bb[1] + 5); pump()
assert app.find_item(fid)["checked"] is False
app.find_item(fid)["checked"] = True
app.delete_checked()
assert asked and len(app.cur_list()["items"]) == 1, app.cur_list()["items"]
print("批次刪除要確認 ok")

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

# 例外螢幕 → 兩個預覽
it = app.cur_list()["items"][1]; it["screen"] = 2; app.save(); app.refresh_preview(); pump()
pvs = [c for b in app.pv_holder.winfo_children() for c in b.winfo_children() if isinstance(c, w.Preview)]
assert [p.screen for p in pvs] == [1, 2]
it["screen"] = 0; app.refresh_preview()
print("雙螢幕預覽 ok")

# 執行：換成假的開啟／排版函式，檢查排到螢幕2 的座標
placed = {}
w.open_item = lambda item: abs(hash(item["id"])) % 100000 + 10
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

# 精簡模式與設定
app.set_compact(True); pump(); assert not app.main.winfo_ismapped() and app.cbar.winfo_ismapped()
app.set_compact(False); pump(); assert app.main.winfo_ismapped()
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
