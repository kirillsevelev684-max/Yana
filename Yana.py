# -*- coding: utf-8 -*-
"""Яна Нейронова 8.0 — чистый ассистент в одном окне.

Главная (Яна, статус, быстрые действия), Чат (текст + всегда слушающий голос),
Модули (голос, глаза, папка, твои программы), Настройки (голос, микро, ключ, темы).
Ничего лишнего. Собирается в один Yana.exe через GitHub Actions.
"""
import json
import os
import shlex
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
    BUNDLE = Path(getattr(sys, "_MEIPASS", ROOT))
else:
    ROOT = Path(__file__).resolve().parent
    BUNDLE = ROOT
sys.path.insert(0, str(ROOT))

from engine import config  # noqa: E402

APP_VERSION = "8.0"
PROGRAMS_FILE = config.DATA_DIR / "programs.json"
APPSET_FILE = config.DATA_DIR / "appstyle.json"


def assets_dir() -> Path:
    if (BUNDLE / "assets" / "yana_logo.png").exists():
        return BUNDLE / "assets"
    return ROOT / "assets"


BG = "#08080b"
PANEL = "#101014"
CARD = "#15151b"
BORDER = "#23232b"
FG = "#f4f4f8"
MUTED = "#9a9fb2"
GREEN = "#4ade80"
RED = "#f87171"
FONT = ("Segoe UI", 10)
FONT_B = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 30, "bold")
FONT_CARD = ("Segoe UI", 11, "bold")
FONT_META = ("Segoe UI", 8)
FONT_SMALL = ("Segoe UI", 9)

ACCENTS = [("#d2a94f", "Золото"), ("#8b7cf6", "Фиолет"),
           ("#4ade80", "Неон"), ("#f87171", "Рокстар")]
BG_THEMES = [(("#0a0a0e", "#101014"), "Графит"),
             ((("#0c0913", "#161226"), "Полночь")),
             ((("#0e0909", "#191114"), "Рокстар"))]
COVER_PRESETS = [
    ("Фиолет", "#2a1f4d", "#8b7cf6"), ("Золото", "#3a2c12", "#d2a94f"),
    ("Неон", "#0f2f1f", "#4ade80"), ("Океан", "#10283f", "#38bdf8"),
    ("Закат", "#3a1620", "#fb7185"), ("Сталь", "#23262e", "#9aa0b8"),
]

TILES = [
    {"id": "voice", "name": "Проверка голоса", "desc": "Тестовая фраза вслух",
     "icon": "🔊", "c1": "#10283f", "c2": "#38bdf8"},
    {"id": "look", "name": "Взглянуть на экран", "desc": "Что там происходит",
     "icon": "👁", "c1": "#3a1620", "c2": "#fb7185"},
    {"id": "watch", "name": "Слежение за экраном", "desc": "Комментирует изменения",
     "icon": "🎥", "c1": "#241a45", "c2": "#8b7cf6"},
    {"id": "folder", "name": "Моя папка", "desc": "Папка Яны",
     "icon": "📁", "c1": "#23262e", "c2": "#9aa0b8"},
]


# ---------- утилиты ----------

def _mix(c1: str, c2: str, f: float) -> str:
    try:
        a = [int(c1.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
        b = [int(c2.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
        m = [int(x + (y - x) * f) for x, y in zip(a, b)]
        return "#{:02x}{:02x}{:02x}".format(*m)
    except Exception:
        return c1


def _lighten(c: str, f: float = 1.18) -> str:
    return _mix(c, "#ffffff", min(0.9, max(0.0, f - 1.0)))


def _darken(c: str, f: float = 0.55) -> str:
    return _mix(c, "#000000", min(0.9, max(0.0, 1.0 - f)))


def fmt_time(seconds: int) -> str:
    seconds = int(seconds or 0)
    if seconds < 60:
        return f"{seconds} сек"
    m = seconds // 60
    if m < 60:
        return f"{m} мин"
    return f"{m // 60} ч {m % 60} мин"


def fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m %H:%M")
    except Exception:
        return "—"


def profile_name() -> str:
    try:
        d = json.loads(config.MEMORY_FILE.read_text(encoding="utf-8"))
        return (d.get("user_name") or "").strip().capitalize() or "Гость"
    except Exception:
        return "Гость"


def home_stats() -> dict:
    d = {"commands": 0, "users": 0, "devices": 0, "on": 0, "facts": 0}
    try:
        from engine.commands import CommandManager
        d["commands"] = len(CommandManager().commands)
    except Exception:
        pass
    try:
        from engine import voice_id
        d["users"] = len(voice_id.list_users())
    except Exception:
        pass
    try:
        from engine.smarthome import DeviceManager
        devs = DeviceManager().devices
        d["devices"] = len(devs)
        d["on"] = sum(1 for x in devs if x.get("state") == "on")
    except Exception:
        pass
    try:
        from engine.memory import Memory
        d["facts"] = len(Memory().data.get("facts", []))
    except Exception:
        pass
    return d


def is_online() -> bool:
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3).close()
        return True
    except Exception:
        return False


def load_settings() -> dict:
    try:
        if config.SETTINGS_FILE.exists():
            return json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_settings(d: dict):
    config.SETTINGS_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- хранилище программ ----------

class ProgramStore:
    def __init__(self, path: Path = PROGRAMS_FILE):
        self.path = path
        self.games: list = []
        self.load()

    def load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            self.games = d.get("games", []) if isinstance(d, dict) else []
        except Exception:
            self.games = []

    def save(self):
        try:
            self.path.write_text(json.dumps({"games": self.games}, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        except Exception:
            pass

    def add(self, name: str, path: str, args="", icon="📦", c1="#23262e", c2="#9aa0b8") -> dict:
        g = {"id": uuid.uuid4().hex[:8], "name": name.strip(), "path": path.strip(),
             "args": (args or "").strip(), "icon": (icon or "📦").strip()[:2],
             "c1": c1, "c2": c2, "launches": 0, "last": "", "seconds": 0}
        self.games.append(g)
        self.save()
        return g

    def update(self, gid: str, **kw):
        for g in self.games:
            if g["id"] == gid:
                g.update(kw)
                self.save()
                return True
        return False

    def delete(self, gid: str):
        self.games = [g for g in self.games if g["id"] != gid]
        self.save()

    def get(self, gid: str):
        for g in self.games:
            if g["id"] == gid:
                return g
        return None

    def bump(self, gid: str):
        for g in self.games:
            if g["id"] == gid:
                g["launches"] = int(g.get("launches", 0)) + 1
                g["last"] = datetime.now().isoformat(timespec="minutes")
                self.save()
                return


class AppStyle:
    def __init__(self, path: Path = APPSET_FILE):
        self.path = path
        self.accent = ACCENTS[0][0]
        self.bg = 0
        self.load()

    def load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                if d.get("accent") in [a[0] for a in ACCENTS]:
                    self.accent = d["accent"]
                self.bg = int(d.get("bg", 0)) % len(BG_THEMES)
        except Exception:
            pass

    def save(self):
        try:
            self.path.write_text(json.dumps({"accent": self.accent, "bg": self.bg},
                                             ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


def _popen_flags(new_console: bool = False):
    if os.name != "nt":
        return {}
    if new_console:
        return {"creationflags": subprocess.CREATE_NEW_CONSOLE}
    return {}


def launch_program(item: dict, store: ProgramStore | None = None):
    """Запустить программу пользователя. Возвращает (ok, сообщение)."""
    try:
        path = (item.get("path") or "").strip()
        if not path:
            return False, "Нет пути — измени запись"
        low = path.lower()
        if low.startswith(("http://", "https://", "www.")):
            url = path if low.startswith("http") else "https://" + path
            webbrowser.open(url)
            if store:
                store.bump(item["id"])
            return True, "Открываю ссылку"
        p = Path(path)
        if not p.exists():
            return False, "Файл не найден — проверь путь"
        if store:
            store.bump(item["id"])
        if p.is_dir():
            if os.name == "nt":
                os.startfile(str(p))
            else:
                subprocess.Popen(["xdg-open", str(p)])
            return True, "Открываю папку"
        args = shlex.split(item.get("args", ""), posix=(os.name != "nt"))
        if p.suffix.lower() in (".bat", ".cmd"):
            subprocess.Popen(["cmd", "/c", str(p), *args], cwd=str(p.parent),
                             **_popen_flags(new_console=True))
            return True, f"Запускаю {item.get('name', '')}"
        try:
            proc = subprocess.Popen([str(p), *args], cwd=str(p.parent))
        except Exception as e:
            return False, f"Не запустилось: {e}"
        if store:
            threading.Thread(target=_track, args=(store, item["id"], proc), daemon=True).start()
        return True, f"Запускаю {item.get('name', '')}"
    except Exception as e:
        return False, f"Ошибка: {e}"


def _track(store: ProgramStore, gid: str, proc):
    t0 = time.time()
    try:
        proc.wait()
    except Exception:
        return
    try:
        g = store.get(gid)
        if g:
            store.update(gid, seconds=int(g.get("seconds", 0)) + int(time.time() - t0))
    except Exception:
        pass


# ================= GUI =================

import tkinter as tk  # noqa: E402
from tkinter import filedialog, messagebox  # noqa: E402


def _round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


def _gradient(canvas, w: int, h: int, c1: str, c2: str, tag="grad"):
    canvas.delete(tag)
    steps = max(1, min(h, 150))
    for i in range(steps):
        y = int(h * i / steps)
        canvas.create_line(0, y, w, y, fill=_mix(c1, c2, i / max(1, steps - 1)),
                           width=max(2, h // steps + 1), tags=tag)


def _soft_round(canvas, w: int, h: int, r: int, bg: str, tag="corners"):
    canvas.delete(tag)
    _round_rect(canvas, 1, 1, w - 1, h - 1, r, fill="", outline=bg,
                width=max(6, r // 2), tags=tag)


def _shadow_text(canvas, x, y, text, font, fill="#ffffff", anchor="w", tag=None):
    canvas.create_text(x + 2, y + 2, text=text, font=font, fill="#000000",
                       anchor=anchor, tags=tag)
    return canvas.create_text(x, y, text=text, font=font, fill=fill,
                              anchor=anchor, tags=tag)


def _pill(canvas, x: int, y: int, text: str, fill: str, fg: str = "#ffffff",
          font=FONT_SMALL):
    t = canvas.create_text(x + 12, y, text=text, font=font, fill=fg, anchor="w")
    x1, y1, x2, y2 = canvas.bbox(t)
    pad = 7
    r = _round_rect(canvas, x1 - pad, y1 - pad + 1, x2 + pad, y2 + pad - 1,
                    11, fill=fill, outline="")
    canvas.tag_lower(r, t)
    return (x2 + pad) - x


def draw_y_logo(canvas, x: int, y: int, size: int, accent: str):
    """Логотип-Y: тёмный тайл + стилизованная Y с объёмом и бликом."""
    _round_rect(canvas, x, y, x + size, y + size, size // 5, fill="#0e0e13", outline="")
    _round_rect(canvas, x, y, x + size, y + size, size // 5,
                fill="", outline=_darken(accent, 0.55), width=2)
    cx = x + size / 2
    top = y + size * 0.20
    mid = y + size * 0.52
    bot = y + size * 0.80
    spread = size * 0.22
    w = max(3, int(size * 0.11))
    canvas.create_line(cx - spread, top, cx, mid, fill=_lighten(accent, 1.3),
                       width=w, capstyle="round")
    canvas.create_line(cx + spread, top, cx, mid, fill=accent, width=w, capstyle="round")
    canvas.create_line(cx, mid, cx, bot, fill=_darken(accent, 0.7), width=w, capstyle="round")
    r = max(2, size * 0.035)
    canvas.create_oval(cx - spread - r, top - r, cx - spread + r, top + r,
                       fill="#ffffff", outline="")


_LOGO_CACHE: dict = {}


def logo_photo(size: int):
    try:
        if size in _LOGO_CACHE:
            return _LOGO_CACHE[size]
        p = assets_dir() / "yana_logo.png"
        if not p.exists():
            return None
        img = tk.PhotoImage(file=str(p))
        n = max(1, round(img.width() / max(1, size)))
        if n > 1:
            img = img.subsample(n)
        _LOGO_CACHE[size] = img
        return img
    except Exception:
        return None


def lab(parent, text, font=FONT, color=FG, **kw):
    return tk.Label(parent, text=text, bg=parent["bg"], fg=color, font=font, **kw)


class RoundedButton(tk.Canvas):
    def __init__(self, parent, text, command=None, width=160, height=40, radius=12,
                 bg="#d2a94f", fg="#1a1408", hover=None, font=FONT_B):
        super().__init__(parent, width=width, height=height, bg=parent["bg"],
                         highlightthickness=0, bd=0, cursor="hand2")
        self._cmd = command
        self._bg = bg
        self._hover = hover or _lighten(bg)
        self._rect = _round_rect(self, 2, 2, width - 2, height - 2, radius, fill=bg, outline="")
        self._txt = self.create_text(width // 2, height // 2, text=text, fill=fg, font=font)
        self.bind("<Enter>", lambda e: self.itemconfig(self._rect, fill=self._hover))
        self.bind("<Leave>", lambda e: self.itemconfig(self._rect, fill=self._bg))
        self.bind("<Button-1>", lambda e: self._click())

    def _click(self):
        if self._cmd:
            self._cmd()

    def set_text(self, t: str):
        self.itemconfig(self._txt, text=t)

    def set_appear(self, bg: str, fg: str, hover: str = None):
        self._bg = bg
        self._hover = hover or _lighten(bg)
        self.itemconfig(self._rect, fill=bg)
        self.itemconfig(self._txt, fill=fg)


class NavButton(tk.Canvas):
    def __init__(self, parent, icon: str, text: str, command=None, sb=BG, accent="#d2a94f"):
        super().__init__(parent, width=188, height=46, bg=sb,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._cmd = command
        self._sb = sb
        self._accent = accent
        self._active = False
        self._bar = self.create_rectangle(8, 10, 12, 36, fill=sb, outline="")
        self._txt = self.create_text(26, 23, text=f"{icon}  {text}", fill=MUTED,
                                     font=("Segoe UI", 11, "bold"), anchor="w")
        self.bind("<Enter>", lambda e: self._paint(hover=True))
        self.bind("<Leave>", lambda e: self._paint())
        self.bind("<Button-1>", lambda e: self._click())

    def _paint(self, hover=False):
        if self._active:
            self.itemconfig(self._bar, fill=self._accent)
            self.itemconfig(self._txt, fill=FG)
        elif hover:
            self.itemconfig(self._bar, fill=BORDER)
            self.itemconfig(self._txt, fill=FG)
        else:
            self.itemconfig(self._bar, fill=self._sb)
            self.itemconfig(self._txt, fill=MUTED)

    def _click(self):
        if self._cmd:
            self._cmd()

    def set_active(self, on: bool):
        self._active = on
        self._paint()

    def recolor(self, sb: str, accent: str):
        self._sb = sb
        self._accent = accent
        self.config(bg=sb)
        self._paint()


class ScrollFrame(tk.Frame):
    def __init__(self, parent, bg=BG):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.bar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                bg=BORDER, troughcolor=bg, relief="flat", borderwidth=0,
                                highlightthickness=0, width=10)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.pack(side="right", fill="y")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(self._win, width=e.width))
        self.inner.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.inner.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _wheel(self, e):
        try:
            self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        except Exception:
            pass

    def bottom(self):
        self.update_idletasks()
        try:
            self.canvas.yview_moveto(1.0)
        except Exception:
            pass


class ProgramModal(tk.Toplevel):
    def __init__(self, parent, game=None):
        super().__init__(parent, bg=BG)
        self.result = None
        game = game or {}
        self.title("Новая программа" if not game else "Изменить программу")
        self.transient(parent)
        self.resizable(False, False)
        parent.update_idletasks()
        w, h = 480, 600
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - w) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=20, pady=14)
        lab(body, self.title(), font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(0, 6))
        self.preview = tk.Canvas(body, width=440, height=110, bg=BG,
                                 highlightthickness=0, bd=0)
        self.preview.pack(fill="x", pady=(0, 8))
        lab(body, "Название:", font=FONT_B).pack(anchor="w")
        self.name = tk.Entry(body, bg=CARD, fg=FG, relief="flat", insertbackground=FG,
                             font=FONT, highlightthickness=0)
        self.name.pack(fill="x", ipady=7, pady=4)
        self.name.insert(0, game.get("name", ""))
        self.name.bind("<KeyRelease>", lambda e: self._preview_paint())
        lab(body, "Путь (файл, папка или ссылка):", font=FONT_B).pack(anchor="w", pady=(4, 0))
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=4)
        self.path = tk.Entry(row, bg=CARD, fg=FG, relief="flat", insertbackground=FG,
                             font=FONT, highlightthickness=0)
        self.path.pack(side="left", fill="x", expand=True, ipady=7)
        self.path.insert(0, game.get("path", ""))
        RoundedButton(row, "📂", command=self._pick, width=44, height=32,
                      bg=CARD, hover=BORDER).pack(side="left", padx=6)
        erow = tk.Frame(body, bg=BG)
        erow.pack(fill="x", pady=(4, 0))
        lab(erow, "Иконка:", font=FONT_B).pack(side="left")
        self.icon = tk.Entry(erow, width=4, bg=CARD, fg=FG, relief="flat",
                             insertbackground=FG, font=("Segoe UI", 12),
                             highlightthickness=0, justify="center")
        self.icon.pack(side="left", padx=8, ipady=4)
        self.icon.insert(0, game.get("icon", "📦"))
        self.icon.bind("<KeyRelease>", lambda e: self._preview_paint())
        lab(erow, "Аргументы:", font=FONT_B).pack(side="left", padx=(12, 0))
        self.args = tk.Entry(erow, bg=CARD, fg=FG, relief="flat", insertbackground=FG,
                             font=FONT, highlightthickness=0)
        self.args.pack(side="left", fill="x", expand=True, padx=8, ipady=7)
        self.args.insert(0, game.get("args", ""))
        lab(body, "Обложка:", font=FONT_B).pack(anchor="w", pady=(8, 0))
        self.cover_idx = 0
        for i, (_, c1, c2) in enumerate(COVER_PRESETS):
            if c1 == game.get("c1") and c2 == game.get("c2"):
                self.cover_idx = i
        crow = tk.Frame(body, bg=BG)
        crow.pack(fill="x", pady=4)
        self._swatches = []
        for i, (nm, c1, c2) in enumerate(COVER_PRESETS):
            c = tk.Canvas(crow, width=60, height=38, bg=BG, highlightthickness=0, bd=0,
                          cursor="hand2")
            c.pack(side="left", padx=3)
            _round_rect(c, 2, 2, 58, 36, 9, fill=c1, outline="")
            c.create_oval(32, 8, 56, 32, fill=c2, outline="")
            c.bind("<Button-1>", lambda e, i=i: self._cover(i))
            self._swatches.append(c)
        self._cover_paint()
        self._preview_paint()
        RoundedButton(body, "Сохранить", command=self._apply, width=440,
                      height=38, bg=parent.accent, fg="#141414").pack(fill="x", pady=(10, 0))
        self.bind("<Return>", lambda e: self._apply())
        self.grab_set()
        self.focus_force()

    def _cover_colors(self):
        return COVER_PRESETS[self.cover_idx][1], COVER_PRESETS[self.cover_idx][2]

    def _preview_paint(self):
        try:
            c = self.preview
            c.delete("all")
            c1, c2 = self._cover_colors()
            _gradient(c, 440, 110, _darken(c1, 0.7), c1)
            c.create_oval(330, -40, 480, 110, fill=_darken(c2, 0.75), outline="")
            icon = (self.icon.get().strip() or "📦")[:2]
            c.create_text(60, 55, text=icon, font=("Segoe UI", 44))
            name = self.name.get().strip() or "Название программы"
            _shadow_text(c, 120, 45, name[:26], ("Segoe UI", 16, "bold"))
            c.create_text(122, 76, text="предпросмотр обложки", fill=MUTED,
                           font=FONT_META, anchor="w")
            _soft_round(c, 440, 110, 16, BG)
        except Exception:
            pass

    def _pick(self):
        p = filedialog.askopenfilename(title="Выбери программу или файл",
                                       filetypes=[("Программы", "*.exe *.bat *.cmd *.lnk"),
                                                  ("Все файлы", "*.*")])
        if p:
            self.path.delete(0, "end")
            self.path.insert(0, p)
            if not self.name.get().strip():
                self.name.insert(0, Path(p).stem.replace("_", " ").title())
                self._preview_paint()

    def _cover(self, i: int):
        self.cover_idx = i
        self._cover_paint()
        self._preview_paint()

    def _cover_paint(self):
        for i, c in enumerate(self._swatches):
            c.delete("sel")
            if i == self.cover_idx:
                c.create_rectangle(1, 1, 59, 37, outline="#ffffff", width=2, tags="sel")

    def _apply(self):
        name = self.name.get().strip()
        path = self.path.get().strip()
        if not name or not path:
            messagebox.showwarning("Программа", "Впиши название и путь.")
            return
        c1, c2 = self._cover_colors()
        self.result = {"name": name, "path": path, "args": self.args.get().strip(),
                       "icon": self.icon.get().strip()[:2] or "📦", "c1": c1, "c2": c2}
        self.destroy()


NAV = [("home", "✨", "Главная"), ("chat", "💬", "Чат"),
       ("modules", "🧩", "Модули"), ("settings", "⚙", "Настройки")]


class App(tk.Tk):
    def __init__(self):
        # Фаза 0: все ссылки заранее
        self.sidebar = None
        self._nav_btns = {}
        self.net_label = None
        self._pages = {}
        self._dot = None
        self._dot_oval = None
        self.status = None
        self.hero = None
        self._home_cards = None
        self.voice_btn = None
        self.chat_status = None
        self.chat_scroll = None
        self.chat_inner = None
        self.chat_entry = None
        self.mod_scroll = None
        self.mod_inner = None
        self._cards = {}
        self._toast = None
        self._toast_job = None
        self._logo_imgs = []
        self._acc_btns = []
        self._acc_hint = None
        self._bg_btns = []

        super().__init__()
        self.store = ProgramStore()
        self.astyle = AppStyle()
        self.accent = self.astyle.accent
        self._sel = "voice"
        self._hover = ""
        self._online = None
        self._brain = None
        self._chat_lock = threading.Lock()
        self._voice_on = False
        self._voice_stop = threading.Event()
        self._voice_wake = None

        self.title("Яна Нейронова")
        self.geometry("1160x720")
        self.minsize(980, 620)
        self.configure(bg=BG)

        self._build_sidebar()
        self._build_footer()
        self._build_pages()
        self._build_toast()

        try:
            from engine import screen_watch
            screen_watch.set_speaker(self._watch_say)
        except Exception:
            pass

        self.show_page("home")
        self._paint_status()
        self.bind("<Delete>", lambda e: self._del_sel())
        threading.Thread(target=self._online_probe, daemon=True).start()
        self.after(2000, self._voice_autostart)

    def _sb(self) -> str:
        return BG_THEMES[self.astyle.bg][0][0]

    def _place_logo(self, canvas, x: int, y: int, size: int):
        try:
            img = logo_photo(size)
            if img is not None:
                self._logo_imgs.append(img)
                canvas.create_image(x, y, image=img, anchor="nw")
                return
        except Exception:
            pass
        try:
            draw_y_logo(canvas, x, y, size, self.accent)
        except Exception:
            pass

    # ----- Фаза 1: сайдбар -----

    def _build_sidebar(self):
        self.sidebar = tk.Frame(self, bg=self._sb(), width=208)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        logo = tk.Canvas(self.sidebar, width=208, height=100, bg=self._sb(),
                         highlightthickness=0, bd=0)
        logo.pack()
        self._place_logo(logo, 22, 22, 56)
        logo.create_text(116, 42, text="ЯНА", fill=FG, font=("Segoe UI", 21, "bold"))
        logo.create_text(116, 66, text="НЕЙРОНОВА", fill=MUTED, font=("Segoe UI", 9, "bold"))
        for key, icon, text in NAV:
            b = NavButton(self.sidebar, icon, text,
                          command=lambda k=key: self.show_page(k),
                          sb=self._sb(), accent=self.accent)
            b.pack(pady=1, padx=8)
            self._nav_btns[key] = b
        prof = tk.Frame(self.sidebar, bg=self._sb())
        prof.pack(side="bottom", fill="x", padx=14, pady=14)
        av = tk.Canvas(prof, width=42, height=42, bg=self._sb(), highlightthickness=0, bd=0)
        av.pack(side="left")
        av.create_oval(2, 2, 40, 40, fill=self.accent, outline="")
        av.create_text(21, 21, text=profile_name()[:1].upper() or "Г",
                       font=("Segoe UI", 15, "bold"), fill="#141414")
        col = tk.Frame(prof, bg=self._sb())
        col.pack(side="left", padx=10)
        lab(col, profile_name(), font=FONT_B).pack(anchor="w")
        self.net_label = lab(col, "…", font=FONT_META, color=MUTED)
        self.net_label.pack(anchor="w")
        main = tk.Frame(self, bg=BG)
        main.pack(side="left", fill="both", expand=True)
        self._main = main

    # ----- Фаза 2: футер -----

    def _build_footer(self):
        foot = tk.Frame(self, bg=PANEL, height=28)
        foot.pack(side="bottom", fill="x")
        self._dot = tk.Canvas(foot, width=14, height=14, bg=PANEL, highlightthickness=0, bd=0)
        self._dot.pack(side="left", padx=(12, 4), pady=6)
        self._dot_oval = self._dot.create_oval(3, 3, 11, 11, fill="#55555f", outline="")
        self.status = lab(foot, "…", font=FONT_SMALL, color=MUTED)
        self.status.pack(side="left")
        lab(foot, f"Яна v{APP_VERSION}", font=FONT_SMALL, color=MUTED).pack(side="right", padx=12)

    # ----- Фаза 3: страницы -----

    def _build_pages(self):
        for key, _, _ in NAV:
            pg = tk.Frame(self._main, bg=BG)
            pg.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._pages[key] = pg
        self._build_home()
        self._build_chat()
        self._build_modules()
        self._build_settings()

    def _build_toast(self):
        self._toast = tk.Label(self, text="", bg=CARD, fg=FG, font=FONT_B, padx=16, pady=9)

    # ----- тост и статус -----

    def toast(self, text: str, ok: bool = True):
        try:
            if self._toast is None:
                return
            self._toast.config(text=text, fg=FG if ok else RED)
            self._toast.place(relx=1.0, rely=1.0, x=-24, y=-48, anchor="se")
            self._toast.lift()
            if self._toast_job:
                self.after_cancel(self._toast_job)
            self._toast_job = self.after(2600, self._toast.place_forget)
        except Exception:
            pass

    def _paint_status(self):
        try:
            if self.status is None or self._dot is None:
                return
            if self._online is True:
                self._dot.itemconfig(self._dot_oval, fill=GREEN)
                net = "онлайн"
            elif self._online is False:
                self._dot.itemconfig(self._dot_oval, fill="#55555f")
                net = "офлайн"
            else:
                net = "…"
            st = home_stats()
            self.status.config(
                text=f"{net} · команд: {st['commands']} · людей: {st['users']} · устройств: {st['devices']}")
            if self.net_label is not None:
                self.net_label.config(text="🟢 в сети" if self._online else (
                    "⚪ офлайн" if self._online is False else "…"))
        except Exception:
            pass

    def _online_probe(self):
        self._online = is_online()
        try:
            self.after(0, self._paint_status)
            self.after(0, self._hero_paint)
        except Exception:
            pass

    def show_page(self, key: str):
        try:
            for k, b in self._nav_btns.items():
                b.set_active(k == key)
            if key in self._pages:
                self._pages[key].tkraise()
            if key == "home":
                self._hero_paint()
                self._home_cards_paint()
        except Exception:
            pass

    def _brain_get(self):
        if self._brain is None:
            from engine.brain import YanaBrain
            from engine.commands import CommandManager
            self._brain = YanaBrain(CommandManager())
        return self._brain

    @staticmethod
    def _speak(text: str):
        try:
            from engine.tts import YanaTTS
            YanaTTS().speak(text)
        except Exception:
            pass

    # ================= ГЛАВНАЯ =================

    def _build_home(self):
        pg = self._pages["home"]
        wrap = tk.Frame(pg, bg=BG)
        wrap.pack(fill="both", expand=True, padx=18, pady=(14, 12))
        self.hero = tk.Canvas(wrap, height=252, bg=BG, highlightthickness=0, bd=0)
        self.hero.pack(fill="x")
        self.hero.bind("<Configure>", lambda e: self._hero_paint())
        qrow = tk.Frame(wrap, bg=BG)
        qrow.pack(fill="x", pady=(12, 6))
        self.home_voice_btn = RoundedButton(qrow, "🎙 Говорить: Выкл", command=self._voice_toggle,
                                            width=190, height=40, bg=CARD, fg=FG, hover=BORDER)
        self.home_voice_btn.pack(side="left", padx=4)
        for text, fn, w in (("💬 Чат", lambda: self.show_page("chat"), 130),
                            ("👁 Взглянуть", self._eyes_look, 150),
                            ("📁 Моя папка", self._folder_open, 150)):
            RoundedButton(qrow, text, command=fn, width=w, height=40,
                          bg=CARD, fg=FG, hover=BORDER).pack(side="left", padx=4)
        lab(wrap, "Состояние", font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(6, 4))
        self._home_cards = tk.Frame(wrap, bg=BG)
        self._home_cards.pack(fill="x")
        self._hero_paint()
        self._home_cards_paint()

    def _hero_paint(self):
        try:
            c = self.hero
            if c is None:
                return
            c.delete("all")
            try:
                w = c.winfo_width() or 800
            except Exception:
                w = 800
            h = 252
            deep = _darken(self.accent, 0.28)
            _gradient(c, w, h, "#0c0c11", deep)
            c.create_oval(w - 260, -140, w + 100, 200, fill=_darken(self.accent, 0.55),
                          outline="")
            c.create_text(w - 120, 190, text="Y", font=("Segoe UI", 230, "bold"),
                          fill=_darken(self.accent, 0.72))
            self._place_logo(c, 36, 56, 140)
            _shadow_text(c, 200, 92, "Яна Нейронова", FONT_TITLE)
            c.create_text(202, 132, text="голосовой ассистент — всегда на связи",
                          fill=MUTED, font=FONT, anchor="w")
            x, y = 200, 182
            if self._online is True:
                x += _pill(c, x, y, "🟢 онлайн", _darken(GREEN, 0.45)) + 10
            elif self._online is False:
                x += _pill(c, x, y, "⚪ офлайн", "#3a3a44") + 10
            else:
                x += _pill(c, x, y, "… сеть", "#3a3a44") + 10
            x += _pill(c, x, y, f"★ версия {APP_VERSION}", _darken(self.accent, 0.5)) + 10
            _pill(c, x, y, f"👤 {profile_name()}", "#2a2a33")
            _soft_round(c, w, h, 20, BG)
        except Exception:
            pass

    def _home_cards_paint(self):
        try:
            if self._home_cards is None:
                return
            for w in self._home_cards.winfo_children():
                w.destroy()
            st = home_stats()
            net = "онлайн" if self._online else ("офлайн" if self._online is False else "…")
            cards = [
                ("🧠 Мозг", net, "гонка из 4 движков"),
                ("🗣 Голос", "Svetlana", f"темп {config.TTS_RATE:+d}% · EQ 5 полос"),
                ("🏠 Дом", f"{st['devices']} устр.", f"включено: {st['on']}"),
                ("👥 Люди", f"{st['users']}", f"команд: {st['commands']} · фактов: {st['facts']}"),
            ]
            for c in range(4):
                self._home_cards.grid_columnconfigure(c, weight=1, uniform="h")
            for i, (title, val, sub) in enumerate(cards):
                card = tk.Frame(self._home_cards, bg=CARD, padx=14, pady=10, cursor="hand2")
                card.grid(row=0, column=i, padx=6, sticky="ew")
                lab(card, title, font=FONT_SMALL, color=MUTED).pack(anchor="w")
                lab(card, val, font=("Segoe UI", 15, "bold")).pack(anchor="w")
                lab(card, sub, font=FONT_META, color=MUTED).pack(anchor="w")
                self._card_click(card, lambda: self.show_page("settings"))
        except Exception:
            pass

    @staticmethod
    def _card_click(card, fn):
        def walk(w):
            for ch in w.winfo_children():
                try:
                    ch.bind("<Button-1>", lambda e: fn(), add="+")
                except Exception:
                    pass
                walk(ch)
        try:
            card.bind("<Button-1>", lambda e: fn(), add="+")
        except Exception:
            pass
        walk(card)

    # ================= ЧАТ + ГОЛОС =================

    def _build_chat(self):
        pg = self._pages["chat"]
        wrap = tk.Frame(pg, bg=BG)
        wrap.pack(fill="both", expand=True, padx=18, pady=(14, 12))
        head = tk.Frame(wrap, bg=BG)
        head.pack(fill="x", pady=(0, 6))
        self.chat_status = lab(head, "🎙 Говорит: пока никто", font=FONT_B, color=self.accent)
        self.chat_status.pack(side="left")
        self.voice_btn = RoundedButton(head, "🎙 Говорить: Выкл", command=self._voice_toggle,
                                       width=170, height=32, bg=CARD, hover=BORDER)
        self.voice_btn.pack(side="right")
        self.chat_scroll = ScrollFrame(wrap)
        self.chat_scroll.pack(fill="both", expand=True)
        self.chat_inner = self.chat_scroll.inner
        bottom = tk.Frame(wrap, bg=BG)
        bottom.pack(fill="x", pady=(8, 0))
        ewrap = tk.Frame(bottom, bg=CARD)
        ewrap.pack(side="left", fill="x", expand=True)
        self.chat_entry = tk.Entry(ewrap, bg=CARD, fg=FG, relief="flat",
                                   insertbackground=FG, font=FONT, highlightthickness=0)
        self.chat_entry.pack(fill="x", padx=12, pady=9)
        self.chat_entry.bind("<Return>", lambda e: self._chat_send())
        RoundedButton(bottom, "➤", command=self._chat_send, width=46, height=34,
                      bg=self.accent, fg="#141414").pack(side="left", padx=6)
        self._bubble("Привет! Я Яна 👋 Говори «Яна» вслух или пиши сюда — я всегда на связи.")

    def _bubble(self, text: str, me: bool = False):
        try:
            row = tk.Frame(self.chat_inner, bg=BG)
            row.pack(fill="x", pady=3, padx=6)
            bg = self.accent if me else CARD
            fg = "#141414" if me else FG
            b = tk.Frame(row, bg=bg, padx=10, pady=7)
            tk.Label(b, text=text, bg=bg, fg=fg, font=FONT, wraplength=430, justify="left").pack()
            b.pack(side="right" if me else "left")
            self.chat_scroll.bottom()
            return row
        except Exception:
            return None

    def _chat_send(self, text=None):
        text = (text if text is not None else self.chat_entry.get()).strip()
        if not text:
            return
        try:
            self.chat_entry.delete(0, "end")
        except Exception:
            pass
        self._bubble(text, me=True)
        typing = self._bubble("…")
        threading.Thread(target=self._chat_worker, args=(text, typing), daemon=True).start()

    def _chat_worker(self, text: str, typing):
        try:
            with self._chat_lock:
                res = self._brain_get().process(text)
            resp = res.get("response", "")
            self.after(0, lambda: self._chat_done(typing, res, resp))
        except Exception as e:
            self.after(0, lambda: self._chat_done(typing, {}, f"Ошибка: {e}"))

    def _chat_done(self, typing, res: dict, resp: str):
        try:
            if typing:
                typing.destroy()
        except Exception:
            pass
        self._bubble(resp)
        if resp:
            threading.Thread(target=self._speak, args=(resp,), daemon=True).start()
        act = res.get("action") or {}
        try:
            if act.get("type") == "site":
                webbrowser.open(act.get("value", ""))
            elif act.get("type") == "app" and os.name == "nt":
                os.startfile(act.get("value", ""))
        except Exception as e:
            self._bubble(f"(не смогла открыть: {e})")

    # ----- голосовой фон -----

    def _voice_autostart(self):
        if not self._voice_on and not self._voice_stop.is_set():
            self._voice_toggle()

    def _voice_toggle(self):
        if self._voice_on:
            self._voice_stop.set()
            try:
                if self._voice_wake:
                    self._voice_wake.stop()
            except Exception:
                pass
            self._voice_off_ui("выключен")
            return
        self._voice_on = True
        self._voice_stop.clear()
        self._voice_btns_paint()
        try:
            self.chat_status.config(text="👂 Включаю слух…")
        except Exception:
            pass
        threading.Thread(target=self._voice_loop, daemon=True).start()

    def _voice_btns_paint(self):
        try:
            for b in (self.voice_btn, getattr(self, "home_voice_btn", None)):
                if b is None:
                    continue
                if self._voice_on:
                    b.set_text("🎙 Говорить: Вкл")
                    b.set_appear(self.accent, "#141414")
                else:
                    b.set_text("🎙 Говорить: Выкл")
                    b.set_appear(CARD, FG, BORDER)
        except Exception:
            pass

    def _voice_off_ui(self, why: str):
        self._voice_on = False
        self._voice_wake = None
        try:
            self._voice_btns_paint()
            self.chat_status.config(text=f"👂 Голос {why}.")
        except Exception:
            pass

    def _voice_loop(self):
        from engine.wake import WakeListener, ensure_vosk_model, strip_wake
        from engine.stt import YanaSTT
        from engine.tts import YanaTTS
        from engine import voice_id
        try:
            self.after(0, lambda: self.chat_status.config(text="👂 Готовлю слух…"))
            try:
                import vosk  # noqa: F401
                ensure_vosk_model()
            except ImportError:
                pass
            if self._voice_stop.is_set():
                return
            wake = WakeListener(use_vosk=True)
            self._voice_wake = wake
            stt = YanaSTT()
            tts = YanaTTS()
            self.after(0, lambda: self._bubble("Голос включён! Просто зови меня: Яна 👂"))
        except Exception as e:
            return self.after(0, lambda: self._voice_off_ui(f"ошибка: {e}"))
        while not self._voice_stop.is_set():
            try:
                self.after(0, lambda: self.chat_status.config(text="👂 Слушаю «Яна»…"))
                heard = wake.wait_for_wake()
            except Exception:
                break
            if self._voice_stop.is_set() or not heard:
                break
            cmd = strip_wake(heard)
            if not cmd:
                self.after(0, lambda: self._bubble("👂 Слушаю!"))
                try:
                    tts.speak_key("listening", "Слушаю!")
                except Exception:
                    pass
                try:
                    self.after(0, lambda: self.chat_status.config(text="👂 Жду команду…"))
                    cmd = stt.listen(timeout=8, phrase_limit=10) or ""
                except Exception:
                    cmd = ""
                if self._voice_stop.is_set():
                    break
                if not cmd:
                    self.after(0, lambda: self._bubble("Не расслышала, повтори с «Яна» 🙂"))
                    continue
                try:
                    desc = voice_id.describe_current()
                    if desc:
                        self.after(0, lambda: self.chat_status.config(text=f"🎙 Говорит: {desc}"))
                    self.after(0, self._paint_status)
                except Exception:
                    pass
                if self._voice_stop.is_set():
                    break
            self.after(0, lambda: self._bubble("🎤 " + cmd, me=True))
            try:
                with self._chat_lock:
                    res = self._brain_get().process(cmd)
                resp = res.get("response", "")
            except Exception as e:
                self.after(0, lambda: self._bubble(f"Ошибка: {e}"))
                continue
            self.after(0, lambda: self._chat_done(None, res, resp))
            try:
                import time as _t
                _t.sleep(0.4)
            except Exception:
                pass
            if res.get("exit"):
                self._voice_stop.set()
                try:
                    wake.stop()
                except Exception:
                    pass
        self.after(0, lambda: self._voice_off_ui("выключен"))

    # ================= МОДУЛИ =================

    def _build_modules(self):
        pg = self._pages["modules"]
        wrap = tk.Frame(pg, bg=BG)
        wrap.pack(fill="both", expand=True, padx=18, pady=(14, 12))
        lab(wrap, "Возможности", font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=(0, 8))
        self.mod_scroll = ScrollFrame(wrap)
        self.mod_scroll.pack(fill="both", expand=True)
        self.mod_inner = self.mod_scroll.inner
        self._modules_refresh()

    def _all_items(self):
        items = [dict(t) for t in TILES]
        items += list(self.store.games)
        return items

    def _sel_item(self):
        try:
            for t in TILES:
                if t["id"] == self._sel:
                    return dict(t, tile=True)
            g = self.store.get(self._sel)
            if g:
                return g
            items = self._all_items()
            if items:
                self._sel = items[0]["id"]
                it = items[0]
                it["tile"] = it["id"] in {t["id"] for t in TILES}
                return it
        except Exception:
            pass
        return None

    def _modules_refresh(self):
        try:
            if self.mod_inner is None:
                return
            for w in self.mod_inner.winfo_children():
                w.destroy()
            self._cards = {}
            tiles = [dict(t, tile=True) for t in TILES]
            progs = list(self.store.games)
            if not any(i["id"] == self._sel for i in tiles + progs):
                self._sel = "voice"
            lab(self.mod_inner, "Яна умеет", font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(2, 4))
            grid = tk.Frame(self.mod_inner, bg=BG)
            grid.pack(fill="x")
            for c in range(4):
                grid.grid_columnconfigure(c, weight=1, uniform="m")
            for i, it in enumerate(tiles):
                self._make_card(grid, it, i)
            row = tk.Frame(self.mod_inner, bg=BG)
            row.pack(fill="x", pady=(12, 4))
            lab(row, "Мои программы", font=("Segoe UI", 13, "bold")).pack(side="left")
            RoundedButton(row, "＋ Добавить", command=lambda: self._prog_modal(None),
                          width=130, height=30, bg=self.accent, fg="#141414",
                          font=FONT_SMALL).pack(side="left", padx=12)
            if not progs:
                lab(self.mod_inner, "Пока пусто — добавь игру или программу кнопкой выше.",
                    font=FONT_SMALL, color=MUTED).pack(anchor="w", pady=4)
            else:
                grid2 = tk.Frame(self.mod_inner, bg=BG)
                grid2.pack(fill="x")
                for c in range(4):
                    grid2.grid_columnconfigure(c, weight=1, uniform="p")
                for i, it in enumerate(progs):
                    self._make_card(grid2, it, i)
        except Exception:
            pass

    def _make_card(self, grid, it: dict, i: int):
        card = tk.Canvas(grid, width=200, height=252, bg=BG,
                         highlightthickness=0, bd=0, cursor="hand2")
        card.grid(row=i // 4, column=i % 4, padx=7, pady=7, sticky="w")
        self._cards[it["id"]] = card
        self._card_paint(card, it)
        card.bind("<Button-1>", lambda e, gid=it["id"]: self._select(gid))
        card.bind("<Double-Button-1>", lambda e, gid=it["id"]: self._play_gid(gid))
        card.bind("<Enter>", lambda e, gid=it["id"]: self._hover_on(gid))
        card.bind("<Leave>", lambda e, gid=it["id"]: self._hover_off(gid))

    def _hover_on(self, gid: str):
        self._hover = gid
        self._repaint_card(gid)

    def _hover_off(self, gid: str):
        if self._hover == gid:
            self._hover = ""
        self._repaint_card(gid)

    def _repaint_card(self, gid: str):
        try:
            card = self._cards.get(gid)
            if card is None:
                return
            it = None
            for t in TILES:
                if t["id"] == gid:
                    it = dict(t, tile=True)
            if it is None:
                it = self.store.get(gid)
            if it is not None:
                self._card_paint(card, it)
        except Exception:
            pass

    def _card_paint(self, card: tk.Canvas, it: dict):
        try:
            card.delete("all")
            cw, ch = 200, 180
            sel = it["id"] == self._sel
            hov = it["id"] == self._hover
            _gradient(card, cw, ch, _darken(it.get("c1", CARD), 0.7), it.get("c1", CARD))
            card.create_oval(126, -34, 236, 76, fill=_darken(it.get("c2", BORDER), 0.75),
                             outline="")
            card.create_oval(-44, 96, 62, 202, fill=_darken(it.get("c2", BORDER), 0.6),
                             outline="")
            card.create_text(100, 80, text=it.get("icon", "📦"), font=("Segoe UI", 54))
            if it.get("tile"):
                _round_rect(card, 10, 10, 56, 32, 8, fill=self.accent, outline="")
                card.create_text(33, 21, text="ЯНА", font=("Segoe UI", 8, "bold"),
                                 fill="#141414")
            _soft_round(card, cw, ch, 18, BG)
            if sel:
                _round_rect(card, 5, 5, cw - 5, ch - 5, 15, fill="",
                            outline=self.accent, width=3)
            elif hov:
                _round_rect(card, 5, 5, cw - 5, ch - 5, 15, fill="",
                            outline=_mix(self.accent, BG, 0.35), width=2)
            card.create_text(100, ch + 22, text=(it.get("name", "") or "")[:24],
                             fill=FG if (sel or hov) else "#d7d7de", font=FONT_CARD)
            if it.get("tile"):
                meta = it.get("desc", "") or ""
            elif it.get("launches"):
                meta = f"▶ {it.get('launches', 0)} · {fmt_time(it.get('seconds', 0))}"
            else:
                meta = "ещё не запускалась"
            card.create_text(100, ch + 42, text=meta[:30], fill=MUTED, font=FONT_META)
        except Exception:
            pass

    def _select(self, gid: str):
        self._sel = gid
        try:
            for cid in self._cards:
                self._repaint_card(cid)
        except Exception:
            pass

    def _play_gid(self, gid: str):
        self._sel = gid
        self._play_sel()

    def _play_sel(self):
        try:
            it = self._sel_item()
            if not it:
                return
            if it["id"] == "voice":
                threading.Thread(target=self._speak,
                                 args=("Привет! Я Яна Нейронова. Голос работает отлично.",),
                                 daemon=True).start()
                self.toast("🔊 Яна говорит…", True)
                return
            if it["id"] == "look":
                return self._eyes_look()
            if it["id"] == "watch":
                return self._watch_toggle()
            if it["id"] == "folder":
                return self._folder_open()
            ok, msg = launch_program(it, self.store)
            self.toast(msg, ok)
            self._modules_refresh()
        except Exception as e:
            self.toast(f"Ошибка: {e}", False)

    def _del_sel(self):
        try:
            it = self._sel_item()
            if not it or it.get("tile"):
                return
            if messagebox.askyesno("Удалить", f"Убрать «{it.get('name', '')}»?\n(файлы не трону)"):
                self.store.delete(it["id"])
                self._sel = "voice"
                self._modules_refresh()
        except Exception:
            pass

    def _prog_modal(self, game):
        try:
            d = ProgramModal(self, game)
            self.wait_window(d)
            if not d.result:
                return
            r = d.result
            if game:
                self.store.update(game["id"], **r)
            else:
                g = self.store.add(**r)
                self._sel = g["id"]
            self._modules_refresh()
            self.toast("Сохранено ✓", True)
        except Exception as e:
            self.toast(f"Ошибка: {e}", False)

    # ----- глаза и папка -----

    def _watch_say(self, text: str):
        try:
            self.after(0, lambda: self.toast("👁 " + text[:140], True))
        except Exception:
            pass
        threading.Thread(target=self._speak, args=(text,), daemon=True).start()

    def _eyes_look(self):
        self.toast("👁 Смотрю на экран…", True)
        threading.Thread(target=self._eyes_look_worker, daemon=True).start()

    def _eyes_look_worker(self):
        try:
            from engine import screen_watch
            txt = screen_watch.describe()
            try:
                self.after(0, lambda: self.toast("👁 " + txt[:140], True))
            except Exception:
                pass
            self._speak(txt)
        except Exception as e:
            try:
                self.after(0, lambda: self.toast(f"Не вышло: {e}", False))
            except Exception:
                pass

    def _watch_toggle(self):
        try:
            from engine import screen_watch
            w = screen_watch.watcher
            if w.running:
                w.stop()
                self.toast("👁 Слежение выключено", True)
            else:
                w.start()
                self.toast("👁 Слежу за экраном! Скажи «хватит следить» чтобы выключить.", True)
        except Exception as e:
            self.toast(f"Ошибка: {e}", False)

    def _folder_open(self):
        try:
            from engine import screen_watch
            from engine.memory import Memory
            self.toast("📁 " + screen_watch.open_home(Memory()), True)
        except Exception as e:
            self.toast(f"Ошибка: {e}", False)

    # ================= НАСТРОЙКИ =================

    def _build_settings(self):
        pg = self._pages["settings"]
        wrap = tk.Frame(pg, bg=BG)
        wrap.pack(fill="both", expand=True, padx=18, pady=(14, 12))
        lab(wrap, "Настройки", font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=(0, 8))
        scroll = ScrollFrame(wrap)
        scroll.pack(fill="both", expand=True)
        f = scroll.inner
        s = load_settings()

        lab(f, "Голос Яны:", font=FONT_B).pack(anchor="w", pady=(4, 2))
        self._voice_vars = {}
        for key, text, lo, hi, attr in (
                ("YANA_TTS_RATE", "Скорость, %", -50, 100, "TTS_RATE"),
                ("YANA_TTS_PITCH", "Тон, Гц", -50, 50, "TTS_PITCH"),
                ("YANA_TTS_VOLUME", "Громкость, %", -50, 50, "TTS_VOLUME")):
            top = tk.Frame(f, bg=BG)
            top.pack(fill="x", pady=(4, 0))
            lab(top, text, font=FONT_SMALL).pack(side="left")
            vlab = lab(top, "", font=FONT_B, color=self.accent)
            vlab.pack(side="right")
            cur = float(s.get(key, getattr(config, attr)))
            var = tk.DoubleVar(value=max(lo, min(hi, cur)))
            self._voice_vars[key] = (var, vlab)
            tk.Scale(f, from_=lo, to=hi, orient="horizontal", variable=var,
                     bg=BG, fg=FG, troughcolor=CARD, highlightthickness=0,
                     activebackground=self.accent, sliderrelief="flat", relief="flat",
                     bd=0, showvalue=False, length=420,
                     command=lambda v, k=key: self._vp(k)).pack(fill="x")
            self._vp(key)
        vrow = tk.Frame(f, bg=BG)
        vrow.pack(fill="x", pady=8)
        RoundedButton(vrow, "🔊 Прослушать", command=self._voice_test,
                      width=150, height=32, bg=CARD, hover=BORDER).pack(side="left", padx=3)
        RoundedButton(vrow, "💾 Сохранить голос", command=self._voice_save,
                      width=180, height=32, bg=self.accent, fg="#141414").pack(side="left", padx=3)

        lab(f, "Микрофон:", font=FONT_B).pack(anchor="w", pady=(10, 2))
        mtop = tk.Frame(f, bg=BG)
        mtop.pack(fill="x")
        lab(mtop, "Чувствительность (порог, ниже — чутче)", font=FONT_SMALL).pack(side="left")
        self._mic_lab = lab(mtop, "", font=FONT_B, color=self.accent)
        self._mic_lab.pack(side="right")
        self._mic_var = tk.DoubleVar(value=float(s.get("YANA_MIC_ENERGY", config.MIC_ENERGY)))
        tk.Scale(f, from_=50, to=600, orient="horizontal", variable=self._mic_var,
                 bg=BG, fg=FG, troughcolor=CARD, highlightthickness=0,
                 activebackground=self.accent, sliderrelief="flat", relief="flat",
                 bd=0, showvalue=False, length=420,
                 command=lambda v: self._mic_lab.config(text=str(int(float(v))))).pack(fill="x")
        self._mic_lab.config(text=str(int(self._mic_var.get())))
        RoundedButton(f, "💾 Сохранить микрофон", command=self._mic_save,
                      width=200, height=32, bg=CARD, hover=BORDER).pack(anchor="w", pady=6)

        lab(f, "Groq API-ключ (умные ответы, бесплатно):", font=FONT_B).pack(anchor="w", pady=(10, 2))
        krow = tk.Frame(f, bg=BG)
        krow.pack(fill="x", pady=4)
        self._key_entry = tk.Entry(krow, bg=CARD, fg=FG, relief="flat", insertbackground=FG,
                                   font=FONT, highlightthickness=0, show="•")
        self._key_entry.pack(side="left", fill="x", expand=True, ipady=7)
        RoundedButton(krow, "Сохранить", command=self._key_save,
                      width=120, height=32, bg=CARD, hover=BORDER).pack(side="left", padx=6)
        self._key_hint = lab(f, "", font=FONT_SMALL, color=MUTED)
        self._key_hint.pack(anchor="w")
        self._key_paint()

        lab(f, "Оформление:", font=FONT_B).pack(anchor="w", pady=(10, 4))
        arow = tk.Frame(f, bg=BG)
        arow.pack(anchor="w")
        self._acc_btns = []
        for color, name in ACCENTS:
            c = tk.Canvas(arow, width=66, height=42, bg=BG, highlightthickness=0, bd=0,
                          cursor="hand2")
            c.pack(side="left", padx=4)
            c.create_oval(15, 5, 51, 37, fill=color, outline="")
            c.bind("<Button-1>", lambda e, col=color: self._set_accent(col))
            self._acc_btns.append((c, color))
        self._acc_hint = lab(f, "", font=FONT_SMALL, color=MUTED)
        self._acc_hint.pack(anchor="w", pady=2)
        self._acc_paint()
        trow = tk.Frame(f, bg=BG)
        trow.pack(anchor="w", pady=4)
        self._bg_btns = []
        for i, ((_, _), name) in enumerate(BG_THEMES):
            b = RoundedButton(trow, name, command=lambda i=i: self._set_bg(i),
                              width=120, height=32, bg=CARD, fg=FG, hover=BORDER,
                              font=FONT_SMALL)
            b.pack(side="left", padx=4)
            self._bg_btns.append(b)
        self._bg_paint()
        lab(f, f"Яна v{APP_VERSION} · движок v{config.VERSION}",
            font=FONT_SMALL, color=MUTED).pack(anchor="w", pady=(14, 0))

    def _vp(self, key: str):
        try:
            var, vlab = self._voice_vars[key]
            vlab.config(text=f"{int(round(var.get())):+d}")
        except Exception:
            pass

    def _voice_test(self):
        try:
            vals = {k: int(round(v.get())) for k, (v, _) in self._voice_vars.items()}
            config.TTS_RATE = vals["YANA_TTS_RATE"]
            config.TTS_PITCH = vals["YANA_TTS_PITCH"]
            config.TTS_VOLUME = vals["YANA_TTS_VOLUME"]
            threading.Thread(target=self._speak,
                             args=("Привет! Я настроила свой голос специально для тебя.",),
                             daemon=True).start()
            self.toast("🔊 Слушай…", True)
        except Exception as e:
            self.toast(f"Ошибка: {e}", False)

    def _voice_save(self):
        try:
            s = load_settings()
            for k, (v, _) in self._voice_vars.items():
                s[k] = int(round(v.get()))
            save_settings(s)
            config.reload_settings(force=True)
            self.toast("Голос сохранён ✓", True)
        except Exception as e:
            self.toast(f"Ошибка: {e}", False)

    def _mic_save(self):
        try:
            s = load_settings()
            s["YANA_MIC_ENERGY"] = int(self._mic_var.get())
            save_settings(s)
            config.reload_settings(force=True)
            self.toast("Микрофон сохранён ✓", True)
        except Exception as e:
            self.toast(f"Ошибка: {e}", False)

    def _key_paint(self):
        try:
            k = (config.GROQ_API_KEY or "").strip()
            if len(k) > 12:
                self._key_hint.config(text=f"Сейчас: {k[:7]}…{k[-3:]} ✓")
            else:
                self._key_hint.config(text="Ключа нет — только офлайн и Pollinations")
        except Exception:
            pass

    def _key_save(self):
        try:
            k = self._key_entry.get().strip()
            if not k:
                self.toast("Вставь ключ", False)
                return
            s = load_settings()
            s["GROQ_API_KEY"] = k
            save_settings(s)
            config.reload_settings(force=True)
            self._key_entry.delete(0, "end")
            self._key_paint()
            self.toast("Ключ сохранён ✓", True)
        except Exception as e:
            self.toast(f"Ошибка: {e}", False)

    def _set_accent(self, color: str):
        try:
            self.accent = color
            self.astyle.accent = color
            self.astyle.save()
            self._acc_paint()
            for b in self._nav_btns.values():
                b.recolor(self._sb(), color)
            self._bg_paint()
            self._hero_paint()
            self._home_cards_paint()
            self._modules_refresh()
            self.toast("Акцент применён ✓", True)
        except Exception:
            pass

    def _acc_paint(self):
        try:
            for c, color in self._acc_btns:
                c.delete("sel")
                if color == self.accent:
                    c.create_oval(11, 1, 55, 41, outline="#ffffff", width=2, tags="sel")
                    nm = dict((a[0], a[1]) for a in ACCENTS).get(color, "")
                    if self._acc_hint is not None:
                        self._acc_hint.config(text=f"Сейчас: {nm}")
        except Exception:
            pass

    def _set_bg(self, i: int):
        try:
            self.astyle.bg = i
            self.astyle.save()
            if self.sidebar is not None:
                self.sidebar.config(bg=self._sb())
            for b in self._nav_btns.values():
                b.recolor(self._sb(), self.accent)
            self._bg_paint()
            self._modules_refresh()
            self.toast("Тема применена ✓", True)
        except Exception:
            pass

    def _bg_paint(self):
        try:
            for i, b in enumerate(self._bg_btns):
                if i == self.astyle.bg:
                    b.set_appear(self.accent, "#141414")
                else:
                    b.set_appear(CARD, FG, BORDER)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        App().mainloop()
    except Exception:
        import traceback
        err = traceback.format_exc()
        try:
            (config.DATA_DIR / "yana_error.log").write_text(err, encoding="utf-8")
        except Exception:
            pass
        try:
            messagebox.showerror("Яна Нейронова", err[-1200:])
        except Exception:
            pass
        raise
