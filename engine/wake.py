# -*- coding: utf-8 -*-
"""Режим «всегда слушает»: строго чёткое «ЯНА» в любом месте фразы — и сразу ответ.

- «Яна, какое время?» или «Сколько время, Яна?» → команда выполняется СРАЗУ.
- Только «Яна» → коротко «Слушаю!» и ждёт команду.
- «Ян», «обезьяна» и прочее — НЕ будят (строгое слово).
- Контекстный буфер: если Vosk разрезал фразу на финалы («сейчас бы пиццу» + «яна»),
  недавний разговор подставляется к имени — команда не теряется.
- VAD-гейт: реагирует только на живую речь, гул/щелчки/шипение игнорирует.
Скорость: модель слуха грузится один раз, конец фразы ловится по стабилизации partial.
"""
import json
import re
import threading
import time
import urllib.request
import zipfile
from collections import deque

from . import config
from .audio_clean import VadGate

VOSK_URL = "https://alphaceps.com/vosk/models/vosk-model-small-ru-0.22.zip"
WAKE_SET = {"яна"}  # СТРОГО только «яна», без вариантов

# partial стабилен столько — считаем, что человек договорил (не ждём долгий финал)
PARTIAL_STABLE_SEC = 0.7
# сколько дослушивать финал после стабилизации
TAIL_SEC = 1.2
# сколько секунд помним разговор для склейки с именем
CONTEXT_SEC = 6.0


def is_wake(text: str) -> bool:
    words = re.findall(r"[а-яёa-z]+", (text or "").lower())
    return any(w in WAKE_SET for w in words)


def strip_wake(text: str) -> str:
    """Убрать имя из фразы с любого места. 'Сколько время, Яна?' -> 'сколько время'."""
    s = (text or "").lower()
    s = re.sub(r"[?!.,;:…\-]+", " ", s)
    words = [w for w in s.split() if w not in WAKE_SET]
    s = re.sub(r"\s+", " ", " ".join(words)).strip()
    return s if len(s) >= 2 else ""


def ensure_vosk_model() -> bool:
    """Скачать офлайн-модель слуха, если её нет. Возвращает True если модель есть."""
    if config.VOSK_MODEL_DIR.exists():
        return True
    print("[WAKE] Качаю офлайн-модель слуха (~50 МБ, один раз)...")
    try:
        config.VOSK_MODEL_DIR.parent.mkdir(parents=True, exist_ok=True)
        zp = config.VOSK_MODEL_DIR.parent / "vosk.zip"
        last_pct = -1

        def hook(block, size, total):
            nonlocal last_pct
            if total and total > 0:
                pct = block * size * 100 // total
                if pct != last_pct and pct % 5 == 0:
                    last_pct = pct
                    print(f"\r[WAKE] {min(pct, 100)}%", end="", flush=True)

        urllib.request.urlretrieve(VOSK_URL, zp, reporthook=hook)
        print()
        print("[WAKE] Распаковываю...")
        with zipfile.ZipFile(zp) as z:
            z.extractall(config.VOSK_MODEL_DIR.parent)
        try:
            zp.unlink()
        except Exception:
            pass
        ok = config.VOSK_MODEL_DIR.exists()
        print("[WAKE] Модель готова ✔" if ok else "[WAKE] Что-то пошло не так с моделью.")
        return ok
    except Exception as e:
        print(f"[WAKE] Не скачалось ({e}), буду слушать через Google (нужен интернет).")
        return False


class WakeListener:
    """Ожидание чёткого «Яна» вместе с командой. Модель грузится один раз."""

    def __init__(self, use_vosk: bool = True):
        try:
            import pyaudio  # noqa: F401
        except ImportError:
            raise RuntimeError("нет микрофона в Python: выполни  pip install pyaudio")
        self.use_vosk = bool(use_vosk) and config.VOSK_MODEL_DIR.exists()
        try:
            from vosk import Model  # noqa: F401
            has_vosk_lib = True
        except ImportError:
            has_vosk_lib = False
        if self.use_vosk and not has_vosk_lib:
            print("[WAKE] Нет библиотеки vosk (pip install vosk) — слушаю через Google.")
            self.use_vosk = False
        self.backend = "vosk" if self.use_vosk else "google"
        self._event = threading.Event()
        self._stop = threading.Event()
        self._heard = ""
        self._recent = deque(maxlen=3)  # последние финалы (время, текст) для склейки
        self._model = None
        self._rec = None
        if self.backend == "vosk":
            from vosk import Model
            print("[WAKE] Гружу слух (один раз)...")
            self._model = Model(str(config.VOSK_MODEL_DIR))
            print("[WAKE] Слушаю окружение через Vosk (офлайн), жду чёткое «Яна»...")
        else:
            try:
                import speech_recognition as sr
                self._rec = sr.Recognizer()
                self._rec.energy_threshold = config.MIC_ENERGY
                self._rec.dynamic_energy_threshold = True
                self._rec.pause_threshold = config.MIC_PAUSE
                self._rec.phrase_threshold = config.MIC_PHRASE
            except ImportError:
                raise RuntimeError("нет SpeechRecognition: выполни  pip install SpeechRecognition")
            print("[WAKE] Слушаю окружение через Google (онлайн), жду чёткое «Яна»...")

    def stop(self):
        self._stop.set()

    def wait_for_wake(self) -> str:
        """Ждать «Яна» (можно сразу с командой). Возвращает услышанную фразу целиком."""
        self._event.clear()
        self._stop.clear()
        self._heard = ""
        if self.backend == "vosk":
            self._wait_vosk()
        else:
            self._wait_sr()
        return self._heard

    def _hit(self, text: str):
        if not strip_wake(text):
            # имя без команды — подставить недавний разговор (Vosk мог разрезать фразу)
            now = time.time()
            ctx = " ".join(t for ts, t in self._recent if now - ts < CONTEXT_SEC and not is_wake(t))
            if ctx:
                text = f"{ctx} {text}"
        print(f"👂 Услышала: «{text}»")
        self._heard = text
        self._event.set()

    # ---------- Vosk: быстрый офлайн ----------

    def _collect_tail(self, rec, stream, partial: str, gate: VadGate) -> str:
        """Partial с именем стабилен — быстро забрать финал (до TAIL_SEC)."""
        end = time.time() + TAIL_SEC
        while time.time() < end and not self._stop.is_set():
            data = stream.read(4000, exception_on_overflow=False)
            gate.push_pcm(data)
            if rec.AcceptWaveform(data):
                t = json.loads(rec.Result()).get("text", "")
                return t or partial
        return partial

    def _wait_vosk(self):
        from vosk import KaldiRecognizer
        import pyaudio
        rec = KaldiRecognizer(self._model, 16000)
        rec.SetWords(False)
        pa = pyaudio.PyAudio()
        stream = pa.open(format=pyaudio.paInt16, channels=1, rate=16000,
                         input=True, frames_per_buffer=4000,
                         input_device_index=config.MIC_INDEX)
        stream.start_stream()
        gate = VadGate(aggr=config.VAD_AGGR)
        last_p, stable_since = "", time.time()
        try:
            while not self._event.is_set() and not self._stop.is_set():
                data = stream.read(4000, exception_on_overflow=False)
                gate.push_pcm(data)
                if rec.AcceptWaveform(data):
                    t = json.loads(rec.Result()).get("text", "")
                    if t:
                        self._recent.append((time.time(), t))
                    if t and is_wake(t):
                        if gate.speech_recent():
                            self._hit(t)  # финал с именем — отдаём сразу
                            break
                        print("[WAKE] Похоже на шум, игнорирую.")
                    last_p, stable_since = "", time.time()
                    continue
                p = json.loads(rec.PartialResult()).get("partial", "")
                if not p or not is_wake(p):
                    if p != last_p:
                        last_p, stable_since = p, time.time()
                    continue
                if p != last_p:
                    last_p, stable_since = p, time.time()
                    continue
                # partial с «яной» стабилен — человек договорил
                if time.time() - stable_since >= PARTIAL_STABLE_SEC:
                    if not gate.speech_recent():
                        print("[WAKE] Похоже на шум, игнорирую.")
                        last_p, stable_since = "", time.time()
                        continue
                    if strip_wake(p):
                        # команда уже в partial — отдаём СРАЗУ, без дослушивания
                        self._hit(p)
                    else:
                        # только имя — даём шанс договорить (до TAIL_SEC)
                        self._hit(self._collect_tail(rec, stream, p, gate))
                    break
        finally:
            try:
                stream.stop_stream()
                stream.close()
                pa.terminate()
            except Exception:
                pass

    # ---------- Google: запасной онлайн ----------

    def _sr_callback(self, recognizer, audio):
        if self._event.is_set():
            return
        try:
            text = recognizer.recognize_google(audio, language="ru-RU")
            if not text:
                return
            self._recent.append((time.time(), text))  # контекст для склейки, как у Vosk
            if is_wake(text):
                self._hit(text)  # фраза уже целиком — команда внутри
        except Exception:
            pass

    def _wait_sr(self):
        import speech_recognition as sr
        # живые настройки без рестарта
        self._rec.energy_threshold = config.MIC_ENERGY
        self._rec.pause_threshold = config.MIC_PAUSE
        self._rec.phrase_threshold = config.MIC_PHRASE
        mic = sr.Microphone(device_index=config.MIC_INDEX)
        with mic as source:
            self._rec.adjust_for_ambient_noise(source, duration=0.5)
        stopper = self._rec.listen_in_background(mic, self._sr_callback, phrase_time_limit=6)
        try:
            while not self._event.is_set() and not self._stop.is_set():
                time.sleep(0.1)
        finally:
            try:
                stopper(wait_for_stop=False)
            except Exception:
                pass
