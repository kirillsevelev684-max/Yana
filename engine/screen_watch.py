# -*- coding: utf-8 -*-
"""Глаза Яны: зрение экрана.

- «Что на экране?» — один взгляд: скриншот -> Groq Vision -> короткий ответ.
- «Что за видео?» — три кадра с паузой: Vision видит движение.
- «Во что я играю?» — взгляд на игру.
- «Что у меня открыто?» — список всех окон и вкладок.
- «Следи за экраном» — фон-наблюдение: замечает изменения и комментирует вслух.
- «Это твоя папка» — запомнить свою папку (открытую в проводнике).

Безопасная деградация: нет дисплея/PIL/сети — честно говорит «не вижу»,
но ничего не роняет. Скриншот жмётся до 768px и нигде не сохраняется.
"""
import base64
import re
import threading
import time

from . import config

VISION_QUESTION = (
    "Ты — Яна, дружелюбный голосовой помощник. Опиши ОДНИМ коротким "
    "предложением по-русски, что происходит на экране: какое окно, сайт или "
    "игра открыты, что делает человек. Без списков, markdown и символов — "
    "только живая речь."
)
WATCH_QUESTION = (
    "Ты — Яна, дружелюбный голосовой помощник. Опиши ОДНИМ коротким "
    "предложением по-русски, что сейчас происходит на экране пользователя. "
    "Без списков, markdown и символов — только живая речь."
)
GAME_QUESTION = (
    "Ты — Яна, дружелюбный голосовой помощник. Человек играет в игру. Опиши "
    "ОДНИМ коротким предложением по-русски: что это за игра и что в ней "
    "происходит. Без списков, markdown и символов — только живая речь."
)
VIDEO_QUESTION = (
    "Ты — Яна, дружелюбный голосовой помощник. Это несколько кадров из видео, "
    "которое смотрит человек. Опиши ОДНИМ коротким предложением по-русски, "
    "что происходит в видео. Без списков, markdown и символов — только живая речь."
)
CHANGE_THRESHOLD = 0.06  # доля изменившихся пикселей — пора комментировать

_speaker = None
_speaker_lock = threading.Lock()


def set_speaker(fn) -> None:
    """Кто озвучивает комментарии слежения (GUI/консоль ставят свой TTS)."""
    global _speaker
    with _speaker_lock:
        _speaker = fn


def _say(text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    with _speaker_lock:
        fn = _speaker
    if fn:
        try:
            fn(text)
            return
        except Exception:
            pass
    print(f"\n👁 Яна (экран): {text}")


# ---------- скриншот ----------

def grab_screen():
    """Скриншот как PIL Image или None (нет дисплея/PIL — не страшно)."""
    try:
        from PIL import ImageGrab
        try:
            return ImageGrab.grab()
        except Exception:
            pass
    except ImportError:
        pass
    try:
        import mss
        with mss.mss() as s:
            mon = s.monitors[1] if len(s.monitors) > 1 else s.monitors[0]
            shot = s.grab(mon)
        try:
            from PIL import Image
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        except ImportError:
            return None
    except Exception:
        return None


def to_jpeg(img, max_side: int = 768, quality: int = 60) -> bytes:
    """Сжать скриншот для отправки в Vision (маленький = быстрый)."""
    try:
        import io
        pic = img.convert("RGB")
        pic.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        pic.save(buf, "JPEG", quality=quality)
        return buf.getvalue()
    except Exception:
        return b""


def active_window_title() -> str:
    """Название активного окна (только Windows; иначе '')."""
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return ""
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
        return (buf.value or "").strip()
    except Exception:
        return ""


def list_windows(limit: int = 12) -> list:
    """Названия видимых окон (только Windows; иначе [])."""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        titles = []

        def _cb(hwnd, _):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                t = (buf.value or "").strip()
                if t:
                    titles.append(t)
            except Exception:
                pass
            return True

        cmp = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(cmp(_cb), 0)
        seen, out = set(), []
        for t in titles:
            if t not in seen:
                seen.add(t)
                out.append(t)
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


def describe_windows() -> str:
    """Что открыто — коротко для озвучки (макс. 2 предложения)."""
    wins = list_windows()
    if not wins:
        return "Не вижу открытых окон — похоже, я запущена без дисплея."
    active = active_window_title()
    others = [w for w in wins if w != active][:3]
    if active and others:
        return (f"Сейчас активно «{active}». "
                "Ещё: " + ", ".join(f"«{w}»" for w in others) + ".")
    if active:
        return f"Открыто окон: {len(wins)}. Сейчас активно «{active}»."
    return f"Открыто окон: {len(wins)}: " + ", ".join(f"«{w}»" for w in wins[:4]) + "."


def explorer_active_path() -> str:
    """Путь папки в активном окне Проводника (только Windows; иначе '')."""
    try:
        import ctypes
        import os as _os
        import subprocess
        if _os.name != "nt":
            return ""
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return ""
        # PowerShell + COM: HWND и путь каждого окна проводника (без лишних библиотек)
        ps = ("(New-Object -ComObject Shell.Application).Windows() | "
              "ForEach-Object { \"$($_.HWND)|$($_.LocationURL)\" }")
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, timeout=12)
        for line in (r.stdout or "").splitlines():
            if "|" not in line:
                continue
            h, url = line.split("|", 1)
            if h.strip() == str(hwnd):
                url = url.strip()
                if url.lower().startswith("file:///"):
                    from urllib.parse import unquote, urlparse
                    p = unquote(urlparse(url).path).strip("/").replace("/", "\\")
                    if _os.path.isdir(p):
                        return p
                return ""  # виртуальная папка (панель управления и т.п.)
        return ""
    except Exception:
        return ""


def _is_online() -> bool:
    try:
        import socket
        socket.create_connection(("8.8.8.8", 53), timeout=3).close()
        return True
    except Exception:
        return False


# ---------- сравнение кадров ----------

def _thumbnail(img) -> bytes:
    try:
        return img.convert("L").resize((64, 36)).tobytes()
    except Exception:
        return b""


def _changed_pct(a: bytes, b: bytes) -> float:
    """Доля пикселей, изменившихся заметно (0.0–1.0)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    try:
        import numpy as np
        x = np.frombuffer(a, dtype=np.uint8).astype(np.int16)
        y = np.frombuffer(b, dtype=np.uint8).astype(np.int16)
        return float((abs(x - y) > 25).mean())
    except Exception:
        n = sum(1 for i, j in zip(a, b) if abs(i - j) > 25)
        return n / max(1, len(a))


# ---------- описание ----------

def _clean(text: str) -> str:
    s = text or ""
    s = re.sub(r"https?://\S+|www\.\S+", "", s)
    s = re.sub(r"[*_#`>|~^]+", "", s)
    s = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE00-\uFEFF]+", "", s)
    s = re.sub(r"\s+", " ", s).strip(" ,")
    # только первые 2 предложения — ответы Яны короткие
    parts = re.split(r"(?<=[.!?…])\s+", s)
    return " ".join(parts[:2]).strip()


def describe_multi(jpegs: list, question: str, timeout: int = 30) -> str:
    """Groq Vision: вопрос по нескольким кадрам сразу. '' если не вышло."""
    jpegs = [j for j in (jpegs or []) if j]
    if not jpegs or not (config.GROQ_API_KEY or "").strip():
        return ""
    try:
        import requests
        content = [{"type": "text", "text": question}]
        for jpeg in jpegs[:4]:
            b64 = base64.b64encode(jpeg).decode("ascii")
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            json={"model": config.GROQ_VISION_MODEL,
                  "messages": [{"role": "user", "content": content}],
                  "max_tokens": 120, "temperature": 0.4},
            timeout=timeout)
        if r.status_code == 200:
            try:
                return _clean(r.json()["choices"][0]["message"]["content"])
            except Exception:
                return ""
        return ""
    except Exception:
        return ""


def describe_cloud(jpeg: bytes, question: str, timeout: int = 25) -> str:
    """Groq Vision: что на скриншоте. '' если не вышло."""
    return describe_multi([jpeg], question, timeout)


def describe(question: str = VISION_QUESTION, online=None) -> str:
    """Один взгляд на экран: облако -> название окна -> честное «не вижу»."""
    img = grab_screen()
    if img is None:
        title = active_window_title()
        if title:
            return f"Скриншот снять не вышло, но активно окно «{title}»."
        return "Не вижу экран — похоже, я запущена без дисплея."
    if online is None:
        online = _is_online()
    if online:
        txt = describe_cloud(to_jpeg(img), question)
        if txt:
            return txt
    title = active_window_title()
    if title:
        return f"Облако недоступно, но вижу окно «{title}»."
    return "Экран передо мной, но без облака разглядеть не могу."


def describe_video(online=None) -> str:
    """Что за видео: 3 кадра с паузой — Vision видит движение."""
    frames = []
    for i in range(3):
        img = grab_screen()
        if img is not None:
            frames.append(to_jpeg(img))
        if i < 2:
            time.sleep(1.0)
    if not frames:
        title = active_window_title()
        if title:
            return f"Кадры снять не вышло, но активно окно «{title}»."
        return "Не вижу экран — похоже, я запущена без дисплея."
    if online is None:
        online = _is_online()
    if online:
        txt = describe_multi(frames, VIDEO_QUESTION)
        if txt:
            return txt
    title = active_window_title()
    if title:
        return f"Облако недоступно, но видео крутится в окне «{title}»."
    return "Кадры есть, но без облака понять видео не могу."


# ---------- своя папка Яны ----------

def get_home(memory) -> str:
    try:
        return ((memory.data.get("yana_folder") if memory else "") or "").strip()
    except Exception:
        return ""


def set_home(memory, path: str) -> bool:
    try:
        import os as _os
        path = (path or "").strip()
        if not memory or not path or not _os.path.isdir(path):
            return False
        memory.data["yana_folder"] = path
        memory.save()
        return True
    except Exception:
        return False


def clear_home(memory) -> None:
    try:
        if memory and "yana_folder" in memory.data:
            del memory.data["yana_folder"]
            memory.save()
    except Exception:
        pass


def bind_home(memory) -> str:
    """«Это твоя папка» — запомнить открытую в проводнике папку."""
    path = explorer_active_path()
    if not path:
        return ("Не вижу открытого проводника. Открой папку, кликни по её окну "
                "и скажи ещё раз «это твоя папка».")
    if set_home(memory, path):
        return f"Запомнила! Теперь моя папка — {path}. Буду хранить там наши файлы."
    return "Не вышло запомнить папку. Попробуй ещё раз."


def open_home(memory) -> str:
    """Открыть свою папку в проводнике."""
    import os as _os
    home = get_home(memory)
    if not home:
        return "У меня пока нет своей папки. Открой её в проводнике и скажи «это твоя папка»."
    try:
        if _os.name == "nt":
            _os.startfile(home)
        else:
            import subprocess
            subprocess.Popen(["xdg-open", home])
        return "Открыла свою папку."
    except Exception:
        return f"Не могу открыть {home}."


def home_summary(memory) -> str:
    """Что в папке Яны — коротко для озвучки."""
    import os as _os
    home = get_home(memory)
    if not home:
        return "У меня пока нет своей папки. Открой её в проводнике и скажи «это твоя папка»."
    if not _os.path.isdir(home):
        return f"Моя папка пропала: {home} больше нет. Покажи новую — скажи «это твоя папка»."
    try:
        items = sorted(_os.listdir(home))
    except Exception:
        return "Не могу заглянуть в свою папку — доступ запрещён."
    if not items:
        return "Моя папка пустая. Клади туда файлы — я за ними присмотрю."
    n_files = sum(1 for i in items if _os.path.isfile(_os.path.join(home, i)))
    n_dirs = len(items) - n_files
    files = [i for i in items if _os.path.isfile(_os.path.join(home, i))]
    head = ", ".join(files[:4])
    s = f"В моей папке: файлов {n_files}, папок {n_dirs}."
    if head:
        s += f" Первые: {head}."
    return s


# ---------- команды ----------

_STOP = ("хватит следи", "перестань следи", "не следи", "выключи наблюд",
        "хватит наблюд", "отключи наблюд", "прекрати следи",
        "не подглядывай", "хватит подглядывать")
_STATUS = ("ты следишь", "следишь за экраном", "ты наблюдаешь", "ты смотришь")
_START = ("следи за экраном", "наблюдай за экраном", "комментируй экран",
          "смотри за экраном", "присматривай за экраном",
          "следи что", "следи, что")
_HOME_BIND = ("это твоя папка", "это твоя папочка", "это папка яны")
_HOME_FORGET = ("забудь свою папку", "удали свою папку", "отвяжи свою папку")
_HOME_OPEN = ("открой свою папку", "покажи свою папку")
_HOME_WHERE = ("где твоя папка",)
_HOME_LIST = ("что в твоей папке", "что у тебя в папке",
              "покажи свои файлы", "что в твоей папке")
_WINDOWS = ("что у меня открыто", "какие окна открыты", "что запущено",
            "какие вкладки открыты", "перечисли окна", "мои окна",
            "что открыто на компьютере")
_VIDEO = ("что за видео", "что в видео", "что происходит в видео",
          "опиши видео", "что там в видео", "что смотрю")
_GAME = ("что за игра", "во что я играю", "в какую игру играю", "опиши игру")
_LOOK = ("что на экране", "что у меня на экране", "посмотри на экран",
         "взгляни на экран", "опиши экран", "что сейчас на экране",
         "что я делаю", "что открыто", "что там на экране", "глянь на экран")

_NO_HOME = ("У меня пока нет своей папки. "
            "Открой её в проводнике и скажи «это твоя папка».")


def handle(norm: str, online: bool = True, brain=None):
    """Команды глаз. Возвращает (текст, движок) или None."""
    n = norm or ""
    if any(s in n for s in _STOP):
        watcher.stop()
        return ("Хорошо, больше не подглядываю.", "глаза")
    if any(s in n for s in _STATUS):
        if watcher.running:
            return ("Да, смотрю за экраном и комментирую изменения.", "глаза")
        return ("Пока не слежу. Скажи «следи за экраном» — и я начну.", "глаза")
    if any(s in n for s in _START):
        watcher.start()
        return ("Слежу за экраном! Буду комментировать, что меняется.", "глаза")
    mem = getattr(brain, "memory", None)
    if any(s in n for s in _HOME_BIND):
        return (bind_home(mem), "глаза")
    if any(s in n for s in _HOME_FORGET):
        clear_home(mem)
        return ("Отвязала папку, файлы не трогала. Покажешь новую?", "глаза")
    if any(s in n for s in _HOME_OPEN):
        return (open_home(mem), "глаза")
    if any(s in n for s in _HOME_WHERE):
        home = get_home(mem)
        if home:
            return (f"Моя папка: {home}.", "глаза")
        return (_NO_HOME, "глаза")
    if any(s in n for s in _HOME_LIST):
        return (home_summary(mem), "глаза")
    if any(s in n for s in _WINDOWS):
        return (describe_windows(), "глаза")
    if any(s in n for s in _VIDEO):
        return (describe_video(online=online), "глаза")
    if any(s in n for s in _GAME):
        return (describe(question=GAME_QUESTION, online=online), "глаза")
    if any(s in n for s in _LOOK):
        return (describe(online=online), "глаза")
    return None


# ---------- фон-слежение ----------

class ScreenWatcher:
    """Фоновое наблюдение: смотрит раз в N сек, комментирует изменения."""

    def __init__(self):
        self.running = False
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            self.running = True
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self.running = False
            self._stop.set()

    def _loop(self) -> None:
        prev = None
        last_title = ""
        last_say = 0.0
        while not self._stop.is_set():
            try:
                interval = max(5, int(getattr(config, "SCREEN_INTERVAL", 15) or 15))
            except (TypeError, ValueError):
                interval = 15
            if self._stop.wait(interval):
                break
            try:
                img = grab_screen()
                if img is None:
                    continue
                small = _thumbnail(img)
                title = active_window_title()
                changed = _changed_pct(prev, small) if prev is not None else 0.0
                prev = small
                now = time.time()
                new_title = bool(title and last_title and title != last_title)
                if title:
                    last_title = title
                if now - last_say < config.SCREEN_COMMENT_COOLDOWN:
                    continue
                comment = ""
                if new_title:
                    comment = f"Теперь открыто «{title}»."
                elif changed >= CHANGE_THRESHOLD:
                    jpeg = to_jpeg(img)
                    if jpeg:
                        comment = describe_cloud(jpeg, WATCH_QUESTION)
                if comment:
                    last_say = now
                    _say(comment)
            except Exception:
                continue


watcher = ScreenWatcher()
