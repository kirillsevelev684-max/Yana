# -*- coding: utf-8 -*-
"""Системные триггеры Windows из коробки: программы, настройки, звук, окна, питание.

Свои команды (CommandManager) проверяются раньше — они важнее.
Запись: (trigger, aliases, kind, do, say).
kind: app (программа) / run (start: exe, ms-settings:, shell:) /
      ps (powershell тихо, без окна) / cmd (команда тихо) / func (ответ текстом).
"""
import datetime
import os
import random
import subprocess
import unicodedata

# (главный триггер, алиасы, kind, do, say)
_DEFS = [
    # ---- программы Windows ----
    ("Блокнот", ["открой блокнот", "запусти блокнот", "блокнот", "notepad"],
     "app", "notepad.exe", "Открываю Блокнот."),
    ("Калькулятор", ["открой калькулятор", "запусти калькулятор", "калькулятор", "calc"],
     "app", "calc.exe", "Открываю Калькулятор."),
    ("Paint", ["открой paint", "запусти paint", "пэйнт", "пейнт", "рисовалка", "mspaint"],
     "app", "mspaint.exe", "Открываю Paint."),
    ("Проводник", ["открой проводник", "запусти проводник", "проводник", "мои файлы", "explorer"],
     "app", "explorer.exe", "Открываю Проводник."),
    ("Командная строка", ["открой терминал", "командная строка", "терминал", "консоль", "cmd"],
     "app", "cmd.exe", "Открываю командную строку."),
    ("PowerShell", ["открой powershell", "запусти powershell", "пауэршелл", "повершелл"],
     "app", "powershell.exe", "Открываю PowerShell."),
    ("Диспетчер задач", ["диспетчер задач", "открой диспетчер", "диспетчер", "taskmgr"],
     "app", "taskmgr.exe", "Открываю Диспетчер задач."),
    ("Панель управления", ["панель управления", "открой панель управления", "control"],
     "app", "control.exe", "Открываю Панель управления."),
    ("Ножницы", ["ножницы", "сделай скриншот", "скриншот", "снимок экрана"],
     "app", "snippingtool.exe", "Открываю Ножницы — выдели область экрана."),
    # ---- настройки Windows ----
    ("Параметры", ["открой параметры", "параметры windows", "настройки windows", "параметры"],
     "run", "ms-settings:", "Открываю Параметры."),
    ("Настройки звука", ["настройки звука", "параметры звука", "открой настройки звука"],
     "run", "ms-settings:sound", "Открываю настройки звука."),
    ("Настройки сети", ["настройки сети", "параметры сети", "настройки вайфай",
                        "вайфай", "wifi", "вафля"],
     "run", "ms-settings:network", "Открываю настройки сети."),
    ("Настройки экрана", ["настройки экрана", "параметры экрана", "разрешение экрана"],
     "run", "ms-settings:display", "Открываю настройки экрана."),
    # ---- папки оболочки ----
    ("Этот компьютер", ["мой компьютер", "этот компьютер", "открой мой компьютер"],
     "run", "shell:MyComputerFolder", "Открываю Этот компьютер."),
    ("Корзина", ["открой корзину", "корзина"],
     "run", "shell:RecycleBinFolder", "Открываю Корзину."),
    ("Загрузки", ["открой загрузки", "папка загрузки", "загрузки"],
     "run", "shell:Downloads", "Открываю Загрузки."),
    # ---- звук (клавиши громкости, тихо) ----
    ("Громче", ["прибавь звук", "громче", "увеличь громкость", "сделай громче"],
     "ps", "$o=New-Object -ComObject WScript.Shell; $o.SendKeys([char]175)",
     "Делаю громче 🔊"),
    ("Тише", ["убавь звук", "тише", "уменьши громкость", "сделай тише"],
     "ps", "$o=New-Object -ComObject WScript.Shell; $o.SendKeys([char]174)",
     "Делаю тише 🔉"),
    ("Выключить звук", ["выключи звук", "заглуши", "мут", "отключи звук"],
     "ps", "$o=New-Object -ComObject WScript.Shell; $o.SendKeys([char]173)",
     "Выключаю звук 🔇"),
    ("Включить звук", ["включи звук", "разглуши", "верни звук", "размьют"],
     "ps", "$o=New-Object -ComObject WScript.Shell; $o.SendKeys([char]173)",
     "Включаю звук 🔊"),
    # ---- окна ----
    ("Свернуть окна", ["сверни все окна", "сверни окна", "покажи рабочий стол"],
     "ps", "(New-Object -ComObject Shell.Application).MinimizeAll()",
     "Сворачиваю все окна."),
    ("Развернуть окна", ["разверни окна", "верни окна", "восстанови окна"],
     "ps", "(New-Object -ComObject Shell.Application).UndoMinimizeAll()",
     "Возвращаю окна."),
    ("Закрыть окно", ["закрой окно", "закрыть окно", "закрой это окно"],
     "ps", "$o=New-Object -ComObject WScript.Shell; $o.SendKeys('%{F4}')",
     "Закрываю окно."),
    # ---- питание ----
    ("Выключить ПК", ["выключи компьютер", "выключи пк", "выключить компьютер", "выключи комп"],
     "cmd", "shutdown /s /t 10 /c \"Яна выключает компьютер\"",
     "Выключаю компьютер через 10 секунд."),
    ("Перезагрузить ПК", ["перезагрузи компьютер", "перезагрузи пк", "перезагрузка", "перезагрузи комп"],
     "cmd", "shutdown /r /t 10 /c \"Яна перезагружает компьютер\"",
     "Перезагружаю компьютер через 10 секунд."),
    ("Отменить выключение", ["отмени выключение", "отмена выключения", "не выключай", "отмена"],
     "cmd", "shutdown /a", "Отменила выключение."),
    ("Сон", ["спящий режим", "усыпи компьютер", "в сон", "уложи спать"],
     "cmd", "rundll32.exe powrprof.dll,SetSuspendState 0,1,0", "Ухожу в сон 😴"),
    ("Блокировка", ["заблокируй компьютер", "заблокируй экран", "заблокируй", "блокировка"],
     "cmd", "rundll32.exe user32.dll,LockWorkStation", "Блокирую экран 🔒"),
    # ---- администрирование и служебные ----
    ("Магазин", ["открой магазин", "магазин windows", "microsoft store",
                 "магазин приложений", "магазин"],
     "run", "ms-windows-store:", "Открываю Магазин."),
    ("Диспетчер устройств", ["диспетчер устройств", "открой диспетчер устройств"],
     "run", "devmgmt.msc", "Открываю Диспетчер устройств."),
    ("Службы", ["службы windows", "открой службы", "службы"],
     "run", "services.msc", "Открываю Службы."),
    ("Сведения о системе", ["сведения о системе", "о системе", "характеристики компьютера",
                            "конфигурация пк"],
     "run", "msinfo32.exe", "Открываю Сведения о системе."),
    ("Очистка диска", ["очистка диска", "очистить диск", "почисти диск"],
     "run", "cleanmgr.exe", "Открываю Очистку диска."),
    ("Экранная клавиатура", ["экранная клавиатура", "клавиатура на экране"],
     "run", "osk.exe", "Открываю экранную клавиатуру."),
    ("Лупа", ["лупа", "экранная лупа", "увеличительное стекло"],
     "run", "magnify.exe", "Открываю Лупу."),
    # ---- встроенные приложения ----
    ("Фотографии", ["открой фотографии", "фотографии", "фото windows"],
     "run", "ms-photos:", "Открываю Фотографии."),
    ("Календарь", ["открой календарь", "календарь"],
     "run", "outlookcal:", "Открываю Календарь."),
    ("Часы", ["открой часы", "часы windows", "таймер windows", "будильник"],
     "run", "ms-clock:", "Открываю Часы."),
    ("Погода", ["открой погоду", "погода windows", "приложение погода"],
     "run", "msnweather:", "Открываю Погоду."),
    ("Карты", ["открой карты", "карты windows"],
     "run", "bingmaps:", "Открываю Карты."),
    ("Почта", ["открой почту", "почта windows", "почтовый клиент"],
     "run", "outlookmail:", "Открываю Почту."),
    # ---- функции (ответ текстом, без системы) ----
    ("Время", ["который час", "сколько времени", "текущее время", "подскажи время", "время"],
     "func", "time", ""),
    ("Дата", ["какое сегодня число", "какая сегодня дата", "число сегодня", "дата"],
     "func", "date", ""),
    ("День недели", ["какой сегодня день", "день недели", "какой день недели"],
     "func", "weekday", ""),
    ("Анекдот", ["расскажи анекдот", "анекдот", "пошути", "шутка", "рассмеши"],
     "func", "joke", ""),
]

QUESTION_STARTS = ("что такое", "что значит", "почему", "зачем", "как ", "когда ",
                   "где ", "кто ", "расскажи про", "расскажи о")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


def _find(norm: str):
    """Самый длинный триггер. Одно слово — только в короткой фразе (<=2 слов),
    чтобы «что такое вайфай» не открывало настройки, а «яна, время» — работало."""
    if norm.startswith(QUESTION_STARTS):
        # вопрос про понятие, а не команда («что такое сон») — пропускаем
        pass
    else:
        best, best_len = None, -1
        words = len(norm.split())
        padded = f" {norm} "
        for entry in _DEFS:
            cands = [entry[0]] + entry[1]
            for t in cands:
                tn = _norm(t)
                if not tn:
                    continue
                if " " not in tn and words > 2:
                    continue
                if f" {tn} " in padded and len(tn) > best_len:
                    best, best_len = entry, len(tn)
        if best:
            return best
    return None


def _exec(kind: str, do: str) -> str:
    """Выполнить. Возвращает '' если ок, иначе текст ошибки."""
    if os.name != "nt":
        return "нужен Windows"
    try:
        if kind == "app":
            os.startfile(do)  # type: ignore[attr-defined]
        elif kind == "run":
            os.system(f'start "" "{do}"')
        elif kind == "ps":
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden",
                              "-Command", do],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=0x08000000)
        elif kind == "cmd":
            subprocess.Popen(do, shell=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=0x08000000)
        else:
            return f"неизвестный тип {kind}"
        return ""
    except Exception as e:
        return str(e)[:120]


_MONTHS = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря"]
_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
_JOKES = [
    "Почему программисты путают Хэллоуин и Рождество? Потому что Oct 31 равно Dec 25!",
    "Заходит нейросеть в бар... а бармена нет — его автоматизировали.",
    "Мой любимый анекдот про UDP? Не факт, что ты его услышишь!",
]


def _func(name: str) -> str:
    now = datetime.datetime.now()
    if name == "time":
        return f"Сейчас {now.hour:02d}:{now.minute:02d}."
    if name == "date":
        return (f"Сегодня {now.day} {_MONTHS[now.month]} {now.year} года, "
                f"{_WEEKDAYS[now.weekday()]}.")
    if name == "weekday":
        return f"Сегодня {_WEEKDAYS[now.weekday()]}."
    if name == "joke":
        return random.choice(_JOKES)
    return "..."


def handle(norm: str, brain=None):
    """Найти системную команду Windows. Возвращает dict для мозга или None."""
    hit = _find(_norm(norm))
    if not hit:
        return None
    _trig, _aliases, kind, do, say = hit
    if kind == "func":
        return {"response": _func(do), "action": None, "exit": False, "engine": "система"}
    err = _exec(kind, do)
    if err == "нужен Windows":
        return {"response": say + " (это команда Windows — здесь только текст).",
                "action": None, "exit": False, "engine": "система"}
    if err:
        return {"response": f"Не вышло: {err}", "action": None, "exit": False, "engine": "система"}
    return {"response": say, "action": None, "exit": False, "engine": "система"}


def list_all() -> list:
    """Все системные команды для вкладки настроек."""
    out = []
    for trig, aliases, kind, do, say in _DEFS:
        value = do if kind in ("app", "run") else (say or "ответ текстом")
        out.append({"trigger": trig, "aliases": list(aliases), "type": kind, "value": value})
    return out
