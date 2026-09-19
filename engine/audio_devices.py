# -*- coding: utf-8 -*-
"""Список микрофонов и колонок для выбора в настройках. Без библиотек — пусто."""
def list_mics() -> list[str]:
    """Микрофоны: ['0: Microphone (...)', ...]. Номер = device_index."""
    try:
        import speech_recognition as sr
        names = sr.Microphone.list_microphone_names() or []
        return [f"{i}: {n}" for i, n in enumerate(names)]
    except Exception:
        return []


def list_speakers() -> list[str]:
    """Колонки (устройства вывода pygame/SDL2)."""
    try:
        import pygame
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        from pygame._sdl2.audio import get_audio_device_names
        return [n for n in (get_audio_device_names(False) or []) if n]
    except Exception:
        return []
