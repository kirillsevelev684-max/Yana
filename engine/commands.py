# -*- coding: utf-8 -*-
"""Менеджер пользовательских команд. У команды: триггер + алиасы + действие.

Формат commands.json:
{
  "commands": [
    {"trigger": "открой ютуб", "aliases": ["ютуб", "включи ютуб"],
     "action": {"type": "site", "value": "https://youtube.com"}},
    {"trigger": "привет", "aliases": [], "action": {"type": "say", "value": "Привет!"}}
  ]
}
Типы action: say (сказать текст), site (открыть ссылку), app (открыть программу/файл).
Старый формат (без aliases) читается как есть.
Живой файл: свежесть — по хэшу содержимого (mtime врёт при быстрых записях),
изменения из настроек/чата видны сразу, без рестарта.
"""
import hashlib
import json
import re
from pathlib import Path
from typing import Optional

from . import config

try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    import difflib
    HAS_RAPIDFUZZ = False


def normalize(text: str) -> str:
    text = text.lower().strip()
    # убираем обращение "яна" в начале/конце
    for w in config.WAKE_WORDS:
        text = re.sub(rf"(^|\s){re.escape(w)}(,|\s|$)", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,.!?")
    return text


def similarity(a: str, b: str) -> int:
    if HAS_RAPIDFUZZ:
        return fuzz.token_sort_ratio(a, b)
    return int(difflib.SequenceMatcher(None, a, b).ratio() * 100)


class CommandManager:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or config.COMMANDS_FILE
        self.commands: list[dict] = []
        self._sig = (0, "")
        self.load()

    def _sig_of_file(self):
        """Сигнатура свежести: (размер, md5). Не врёт, в отличие от mtime."""
        try:
            if self.path.exists():
                data = self.path.read_bytes()
                return (len(data), hashlib.md5(data).hexdigest())
        except Exception:
            pass
        return (0, "")

    def load(self):
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.commands = data.get("commands", [])
            except Exception:
                self.commands = []
        else:
            self.commands = []
            self.save()
            return
        self._sig = self._sig_of_file()

    def save(self):
        data = {"commands": self.commands}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._sig = self._sig_of_file()

    def _fresh(self):
        """Перечитать файл, если его поменяли в другом месте. Без рестарта."""
        try:
            if self._sig_of_file() != self._sig:
                self.load()
        except Exception:
            pass

    @staticmethod
    def _variants(cmd: dict) -> list[str]:
        out = [cmd.get("trigger", "")]
        out += cmd.get("aliases", []) or []
        return [v for v in out if v]

    def find(self, text: str) -> Optional[dict]:
        """Нечёткий поиск по триггеру И алиасам. Возвращает {'trigger','action','score'}."""
        self._fresh()
        norm = normalize(text)
        if not norm:
            return None
        best, best_score = None, 0
        for cmd in self.commands:
            for trig_raw in self._variants(cmd):
                trig = normalize(trig_raw)
                if not trig:
                    continue
                # точное вхождение — сразу победа
                if trig == norm or trig in norm or norm in trig:
                    return {"trigger": cmd["trigger"], "action": cmd["action"], "score": 100}
                score = similarity(norm, trig)
                if score > best_score:
                    best, best_score = cmd, score
        if best and best_score >= config.FUZZY_THRESHOLD:
            return {"trigger": best["trigger"], "action": best["action"], "score": best_score}
        return None

    def add(self, trigger: str, action_type: str, value: str, aliases=None) -> None:
        self._fresh()
        trigger = trigger.strip()
        aliases = [a.strip() for a in (aliases or []) if a and a.strip()]
        # обновить если такая уже есть
        for cmd in self.commands:
            if normalize(cmd.get("trigger", "")) == normalize(trigger):
                cmd["action"] = {"type": action_type, "value": value}
                cmd["aliases"] = aliases
                self.save()
                return
        self.commands.append({"trigger": trigger, "aliases": aliases,
                              "action": {"type": action_type, "value": value}})
        self.save()

    def delete(self, trigger: str) -> bool:
        self._fresh()
        norm = normalize(trigger)
        before = len(self.commands)
        self.commands = [c for c in self.commands
                         if all(normalize(v) != norm for v in self._variants(c))]
        self.save()
        return len(self.commands) < before

    def list_triggers(self) -> list[str]:
        self._fresh()
        return [c.get("trigger", "") for c in self.commands]
