"""画面の識別：各画面の中央に大きく番号を数秒間表示する（Windows の「識別」と同じ）

本体（stage.py）から別プロセスで起動する。引数は JSON:
[{"rect": [x, y, w, h], "label": "1", "sub": "説明"}, ...]
"""
import ctypes
import json
import sys
import tkinter as tk

try:  # 画面の座標を本体（GLFW）と同じ実ピクセルで扱う
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def main():
    items = json.loads(sys.argv[1])
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0
    root = tk.Tk()
    root.withdraw()
    for it in items:
        x, y, w, h = it['rect']
        bw, bh = max(260, w // 4), max(220, h // 3)
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes('-topmost', True)
        win.configure(bg='#111')
        win.geometry(f'{bw}x{bh}+{x + (w - bw) // 2}+{y + (h - bh) // 2}')
        tk.Label(win, text=it['label'], fg='#3ee08f', bg='#111',
                 font=('Segoe UI', -int(bh * 0.55), 'bold')).pack(expand=True)
        tk.Label(win, text=it.get('sub', ''), fg='#ddd', bg='#111',
                 font=('Yu Gothic UI', -max(14, bh // 14)), justify='center').pack(pady=(0, bh // 14))
        win.bind('<Button-1>', lambda e: root.destroy())
    root.after(int(secs * 1000), root.destroy)
    root.mainloop()


if __name__ == '__main__':
    main()
