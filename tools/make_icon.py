# 產生 app.ico（A 款：四格拼貼）。執行：python tools/make_icon.py
from PIL import Image, ImageDraw
import os

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((0, 0, S - 1, S - 1), radius=int(S * 0.22), fill="#1F2A44")
pad, gap = int(S * 0.17), int(S * 0.055)
cell = (S - 2 * pad - gap) // 2
cols = ["#4C8DF6", "#E0B03A", "#3FB68B", "#8A93A6"]
for i, c in enumerate(cols):
    x = pad + (i % 2) * (cell + gap)
    y = pad + (i // 2) * (cell + gap)
    d.rounded_rectangle((x, y, x + cell, y + cell), radius=int(S * 0.06), fill=c)
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app.ico")
sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
img.save(out, format="ICO", sizes=sizes)
img.resize((256, 256), Image.LANCZOS).save(out.replace("app.ico", "app_preview.png"))
print("ok", out)
