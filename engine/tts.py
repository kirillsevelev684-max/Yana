# -*- coding: utf-8 -*-
"""Озвучка Яны: своя речевая библиотека + живые TTS.

Приоритет:
1. voice_library/*.mp3 — точный голос Яны (студийные фразы, работают без интернета)
2. Кэш последнего ответа (мгновенно; новый запрос стирает предыдущий)
3. Edge-TTS онлайн (бесплатно, Svetlana) — ТОЛЬКО чистый текст, без SSML!
   Ручная настройка: скорость/тон/громкость + 5-полосный эквалайзер.
4. Silero офлайн (если есть torch)
5. pyttsx3 офлайн — последний фолбэк

Принцип: просто запрос и ответ, без filler-озвучек «думаю...».
Звук ВСЕГДА внутри процесса: никаких внешних окон плеера и дублей.
Перед озвучкой текст чистится и нормализуется для чистого произношения.
"""
import hashlib
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config

CACHE_VERSION = "2"
MAX_CACHE_FILES = 80   # жёсткий лимит на всякий случай (обычно лежит 1 ответ)
MAX_CACHE_MB = 60

_PLAY_LOCK = threading.Lock()
_LAST_PLAY = {"path": "", "t": 0.0}  # защита от повтора того же файла

# Как произносить аббревиатуры и латиницу — словами, чисто
_SPOKEN_WORDS = {
    "mp3": "мп-три", "mp4": "мп-четыре", "wav": "вав",
    "wi-fi": "вайфай", "wifi": "вайфай", "api": "апи", "url": "урл",
    "http": "эйч-ти-ти-пи", "https": "эйч-ти-ти-пи-эс",
    "pc": "пэ-ка", "usb": "ю-эс-би", "ai": "аи", "gpt": "гэ-пэ-тэ",
    "llm": "эл-эл-эм", "exe": "экзэ", "app": "приложение",
    "e-mail": "имейл", "email": "имейл", "online": "онлайн", "offline": "офлайн",
    "windows": "виндовс", "mqtt": "эм-ка-тэ-тэ", "vosk": "воск",
    "silero": "силеро", "edge": "эдж", "groq": "грок", "ollama": "оллама",
    "github": "гитхаб", "python": "питон", "json": "джейсон", "vosk-model": "воск-модель",
}

# Точные тексты студийных фраз -> файл в voice_library
LIBRARY_TEXTS = {
    "здравствуйте! яна нейронова на связи. чем помочь?": "greeting.mp3",
    "слушаю вас.": "listening.mp3",
    "слушаю вас": "listening.mp3",
    "готово! команда выполнена.": "done.mp3",
    "извините, я не совсем поняла. повторите, пожалуйста, чуть медленнее.": "not_understood.mp3",
    "нет соединения с интернетом. работаю в офлайн режиме.": "offline.mp3",
    "до свидания! я всегда рядом. просто позовите: яна.": "goodbye.mp3",
    "что мне запомнить? назовите фразу для команды и то, что я должна ответить или открыть.": "remember.mp3",
    "запомнила! теперь я знаю эту команду даже без интернета.": "remembered_ok.mp3",
    # расширенная библиотека (точный голос Яны)
    "о, привет! я как раз скучала. чем помочь?": "greet2.mp3",
    "привет-привет! какие планы?": "greet4.mp3",
    "отлично! все системы в норме, настроение боевое. а ты как?": "howru1.mp3",
    "я яна нейронова, твой голосовой помощник. живу чтобы помогать тебе!": "whoami1.mp3",
    "обращайся в любой момент!": "thanks2.mp3",
    "открываю, секундочку!": "open1.mp3",
    "почему программисты путают хэллоуин и рождество? потому что oct 31 равно dec 25!": "joke1.mp3",
    "заходит нейросеть в бар... а бармена нет — его автоматизировали.": "joke_bar.mp3",
    "мой любимый анекдот про udp? не факт, что ты его услышишь!": "joke_udp.mp3",
    "скучать — запрещено! давай я расскажу шутку? или открою тебе музыку?": "bored1.mp3",
    # filler'ы (сейчас отключены: просто запрос и ответ)
    "секундочку, думаю...": "think1.mp3",
    "сейчас посмотрю...": "think2.mp3",
    "хм, интересный вопрос...": "think3.mp3",
}

LIBRARY_KEYS = {
    "greeting": "greeting.mp3",
    "listening": "listening.mp3",
    "done": "done.mp3",
    "not_understood": "not_understood.mp3",
    "offline": "offline.mp3",
    "goodbye": "goodbye.mp3",
    "remember": "remember.mp3",
    "remembered_ok": "remembered_ok.mp3",
}

THINK_FILLERS = ["think1.mp3", "think2.mp3", "think3.mp3"]


class YanaTTS:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._pygame_init = False
        self._pygame_device = None
        self._pyttsx3_engine = None
        self._silero = None  # модель Silero (ленивая загрузка)
        self._last_think = ""
        self._ensure_cache_version()

    # ---------- публичное ----------

    def speak(self, text: str) -> None:
        """Сказать текст голосом Яны. Печатает как есть, озвучивает чистый текст."""
        text = (text or "").strip()
        if not text:
            return
        print(f"\n🗣️ Яна: {text}")
        if not self.enabled:
            return

        # 1. точная студийная фраза целиком (мгновенно)
        lib = self._library_for_text(text)
        if lib and lib.exists():
            self._engine_log("студия Яны")
            self._play(lib)
            return

        say = self._clean_for_speech(text)
        if not say:
            return
        say = self._normalize_spoken(say)
        if not say:
            return

        # новый запрос — предыдущий стираем сразу: в кэше только последний ответ
        chunks = self._split_sentences(say)
        try:
            self.prune_cache(keep={str(self._cache_path(c)) for c in chunks})
        except Exception:
            pass

        # 2. длинный текст — по фразам (параллельный синтез + кэш фраз)
        if len(chunks) > 1:
            if self._speak_chunks(chunks):
                return

        # 3. одна фраза: кэш (мгновенно) -> edge-tts -> silero -> системный
        cached = self._cache_path(say)
        if cached.exists():
            self._engine_log("кэш")
            self._play(cached)
            return
        if self._try_edge_tts(say, cached):
            self._engine_log("edge-tts (живой)")
            self._play(cached)
            return
        wav_cached = cached.with_suffix(".wav")
        if self._try_silero(say, wav_cached):
            self._engine_log("silero (офлайн)")
            self._play(wav_cached)
            return
        self._engine_log("системный")
        self._try_pyttsx3(say)

    def speak_key(self, key: str, fallback_text: str = "") -> None:
        """Сказать базовую фразу по ключу (greeting, goodbye, done ...)."""
        fname = LIBRARY_KEYS.get(key)
        if fname:
            f = config.VOICE_LIBRARY_DIR / fname
            if f.exists():
                print(f"\n🗣️ Яна: [{key}]")
                if self.enabled:
                    self._engine_log("студия Яны")
                    self._play(f)
                return
        if fallback_text:
            self.speak(fallback_text)

    def speak_think(self) -> None:
        """Короткое «думаю...» (сейчас не вызывается: просто запрос и ответ)."""
        if not self.enabled:
            return
        avail = [f for f in THINK_FILLERS if (config.VOICE_LIBRARY_DIR / f).exists()]
        if not avail:
            return
        opts = [f for f in avail if f != self._last_think] or avail
        pick = random.choice(opts)
        self._last_think = pick
        print("💭 (думаю...)")
        self._play(config.VOICE_LIBRARY_DIR / pick)

    @staticmethod
    def _engine_log(name: str):
        print(f"[🔊 голос: {name}]")

    # ---------- чистка текста для речи ----------

    @staticmethod
    def _clean_for_speech(text: str) -> str:
        """Убрать всё, что ужасно звучит вслух: ссылки, markdown, таблицы, эмодзи."""
        s = text or ""
        s = re.sub(r"https?://\S+|www\.\S+", "", s)          # ссылки не читаем
        s = re.sub(r"[*_#`>|~^]+", "", s)                     # markdown и таблицы
        s = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE00-\uFEFF]+", "", s)  # эмодзи
        s = s.replace("&", " и ")
        s = re.sub(r"[<>\[\]]+", "", s)
        s = s.replace("(", ", ").replace(")", ", ")
        s = re.sub(r"\s+", " ", s).strip(" ,")
        s = re.sub(r"\s+([,.!?;:])", r"\1", s)
        return s.strip()

    @staticmethod
    def _normalize_spoken(text: str) -> str:
        """Чистое произношение: аббревиатуры — словами, знаки — паузой."""
        s = f" {text or ''} "
        for k, v in _SPOKEN_WORDS.items():
            s = re.sub(rf"(?<![а-яёa-z]){re.escape(k)}(?![а-яёa-z])", v, s,
                       flags=re.IGNORECASE)
        s = s.replace("—", ", ").replace("–", ", ").replace("…", ".")
        s = s.replace("/", " или ").replace("·", ", ").replace("•", ", ")
        s = s.replace("+", " плюс ").replace("=", " равно ").replace("%", " процентов ")
        s = s.replace("«", "").replace("»", "").replace(""", "").replace(""", "")
        s = re.sub(r"\s+", " ", s).strip(" ,")
        s = re.sub(r"\s+([,.!?;:])", r"\1", s)
        return s.strip()

    # ---------- фразы ----------

    @staticmethod
    def _split_sentences(text: str, limit: int = 240) -> list[str]:
        """Разбить текст на фразы для живой озвучки без пауз."""
        parts = re.split(r"(?<=[.!?…])\s+", text.strip())
        chunks, cur = [], ""
        for p in parts:
            p = p.strip()
            if not p:
                continue
            if len(cur) + len(p) + 1 <= limit:
                cur = (cur + " " + p).strip()
            else:
                if cur:
                    chunks.append(cur)
                # очень длинное предложение — режем по запятым
                while len(p) > limit:
                    cut = p.rfind(",", 0, limit)
                    cut = cut if cut > 40 else limit
                    chunks.append(p[:cut].strip())
                    p = p[cut:].lstrip(", ").strip()
                cur = p
        if cur:
            chunks.append(cur)
        return chunks or [text]

    def _synth_chunks_all(self, chunks: list[str]):
        """Параллельный синтез всех фраз (кэш + edge). Возвращает пути или None."""
        def one(ch: str):
            p = self._cache_path(ch)
            if p.exists():
                return p
            if self._try_edge_tts(ch, p):
                return p
            return None

        paths: list = [None] * len(chunks)
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(one, ch): i for i, ch in enumerate(chunks)}
            for f in futs:
                try:
                    paths[futs[f]] = f.result()
                except Exception:
                    paths[futs[f]] = None
        return paths if all(paths) else None

    def _speak_chunks(self, chunks: list[str]) -> bool:
        """Озвучить фразы: параллельный синтез, играть по порядку."""
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            return False
        paths = self._synth_chunks_all(chunks)
        if not paths:
            return False
        self._engine_log("edge-tts (живой, по фразам)")
        for p in paths:
            self._play(p)
        return True

    # ---------- библиотека и кэш ----------

    def _ensure_cache_version(self):
        """Старт: стереть отравленный кэш v1, убрать обрезки, подрезать размер."""
        try:
            config.VOICE_CACHE_DIR.mkdir(exist_ok=True)
            vf = config.VOICE_CACHE_DIR / ".v"
            if vf.exists() and vf.read_text().strip() == CACHE_VERSION:
                self.prune_cache()
                return
            n = 0
            for p in config.VOICE_CACHE_DIR.glob("*"):
                try:
                    if p.is_file() and p.suffix.lower() in (".mp3", ".wav"):
                        p.unlink()
                        n += 1
                except Exception:
                    pass
            vf.write_text(CACHE_VERSION)
            if n:
                print(f"[TTS] Старый кэш голоса очищен ({n} файлов), пересоздам заново.")
        except Exception:
            pass
        self.prune_cache()

    @staticmethod
    def prune_cache(keep: set | None = None) -> None:
        """Кэш: только последний ответ + жёсткий лимит 80 файлов / 60 МБ (LRU).

        keep — пути файлов текущего ответа: всё остальное стирается сразу.
        """
        try:
            for tmp in config.VOICE_CACHE_DIR.glob("*.tmp.mp3"):
                try:
                    tmp.unlink()
                except Exception:
                    pass
            files = [p for p in config.VOICE_CACHE_DIR.glob("*")
                     if p.is_file() and p.suffix.lower() in (".mp3", ".wav")]
            with _PLAY_LOCK:  # пока что-то играет — не трогаем его файл
                if keep:
                    gone = 0
                    for p in files:
                        if str(p) not in keep:
                            try:
                                p.unlink()
                                gone += 1
                            except Exception:
                                pass
                    if gone:
                        print(f"[TTS] Старый кэш стёрт ({gone} файлов) — храню только последний ответ.")
                    files = [p for p in files if p.exists()]
                files.sort(key=lambda p: p.stat().st_mtime)
                total = sum(p.stat().st_size for p in files)
                removed = 0
                while (len(files) > MAX_CACHE_FILES or total > MAX_CACHE_MB * 1048576) and files:
                    p = files.pop(0)
                    try:
                        total -= p.stat().st_size
                        p.unlink()
                        removed += 1
                    except Exception:
                        pass
                if removed:
                    print(f"[TTS] Кэш подрезан: удалено {removed} старых файлов.")
        except Exception:
            pass

    def _library_for_text(self, text: str):
        key = " ".join(text.lower().strip().split())
        fname = LIBRARY_TEXTS.get(key)
        if not fname:
            return None
        p = (config.VOICE_LIBRARY_DIR / fname).resolve()
        return p

    @staticmethod
    def _eq_active() -> bool:
        try:
            return bool(config.EQ_SUB or config.EQ_LOW or config.EQ_MID
                        or config.EQ_PRES or config.EQ_HIGH)
        except Exception:
            return False

    def _cache_path(self, text: str) -> Path:
        # ключ зависит от настроек голоса и EQ — сменил тон, получил новый синтез
        tag = (f"{text.strip().lower()}|{config.EDGE_VOICE}|"
               f"{config.TTS_RATE}|{config.TTS_PITCH}|{config.TTS_VOLUME}|"
               f"{config.EQ_SUB}|{config.EQ_LOW}|{config.EQ_MID}|"
               f"{config.EQ_PRES}|{config.EQ_HIGH}")
        h = hashlib.md5(tag.encode("utf-8")).hexdigest()[:16]
        safe = "".join(c if c.isalnum() else "_" for c in text[:24]).strip("_") or "phrase"
        ext = ".wav" if self._eq_active() else ".mp3"
        return config.VOICE_CACHE_DIR / f"{h}_{safe}{ext}"

    # ---------- воспроизведение ----------

    def _ensure_pygame(self) -> bool:
        want = config.AUDIO_OUT or None
        if self._pygame_init and self._pygame_device == want:
            return True
        try:
            import pygame
            if pygame.mixer.get_init() and self._pygame_device != want:
                try:  # сменили колонки в настройках — переоткрываем на новых
                    pygame.mixer.quit()
                except Exception:
                    pass
            if not pygame.mixer.get_init():
                # увеличенный буфер — убирает запинки и треск
                pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=2048)
                try:
                    pygame.mixer.init(devicename=want)
                except Exception:
                    if want:
                        print(f"[TTS] Колонки «{want}» недоступны, играю в системные.")
                    pygame.mixer.init()
            self._pygame_device = want
            self._pygame_init = True
            return True
        except Exception as e:
            print(f"[TTS] pygame недоступен ({e}).")
            return False

    def _play(self, path: Path) -> None:
        """Проиграть файл внутри процесса. Без внешних окон, без дублей."""
        import sys
        key = str(path)
        with _PLAY_LOCK:
            now = time.time()
            if _LAST_PLAY["path"] == key and now - _LAST_PLAY["t"] < 1.5:
                return  # тот же файл только что играл — не дублируем
            _LAST_PLAY["path"] = key
            _LAST_PLAY["t"] = now
            if self._ensure_pygame():
                try:
                    import pygame
                    try:
                        pygame.mixer.music.stop()  # предыдущий звук — стоп, новый — сразу
                    except Exception:
                        pass
                    pygame.mixer.music.load(key)
                    pygame.mixer.music.play()
                    t0 = time.time()
                    while time.time() - t0 < 60:
                        try:
                            if not pygame.mixer.music.get_busy():
                                break
                        except Exception:
                            break
                        time.sleep(0.05)
                    return
                except Exception as e:
                    print(f"[TTS] pygame не смог проиграть {path.name}: {e}")
            # запасной вариант БЕЗ внешних окон: winsound для wav на Windows
            if sys.platform.startswith("win") and path.suffix.lower() == ".wav":
                try:
                    import winsound
                    winsound.PlaySound(key, winsound.SND_FILENAME)
                    return
                except Exception as e:
                    print(f"[TTS] winsound не смог: {e}")
            print(f"[TTS] Не смогла проиграть звук ({path.name}) — только текст.")

    # ---------- движки ----------

    def _try_edge_tts(self, text: str, out_path: Path) -> bool:
        """Только чистый текст! SSML библиотека экранирует и читает теги вслух."""
        try:
            import asyncio
            import edge_tts
        except ImportError:
            return False
        # с активным EQ синтез идёт во временный mp3, потом — обработка в wav
        mp3_path = out_path.with_suffix(".tmp.mp3") if out_path.suffix == ".wav" else out_path
        try:
            async def _run():
                # ручная настройка голоса из config (скорость/тон/громкость)
                tts = edge_tts.Communicate(
                    text, voice=config.EDGE_VOICE,
                    rate=f"{config.TTS_RATE:+d}%",
                    pitch=f"{config.TTS_PITCH:+d}Hz",
                    volume=f"{config.TTS_VOLUME:+d}%",
                )
                await tts.save(str(mp3_path))
            asyncio.run(_run())
            if not (mp3_path.exists() and mp3_path.stat().st_size > 0):
                return False
            if mp3_path != out_path:
                if not self._eq_convert(mp3_path, out_path):
                    return False
                try:
                    mp3_path.unlink()
                except Exception:
                    pass
            self.prune_cache()
            return True
        except Exception as e:
            print(f"[TTS] Edge-TTS недоступен ({e}).")
        for p in (mp3_path, out_path):
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass
        return False

    def _eq_convert(self, mp3_path: Path, wav_path: Path) -> bool:
        """Декодировать mp3, применить 5-полосный эквалайзер, сохранить wav."""
        try:
            import miniaudio
            import numpy as np
        except ImportError:
            print("[TTS] Для эквалайзера нужно: pip install miniaudio numpy")
            return False
        try:
            dec = miniaudio.decode_file(str(mp3_path), output_format=miniaudio.SampleFormat.SIGNED16)
            pcm = np.frombuffer(dec.samples, dtype=np.int16).astype(np.float32)
            rate = getattr(dec, "sample_rate", 0) or 24000
            nch = getattr(dec, "nchannels", 0) or 1
            if nch > 1:
                pcm = pcm.reshape(-1, nch).mean(axis=1)
            # 5 полос через FFT: саб / низкие / средние / присутствие / высокие
            spec = np.fft.rfft(pcm)
            freqs = np.fft.rfftfreq(len(pcm), 1 / rate)
            g = np.ones_like(freqs)
            g[freqs < 120] *= 10 ** (config.EQ_SUB / 20)
            g[(freqs >= 120) & (freqs < 300)] *= 10 ** (config.EQ_LOW / 20)
            g[(freqs >= 300) & (freqs < 3000)] *= 10 ** (config.EQ_MID / 20)
            g[(freqs >= 3000) & (freqs < 6000)] *= 10 ** (config.EQ_PRES / 20)
            g[freqs >= 6000] *= 10 ** (config.EQ_HIGH / 20)
            out = np.fft.irfft(spec * g, n=len(pcm))
            peak = float(np.abs(out).max()) if len(out) else 0.0
            if peak > 32767:  # защита от клиппинга
                out = out * (32767 / peak)
            import wave
            with wave.open(str(wav_path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(out.astype(np.int16).tobytes())
            print(f"[TTS] EQ {config.EQ_SUB:+d}/{config.EQ_LOW:+d}/{config.EQ_MID:+d}/"
                  f"{config.EQ_PRES:+d}/{config.EQ_HIGH:+d} дБ")
            return wav_path.exists()
        except Exception as e:
            print(f"[TTS] Эквалайзер не сработал ({e}).")
            return False

    def _try_silero(self, text: str, out_wav: Path) -> bool:
        try:
            import torch
        except ImportError:
            return False
        try:
            if self._silero is None:
                model, _ = torch.hub.load(
                    repo_or_dir="snakers4/silero-models",
                    model="silero_tts",
                    language="ru",
                    speaker="v3_1_ru",
                    trust_repo=True,
                )
                model.to(torch.device("cpu"))
                self._silero = model
            model = self._silero
            model.save_wav(
                text=text,
                speaker=config.SILERO_SPEAKER,
                sample_rate=config.SILERO_SAMPLE_RATE,
                audio_path=str(out_wav),
            )
            ok = out_wav.exists()
            if ok:
                self.prune_cache()
            return ok
        except Exception as e:
            print(f"[TTS] Silero недоступен ({e}).")
            return False

    def _try_pyttsx3(self, text: str) -> bool:
        try:
            import pyttsx3
        except ImportError:
            print("[TTS] Нет ни одного голосового движка. Установи: pip install edge-tts pyttsx3 pygame")
            return False
        try:
            if self._pyttsx3_engine is None:
                eng = pyttsx3.init()
                eng.setProperty("rate", 185)
                eng.setProperty("volume", 1.0)
                # пробуем выбрать женский русский голос
                try:
                    for v in eng.getProperty("voices"):
                        name = (v.name or "").lower()
                        if any(k in name for k in ["irina", "elena", "svetlana", "tatiana", "milena", "ksen", "xenia", "anna", "alena"]):
                            eng.setProperty("voice", v.id)
                            break
                except Exception:
                    pass
                self._pyttsx3_engine = eng
            self._pyttsx3_engine.say(text)
            self._pyttsx3_engine.runAndWait()
            return True
        except Exception as e:
            print(f"[TTS] pyttsx3 ошибка: {e}")
            return False
