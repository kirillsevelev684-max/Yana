# -*- coding: utf-8 -*-
"""Мозг Яны v3 — живой диалог, параллельный опрос ИИ-движков.

Цепочка: команды -> факты -> гонка бесплатного ИИ с памятью -> умный офлайн-диалог.
Гонка: Ollama, Pollinations (без ключа), Gemini, Groq опрашиваются ОДНОВРЕМЕННО,
отвечает тот, кто успел первым. Это убирает ожидание медленных движков.
"""
import datetime
import random
import re
import socket
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from . import config
from . import personality as P
from .commands import CommandManager, normalize
from .memory import Memory

EXIT_WORDS = ["пока", "до свидания", "выключись", "выход", "завершить", "прощай", "спокойной ночи"]
REMEMBER_WORDS = ["запомни команду", "новая команда", "добавь команду", "запомни"]
HELP_WORDS = ["помощь", "что ты умеешь", "команды", "help"]

# Точные фразы из студийной библиотеки (звучат настоящим голосом Яны даже офлайн)
GOODBYE_TEXT = "До свидания! Я всегда рядом. Просто позовите: Яна."
REMEMBER_TEXT = "Что мне запомнить? Назовите фразу для команды и то, что я должна ответить или открыть."
REMEMBERED_TEXT = "Запомнила! Теперь я знаю эту команду даже без интернета."
NOT_UNDERSTOOD = "Извините, я не совсем поняла. Повторите, пожалуйста, чуть медленнее."

NAME_PATTERNS = [
    r"меня зовут ([а-яёa-z\-]+)",
    r"мо[её] имя ([а-яёa-z\-]+)",
    r"зови меня ([а-яёa-z\-]+)",
    r"меня звать ([а-яёa-z\-]+)",
]

STOPWORDS = set("""
это что как или для при про меня тебя себя нас вас они она оно этот эта это того так уже даже если есть будет было
яна ян нейронова яночка пожалуйста скажи расскажи подружка давай ну вот там тут такой такая такое меня тебе
думаешь думаю думает знаешь хочешь можешь можно надо просто очень хочу скажешь ответь
""".split())


class YanaBrain:
    def __init__(self, commands: CommandManager, debug: bool = False):
        self.commands = commands
        self.debug = debug
        self.memory = Memory(max_turns=config.MAX_HISTORY_TURNS)
        self._online_cache = None
        self._online_at = 0.0

    def log(self, *a):
        if self.debug:
            print("[BRAIN]", *a)

    # ---------- интернет ----------

    def is_online(self) -> bool:
        now = time.time()
        if self._online_cache is not None and now - self._online_at < 30:
            return self._online_cache
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=2).close()
            self._online_cache = True
        except Exception:
            self._online_cache = False
        self._online_at = now
        return self._online_cache

    # ---------- главный метод ----------

    def process(self, text: str) -> dict:
        raw = (text or "").strip()
        norm = normalize(text)

        if not norm:
            return self._answer(raw, NOT_UNDERSTOOD, engine="сервис")

        self.memory.add_turn("user", raw)

        # 0. пользователь представился — запоминаем!
        name = self._extract_name(norm)
        if name:
            self.memory.set_name(name)
            resp = self._format(random.choice([
                "Очень приятно, {name}! Я тебя запомнила. Чем займёмся?",
                "{name}, какое красивое имя! Запомнила. Что делаем?",
                "Привет, {name}! Теперь мы знакомы официально. Слушаю тебя!",
            ]))
            return self._answer(raw, resp, engine="память")

        # 0.1 забыть всё
        if "забудь меня" in norm or "забудь всё" in norm or "забудь все" in norm:
            self.memory.data["user_name"] = ""
            self.memory.clear_history()
            return self._answer(raw, "Хорошо, забыла! Как будто мы только познакомились. Как тебя зовут?", engine="память")

        # 1. выход (точный студийный голос)
        if any(w in norm for w in EXIT_WORDS):
            return self._answer(raw, GOODBYE_TEXT, exit=True, engine="сервис")

        # 2. запомнить команду
        if any(w in norm for w in REMEMBER_WORDS):
            return self._answer(raw, REMEMBER_TEXT, engine="сервис", remember=True)

        # 3. помощь
        if any(w in norm for w in HELP_WORDS):
            return self._answer(raw, self._help_text(), engine="факт")

        # 4. удаление команды
        if norm.startswith("удали команду") or norm.startswith("удалить команду"):
            trig = norm.replace("удали команду", "").replace("удалить команду", "").strip()
            if trig and self.commands.delete(trig):
                return self._answer(raw, f"Удалила команду «{trig}».", engine="память")
            return self._answer(raw, "Не нашла такую команду для удаления.", engine="память")

        # 5. свои команды (офлайн, нечёткий поиск)
        hit = self.commands.find(text)
        if hit:
            act = hit["action"]
            if act.get("type") == "say":
                return self._answer(raw, act.get("value", ""), engine="команда")
            if act.get("type") in ("site", "app"):
                return self._answer(raw, self._format(self.memory.pick_fresh("open", P.OPEN_VARIANTS)),
                                    action=act, engine="команда")

        # 5.5 системные команды (точные триггеры из коробки; свои выше — они важнее)
        try:
            from . import system_commands as _sys
            sys_hit = _sys.handle(norm, self)
            if sys_hit:
                return sys_hit
        except Exception as e:
            self.log(f"syscmd: {e}")

        # 5.6 глаза Яны: зрение экрана (один взгляд + слежение)
        try:
            from . import screen_watch as _eyes
            eyes_hit = _eyes.handle(norm, self.is_online(), self)
            if eyes_hit:
                return self._answer(raw, eyes_hit[0], engine=eyes_hit[1])
        except Exception as e:
            self.log(f"eyes: {e}")

        # 6. факты живыми словами
        base = self._base_fact(norm)
        if base:
            return base if isinstance(base, dict) else self._answer(raw, base, engine="факт")

        # 6.7 Умный дом и инструменты — мгновенно, до облачного ИИ
        try:
            from . import smarthome as _home, tools as _tools
            dev = _home.handle(norm)
            if dev:
                return self._answer(raw, dev, engine="дом")
            calc = _tools.try_calc(norm)
            if calc:
                return self._answer(raw, calc, engine="калькулятор")
            note = _tools.try_notes(norm)
            if note:
                return self._answer(raw, note, engine="заметки")
            timer = _tools.try_timer(norm)
            if timer:
                return self._answer(raw, timer, engine="таймер")
            weather = _tools.try_weather(norm)
            if weather:
                return self._answer(raw, weather, engine="погода")
        except Exception as e:
            self.log(f"tools: {e}")

        # 6.5 МГНОВЕННЫЕ живые ответы — без ожидания облачного ИИ (скорость!)
        if "доброе утро" in norm:
            return self._answer(raw, self._format(self.memory.pick_fresh("morning", P.MORNING)), engine="мгновенно")
        if "добрый вечер" in norm:
            return self._answer(raw, self._format(self.memory.pick_fresh("evening", P.EVENING)), engine="мгновенно")
        if "доброй ночи" in norm:
            return self._answer(raw, self._format(self.memory.pick_fresh("night", P.NIGHT)), engine="мгновенно")
        for i, topic in enumerate(P.OFFLINE_TOPICS):
            if any(self._key_hit(norm, k) for k in topic["keys"]):
                resp = self._format(self.memory.pick_fresh(f"topic:{i}", topic["answers"]))
                return self._answer(raw, resp + self._maybe_followup(), engine="мгновенно")

        # 7. гонка ИИ-движков: кто первый ответил — тот и говорит
        online = self.is_online()
        chain = [("ollama", self._ask_ollama)]
        if online:
            chain += [("pollinations", self._ask_pollinations),
                      ("gemini", self._ask_gemini),
                      ("groq", self._ask_groq)]
        else:
            self.log("офлайн: пропускаю облачный ИИ")
        win = self._race(chain, raw, timeout=40)
        if win:
            self.log(f"ответил движок: {win[0]}")
            return self._answer(raw, win[1], engine=win[0])

        # 8. умный офлайн-диалог (не заготовка!)
        return self._answer(raw, self._offline_live(norm, raw), engine="офлайн-диалог")

    def _answer(self, raw: str, response: str, action=None, exit=False, engine="?", remember=False) -> dict:
        if not exit and not remember:
            self.memory.add_turn("assistant", response)
        return {"response": response, "action": action, "exit": exit,
                "engine": engine, "need_remember": remember}

    # ---------- гонка движков ----------

    def _race(self, chain, text: str, timeout: int = 40):
        """Опросить движки параллельно. Возвращает (engine, ответ) первого успевшего."""
        chain = [(n, fn) for n, fn in chain if fn is not None]
        if not chain:
            return None
        if len(chain) == 1:
            name, fn = chain[0]
            try:
                ans = fn(text)
                return (name, ans) if ans else None
            except Exception as e:
                self.log(f"{name} ошибка: {e}")
                return None
        with ThreadPoolExecutor(max_workers=len(chain), thread_name_prefix="yana") as ex:
            futs = {ex.submit(self._safe_ask, fn, text): name for name, fn in chain}
            try:
                for f in as_completed(futs, timeout=timeout):
                    name = futs[f]
                    try:
                        ans = f.result()
                    except Exception as e:
                        self.log(f"{name} ошибка: {e}")
                        continue
                    if ans:
                        for other in futs:
                            other.cancel()
                        return (name, ans)
            except Exception as e:
                self.log(f"гонка завершена: {e}")
        return None

    @staticmethod
    def _safe_ask(fn, text: str) -> str:
        try:
            return fn(text) or ""
        except Exception:
            return ""

    # ---------- имя и форматирование ----------

    def _extract_name(self, norm: str):
        for pat in NAME_PATTERNS:
            m = re.search(pat, norm)
            if m:
                cand = m.group(1).strip()
                if cand not in ("меня", "тебя", "яна", "ян") and len(cand) >= 2:
                    return cand
        return ""

    def _format(self, template: str) -> str:
        name = self.memory.get_name()
        s = template.replace("{name}", name)
        s = s.replace("{name_part}", f", {name}" if name else "")
        s = s.replace("{name_q}", f", {name}" if name else "")
        s = re.sub(r"\s+", " ", s)
        s = s.replace(" ,", ",").replace(", !", "!").replace(", .", ".").replace(", ?", "?")
        s = s.replace("  ", " ").strip()
        return s

    # ---------- факты ----------

    def _base_fact(self, norm: str):
        now = datetime.datetime.now()
        if "который час" in norm or norm in ("время", "времени", "часы") or "сколько время" in norm or "сколько времени" in norm:
            t = now.strftime("%H:%M")
            return self._format(self.memory.pick_fresh("time", P.TIME_VARIANTS)).replace("{t}", t)
        if "какое сегодня число" in norm or "какая сегодня дата" in norm or norm in ("дата", "сегодня", "какой сегодня день"):
            months = ["января", "февраля", "марта", "апреля", "мая", "июня",
                      "июля", "августа", "сентября", "октября", "ноября", "декабря"]
            d = f"{now.day} {months[now.month-1]} {now.year} года"
            return self._format(self.memory.pick_fresh("date", P.DATE_VARIANTS)).replace("{d_cap}", d.capitalize()).replace("{d}", d)
        if "шутк" in norm or "рассмеши" in norm or "анекдот" in norm or "смешно" in norm:
            # банк шуток без повторов — мгновенно, без ожидания ИИ
            return self._format(self.memory.pick_fresh("joke", P.JOKES))
        if "ютуб" in norm or "youtube" in norm:
            return {"response": self._format(self.memory.pick_fresh("open", P.OPEN_VARIANTS)),
                    "action": {"type": "site", "value": "https://youtube.com"},
                    "exit": False, "engine": "факт"}
        if "музык" in norm and ("включи" in norm or "открой" in norm or "хочу" in norm):
            return {"response": self._format(self.memory.pick_fresh("open", P.OPEN_VARIANTS)),
                    "action": {"type": "site", "value": "https://music.youtube.com"},
                    "exit": False, "engine": "факт"}
        if "погода" in norm and ("открой" in norm or "покажи" in norm):
            return {"response": self._format(self.memory.pick_fresh("open", P.OPEN_VARIANTS)),
                    "action": {"type": "site", "value": "https://yandex.ru/pogoda"},
                    "exit": False, "engine": "факт"}
        if "гугл" in norm or norm == "google":
            return {"response": self._format(self.memory.pick_fresh("open", P.OPEN_VARIANTS)),
                    "action": {"type": "site", "value": "https://google.com"},
                    "exit": False, "engine": "факт"}
        return None

    def _help_text(self) -> str:
        intro = self._format(self.memory.pick_fresh("help", [
            "Слушаю и помогаю! Время, погода, калькулятор, заметки, таймеры. Управляю домом: скажи «включи свет».",
            "Я умею много всего: погода, заметки, таймеры, умный дом. А ещё меня можно учить: «запомни команду».",
        ]))
        triggers = self.commands.list_triggers()
        tail = "Скажи «запомни команду», чтобы научить меня новому."
        if triggers:
            tail += " Твои команды: " + ", ".join(triggers[:8]) + "."
        return f"{intro} {tail}"

    # ---------- умный офлайн-диалог ----------

    @staticmethod
    def _key_hit(norm: str, key: str) -> bool:
        """Ключ темы — только как отдельное слово/фраза, не кусок другого слова."""
        return re.search(rf"(^|[\s,.!?;:]){re.escape(key)}([\s,.!?;:]|$)", norm) is not None

    def _offline_live(self, norm: str, raw: str) -> str:
        # время суток
        if "доброе утро" in norm:
            return self._format(self.memory.pick_fresh("morning", P.MORNING))
        if "добрый вечер" in norm or "добрый вечр" in norm:
            return self._format(self.memory.pick_fresh("evening", P.EVENING))
        if "доброй ночи" in norm or "не спится" in norm:
            return self._format(self.memory.pick_fresh("night", P.NIGHT))

        # темы (ключи ищем по границам слов, чтобы «а» не срабатывало внутри «космос»)
        for i, topic in enumerate(P.OFFLINE_TOPICS):
            if any(self._key_hit(norm, k) for k in topic["keys"]):
                resp = self._format(self.memory.pick_fresh(f"topic:{i}", topic["answers"]))
                return resp + self._maybe_followup()

        # эхо: цепляемся за значимые слова
        topic = self._keywords(raw)
        if topic:
            t = self.memory.pick_fresh("reflect", P.REFLECTIVE_TEMPLATES)
            return t.replace("{topic}", topic)

        # совсем непонятно — живо переспрашиваем
        return self._format(self.memory.pick_fresh("clarify", P.CLARIFY_VARIANTS))

    def _keywords(self, raw: str) -> str:
        words = re.findall(r"[а-яёa-z\-]{4,}", raw.lower())
        words = [w for w in words if w not in STOPWORDS]
        if not words:
            return ""
        words = sorted(set(words), key=len, reverse=True)[:2]
        return " ".join(words)

    def _maybe_followup(self) -> str:
        if random.random() < 0.3:
            fu = self._format(random.choice(P.FOLLOWUPS))
            return f" {fu}" if fu else ""
        return ""

    # ---------- бесплатный ИИ ----------

    def _system(self) -> str:
        s = P.SYSTEM_PROMPT
        name = self.memory.get_name()
        if name:
            s += f" Пользователя зовут {name}."
        facts = self.memory.get_facts()
        if facts:
            s += " Факты о пользователе, помни и используй: " + "; ".join(facts[:10]) + "."
        try:
            from . import voice_id as _vid
            spk = _vid.describe_current()
            if spk:
                s += f" Сейчас говорит: {spk}."
        except Exception:
            pass
        # сегодняшняя дата, чтобы не врала про время
        s += f" Сегодня {datetime.datetime.now().strftime('%d.%m.%Y, %H:%M')}."
        return s

    @staticmethod
    def _clean(text: str) -> str:
        text = re.sub(r"[*_#`>]+", "", text).strip()
        text = re.sub(r"\s+", " ", text).strip()  # переносы строк в пробелы (для озвучки)
        text = re.sub(r"^(яна|ассистент)\s*:\s*", "", text, flags=re.I).strip()
        # голосом длинное не говорим — режем до ~300 символов по границе предложения
        if len(text) > 300:
            cut = text[:300]
            m = list(re.finditer(r"[.!?…]", cut))
            text = cut[:m[-1].end()] if m else cut + "…"
        return text.strip()

    def _ask_ollama(self, text: str) -> str:
        url = f"{config.OLLAMA_URL}/api/chat"
        msgs = self.memory.llm_messages(self._system(), text)
        try:
            r = requests.post(url, json={"model": config.OLLAMA_MODEL, "messages": msgs,
                                         "stream": False, "options": {"temperature": 0.8}},
                              timeout=30)
            if r.status_code == 200:
                return self._clean(r.json().get("message", {}).get("content", ""))
        except requests.RequestException:
            pass
        # старый endpoint на всякий случай
        r = requests.post(f"{config.OLLAMA_URL}/api/generate", json={
            "model": config.OLLAMA_MODEL,
            "prompt": f"{self._system()}\nПользователь: {text}\nЯна:",
            "stream": False}, timeout=30)
        if r.status_code == 200:
            return self._clean(r.json().get("response", ""))
        return ""

    def _ask_pollinations(self, text: str) -> str:
        """Pollinations — бесплатно и БЕЗ ключа. История ужата для скорости."""
        hist = self.memory.get_history()
        lines = []
        for m in hist[-2:]:
            who = "Пользователь" if m["role"] == "user" else "Яна"
            lines.append(f"{who}: {m['content'][:120]}")
        lines.append(f"Пользователь: {text}")
        lines.append("Яна:")
        prompt = "\n".join(lines)
        q = urllib.parse.quote(prompt[-1000:])  # короткий промпт = быстрее ответ
        sys = urllib.parse.quote(self._system())
        url = f"https://text.pollinations.ai/{q}?model={config.POLLINATIONS_MODEL}&system={sys}&private=true"
        r = requests.get(url, timeout=config.POLLINATIONS_TIMEOUT)
        if r.status_code == 200 and r.text.strip():
            return self._clean(r.text)
        return ""

    def _ask_gemini(self, text: str) -> str:
        if not config.GEMINI_API_KEY:
            return ""
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}")
        contents = []
        for m in self.memory.get_history():
            contents.append({"role": "user" if m["role"] == "user" else "model",
                             "parts": [{"text": m["content"]}]})
        contents.append({"role": "user", "parts": [{"text": text}]})
        r = requests.post(url, json={
            "system_instruction": {"parts": [{"text": self._system()}]},
            "contents": contents[-12:],
            "generationConfig": {"maxOutputTokens": 250, "temperature": 0.8},
        }, timeout=20)
        if r.status_code == 200:
            try:
                return self._clean(r.json()["candidates"][0]["content"]["parts"][0]["text"])
            except Exception:
                return ""
        return ""

    def _ask_groq(self, text: str) -> str:
        if not config.GROQ_API_KEY:
            return ""
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                          headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
                          json={"model": config.GROQ_MODEL,
                                "messages": self.memory.llm_messages(self._system(), text)[-12:],
                                "max_tokens": 250, "temperature": 0.8},
                          timeout=20)
        if r.status_code == 200:
            try:
                return self._clean(r.json()["choices"][0]["message"]["content"])
            except Exception:
                return ""
        return ""
