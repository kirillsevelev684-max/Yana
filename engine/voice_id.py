# -*- coding: utf-8 -*-
"""Кто говорит: женский / мужской / детский голос + узнавание пользователей.

- Классификация по высоте голоса (F0): быстро, офлайн, без моделей.
- Отпечаток голоса: высота + разброс + тембр + звонкость + громкость + интонация.
- observe() вызывается из STT автоматически: сохраняет образец в фоне
  (voice_samples/pending) и запоминает, кто говорит сейчас.
- Незнакомца Яна встречает знакомством (interview_unknown): имя + 2 образца
  + интонация сразу в базу.
- Закрепление образцов за пользователями — через настройки (.exe).
"""
import json
import struct
import time

try:
    import numpy as np
    HAS_NUMPY = True
except Exception:
    np = None  # type: ignore
    HAS_NUMPY = False

from . import config
from .audio_clean import wav_to_pcm16k

PENDING_DIR = config.SAMPLES_DIR / "pending"
USERS_DIR = config.SAMPLES_DIR / "users"
PENDING_DIR.mkdir(parents=True, exist_ok=True)
USERS_DIR.mkdir(parents=True, exist_ok=True)

MAX_PENDING = 50
MATCH_DIST = 0.35  # дистанция отпечатков: меньше = тот же человек

_current = {"user": "", "class": "", "f0": 0.0}
_last_observe_t = 0.0
_last_interview_t = 0.0


def _f0_of_frame(x) -> float:
    """Высота тона кадра (30 мс) автокорреляцией. 0 = не голос."""
    x = x - x.mean()
    if float(np.sqrt((x ** 2).mean())) < 300:
        return 0.0
    ac = np.correlate(x, x, mode="full")[len(x) - 1:]
    if ac[0] <= 0:
        return 0.0
    ac = ac / ac[0]
    lo, hi = 32, 400  # период 2..25 мс -> 40..500 Гц
    if len(ac) <= hi:
        return 0.0
    peak = int(np.argmax(ac[lo:hi])) + lo
    if float(ac[peak]) < 0.35:  # непериодический звук — не голос
        return 0.0
    return 16000.0 / peak


def analyze(pcm: bytes) -> dict:
    """Полный анализ голоса: класс, отпечаток, интонация. Никогда не падает."""
    empty = {"f0": 0.0, "f0std": 0.0, "class": "", "conf": 0.0, "print": [], "inton": {}}
    if not HAS_NUMPY or not pcm:
        return empty
    try:
        n = len(pcm) // 2
        if n < 480 * 3:
            return empty
        sig = np.array(struct.unpack("<%dh" % n, pcm[:n * 2]), dtype=np.float32)
        step = 480  # 30 мс
        f0s = []
        nframes = 0
        for i in range(0, len(sig) - step + 1, step):
            nframes += 1
            f = _f0_of_frame(sig[i:i + step])
            if f > 0:
                f0s.append(f)
        if len(f0s) < 3:
            return empty
        f0s = np.array(f0s)
        f0med = float(np.median(f0s))
        f0std = float(np.std(f0s))
        f0range = float(f0s.max() - f0s.min())
        voiced = len(f0s) / max(1, nframes)
        # --- классификация по высоте ---
        if f0med < 170:
            cls = "мужской"
            conf = min(1.0, (170 - f0med) / 60 + 0.5)
        elif f0med <= 255:
            cls = "женский"
            conf = min(1.0, 0.55 + min(f0med - 170, 255 - f0med) / 85)
        else:
            cls = "детский"
            conf = min(1.0, (f0med - 255) / 60 + 0.5)
        # --- отпечаток: высота + разброс + тембр + звонкость + громкость ---
        seg = sig[max(0, len(sig) // 2 - 8000):][:16000]
        if len(seg) < 4000:
            seg = sig
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        freqs = np.fft.rfftfreq(len(seg), 1 / 16000)
        cent = float((spec * freqs).sum() / (spec.sum() + 1e-9))
        zcr = float(((seg[:-1] * seg[1:]) < 0).mean())
        rms = float(np.sqrt((seg ** 2).mean()))
        print_vec = [round(f0med / 400, 4), round(min(f0std, 150) / 150, 4),
                     round(min(cent, 6000) / 6000, 4), round(zcr, 4),
                     round(min(rms, 12000) / 12000, 4)]
        inton = {"range": round(f0range, 1), "std": round(f0std, 1),
                 "voiced": round(voiced, 2)}
        return {"f0": round(f0med, 1), "f0std": round(f0std, 1),
                "class": cls, "conf": round(conf, 2), "print": print_vec,
                "inton": inton}
    except Exception:
        return empty


# ---------- профили пользователей ----------

def _load_users() -> dict:
    try:
        if config.USERS_FILE.exists():
            return json.loads(config.USERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_users(d: dict):
    try:
        config.USERS_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def list_users() -> dict:
    return _load_users()


def enroll(name: str, pcm: bytes) -> dict:
    """Закрепить образец голоса за пользователем (отпечаток + интонация усредняются)."""
    name = (name or "").strip()
    info = analyze(pcm)
    if not name or not info["print"]:
        return {}
    users = _load_users()
    u = users.get(name, {"print": None, "n": 0, "class": ""})
    n = u.get("n", 0)
    if u.get("print") and n > 0:
        avg = [(a * n + b) / (n + 1) for a, b in zip(u["print"], info["print"])]
    else:
        avg, n = info["print"], 0
    oi, ni = u.get("inton") or {}, info.get("inton") or {}
    if oi and ni and n > 0:
        inton = {k: round((oi.get(k, 0) * n + ni.get(k, 0)) / (n + 1), 2)
                 for k in ("range", "std", "voiced")}
    else:
        inton = ni or oi or {}
    prof = {"print": [round(x, 4) for x in avg], "n": n + 1,
            "class": info["class"], "inton": inton}
    if u.get("role"):  # роль не теряем при дообучении
        prof["role"] = u["role"]
    users[name] = prof
    _save_users(users)
    return users[name]


def delete_user(name: str) -> bool:
    users = _load_users()
    if name in users:
        del users[name]
        _save_users(users)
        return True
    return False


def rename_user(old: str, new: str) -> bool:
    """Переименовать профиль (вместе с папкой образцов)."""
    users = _load_users()
    new = (new or "").strip().capitalize()
    if old not in users or not new or new in users:
        return False
    users[new] = users.pop(old)
    _save_users(users)
    try:
        src = USERS_DIR / old
        if src.exists():
            src.rename(USERS_DIR / new)
    except Exception:
        pass
    return True


def auto_enroll_unknown(pcm: bytes) -> str:
    """Узнать говорящего или создать «Неопознанный N». Возвращает имя профиля."""
    name, _, info = identify(pcm)
    if name:
        return name
    if not info["print"]:
        return ""
    users = _load_users()
    n = 1
    while f"Неопознанный {n}" in users:
        n += 1
    label = f"Неопознанный {n}"
    enroll(label, pcm)
    return label


def identify(pcm: bytes):
    """Узнать пользователя. Возвращает (имя|None, схожесть, info)."""
    info = analyze(pcm)
    if not info["print"]:
        return None, 0.0, info
    users = _load_users()
    best, best_d = None, 1e9
    for name, u in users.items():
        p = u.get("print") or []
        if len(p) != len(info["print"]):
            continue
        d = sum((a - b) ** 2 for a, b in zip(p, info["print"])) ** 0.5
        # интонация уточняет: диапазон + живость + доля речи
        ui, ii = u.get("inton") or {}, info.get("inton") or {}
        if ui and ii:
            dr = abs(ui.get("range", 0) - ii.get("range", 0)) / 400
            ds = abs(ui.get("std", 0) - ii.get("std", 0)) / 150
            dv = abs(ui.get("voiced", 0) - ii.get("voiced", 0))
            d += 0.25 * (dr + ds + dv)
        if d < best_d:
            best, best_d = name, d
    if best is not None and best_d <= MATCH_DIST:
        return best, round(1 - best_d, 2), info
    return None, 0.0, info


# ---------- фоновая работа из STT ----------

def quick_class(wav_bytes: bytes) -> dict:
    """Быстро: класс голоса + узнавание. Не падает никогда."""
    try:
        pcm = wav_to_pcm16k(wav_bytes)
        name, score, info = identify(pcm)
        return {"class": info.get("class", ""), "f0": info.get("f0", 0.0),
                "conf": info.get("conf", 0.0), "user": name or "", "score": score}
    except Exception:
        return {"class": "", "f0": 0.0, "conf": 0.0, "user": "", "score": 0.0}


def observe(wav_bytes: bytes) -> dict:
    """Фон: сохранить образец + запомнить, кто говорит. Вызывается из STT."""
    global _last_observe_t
    _last_observe_t = time.time()
    info = quick_class(wav_bytes)
    # незнакомый голос — сразу в базу как «Неопознанный N», дальше узнаём
    if info.get("class") and not info.get("user"):
        try:
            if sum(1 for n in _load_users() if n.startswith("Неопознанный")) < 20:
                new = auto_enroll_unknown(wav_to_pcm16k(wav_bytes))
                if new:
                    info["user"] = new
                    info["score"] = 0.5
        except Exception:
            pass
    # копия голоса — в папку человека (послушать и закрепить вручную в «Людях»)
    if info.get("user"):
        try:
            _save_sample_wav(info["user"], wav_bytes)
        except Exception:
            pass
    global _current
    _current = {"user": info["user"], "class": info["class"], "f0": info["f0"]}
    try:
        files = sorted(PENDING_DIR.glob("*.wav"))
        while len(files) >= MAX_PENDING:
            try:
                files.pop(0).unlink()
            except Exception:
                break
        tag = info.get("class") or "unknown"
        (PENDING_DIR / f"{time.strftime('%y%m%d_%H%M%S')}_{tag}.wav").write_bytes(wav_bytes)
    except Exception:
        pass
    if info["class"]:
        who = f", похож на: {info['user']}" if info["user"] else ""
        print(f"🎙 Голос: {info['class']} (~{info['f0']:.0f} Гц){who}")
    return info


def get_current() -> dict:
    return dict(_current)


def needs_interview(max_age: float = 20.0, cooldown: float = 120.0) -> bool:
    """True, если только что говорил незнакомец и пора познакомиться."""
    if time.time() - _last_observe_t > max_age:
        return False  # образец старый — кто говорит сейчас, неизвестно
    if time.time() - _last_interview_t < cooldown:
        return False  # уже знакомились недавно — не пристаём
    u = _current.get("user", "")
    return (not u) or u.startswith("Неопознанный")


USER_ROLES = ["", "Семья", "Родственник", "Друг", "Знакомый", "Гость"]


def set_role(name: str, role: str) -> bool:
    """Задать роль пользователя (Семья, Знакомый и т.п.)."""
    users = _load_users()
    if name not in users or role not in USER_ROLES:
        return False
    users[name]["role"] = role
    _save_users(users)
    return True


def describe_current() -> str:
    u, c = _current.get("user", ""), _current.get("class", "")
    role = ""
    if u:
        try:
            role = (_load_users().get(u, {}) or {}).get("role", "")
        except Exception:
            role = ""
    if u and c:
        base = f"{u} ({c} голос)"
    elif u:
        base = u
    elif c:
        base = f"{c} голос"
    else:
        return ""
    if role:
        base += f", {role.lower()}"
    return base


# ---------- знакомство с незнакомцем ----------

REFUSE = {"не знаю", "не помню", "никак", "потом", "отмена", "не хочу",
          "не скажу", "не буду", "потом скажу", "да", "нет", "ага", "угу",
          "конечно", "наверное"}
STOPWORDS = {"это", "меня", "здесь", "вот", "ну", "же", "просто", "типа",
             "думаю", "хочу", "могу", "буду", "знаю", "помню", "сказать",
             "говорю", "кажется", "такой", "такая", "такое", "его", "её", "мой", "моя"}


def clean_spoken_name(text: str) -> str:
    """Вытащить имя из фразы («меня зовут Макс» → «Макс»). '' = не вышло/отказ."""
    import re as _re
    s = (text or "").lower().strip()
    if not s:
        return ""
    s = _re.sub(r"[.?!,;:]+$", "", s).strip()
    for w in ("яна", "нейронова"):
        s = _re.sub(rf"\b{w}\b", "", s)
    s = _re.sub(r"\s+", " ", s).strip()
    if not s or s in REFUSE:
        return ""
    m = _re.search(r"(?:меня зовут|мо[её] имя|я)\s+([а-яёa-z\-]+(?:\s+[а-яёa-z\-]+)?)", s)
    cand = (m.group(1) if m else s).strip()
    cand = _re.sub(r"\s+", " ", cand)
    parts = cand.split()
    if not parts or len(parts) > 2:
        return ""
    if any(not _re.fullmatch(r"[а-яёa-z\-]{2,20}", p) for p in parts):
        return ""
    if cand in REFUSE or any(p in STOPWORDS for p in parts):
        return ""
    return " ".join(p.capitalize() for p in parts)


def _save_sample_wav(name: str, raw: bytes):
    """Сохранить образец голоса в папку пользователя (не больше 5)."""
    try:
        if not raw or len(raw) < 2000:
            return
        d = USERS_DIR / name
        d.mkdir(parents=True, exist_ok=True)
        for old in sorted(d.glob("*.wav"), key=lambda p: p.stat().st_mtime)[:-4]:
            try:
                old.unlink()
            except Exception:
                pass
        (d / f"{time.strftime('%y%m%d_%H%M%S')}.wav").write_bytes(raw)
    except Exception:
        pass


def interview_unknown(stt, tts, log=None) -> str:
    """Знакомство голосом: имя + 2 образца + интонация сразу в базу.

    stt — YanaSTT (нужен .listen и свежий .last_wav), tts — YanaTTS,
    log(msg) — куда дублировать реплики Яны (print / пузырь чата).
    Возвращает имя или ''.
    """
    global _last_interview_t
    _last_interview_t = time.time()

    def say(text: str):
        if log:
            try:
                log(f"🗣️ Яна: {text}")
            except Exception:
                pass
        try:
            tts.speak(text)
        except Exception:
            pass

    def hear(timeout: int = 9) -> str:
        try:
            return (stt.listen(timeout=timeout, phrase_limit=8) or "").strip()
        except Exception:
            return ""

    def sample_pcm() -> bytes:
        try:
            raw = getattr(stt, "last_wav", b"") or b""
            return wav_to_pcm16k(raw) if raw else b""
        except Exception:
            return b""

    if not HAS_NUMPY:
        say("К сожалению, запоминать голоса на этом компьютере я не умею.")
        return ""
    label = _current.get("user") or ""
    if label and not label.startswith("Неопознанный"):
        return label  # уже знакомы

    say("Привет! Кажется, мы ещё не знакомы. Как тебя зовут?")
    name, pcm1, wav1 = "", b"", b""
    for attempt in range(2):
        heard = hear()
        name = clean_spoken_name(heard)
        if name:
            pcm1 = sample_pcm()
            try:
                wav1 = getattr(stt, "last_wav", b"") or b""
            except Exception:
                wav1 = b""
            break
        if attempt == 0:
            say("Не расслышала имя. Повтори, пожалуйста, чётко.")
    if not name:
        say("Ладно, познакомимся позже 🙂")
        return ""

    users = _load_users()
    if name in users:
        # такое имя уже есть — уточняем, он ли это
        say(f"Хм, {name} у меня уже есть. Это ты? Скажи да или нет.")
        ans = hear().lower()
        if not any(w in ans for w in ("да", "ага", "угу", "конечно", "верно", "это я")):
            say("Хорошо, тогда познакомимся позже 🙂")
            return ""
        if pcm1 and analyze(pcm1).get("print"):
            enroll(name, pcm1)
            _save_sample_wav(name, wav1)
        _current["user"] = name
        say(f"Отлично, {name}! Я обновила твой голос в памяти ✨")
        return name

    # новое имя: переименовываем «Неопознанного» (отпечаток уже есть) или создаём
    if label.startswith("Неопознанный") and label in users:
        rename_user(label, name)
    elif pcm1 and analyze(pcm1).get("print"):
        enroll(name, pcm1)
    _save_sample_wav(name, wav1)
    say(f"Приятно познакомиться, {name}! Скажи ещё пару слов, чтобы я точно запомнила твой голос.")
    hear()
    pcm2 = sample_pcm()
    try:
        wav2 = getattr(stt, "last_wav", b"") or b""
    except Exception:
        wav2 = b""
    if pcm2 and analyze(pcm2).get("print"):
        enroll(name, pcm2)
        _save_sample_wav(name, wav2)
    info = analyze(pcm2 or pcm1)
    _current["user"] = name
    _current["class"] = info.get("class", "") or _current.get("class", "")
    desc = f" У тебя {info['class']} голос." if info.get("class") else ""
    say(f"Готово, {name}! Теперь я тебя узнаю ✨{desc}")
    return name
