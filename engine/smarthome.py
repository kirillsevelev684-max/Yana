# -*- coding: utf-8 -*-
"""Умный дом v1: виртуальные устройства + MQTT-мост к реальным.

Сейчас устройства виртуальные (состояние в devices.json) — архитектура готова:
задай YANA_MQTT=host:port/префикс и установи paho-mqtt, и команды полетят
в настоящий брокер (Home Assistant, MajorDoMo и т.п.).
"""
import json
import re

from . import config

SEED = [
    {"name": "свет", "type": "свет", "state": "off", "level": 100},
    {"name": "розетка", "type": "розетка", "state": "off", "level": 100},
]


class DeviceManager:
    def __init__(self):
        self.devices: list[dict] = []
        self.load()

    def load(self):
        try:
            if config.DEVICES_FILE.exists():
                self.devices = json.loads(config.DEVICES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        if not self.devices:
            self.devices = [dict(d) for d in SEED]
            self.save()

    def save(self):
        try:
            config.DEVICES_FILE.write_text(json.dumps(self.devices, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
        except Exception:
            pass

    def find(self, name: str):
        name = (name or "").lower().strip(" ?!.,")
        if not name:
            return None
        best = None  # самое конкретное совпадение: «свет в кухне» бьёт «свет»
        for d in self.devices:
            dn = d["name"].lower()
            if dn in name or name in dn:
                if best is None or len(dn) > len(best["name"]):
                    best = d
        return best

    def set_state(self, dev: dict, state: str) -> str:
        dev["state"] = state
        self.save()
        self._mqtt(dev, state)
        return f"{dev['name'].capitalize()} {'включён' if state == 'on' else 'выключен'}."

    def _mqtt(self, dev: dict, payload):
        import os
        broker = os.getenv("YANA_MQTT", "")
        if not broker:
            return
        try:
            import paho.mqtt.publish as pub
            m = re.match(r"([^:/]+)(?::(\d+))?(?:/(.*))?", broker)
            host = m.group(1)
            port = int(m.group(2) or 1883)
            prefix = (m.group(3) or "yana").rstrip("/")
            topic = f"{prefix}/{dev['name']}"
            pub.single(topic, str(payload), hostname=host, port=port)
            print(f"[MQTT] {topic} <- {payload}")
        except Exception as e:
            print(f"[MQTT] не отправлено: {e}")


_mgr = None


def _manager() -> DeviceManager:
    global _mgr
    if _mgr is None:
        _mgr = DeviceManager()
    return _mgr


def handle(norm: str):
    """Разобрать команду умного дома. Возвращает ответ или None (не наше)."""
    m = _manager()
    low = norm or ""

    # добавить устройство — первым: «устройство» содержит «устройств»,
    # иначе ветка списка ниже съедала бы добавление
    ma = re.search(r"добавь устройств[оа] (.+)", low)
    if ma:
        name = ma.group(1).strip(" ?!.,")
        if len(name) < 2:
            return None
        dtype = "свет" if ("свет" in name or "ламп" in name) else "прибор"
        m.devices.append({"name": name, "type": dtype, "state": "off", "level": 100})
        m.save()
        return f"Добавила устройство «{name}». Скажи «включи {name}»."

    # список / статус — только для вопросов про список, остальные проваливаются дальше
    if ("устройств" in low or "умный дом" in low) and \
            any(k in low for k in ("какие", "список", "покажи", "статус", "что есть", "мои")):
        if not m.devices:
            return "Устройств пока нет. Скажи: добавь устройство свет в кухне."
        parts = []
        for d in m.devices:
            s = f"{d['name']} — {'включён' if d['state'] == 'on' else 'выключен'}"
            if d.get("type") == "свет" and d["state"] == "on":
                s += f", яркость {d.get('level', 100)}%"
            parts.append(s)
        return "Устройства: " + "; ".join(parts) + "."

    # яркость
    ma = re.search(r"яркость (.+?) (\d{1,3})", low)
    if ma:
        dev = m.find(ma.group(1))
        if not dev:
            return f"Устройство «{ma.group(1).strip()}» не найдено."
        lvl = max(0, min(100, int(ma.group(2))))
        dev["level"] = lvl
        dev["state"] = "on" if lvl > 0 else "off"
        m.save()
        m._mqtt(dev, f"level:{lvl}")
        return f"Яркость «{dev['name']}» — {lvl}%."

    # вкл / выкл / переключить (только свои устройства, чужое отдаём дальше)
    for verb, state in (("выключи", "off"), ("включи", "on")):
        if verb in low:
            dev = m.find(low.split(verb, 1)[1])
            if dev:
                return m.set_state(dev, state)
            return None
    if "переключи" in low:
        dev = m.find(low.split("переключи", 1)[1])
        if dev:
            return m.set_state(dev, "off" if dev["state"] == "on" else "on")
        return None
    return None
