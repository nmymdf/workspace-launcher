import os, sys, tempfile
sys.path.insert(0, os.path.dirname(__file__))
import workspace_launcher as w

# 自動排版：格子不重疊、不留縫、面積加總 = 1
for n in range(1, 13):
    r = w.auto_rects(n)
    assert len(r) == n
    assert abs(sum(x[2]*x[3] for x in r) - 1) < 1e-6, n
    for i, a in enumerate(r):
        for b in r[i+1:]:
            ox = min(a[0]+a[2], b[0]+b[2]) - max(a[0], b[0])
            oy = min(a[1]+a[3], b[1]+b[3]) - max(a[1], b[1])
            assert not (ox > 1e-6 and oy > 1e-6), (n, a, b)
print("auto_rects ok")

# 群組、跟隨清單、例外螢幕
l = w.new_list("t")
l["items"] += [
    {"id": "a", "type": "檔案", "name": "A", "path": "x", "screen": 0, "checked": True},
    {"id": "b", "type": "網址", "name": "B", "path": "https://x.com", "screen": 0, "checked": True},
    {"id": "c", "type": "App", "name": "C", "path": "x", "screen": 1, "checked": True},
    {"id": "d", "type": "App", "name": "D", "path": "x", "screen": 0, "checked": False},
]
l["screen"] = 2
assert w.group_ids(l, 2) == ["self", "a", "b"]
assert w.group_ids(l, 1) == ["c"]
assert w.screens_in_use(l) == [1, 2]
r2 = w.get_rects(l, 2)
assert r2["self"] == (0, 0, 0.6, 1.0) and len(r2) == 3
print("groups ok")

# 拖邊界
nr = w.move_edge(r2, "v", 0.6, 0.5)
assert nr and abs(nr["self"][2] - 0.5) < 1e-9 and abs(nr["a"][0] - 0.5) < 1e-9 and abs(nr["a"][2] - 0.5) < 1e-9
assert w.move_edge(r2, "v", 0.6, 0.05) is None   # 太小不准
assert w.move_edge(r2, "h", 0.5, 0.7)["a"][3] == 0.7 - 0
segs = w.edge_segments(r2)
assert [x for x in segs if x[0] == "v" and abs(x[1]-0.6) < 1e-9] and any(x[0] == "h" for x in segs)
assert w.snap(0.52) == 0.5 and w.snap(0.33) == 1/3
print("edges ok")

# 自訂排版只在組成沒變時沿用
l["layout"] = "custom"
l["rects"] = {"2": {k: list(v) for k, v in nr.items()}}
assert w.get_rects(l, 2)["self"] == (0.0, 0.0, 0.5, 1.0) and w.get_rects(l, 2)["a"][0] == 0.5 and len(w.get_rects(l, 2)) == 3
l["items"][2]["checked"] = False     # 組成改變 → 這個螢幕回到自動平分
rr = w.get_rects(l, 2)
assert set(rr) == {"self", "a"} and rr["self"] == (0.0, 0.0, 0.5, 1.0) and rr["a"] == (0.5, 0.0, 0.5, 1.0)
l["items"][1]["checked"] = False
assert w.get_rects(l, 2) == {"self": (0.0, 0.0, 1.0, 1.0)}
print("custom ok")

# 驗證／路徑
assert w.validate("網址", "https://a.com") == ""
assert w.validate("網址", "a.com")
assert w.validate("資料夾", tempfile.gettempdir()) == ""
assert w.validate("檔案", "/no/such") == "找不到這個路徑"
assert w.guess_type("www.a.com") == "網址" and w.guess_type(tempfile.gettempdir()) == "資料夾"
assert w.guess_type("C:\\x\\a.exe") == "App" and w.guess_type("C:\\x\\a.xlsx") == "檔案"
assert w.norm_path("www.a.com", "") == "https://www.a.com"
assert w.default_name("資料夾", "D:\\專案\\2026\\") == "2026"
print("validate ok")

# 設定檔存取
d = tempfile.mkdtemp(); f = os.path.join(d, "c.json")
c = w.load_config(f); w.save_config(c, f); c2 = w.load_config(f)
assert c2["lists"][0]["items"][0]["id"] == "self" and c2["autostart"] is False
print("config ok")
