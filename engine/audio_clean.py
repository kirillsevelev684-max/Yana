# -*- coding: utf-8 -*-
"""Антишум: VAD-гейт (только речь) + мягкое шумоподавление.

Всё опционально: без webrtcvad/noisereduce тихо работает простой энергетический режим.
Для максимума: pip install webrtcvad noisereduce
"""
import io
import struct
import wave

_vad = None


def _get_vad(aggr: int = 2):
    global _vad
    if _vad is None:
        try:
            import webrtcvad
            _vad = webrtcvad.Vad(aggr)
        except Exception:
            _vad = False
    return _vad or None


def vad_available() -> bool:
    return _get_vad() is not None


def _frames_30ms(pcm: bytes, rate: int = 16000):
    step = int(rate * 0.03) * 2  # 30 мс 16-bit mono
    for i in range(0, len(pcm) - step + 1, step):
        yield pcm[i:i + step]


def _energy_voice(frame: bytes, thresh: float = 500.0) -> bool:
    """Грубый детектор речи по громкости (запасной, если нет webrtcvad)."""
    n = len(frame) // 2
    if n == 0:
        return False
    samples = struct.unpack("<%dh" % n, frame)
    rms = (sum(s * s for s in samples) / n) ** 0.5
    return rms > thresh


class VadGate:
    """Скользящее окно голосовой активности для wake-режима."""

    def __init__(self, aggr: int = 2, window: int = 10, need: int = 3):
        self.vad = _get_vad(aggr)
        self.window = window
        self.need = need
        self.hist: list[bool] = []

    def _is_voice(self, frame: bytes) -> bool:
        if self.vad is not None:
            try:
                return bool(self.vad.is_speech(frame, 16000))
            except Exception:
                pass
        return _energy_voice(frame)

    def push_pcm(self, pcm_16k_mono16: bytes):
        for fr in _frames_30ms(pcm_16k_mono16):
            self.hist.append(self._is_voice(fr))
            self.hist = self.hist[-self.window:]

    def speech_recent(self) -> bool:
        if not self.hist:
            return False
        return sum(self.hist) >= min(self.need, len(self.hist))


def wav_to_pcm16k(wav_bytes: bytes) -> bytes:
    """WAV -> сырой PCM 16kHz mono 16-bit (стерео/частоты приводятся грубо)."""
    try:
        wf = wave.open(io.BytesIO(wav_bytes))
    except Exception:
        return b""
    rate, ch, width = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
    raw = wf.readframes(wf.getnframes())
    if width != 2 or not raw:
        return b""
    if ch > 1:  # стерео -> моно (первый канал)
        samples = struct.unpack("<%dh" % (len(raw) // 2), raw)
        raw = struct.pack("<%dh" % len(samples[0::ch]), *samples[0::ch])
    if rate != 16000:  # грубый ресемпл
        n = len(raw) // 2
        samples = struct.unpack("<%dh" % n, raw)
        ratio = rate / 16000
        new_n = max(1, int(n / ratio))
        raw = struct.pack("<%dh" % new_n, *[samples[int(i * ratio)] for i in range(new_n)])
    return raw


def speech_ratio(wav_bytes: bytes, aggr: int = 2) -> float:
    """Доля кадров с речью (0..1). Ошибка анализа = 1.0 (не блокируем речь)."""
    try:
        frames = list(_frames_30ms(wav_to_pcm16k(wav_bytes)))
        if not frames:
            return 0.0
        vad = _get_vad(aggr)
        hits = 0
        for fr in frames:
            if vad is not None:
                try:
                    if vad.is_speech(fr, 16000):
                        hits += 1
                    continue
                except Exception:
                    pass
            if _energy_voice(fr):
                hits += 1
        return hits / len(frames)
    except Exception:
        return 1.0


def denoise_wav(wav_bytes: bytes) -> bytes:
    """Мягкое шумоподавление перед отправкой в Google. Без noisereduce — как есть."""
    try:
        import numpy as np
        import noisereduce as nr
    except Exception:
        return wav_bytes
    try:
        wf = wave.open(io.BytesIO(wav_bytes))
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        clean = nr.reduce_noise(y=data, sr=rate, stationary=False, prop_decrease=0.7)
        clean = np.clip(clean, -32768, 32767).astype(np.int16).tobytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(clean)
        return buf.getvalue()
    except Exception:
        return wav_bytes
