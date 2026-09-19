# -*- coding: utf-8 -*-
"""Инструменты Яны: калькулятор, погода (без ключа), заметки, таймеры."""
import json
import re
import threading
import urllib.parse

from . import config


# ---------- калькулятор ----------

def try_calc(norm: str):
    m = re.search(r"(?:посчитай|сколько будет|вычисли|реши пример)(.+)", norm or "")
    if not m:
        return None
    expr = m.group(1).strip()
    expr = re.sub(r"умножить(\s+на)?", "*", expr)
    expr = re.sub(r"разделить(\s+на)?", "/", expr)
    expr = expr.replace("плюс", "+").replace("минус", "-")
    expr = expr.replace("×", "*").replace("x", "*").replace("÷", "/").replace(",", ".")
    expr = expr.strip()
    if not expr or len(expr) > 40 or not re.fullmatch(r"[0-9+\-*/().%\s]+", expr):
        return "Не поняла пример. Скажи, например: посчитай 12 умножить на 8."
    try:
        result = eval(compile(expr, "<calc>", "eval"), {"__builtins__": {}})  # noqa: S307
    except ZeroDivisionError:
        return "На ноль делить нельзя!"
    except Exception:
        return "Не смогла посчитать. Проверь пример."
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return f"Получается {result}."


# ---------- погода: wttr.in, бесплатно и без ключа ----------

def try_weather(norm: str):
    low = norm or ""
    if "погода" not in low:
        return None
    if any(k in low for k in ("открой", "покажи", "сайт")):
        return None  # этим занимается открывалка сайтов
    m = re.search(r"погода (?:в|во|на) ([а-яёa-z\- ]+)", low)
    city = m.group(1).strip(" ?!.,") if m else ""
    try:
        import requests
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=3&lang=ru" if city else \
            "https://wttr.in/?format=3&lang=ru"
        r = requests.get(url, timeout=6)
        txt = (r.text or "").strip()
        if r.status_code == 200 and txt and "Unknown" not in txt:
            return txt
    except Exception:
        pass
    return None


# ---------- заметки ----------

def _load_notes() -> list:
    try:
        if config.NOTES_FILE.exists():
            data = json.loads(config.NOTES_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def _save_notes(items: list):
    try:
        config.NOTES_FILE.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def try_notes(norm: str):
    low = (norm or "").strip()
    if any(k in low for k in ("покажи заметки", "что в заметках", "мои заметки",
                              "список заметок", "прочитай заметки")) or low in ("заметки",):
        items = _load_notes()
        if not items:
            return "Заметок пока нет. Скажи: запиши купить молоко."
        return "Заметки: " + "; ".join(f"{i + 1}. {t}" for i, t in enumerate(items[-7:])) + "."
    if "удали заметки" in low or "очисти заметки" in low or "сотри заметки" in low:
        _save_notes([])
        return "Все заметки удалены."
    m = re.search(r"(?:запиши|добавь в заметки|заметка:?)\s*(.+)", low)
    if m:
        val = m.group(1).strip(" ?!.,")
        if len(val) < 2:
            return None
        items = _load_notes()
        items.append(val)
        _save_notes(items)
        return f"Записала: {val}."
    return None


# ---------- таймеры и напоминания ----------

def _fire(label: str):
    print(f"\n⏰ НАПОМИНАНИЕ: {label}")
    try:
        import winsound
        for _ in range(3):
            winsound.Beep(880, 400)
    except Exception:
        pass
    try:
        p = config.VOICE_LIBRARY_DIR / "done.mp3"
        if p.exists():
            import pygame
            pygame.mixer.init()
            pygame.mixer.music.load(str(p))
            pygame.mixer.music.play()
    except Exception:
        pass


def try_timer(norm: str):
    low = norm or ""
    m = re.search(r"(?:таймер|напомни через|разбуди через)\s*(\d+)\s*"
                  r"(секунд[уы]?|сек|минут[уы]?|мин|час[аов]?)", low)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    mult = 3600 if unit.startswith("час") else (60 if unit.startswith("мин") else 1)
    secs = max(1, min(n * mult, 12 * 3600))
    tail = re.split(r"(?:таймер|напомни через|разбуди через)\s*\d+\s*"
                    r"(?:секунд[уы]?|сек|минут[уы]?|мин|час[аов]?)", low, maxsplit=1)
    label = (tail[1] if len(tail) > 1 else "").strip(" ?!.,") or "время вышло"
    t = threading.Timer(secs, _fire, args=[label])
    t.daemon = True
    t.start()
    when = f"{secs} сек" if secs < 60 else (f"{secs // 60} мин" if secs < 3600 else f"{secs // 3600} ч")
    return f"Поставила таймер на {when}: {label}."
