# -*- coding: utf-8 -*-
"""Распознавание речи: чувствительный микрофон + антишум-гейт + Vosk офлайн / Google."""
import io
import json
import wave

from . import audio_clean, config

try:
    import speech_recognition as sr
    HAS_SR = True
except ImportError:
    sr = None  # type: ignore
    HAS_SR = False

_vosk_model = None


def _load_vosk():
    global _vosk_model
    if _vosk_model is not None:
        return _vosk_model
    try:
        from vosk import Model
        if config.VOSK_MODEL_DIR.exists():
            print(f"[STT] Загружаю офлайн-модель Vosk: {config.VOSK_MODEL_DIR.name} ...")
            _vosk_model = Model(str(config.VOSK_MODEL_DIR))
        else:
            print("[STT] Vosk-модель не найдена, будет онлайн-распознавание Google (бесплатно).")
            print(f"[STT] Скачай для офлайна: https://alphaceps.com/vosk/models → "
                  f"vosk-model-small-ru-0.22 → распакуй в {config.VOSK_MODEL_DIR}")
    except ImportError:
        print("[STT] Библиотека vosk не установлена (pip install vosk).")
    except Exception as e:
        print(f"[STT] Vosk не загрузился: {e}")
    return _vosk_model


class YanaSTT:
    def __init__(self):
        self.recognizer = sr.Recognizer() if HAS_SR else None
        if self.recognizer:
            # чувствительный микрофон (настройки в config.py / .env)
            self.recognizer.energy_threshold = config.MIC_ENERGY
            self.recognizer.dynamic_energy_threshold = True
            self.recognizer.pause_threshold = config.MIC_PAUSE
            self.recognizer.phrase_threshold = config.MIC_PHRASE
        self.vosk = _load_vosk()
        self.last_wav: bytes = b""  # сырая запись последнего listen() для знакомства

    def reload_vosk(self):
        """Перечитать Vosk-модель (нужно, если модель скачалась уже после старта)."""
        self.vosk = _load_vosk()

    def has_mic(self) -> bool:
        if not HAS_SR:
            return False
        try:
            import pyaudio  # noqa: F401
            return True
        except ImportError:
            try:
                with sr.Microphone():
                    return True
            except Exception:
                return False

    def listen(self, timeout: int = 6, phrase_limit: int = 12):
        """Слушать микрофон. Возвращает текст или None (тишина/шум тоже дают None)."""
        self.last_wav = b""
        if not self.recognizer:
            return None
        try:
            # живые настройки без рестарта
            self.recognizer.energy_threshold = config.MIC_ENERGY
            self.recognizer.pause_threshold = config.MIC_PAUSE
            self.recognizer.phrase_threshold = config.MIC_PHRASE
            with sr.Microphone(device_index=config.MIC_INDEX) as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
                print("🎤 Слушаю... (говорите)")
                audio = self.recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
        except Exception as e:
            print(f"[STT] Микрофон недоступен: {e}")
            return None

        wav = audio.get_wav_data(convert_rate=16000, convert_width=2)
        self.last_wav = wav

        # антишум-гейт: только речь пользователя, гул/щелчки отбрасываем
        try:
            ratio = audio_clean.speech_ratio(wav, aggr=config.VAD_AGGR)
        except Exception:
            ratio = 1.0
        if ratio < config.NOISE_GATE:
            print("[STT] Слышу только шум — пропускаю.")
            return None

        # фон: образец голоса в архив + кто говорит (жен/муж/детский)
        try:
            from . import voice_id as _vid
            _vid.observe(wav)
        except Exception:
            pass

        # 1) пробуем офлайн Vosk
        text = self._recognize_vosk(wav)
        if text:
            return text
        # 2) бесплатный онлайн Google (запись сначала чистим от шума)
        try:
            clean = audio_clean.denoise_wav(wav)
            with sr.AudioFile(io.BytesIO(clean)) as src:
                audio2 = self.recognizer.record(src)
            return self.recognizer.recognize_google(audio2, language=config.STT_LANGUAGE)
        except Exception:
            return None

    def _recognize_vosk(self, wav_bytes: bytes) -> str:
        if not self.vosk:
            return ""
        try:
            from vosk import KaldiRecognizer
            wf = wave.open(io.BytesIO(wav_bytes))
            rec = KaldiRecognizer(self.vosk, wf.getframerate())
            rec.SetWords(True)
            data = wf.readframes(wf.getnframes())
            rec.AcceptWaveform(data)
            res = json.loads(rec.FinalResult())
            return (res.get("text") or "").strip()
        except Exception as e:
            print(f"[STT] Vosk ошибка: {e}")
            return ""

    @staticmethod
    def ask_text(prompt: str = "Вы: ") -> str:
        try:
            return input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            return ""
