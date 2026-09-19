# -*- coding: utf-8 -*-
"""Память Яны: имя, факты о пользователе, история диалога, защита от повторов.

Факты извлекаются сами из речи («я люблю космос», «мне 25», «я из Москвы»)
и подставляются в промпт ИИ — контекст не теряется никогда.
"""
import json
import random
import re
from pathlib import Path
from typing import Optional

from . import config

# осторожные паттерны фактов: (регулярка, шаблон факта)
FACT_PATTERNS = [
    (r"я люблю ([^,?!]+)", "любит {}"),
    (r"мне (\d{1,3}) (?:год|лет|года)", "возраст: {}"),
    (r"я из ([^,?!]+)", "из {}"),
    (r"моя? любим(?:ый|ая|ое) ([^,?!]+)", "любимое: {}"),
    (r"у меня есть ([^,?!]+)", "есть {}"),
    (r"я работаю ([^,?!]+)", "работа: {}"),
]

MAX_FACTS = 20


class Memory:
    def __init__(self, path: Optional[Path] = None, max_turns: int = 6):
        self.path = path or config.MEMORY_FILE
        self.max_turns = max_turns
        self.data = {"user_name": "", "facts": [], "history": [], "used": []}
        self.load()

    # ----- загрузка/сохранение -----

    def load(self):
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                self.data.update(loaded)
            except Exception:
                pass
        for k, v in (("user_name", ""), ("facts", []), ("history", []), ("used", [])):
            self.data.setdefault(k, v)

    def save(self):
        try:
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ----- имя -----

    def get_name(self) -> str:
        return (self.data.get("user_name") or "").strip()

    def set_name(self, name: str):
        name = (name or "").strip().capitalize()
        if name and len(name) <= 20:
            self.data["user_name"] = name
            self.save()

    # ----- факты (долгосрочный контекст) -----

    def get_facts(self) -> list[str]:
        return list(self.data.get("facts", []))

    def add_fact(self, fact: str):
        fact = re.sub(r"\s+", " ", (fact or "").strip(" ?!.,")).strip()
        if not fact or len(fact) > 80:
            return
        facts = self.data.setdefault("facts", [])
        if fact.lower() in (f.lower() for f in facts):
            return
        facts.append(fact)
        self.data["facts"] = facts[-MAX_FACTS:]
        print(f"🧠 Запомнила факт: {fact}")
        self.save()

    def _learn(self, text: str):
        """Извлечь факты из реплики пользователя."""
        low = (text or "").lower().strip()
        if not low or len(low) > 200:
            return
        for pat, tpl in FACT_PATTERNS:
            m = re.search(pat, low)
            if m:
                val = re.sub(r"\s+", " ", m.group(1).strip(" ?!.,"))
                if 1 < len(val) <= 60 and "?" not in val:
                    self.add_fact(tpl.format(val))

    # ----- история -----

    def add_turn(self, role: str, content: str):
        content = (content or "").strip()
        if not content:
            return
        if role == "user":
            self._learn(content)
        self.data.setdefault("history", []).append({"role": role, "content": content[:500]})
        # держим последние N пар реплик
        self.data["history"] = self.data["history"][-(self.max_turns * 2):]
        self.save()

    def get_history(self) -> list[dict]:
        return list(self.data.get("history", []))

    def llm_messages(self, system: str, current: str) -> list[dict]:
        msgs = [{"role": "system", "content": system}]
        msgs.extend(self.get_history())
        msgs.append({"role": "user", "content": current})
        return msgs

    # ----- защита от повторов -----

    def pick_fresh(self, key: str, options: list[str]) -> str:
        """Выбрать вариант, который давно не звучал."""
        if not options:
            return ""
        if len(options) == 1:
            return options[0]
        used = set(self.data.get("used", []))
        fresh = [o for i, o in enumerate(options) if f"{key}:{i}" not in used]
        if not fresh:  # все звучали — сбрасываем ключ и начинаем заново
            used = {u for u in used if not u.startswith(f"{key}:")}
            self.data["used"] = sorted(used)
            fresh = options
        choice = random.choice(fresh)
        idx = options.index(choice)
        self.data.setdefault("used", []).append(f"{key}:{idx}")
        self.data["used"] = self.data["used"][-60:]  # не расти бесконечно
        self.save()
        return choice

    def clear_history(self):
        """Забыть всё: историю и факты («забудь меня»)."""
        self.data["history"] = []
        self.data["facts"] = []
        self.save()
