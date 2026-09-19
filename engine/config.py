# -*- coding: utf-8 -*-
"""Настройки Яны Нейроновой. Только бесплатные API и офлайн-режим.

Два корня:
- BUNDLE_DIR — что зашито ВНУТРИ .exe (только чтение): голос, картинки.
- DATA_DIR — что лежит РЯДОМ с .exe (чтение+запись): память, команды, кэш.
В обычном запуске из исходников оба указывают на папку проекта.
"""
import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    DATA_DIR = Path(sys.executable).resolve().parent
else:
    BUNDLE_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = BUNDLE_DIR
BASE_DIR = DATA_DIR  # совместимость

NAME = "Яна Нейронова"
SHORT_NAME = "Яна"
VERSION = "8.0"

# Слова для активации (регистр не важен)
WAKE_WORDS = ["яна", "ян", "нейронова", "яночка"]

# --- Пути: чтение из бандла, запись рядом с программой ---
VOICE_LIBRARY_DIR = BUNDLE_DIR / "voice_library"   # точный голос Яны (mp3 из студии)
VOICE_CACHE_DIR = DATA_DIR / "voice_cache"          # последний озвученный ответ (старое стирается само)
COMMANDS_FILE = DATA_DIR / "commands.json"
MEMORY_FILE = DATA_DIR / "memory.json"              # имя пользователя + история + антиповторы
VOSK_MODEL_DIR = DATA_DIR / "model" / "vosk-model-small-ru-0.22"
SETTINGS_FILE = DATA_DIR / "settings.json"             # настройки (перекрываются .env)
SAMPLES_DIR = DATA_DIR / "voice_samples"                # образцы голосов пользователей
USERS_FILE = SAMPLES_DIR / "users.json"                 # голосовые профили
NOTES_FILE = DATA_DIR / "notes.json"
DEVICES_FILE = DATA_DIR / "devices.json"

VOICE_CACHE_DIR.mkdir(exist_ok=True)
SAMPLES_DIR.mkdir(exist_ok=True)
(SAMPLES_DIR / "pending").mkdir(exist_ok=True)


def _load_settings_file():
    """Настройки из settings.json -> значения по умолчанию для переменных."""
    try:
        import json
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, (str, int, float)) and k.isupper():
                        os.environ.setdefault(k, str(v))
    except Exception:
        pass


_load_settings_file()

# --- TTS ---
EDGE_VOICE = os.getenv("YANA_EDGE_VOICE", "ru-RU-SvetlanaNeural")
SILERO_SPEAKER = os.getenv("YANA_SILERO_SPEAKER", "xenia")
SILERO_SAMPLE_RATE = 48000

# --- STT ---
STT_LANGUAGE = "ru-RU"

# --- Микрофон: чувствительность и антишум ---
MIC_ENERGY = int(os.getenv("YANA_MIC_ENERGY", "150"))
MIC_PAUSE = float(os.getenv("YANA_MIC_PAUSE", "1.0"))
MIC_PHRASE = float(os.getenv("YANA_MIC_PHRASE", "0.2"))
NOISE_GATE = float(os.getenv("YANA_NOISE_GATE", "0.25"))
VAD_AGGR = int(os.getenv("YANA_VAD", "2"))

# --- Аудиоустройства (пусто = системные) ---
def _mic_index():
    v = os.getenv("YANA_MIC_INDEX", "").strip()
    try:
        return int(v) if v else None
    except ValueError:
        return None
MIC_INDEX = _mic_index()
AUDIO_OUT = os.getenv("YANA_AUDIO_OUT", "")

# --- Голос Яны: ручная настройка живого синтеза ---
TTS_RATE = int(os.getenv("YANA_TTS_RATE", "5"))
TTS_PITCH = int(os.getenv("YANA_TTS_PITCH", "0"))
TTS_VOLUME = int(os.getenv("YANA_TTS_VOLUME", "0"))

# --- Эквалайзер голоса (живой синтез edge-tts), дБ: -12..+12 ---
EQ_SUB = int(os.getenv("YANA_EQ_SUB", "0"))
EQ_LOW = int(os.getenv("YANA_EQ_LOW", "0"))
EQ_MID = int(os.getenv("YANA_EQ_MID", "0"))
EQ_PRES = int(os.getenv("YANA_EQ_PRES", "0"))
EQ_HIGH = int(os.getenv("YANA_EQ_HIGH", "0"))

_SETTINGS_MTIME = 0.0


def reload_settings(force: bool = False) -> bool:
    """Живая перезагрузка settings.json БЕЗ рестарта. True если что-то применилось."""
    global _SETTINGS_MTIME, MIC_ENERGY, MIC_PAUSE, MIC_PHRASE, NOISE_GATE, VAD_AGGR
    global MIC_INDEX, AUDIO_OUT, TTS_RATE, TTS_PITCH, TTS_VOLUME
    global EQ_SUB, EQ_LOW, EQ_MID, EQ_PRES, EQ_HIGH
    global GROQ_API_KEY, SCREEN_INTERVAL
    try:
        import json
        if not SETTINGS_FILE.exists():
            return False
        mt = SETTINGS_FILE.stat().st_mtime
        if not force and mt == _SETTINGS_MTIME:
            return False
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False
        _SETTINGS_MTIME = mt

        def _get(key, cast, default):
            try:
                return cast(data.get(key, default))
            except (TypeError, ValueError):
                return default

        MIC_ENERGY = _get("YANA_MIC_ENERGY", int, MIC_ENERGY)
        MIC_PAUSE = _get("YANA_MIC_PAUSE", float, MIC_PAUSE)
        MIC_PHRASE = _get("YANA_MIC_PHRASE", float, MIC_PHRASE)
        NOISE_GATE = _get("YANA_NOISE_GATE", float, NOISE_GATE)
        VAD_AGGR = _get("YANA_VAD", int, VAD_AGGR)
        try:
            mi = str(data.get("YANA_MIC_INDEX", "") or "").strip()
            MIC_INDEX = int(mi) if mi else None
        except ValueError:
            pass
        AUDIO_OUT = str(data.get("YANA_AUDIO_OUT", AUDIO_OUT))
        TTS_RATE = _get("YANA_TTS_RATE", int, TTS_RATE)
        TTS_PITCH = _get("YANA_TTS_PITCH", int, TTS_PITCH)
        TTS_VOLUME = _get("YANA_TTS_VOLUME", int, TTS_VOLUME)
        EQ_SUB = _get("YANA_EQ_SUB", int, EQ_SUB)
        EQ_LOW = _get("YANA_EQ_LOW", int, EQ_LOW)
        EQ_MID = _get("YANA_EQ_MID", int, EQ_MID)
        EQ_PRES = _get("YANA_EQ_PRES", int, EQ_PRES)
        EQ_HIGH = _get("YANA_EQ_HIGH", int, EQ_HIGH)
        GROQ_API_KEY = str(data.get("GROQ_API_KEY", GROQ_API_KEY))
        SCREEN_INTERVAL = _get("YANA_SCREEN_INTERVAL", int, SCREEN_INTERVAL)
        if "YANA_MQTT" in data:
            os.environ["YANA_MQTT"] = str(data["YANA_MQTT"])
        return True
    except Exception:
        return False

# --- Бесплатный ИИ (гонка: Ollama -> Pollinations БЕЗ ключа -> Gemini -> Groq) ---
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
POLLINATIONS_MODEL = os.getenv("YANA_POLLINATIONS_MODEL", "openai")
POLLINATIONS_TIMEOUT = int(os.getenv("YANA_POLLINATIONS_TIMEOUT", "45"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "") or ""
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
GROQ_VISION_MODEL = os.getenv("YANA_GROQ_VISION", "meta-llama/llama-4-scout-17b-16e-instruct")

# --- Глаза Яны: зрение экрана ---
SCREEN_INTERVAL = int(os.getenv("YANA_SCREEN_INTERVAL", "15"))
SCREEN_COMMENT_COOLDOWN = 25

# Сколько пар реплик помнит Яна
MAX_HISTORY_TURNS = int(os.getenv("YANA_HISTORY", "6"))

# Порог нечёткого совпадения команд (0-100)
FUZZY_THRESHOLD = int(os.getenv("YANA_FUZZY", "75"))

SYSTEM_PROMPT = (
    "Ты — Яна Нейронова, дружелюбный голосовой помощник. "
    "Отвечаешь кратко (1-3 предложения), по-русски, тёплым женским тоном, как Алиса. "
    "Без списков и markdown, только живая речь."
)
