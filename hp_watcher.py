# -*- coding: utf-8 -*-
"""
HP Watcher v2.4 — следит за полосками HP/MP в Lineage 2 и выполняет сценарии
нажатий клавиш по условиям. Все настройки — в config.json рядом со скриптом.
Инструкция — в README.md.
"""

import copy
import ctypes
import json
import logging
import operator
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from logging.handlers import RotatingFileHandler

import numpy as np

VERSION = "2.4"
IS_WIN = sys.platform == "win32"

if IS_WIN:
    import ctypes.wintypes as wt
    import winsound

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
    user32.IsWindow.argtypes = [wt.HWND]
    user32.IsWindowVisible.argtypes = [wt.HWND]
    user32.IsIconic.argtypes = [wt.HWND]
    user32.GetWindowTextLengthW.argtypes = [wt.HWND]
    user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    user32.GetForegroundWindow.restype = wt.HWND
    user32.GetParent.argtypes = [wt.HWND]
    user32.GetParent.restype = wt.HWND
    user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    kernel32.GetConsoleWindow.restype = wt.HWND


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(app_dir(), "config.json")
LOG_PATH = os.path.join(app_dir(), "hp_watcher.log")

BAR_KEYS = ["target_hp", "my_hp", "my_mp"]
BAR_NAMES = {"target_hp": "HP цели", "my_hp": "моё HP", "my_mp": "моё MP"}
VALUE_KEYS = BAR_KEYS + ["no_target_sec"]
MIN_REPEAT = 0.3  # сценарий без события не повторяется чаще, чем раз в столько секунд
OVERLAY_COLORS = {"target_hp": "#ffd400", "my_hp": "#3cff6a", "my_mp": "#38d6ff"}
EVENTS = {"target_died", "target_lost"}
OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt,
       ">=": operator.ge, "==": operator.eq, "!=": operator.ne}
MODIFIERS = {"ctrl", "alt", "shift", "win", "ctrlleft", "ctrlright", "altleft",
             "altright", "shiftleft", "shiftright", "winleft", "winright",
             "left ctrl", "right ctrl", "left alt", "right alt", "alt gr",
             "left shift", "right shift", "left windows", "right windows"}
KEY_ALIASES = {"escape": "esc", "return": "enter", "spacebar": "space",
               "page up": "pageup", "page down": "pagedown", "caps lock": "capslock",
               "num lock": "numlock", "scroll lock": "scrolllock",
               "print screen": "printscreen", "delete": "del"}
BEEPS = {
    "start": [(660, 70), (880, 90)],
    "pause": [(440, 160)],
    "resume": [(660, 70), (880, 90)],
    "action": [(1200, 50)],
    "calib": [(1400, 60)],
    "error": [(300, 280)],
    "rec_on": [(1000, 70), (1500, 90)],
    "rec_off": [(1500, 70), (1000, 90)],
    "test_on": [(900, 60), (900, 60), (900, 60)],
    "test_off": [(900, 120)],
    "alarm": [(1800, 180), (900, 180)],
}

DEFAULT_CONFIG = {
    "_пояснения": {
        "window_title": "Часть заголовка окна игры. Пусто — координаты считаются от угла экрана. "
                        "Удобнее всего привязать окно горячей клавишей bind_window.",
        "bars": "Координаты полосок относительно окна игры. Заполняются калибровкой, руками трогать не обязательно.",
        "scenarios": "Сценарий срабатывает, когда выполнены ВСЕ условия. Условия: {\"event\": \"target_died\"} "
                     "или {\"value\": \"target_hp|my_hp|my_mp\", \"op\": \"<\", \"than\": 40}. "
                     "Операции: < <= > >= == !=. Меньше priority — проверяется раньше. cooldown — секунды.",
        "actions": "Список [клавиша, пауза после нажатия в секундах]. Клавиши: f1..f12, 0..9, a..z, esc, enter, "
                   "space, tab, up/down/left/right. Сочетания через +: \"ctrl+1\". [\"wait\", 1.5] — просто пауза.",
        "skill_keys": "Именованные кнопки панели скиллов. В действиях пишите \"@attack\" вместо \"f1\" — "
                      "тогда при переносе скилла на другую клавишу достаточно поменять её здесь. "
                      "Можно добавлять свои имена: \"buff\": \"f5\", \"heal\": \"alt+1\".",
        "no_target_sec": "Условие {\"value\": \"no_target_sec\", \"op\": \">=\", \"than\": 4} — цели нет "
                         "(или её HP = 0) уже 4 секунды. Нужно для сценария «Поиск цели».",
        "vision": "Зрение: band — сколько строк пикселей читать (медиана); min_confidence — % согласия "
                  "с моделью «заливка слева, пусто справа», ниже — значение недостоверно и не используется; "
                  "min_coverage — % столбцов, похожих на полоску, ниже — полоски нет на экране; "
                  "kill_if_vanished_below — если полоска цели исчезла, а HP было ниже этого %, считаем убийством; "
                  "track_moved — искать сдвинутое окно цели; learn_empty — уточнять цвет пустой части на лету.",
        "dry_run": "Тестовый режим: всё распознаётся и пишется в лог, но клавиши не нажимаются.",
        "emergency": "Аварийная пауза: on_death — при смерти (моё HP = 0); hp_drop_percent за hp_drop_seconds — "
                     "при резком падении HP (0 — выключено). Работает, только если откалибровано моё HP.",
        "screenshots": "Скриншоты окна игры в папку folder: при запуске сценария, аварии, убийстве цели. "
                       "keep — сколько последних хранить.",
    },
    "window_title": "Lineage",
    "skill_keys": {
        "attack": "f1",
        "next_target": "f2",
    },
    "dry_run": False,
    "emergency": {
        "on_death": True,
        "hp_drop_percent": 40,
        "hp_drop_seconds": 3.0,
        "alarm_repeats": 3,
    },
    "screenshots": {
        "enabled": True,
        "folder": "screenshots",
        "keep": 200,
        "on_scenario": True,
        "on_emergency": True,
        "on_kill": False,
    },
    "only_when_window_active": True,
    "check_interval": 0.1,
    "min_gap": 0.2,
    "vision": {
        "band": 3,
        "min_confidence": 90,
        "min_coverage": 50,
        "kill_if_vanished_below": 15,
        "track_moved": True,
        "learn_empty": True,
    },
    "dead_confirm": 3,
    "event_ttl": 3.0,
    "key_hold": 0.05,
    "sound": True,
    "overlay": True,
    "overlay_frames": True,
    "status_window_pos": [20, 200],
    "require_admin": True,
    "bars": {
        "target_hp": {"x1": 0, "x2": 0, "y": 0, "color": [180, 40, 40], "tolerance": 60},
        "my_hp": {"x1": 0, "x2": 0, "y": 0, "color": [180, 40, 40], "tolerance": 60},
        "my_mp": {"x1": 0, "x2": 0, "y": 0, "color": [40, 80, 200], "tolerance": 60},
    },
    "hotkeys": {
        "calib_left": "ctrl+alt+1",
        "calib_right": "ctrl+alt+2",
        "calib_next_bar": "ctrl+alt+3",
        "auto_find": "ctrl+alt+4",
        "bind_window": "ctrl+alt+5",
        "record": "ctrl+alt+6",
        "dry_run": "ctrl+alt+7",
        "stats_reset": "ctrl+alt+8",
        "run_now": "ctrl+alt+t",
        "settings": "ctrl+alt+s",
        "reload": "ctrl+alt+9",
        "pause": "ctrl+alt+0",
        "exit": "ctrl+alt+end",
    },
    "offered_defaults": [],
    "record_into": "После убийства моба",
    "record_last_delay": 0.5,
    "scenarios": [
        {
            "name": "После убийства моба",
            "enabled": True,
            "priority": 10,
            "cooldown": 0,
            "conditions": [{"event": "target_died"}],
            "actions": [["@next_target", 0.4], ["@attack", 0.3]],
        },
        {
            "name": "После потери цели",
            "enabled": True,
            "priority": 15,
            "cooldown": 0,
            "conditions": [{"event": "target_lost"}],
            "actions": [["@next_target", 0.4], ["@attack", 0.3]],
        },
        {
            "name": "Поиск цели",
            "enabled": False,
            "priority": 20,
            "cooldown": 3,
            "conditions": [{"value": "no_target_sec", "op": ">=", "than": 4}],
            "actions": [["@next_target", 0.4], ["@attack", 0.3]],
        },
        {
            "name": "Скилл в бою",
            "enabled": False,
            "priority": 30,
            "cooldown": 5,
            "conditions": [{"value": "target_hp", "op": ">", "than": 0}],
            "actions": [["f3", 0.5]],
        },
        {
            "name": "Бафф",
            "enabled": False,
            "priority": 40,
            "cooldown": 1200,
            "conditions": [{"value": "no_target_sec", "op": ">=", "than": 1}],
            "actions": [["f5", 2.0]],
        },
        {
            "name": "Банка HP",
            "enabled": False,
            "priority": 1,
            "cooldown": 15,
            "conditions": [{"value": "my_hp", "op": "<", "than": 40}],
            "actions": [["f9", 0.3]],
        },
        {
            "name": "Банка MP",
            "enabled": False,
            "priority": 2,
            "cooldown": 15,
            "conditions": [{"value": "my_mp", "op": "<", "than": 20}],
            "actions": [["f10", 0.3]],
        },
    ],
}

log = logging.getLogger("hpw")


# ============================================================
#  Чистая логика (без Windows) — её можно тестировать где угодно
# ============================================================

REPLACE_WHOLE = {"skill_keys"}  # эти словари пользователь редактирует целиком — не подмешивать умолчания


def deep_merge(base, override):
    """Добавляет в настройки пользователя ключи, которых там ещё нет."""
    out = copy.deepcopy(base)
    for k, v in override.items():
        if k in REPLACE_WHOLE:
            out[k] = copy.deepcopy(v)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def bar_ready(bar):
    return bool(bar) and bar.get("x2", 0) > bar.get("x1", 0) >= 0


def match_mask(rgb, color, tol):
    """rgb: (N,3). True там, где цвет близок к color (по каждому каналу не дальше tol)."""
    diff = np.abs(rgb.astype(np.int16) - np.asarray(color, dtype=np.int16))
    return diff.max(axis=-1) <= tol


def fill_percent(mask, min_run=2):
    """Насколько заполнена полоска, %. Полоска растёт слева, поэтому:
    - если у левого края нет заливки — 0 (фон за пустым окном цели не считается);
    - иначе конец заливки — самый правый участок из min_run подряд «своих» пикселей
      (цифры поверх полоски не мешают)."""
    n = len(mask)
    if n == 0 or not mask[: max(min_run * 2, 4)].any():
        return 0.0
    m = mask.astype(np.int16)
    runs = np.flatnonzero(np.convolve(m, np.ones(min_run, np.int16), "valid") >= min_run)
    if runs.size == 0:
        return 0.0
    return min(100.0, (runs[-1] + min_run) / n * 100.0)


# ---------- Зрение v2: полоса пикселей, два цвета, оптимальная граница, самопроверка ----------

OTHER, FILL, EMPTY = 0, 1, 2
UNIFORM_STD = 14   # заливка и пустота полоски однородны; пёстрый фон — нет


def default_empty(fill):
    """Цвет пустой части, пока он не известен точно: тёмный вариант заливки."""
    return [int(c * 0.25) for c in fill]


def column_colors(band):
    """band: (строки, столбцы, 3) -> (столбцы, 3). Медиана по строкам гасит цифры и рамку."""
    band = np.asarray(band)
    if band.ndim == 2:
        return band.astype(np.int16)
    return np.median(band, axis=0).astype(np.int16)


def classify(cols, fill, empty, tol):
    """Каждый столбец: FILL — похож на заливку, EMPTY — на пустую часть, OTHER — ни на что."""
    c = np.asarray(cols, dtype=np.int16)
    df = np.abs(c - np.asarray(fill, dtype=np.int16)).max(axis=-1)
    de = np.abs(c - np.asarray(empty, dtype=np.int16)).max(axis=-1)
    tol_e = min(tol, 25)  # пустая часть тёмная — с тёмным фоном мира путать нельзя, допуск строже
    cls = np.full(len(c), OTHER, dtype=np.int8)
    cls[(df <= tol) & (df <= de)] = FILL
    cls[(de <= tol_e) & (de < df)] = EMPTY
    return cls


def analyze_bar(cols, cls, min_confidence=90, min_coverage=50):
    """Самопроверка и процент.
    status: ok — похоже на полоску, значение достоверно;
            unsure — что-то похожее, но не сходится с моделью (окно перекрыто, эффекты);
            absent — полоски на экране нет (окно цели закрыто).
    value: % заполнения — граница, лучше всего делящая столбцы на «заливка слева / пусто справа»."""
    cls = np.array(cls, dtype=np.int8)
    cols = np.asarray(cols, dtype=np.int16)
    n = len(cls)
    out = {"status": "absent", "value": None, "agree": 0.0, "coverage": 0.0, "split": 0, "cls": cls}
    if n == 0:
        return out
    for kind in (FILL, EMPTY):
        m = cls == kind
        if m.sum() >= 4 and cols[m].std(axis=0).max() > UNIFORM_STD:
            cls[m] = OTHER  # пёстро — это не полоска, а фон
    # Цвет пустой части ещё не выучен? Если заливка начинается у левого края, а правее неё
    # однородный участок — это и есть пустая часть.
    fi = np.flatnonzero(cls == FILL)
    if fi.size and fi[0] <= max(2, n // 50):
        tail = slice(fi[-1] + 1, n)
        t_cols, t_cls = cols[tail], cls[tail]
        if len(t_cls) >= 3:
            med = np.median(t_cols, axis=0)
            near = np.abs(t_cols - med).max(axis=-1) <= 12
            if near.mean() >= 0.8:
                t_cls[(t_cls == OTHER) & near] = EMPTY
                cls[tail] = t_cls
    f, e = cls == FILL, cls == EMPTY
    cov = (f.sum() + e.sum()) / n
    out["coverage"] = round(cov * 100, 1)
    if cov * 100 < min_coverage:
        return out
    pf = np.concatenate(([0], np.cumsum(f)))
    pe = np.concatenate(([0], np.cumsum(e)))
    correct = pf + (e.sum() - pe)
    wrong = pe + (f.sum() - pf)
    k = int(np.argmax(correct - wrong))
    agree = correct[k] / max(1, correct[k] + wrong[k])
    out.update(agree=round(float(agree) * 100, 1), split=k)
    if agree * 100 < min_confidence:
        out["status"] = "unsure"
        return out
    out.update(status="ok", value=round(k / n * 100, 1))
    return out


def learn_empty(cols, res, old, rate=0.15):
    """Уточняет цвет пустой части по уверенному чтению. Возвращает новый цвет или None."""
    if res["status"] != "ok" or res["agree"] < 97:
        return None
    m = res["cls"] == EMPTY
    if m.sum() < max(5, len(m) // 10):
        return None
    seen = np.median(np.asarray(cols)[m], axis=0)
    new = np.asarray(old, dtype=float) * (1 - rate) + seen * rate
    return [int(round(v)) for v in new]


def bar_colors(line, tol):
    """Цвета заливки и пустоты по строке при калибровке: заливка — левая четверть,
    пустота — правые 10%, если они заметно отличаются и однородны; иначе — тёмная заливка."""
    line = np.asarray(line, dtype=np.int16)
    fill = median_color(line)
    tail = line[-max(3, len(line) // 10):]
    tail_c = np.median(tail, axis=0)
    if (np.abs(tail_c - fill).max() > tol and tail.std(axis=0).max() <= UNIFORM_STD):
        return fill, [int(v) for v in tail_c]
    return fill, default_empty(fill)


def band_box(bar, band):
    """Прямоугольник (x, y, w, h) полосы относительно окна игры."""
    h = max(1, int(band))
    return bar["x1"], bar["y"] - (h - 1) // 2, bar["x2"] - bar["x1"] + 1, h


def read_bar_band(rgb_band, bar, vis):
    cols = column_colors(rgb_band)
    fill = bar.get("color", [180, 40, 40])
    empty = bar.get("empty_color") or default_empty(fill)
    cls = classify(cols, fill, empty, int(bar.get("tolerance", 60)))
    res = analyze_bar(cols, cls, float(vis.get("min_confidence", 90)), float(vis.get("min_coverage", 50)))
    res["cols"] = cols
    return res


def find_moved_bar(region, bar, n, min_share=0.9):
    """Ищет в region (h, w, 3) горизонтальный отрезок длиной n, похожий на полоску
    (заливка/пустота с границами по краям). Возвращает список (x, y) в координатах region."""
    fill = bar.get("color", [180, 40, 40])
    empty = bar.get("empty_color") or default_empty(fill)
    tol = int(bar.get("tolerance", 60))
    h, w, _ = region.shape
    if w < n + 2:
        return []
    found = []
    kernel = np.ones(n, dtype=np.int32)
    for y in range(h):
        row = region[y].astype(np.int16)
        cls = classify(row, fill, empty, tol)
        good = (cls != OTHER).astype(np.int32)
        share = np.convolve(good, kernel, "valid")  # share[x] = сколько «своих» в [x, x+n)
        for x in np.flatnonzero(share >= n * min_share):
            if x == 0 or x + n >= w:
                continue
            if good[x - 1] or good[x + n]:
                continue  # отрезок должен быть ограничен рамкой, а не частью длинного красного
            if cls[x] != FILL and cls[x] != EMPTY:
                continue
            res = analyze_bar(row[x:x + n], cls[x:x + n].copy(), 95, 80)
            if res["status"] == "ok":
                found.append((int(x), int(y)))
    return found


def median_color(line):
    """Типичный цвет полоски: медиана по левой четверти (там заливка есть почти всегда)."""
    part = line[: max(3, len(line) // 4)]
    return [int(v) for v in np.median(part.reshape(-1, 3), axis=0)]


def is_reddish(rgb):
    r, g, b = (rgb[..., i].astype(np.int16) for i in range(3))
    return (r >= 100) & (r * 10 >= g * 17) & (r * 10 >= b * 17)


def is_bluish(rgb):
    r, g, b = (rgb[..., i].astype(np.int16) for i in range(3))
    return (b >= 100) & (b * 10 >= r * 14) & (b * 10 >= g * 11)


def longest_run(row_mask, gap=3):
    xs = np.flatnonzero(row_mask)
    if xs.size == 0:
        return 0, 0, 0
    breaks = np.flatnonzero(np.diff(xs) > gap + 1)
    starts = np.concatenate(([xs[0]], xs[breaks + 1]))
    ends = np.concatenate((xs[breaks], [xs[-1]]))
    i = int(np.argmax(ends - starts))
    return int(ends[i] - starts[i] + 1), int(starts[i]), int(ends[i])


def find_bar(rgb, predicate, min_len=40, gap=3):
    """Ищет самую длинную горизонтальную полосу нужного цвета.
    Возвращает (y, x1, x2, color) или None. y — в верхней трети полосы,
    чтобы не попадать на цифры, которые обычно написаны посередине."""
    mask = predicate(rgb)
    runs = [longest_run(mask[y], gap) for y in range(mask.shape[0])]
    if not runs:
        return None
    best_y = max(range(len(runs)), key=lambda y: runs[y][0])
    length, x1, x2 = runs[best_y]
    if length < min_len:
        return None

    def same(y):
        l, a, b = runs[y]
        return l >= length * 0.8 and abs(a - x1) <= 3 and abs(b - x2) <= 3

    top = bottom = best_y
    while top > 0 and same(top - 1):
        top -= 1
    while bottom < len(runs) - 1 and same(bottom + 1):
        bottom += 1
    y = top + (bottom - top) // 3
    return y, x1, x2, median_color(rgb[y, x1:x2 + 1])


def auto_find_bars(rgb):
    """rgb — верхняя часть окна игры. Окно цели ищем по центру, свои полоски — слева."""
    h, w, _ = rgb.shape
    min_len = max(30, w // 40)
    res = {}

    def search(xa, xb, pred):
        x0, x1 = int(w * xa), int(w * xb)
        r = find_bar(rgb[:, x0:x1], pred, min_len=min_len)
        if r is None:
            return None
        y, a, b, color = r
        return y, a + x0, b + x0, color

    res["target_hp"] = search(0.25, 0.75, is_reddish)
    res["my_hp"] = search(0.0, 0.35, is_reddish)
    res["my_mp"] = search(0.0, 0.35, is_bluish)
    return res


def parse_action(item):
    if isinstance(item, (list, tuple)):
        return str(item[0]).strip().lower(), float(item[1]) if len(item) > 1 else 0.0
    if isinstance(item, dict):
        return str(item["key"]).strip().lower(), float(item.get("delay", 0))
    return str(item).strip().lower(), 0.0


def resolve_key(key, skill_keys):
    """\"@attack\" -> клавиша из skill_keys. Обычная клавиша возвращается как есть.
    None — такого имени нет."""
    if key.startswith("@"):
        k = skill_keys.get(key[1:])
        return str(k).strip().lower() if k else None
    return key


def named_key(key, skill_keys):
    """Обратное: клавиша -> \"@имя\", если она есть в skill_keys (для записи макроса)."""
    for name, k in skill_keys.items():
        if str(k).strip().lower() == key:
            return "@" + name
    return key


def check_condition(cond, values, events):
    if "event" in cond:
        return cond["event"] in events
    v = values.get(cond.get("value"))
    op = OPS.get(cond.get("op", "<"))
    if v is None or op is None:
        return False
    return op(v, float(cond.get("than", 0)))


def round_delay(d, step=0.05):
    return round(max(0.0, round(d / step) * step), 2)


def fmt_dur(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def safe_filename(text, limit=60):
    return re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", text).strip(" ._")[:limit] or "shot"


class Stats:
    """Статистика сессии. Активное время — без пауз и без времени вне игры."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.started = time.time()
        self.active = 0.0
        self.kills = 0
        self.lost = 0
        self.emergencies = 0
        self.scenarios = {}

    def per_hour(self):
        return self.kills * 3600 / self.active if self.active >= 60 else None

    def short(self):
        ph = self.per_hour()
        rate = f"{ph:.0f}/ч" if ph is not None else "—/ч"
        text = f"Убийств: {self.kills} ({rate})  время {fmt_dur(self.active)}"
        if self.lost:
            text += f"  потеряно: {self.lost}"
        if self.emergencies:
            text += f"  аварий: {self.emergencies}"
        return text

    def summary(self):
        lines = [f"Сессия: всего {fmt_dur(time.time() - self.started)}, активно {fmt_dur(self.active)}",
                 f"Убийств: {self.kills}"]
        if self.lost:
            lines.append(f"Потеряно целей (не убиты): {self.lost}")
        ph = self.per_hour()
        if ph is not None:
            lines.append(f"В час: {ph:.1f}, в среднем {self.active / max(1, self.kills):.1f} c на моба")
        if self.emergencies:
            lines.append(f"Аварийных остановок: {self.emergencies}")
        for name, n in sorted(self.scenarios.items(), key=lambda kv: -kv[1]):
            lines.append(f"Сценарий «{name}»: {n} раз")
        return lines


class EmergencyDetector:
    """Смерть: моё HP = 0 confirm проверок подряд после того, как было > 0.
    Резкое падение: HP упало на drop_percent и больше за drop_seconds."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.alive = False
        self.zero = 0
        self.hist = deque()

    def update(self, hp, now, on_death=True, confirm=3, drop_percent=40.0, drop_seconds=3.0):
        """Возвращает причину аварии или None."""
        if hp > 0:
            self.alive = True
            self.zero = 0
        else:
            self.zero += 1
            if self.alive and self.zero >= confirm:
                self.alive = False
                self.hist.clear()
                return "смерть персонажа" if on_death else None
        self.hist.append((now, hp))
        while self.hist and now - self.hist[0][0] > drop_seconds:
            self.hist.popleft()
        if drop_percent > 0 and hp > 0:
            peak = max(v for _, v in self.hist)
            if peak - hp >= drop_percent:
                self.hist.clear()
                return f"HP упало на {peak - hp:.0f}% за {drop_seconds:g} c"
        return None


def hex_rgb(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def draw_bar_frames(rgb, bars):
    """Рисует на копии кадра рамки вокруг считываемых линий (как на оверлее)."""
    img = np.array(rgb, dtype=np.uint8, copy=True)
    h, w, _ = img.shape
    for key, bar in bars.items():
        if not bar_ready(bar):
            continue
        c = hex_rgb(OVERLAY_COLORS.get(key, "#ffffff"))
        x1, x2 = max(0, bar["x1"] - 5), min(w - 1, bar["x2"] + 5)
        for yy in (bar["y"] - 5, bar["y"] + 5):
            if 0 <= yy < h and x1 <= x2:
                img[yy, x1:x2 + 1] = c
        y1, y2 = max(0, bar["y"] - 5), min(h - 1, bar["y"] + 5)
        for xx in (bar["x1"] - 5, bar["x2"] + 5):
            if 0 <= xx < w and y1 <= y2:
                img[y1:y2 + 1, xx] = c
    return img


# ============================================================
#  Windows: окна, мышь, клавиши
# ============================================================

def set_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    """True — запущена копия с правами администратора, эту надо закрыть."""
    if is_admin():
        return False
    if getattr(sys, "frozen", False):
        params = subprocess.list2cmdline(sys.argv[1:])
    else:
        params = subprocess.list2cmdline([os.path.abspath(sys.argv[0])] + sys.argv[1:])
    r = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, app_dir(), 1)
    if int(r) > 32:
        return True
    log.warning("Работаю без прав администратора — нажатия в игру могут не доходить.")
    return False


def cursor_pos():
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def window_title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def client_rect(hwnd):
    """(x, y, ширина, высота) клиентской области окна на экране или None."""
    if user32.IsIconic(hwnd):
        return None
    r = wt.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(r)) or r.right <= 0 or r.bottom <= 0:
        return None
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y, r.right, r.bottom


SKIP_WINDOW_CLASSES = {"CabinetWClass", "ExploreWClass", "ConsoleWindowClass",
                       "CASCADIA_HOSTING_WINDOW_CLASS", "Chrome_WidgetWin_1",
                       "MozillaWindowClass", "Notepad", "TkTopLevel"}


class GameWindow:
    def __init__(self):
        self.hwnd = None
        self._last_search = 0.0
        self._announced = None

    def find(self, substr):
        sub = substr.lower()
        if self.hwnd and user32.IsWindow(self.hwnd) and sub in window_title(self.hwnd).lower():
            return self.hwnd
        self.hwnd = None
        now = time.time()
        if now - self._last_search < 1.0:
            return None
        self._last_search = now
        own_pid = os.getpid()
        console = kernel32.GetConsoleWindow()
        best = [None, 0]

        def cb(hwnd, _):
            if not user32.IsWindowVisible(hwnd) or hwnd == console:
                return True
            if sub not in window_title(hwnd).lower():
                return True
            if window_pid(hwnd) == own_pid or window_class(hwnd) in SKIP_WINDOW_CLASSES:
                return True
            r = client_rect(hwnd)
            area = r[2] * r[3] if r else 1
            if area > best[1]:
                best[0], best[1] = hwnd, area
            return True

        user32.EnumWindows(WNDENUMPROC(cb), 0)
        self.hwnd = best[0]
        if self.hwnd and self.hwnd != self._announced:
            log.info("Найдено окно игры: «%s»", window_title(self.hwnd))
            self._announced = self.hwnd
        return self.hwnd


_scan_to_name = None


def key_name_from_event(e):
    """Имя клавиши для pydirectinput. При русской раскладке keyboard отдаёт «к»
    вместо «r» — тогда восстанавливаем английское имя по скан-коду."""
    global _scan_to_name
    import pydirectinput
    mapping = pydirectinput.KEYBOARD_MAPPING
    name = (e.name or "").lower()
    name = KEY_ALIASES.get(name, name)
    if getattr(e, "is_keypad", False) and ("num" + name) in mapping:
        return "num" + name
    if name in mapping:
        return name
    if _scan_to_name is None:
        _scan_to_name = {}
        for n, code in mapping.items():
            old = _scan_to_name.get(code)
            if code and (old is None or len(n) < len(old)):
                _scan_to_name[code] = n
    return _scan_to_name.get(e.scan_code)


def press_key(key, hold):
    import pydirectinput
    parts = [p.strip() for p in key.lower().split("+") if p.strip()]
    if not parts:
        return
    *mods, main = parts
    for m in mods:
        pydirectinput.keyDown(m)
    try:
        pydirectinput.keyDown(main)
        time.sleep(hold)
        pydirectinput.keyUp(main)
    finally:
        for m in reversed(mods):
            pydirectinput.keyUp(m)


def grab_rgb(sct, left, top, width, height):
    shot = sct.grab({"left": int(left), "top": int(top), "width": int(width), "height": int(height)})
    arr = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
    return arr[:, :, 2::-1]  # BGRA -> RGB


def read_bar(sct, ox, oy, bar, vis=None):
    """Снимает полосу пикселей под полоской и возвращает результат analyze_bar (+ band, cols)."""
    vis = vis or DEFAULT_CONFIG["vision"]
    x, y, w, h = band_box(bar, vis.get("band", 3))
    band = grab_rgb(sct, ox + x, oy + y, w, h)
    res = read_bar_band(band, bar, vis)
    res["band"] = np.array(band)
    return res


# ============================================================
#  Приложение
# ============================================================

class App:
    def __init__(self):
        self.lock = threading.RLock()
        self.cfg = copy.deepcopy(DEFAULT_CONFIG)
        self.cfg_mtime = 0.0
        self._last_cfg_check = 0.0
        self._hotkeys_registered = None

        self.running = True
        self.paused = False
        self.pause_reason = None
        self.vision = {}            # последнее чтение каждой полоски (для окна «Что видит программа»)
        self.target_absent = 0
        self.target_last_hp = None
        self._track = {"last": 0.0, "cand": None, "hits": 0}
        self._learn_dirty = 0.0
        self.manual_request = None
        self.manual_request_time = 0.0
        self.last_scenario_end = 0.0
        self.ui_requests = deque()
        self.no_target_since = None
        self.recording = False
        self.stats = Stats()
        self.emergency = EmergencyDetector()
        self._reset_emergency = False
        self._sct = None
        self._last_poll = 0.0
        self.executing = None
        self.calib_index = 0

        self.window = GameWindow()
        self.origin = None
        self.window_active = False
        self.values = {}
        self.events = {}
        self.last_run = {}
        self.target_alive = False
        self.zero_count = 0
        self.last_action = "—"

        self._rec_hook = None
        self._rec_events = []
        self._rec_held = set()

    # ---------- конфиг ----------

    def load_config(self, first=False):
        if not os.path.exists(CONFIG_PATH):
            with self.lock:
                self.cfg = copy.deepcopy(DEFAULT_CONFIG)
                self.migrate_config()  # только отметит готовые сценарии как предложенные
            self.save_config()
            log.info("Создан config.json с настройками по умолчанию")
            return True
        try:
            with open(CONFIG_PATH, encoding="utf-8-sig") as f:
                user = json.load(f)
        except (OSError, ValueError) as e:
            log.error("Не удалось прочитать config.json: %s", e)
            if first:
                log.error("Работаю с настройками по умолчанию, файл не перезаписываю.")
            self.beep("error")
            return False
        with self.lock:
            self.cfg = deep_merge(DEFAULT_CONFIG, user)
            self.cfg_mtime = os.path.getmtime(CONFIG_PATH)
            if self.migrate_config():
                self.save_config()
        self.validate_config()
        return True

    def migrate_config(self):
        """Подтягивает новые сценарии по умолчанию в конфиг от прошлых версий.
        Каждый готовый сценарий предлагается один раз: если его потом удалили — не возвращаем."""
        changed = False
        scs = self.cfg.setdefault("scenarios", [])
        offered = self.cfg.get("offered_defaults")
        if not isinstance(offered, list):
            offered = []
        by_name = {s.get("name"): s for s in scs}
        for d in DEFAULT_CONFIG["scenarios"]:
            if d["name"] in offered:
                continue
            offered.append(d["name"])
            changed = True
            cur = by_name.get(d["name"])
            if cur is None:
                new = copy.deepcopy(d)
                sk = self.cfg.get("skill_keys", {})
                missing = sorted({parse_action(a)[0] for a in new.get("actions", [])
                                  if parse_action(a)[0].startswith("@") and not sk.get(parse_action(a)[0][1:])})
                if missing and new.get("enabled", True):
                    new["enabled"] = False
                    log.info("Сценарий «%s» добавлен выключенным: нет кнопок %s — задайте их в настройках",
                             d["name"], ", ".join(missing))
                scs.append(new)
                log.info("В config.json добавлен сценарий «%s» (%s)", d["name"],
                         "включён" if new.get("enabled", True) else "выключен")
            elif not cur.get("actions") and d.get("actions"):
                cur["actions"] = copy.deepcopy(d["actions"])
                log.info("Сценарий «%s»: добавлены действия по умолчанию %s", d["name"], d["actions"])
        self.cfg["offered_defaults"] = offered
        return changed

    def save_config(self):
        with self.lock:
            data = json.dumps(self.cfg, ensure_ascii=False, indent=2)
            tmp = CONFIG_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, CONFIG_PATH)
            self.cfg_mtime = os.path.getmtime(CONFIG_PATH)

    def maybe_reload_config(self):
        now = time.time()
        if now - self._last_cfg_check < 1.0:
            return
        self._last_cfg_check = now
        try:
            m = os.path.getmtime(CONFIG_PATH)
        except OSError:
            return
        if m != self.cfg_mtime:
            self.cfg_mtime = m
            if self.load_config():
                log.info("config.json изменён — настройки перечитаны")
                if self.cfg["hotkeys"] != self._hotkeys_registered:
                    self.register_hotkeys()

    def validate_config(self):
        import pydirectinput
        mapping = pydirectinput.KEYBOARD_MAPPING
        for sc in self.cfg.get("scenarios", []):
            name = sc.get("name", "?")
            for item in sc.get("actions", []):
                try:
                    key, _ = parse_action(item)
                except Exception:
                    log.warning("Сценарий «%s»: непонятное действие %r", name, item)
                    continue
                if key == "wait":
                    continue
                real = resolve_key(key, self.cfg.get("skill_keys", {}))
                if real is None:
                    log.warning("Сценарий «%s»: кнопка %s не задана в skill_keys", name, key)
                    continue
                key = real
                bad = [p for p in key.split("+") if p.strip() not in mapping]
                if bad:
                    log.warning("Сценарий «%s»: неизвестная клавиша %s", name, ", ".join(bad))
            for c in sc.get("conditions", []):
                if "event" in c:
                    if c["event"] not in EVENTS:
                        log.warning("Сценарий «%s»: неизвестное событие %r", name, c["event"])
                elif c.get("value") not in VALUE_KEYS or c.get("op", "<") not in OPS:
                    log.warning("Сценарий «%s»: непонятное условие %r", name, c)
                elif c["value"] == "no_target_sec":
                    if not bar_ready(self.cfg["bars"].get("target_hp")):
                        log.warning("Сценарий «%s»: полоска HP цели ещё не откалибрована", name)
                elif not bar_ready(self.cfg["bars"].get(c["value"])):
                    log.warning("Сценарий «%s»: полоска %s ещё не откалибрована",
                                name, BAR_NAMES[c["value"]])

    # ---------- горячие клавиши ----------

    def register_hotkeys(self):
        import keyboard
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        handlers = {
            "calib_left": self.calib_left,
            "calib_right": self.calib_right,
            "calib_next_bar": self.calib_next_bar,
            "auto_find": self.auto_find,
            "bind_window": self.bind_window,
            "record": self.toggle_record,
            "dry_run": self.toggle_dry_run,
            "stats_reset": self.stats_reset,
            "run_now": self.request_run_now,
            "settings": self.open_settings,
            "reload": self.reload,
            "pause": self.toggle_pause,
            "exit": self.stop,
        }
        hk = self.cfg["hotkeys"]
        for name, fn in handlers.items():
            combo = hk.get(name)
            if not combo:
                continue
            try:
                keyboard.add_hotkey(combo, self._in_thread(fn))
            except Exception as e:
                log.error("Не удалось назначить %s на «%s»: %s", name, combo, e)
        self._hotkeys_registered = copy.deepcopy(hk)

    def _in_thread(self, fn):
        def run():
            try:
                fn()
            except Exception:
                log.exception("Ошибка в %s", fn.__name__)
                self.beep("error")
        return lambda: threading.Thread(target=run, daemon=True).start()

    def print_help(self):
        hk = self.cfg["hotkeys"]
        rows = [
            ("bind_window", "привязаться к активному окну (нажать в игре)"),
            ("auto_find", "автопоиск полосок (цель с полным HP, свои полоски полные)"),
            ("calib_next_bar", "выбрать полоску для ручной калибровки"),
            ("calib_left", "левый край полоски под курсором"),
            ("calib_right", "правый край полоски под курсором"),
            ("record", "запись последовательности клавиш вкл/выкл"),
            ("dry_run", "тестовый режим вкл/выкл (без нажатий)"),
            ("stats_reset", "вывести статистику и обнулить"),
            ("run_now", "проверка: сразу выполнить сценарий «после убийства»"),
            ("settings", "окно настроек"),
            ("pause", "пауза / продолжить (и снять аварийную паузу)"),
            ("reload", "перечитать config.json"),
            ("exit", "выход"),
        ]
        log.info("HP Watcher %s. Горячие клавиши:", VERSION)
        for key, text in rows:
            log.info("  %-14s — %s", hk.get(key, "-"), text)

    # ---------- окно ----------

    def get_origin(self):
        title = self.cfg.get("window_title", "").strip()
        if not title:
            return (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)), True
        hwnd = self.window.find(title)
        if not hwnd:
            return None, False
        return client_rect(hwnd), user32.GetForegroundWindow() == hwnd

    def window_is_active(self):
        if not self.cfg.get("window_title", "").strip():
            return True
        return bool(self.window.hwnd) and user32.GetForegroundWindow() == self.window.hwnd

    def bind_window(self):
        hwnd = user32.GetForegroundWindow()
        if not hwnd or window_pid(hwnd) == os.getpid():
            log.warning("Переключитесь в окно игры и нажмите сочетание там.")
            self.beep("error")
            return
        title = window_title(hwnd).strip()
        if not title:
            log.warning("У активного окна нет заголовка — привязать не получится.")
            self.beep("error")
            return
        with self.lock:
            self.cfg["window_title"] = title
            self.window.hwnd = hwnd
            self.save_config()
        log.info("Привязано к окну «%s». Если в заголовке меняется ник персонажа — "
                 "оставьте в config.json только постоянную часть.", title)
        self.beep("calib")

    # ---------- калибровка ----------

    def _cursor_rel(self):
        origin, _ = self.get_origin()
        if origin is None:
            log.warning("Окно игры не найдено (window_title = «%s»). "
                        "Нажмите %s в окне игры, чтобы привязаться.",
                        self.cfg.get("window_title"), self.cfg["hotkeys"].get("bind_window"))
            self.beep("error")
            return None, None
        x, y = cursor_pos()
        return origin, (x - origin[0], y - origin[1])

    def calib_next_bar(self):
        self.calib_index = (self.calib_index + 1) % len(BAR_KEYS)
        log.info("Калибруем: %s", BAR_NAMES[BAR_KEYS[self.calib_index]])
        self.beep("calib")

    def calib_left(self):
        key = BAR_KEYS[self.calib_index]
        origin, rel = self._cursor_rel()
        if origin is None:
            return
        with self.lock:
            bar = self.cfg["bars"][key]
            bar["x1"], bar["y"] = rel
            if bar.get("x2", 0) <= bar["x1"]:
                bar["x2"] = 0
            self.save_config()
        log.info("[%s] левый край: x=%d y=%d. Теперь правый край.", BAR_NAMES[key], *rel)
        self.beep("calib")

    def calib_right(self):
        import mss
        key = BAR_KEYS[self.calib_index]
        origin, rel = self._cursor_rel()
        if origin is None:
            return
        with self.lock:
            bar = self.cfg["bars"][key]
            if rel[0] <= bar.get("x1", 0) or (bar.get("x1", 0) == 0 and bar.get("y", 0) == 0):
                log.warning("[%s] сначала отметьте левый край, правый должен быть правее него.",
                            BAR_NAMES[key])
                self.beep("error")
                return
            if abs(rel[1] - bar["y"]) > 3:
                log.warning("[%s] правый край на %d px выше/ниже левого — беру высоту левого.",
                            BAR_NAMES[key], abs(rel[1] - bar["y"]))
            bar["x2"] = rel[0]
            with mss.mss() as sct:
                line = grab_rgb(sct, origin[0] + bar["x1"], origin[1] + bar["y"],
                                bar["x2"] - bar["x1"] + 1, 1)[0]
            bar["color"], bar["empty_color"] = bar_colors(line, int(bar.get("tolerance", 60)))
            self.save_config()
        log.info("[%s] правый край: x=%d, ширина %d px, цвет полоски %s. "
                 "Полоска при калибровке должна быть заполнена хотя бы на треть.",
                 BAR_NAMES[key], rel[0], bar["x2"] - bar["x1"] + 1, tuple(bar["color"]))
        self.beep("calib")

    def auto_find(self):
        import mss
        origin, _ = self.get_origin()
        if origin is None:
            log.warning("Окно игры не найдено — сначала привяжитесь (%s).",
                        self.cfg["hotkeys"].get("bind_window"))
            self.beep("error")
            return
        ox, oy, w, h = origin
        with mss.mss() as sct:
            rgb = grab_rgb(sct, ox, oy, w, max(1, int(h * 0.30)))
        found = auto_find_bars(rgb)
        ok = 0
        with self.lock:
            for key in BAR_KEYS:
                res = found.get(key)
                if res is None:
                    log.warning("Автопоиск: %s не найдено", BAR_NAMES[key])
                    continue
                y, x1, x2, color = res
                self.cfg["bars"][key].update(x1=x1, x2=x2, y=y, color=color,
                                             empty_color=default_empty(color))
                ok += 1
                log.info("Автопоиск: %s — x %d…%d, y %d, цвет %s",
                         BAR_NAMES[key], x1, x2, y, tuple(color))
            if ok:
                self.save_config()
        if ok:
            log.info("Проверьте рамки на оверлее. Если правый край короче полоски — "
                     "значит она была неполной; откалибруйте её вручную.")
        self.beep("calib" if ok else "error")

    # ---------- пауза / выход / перечитать ----------

    def toggle_pause(self):
        if self.paused:
            self._reset_emergency = True  # детектор сбросит рабочий поток
            self.events = {}
            self.pause_reason = None
            self.paused = False
            log.info("Продолжаю")
            self.beep("resume")
        else:
            self.paused = True
            log.info("ПАУЗА")
            self.beep("pause")

    def emergency_stop(self, reason):
        self.paused = True
        self.pause_reason = reason
        self.stats.emergencies += 1
        log.warning("!!! АВАРИЙНАЯ ПАУЗА: %s. Продолжить — %s", reason, self.cfg["hotkeys"].get("pause"))
        self.beep("alarm", repeats=int(self.cfg["emergency"].get("alarm_repeats", 3)))
        if self.cfg["screenshots"].get("on_emergency", True):
            self.screenshot("АВАРИЯ " + reason)

    def request_run_now(self):
        name = self.cfg.get("record_into") or "После убийства моба"
        sc = next((s for s in self.cfg.get("scenarios", []) if s.get("name") == name), None)
        if sc is None or not sc.get("actions"):
            log.warning("Сценарий «%s» не найден или пуст", name)
            self.beep("error")
            return
        self.request_run(name)

    def request_run(self, name):
        """Выполнить сценарий вне очереди, как только окно игры станет активным (ждём до 15 с)."""
        self.manual_request = name
        self.manual_request_time = time.time()
        log.info("Проверка: выполню «%s», когда окно игры будет активно и Ctrl/Alt отпущены", name)

    def open_settings(self):
        self.ui_requests.append("settings")

    def apply_config(self, new_cfg):
        """Применить настройки из окна: сохранить, проверить, переназначить горячие клавиши."""
        with self.lock:
            self.cfg = deep_merge(DEFAULT_CONFIG, new_cfg)
            self.save_config()
        log.info("Настройки сохранены")
        self.validate_config()
        if self.cfg["hotkeys"] != self._hotkeys_registered:
            self.register_hotkeys()
        self.beep("calib")

    def wait_modifiers_released(self, timeout=2.0):
        """Если пользователь держит Ctrl/Alt/Shift, игра получит Ctrl+F1 вместо F1.
        Ждём, пока отпустит (не дольше timeout)."""
        try:
            import keyboard
        except Exception:
            return
        end = time.time() + timeout
        while time.time() < end and any(keyboard.is_pressed(m) for m in ("ctrl", "alt", "shift")):
            time.sleep(0.02)

    def toggle_dry_run(self):
        with self.lock:
            self.cfg["dry_run"] = not self.cfg.get("dry_run", False)
            self.save_config()
        on = self.cfg["dry_run"]
        log.info("ТЕСТОВЫЙ РЕЖИМ: клавиши не нажимаются, только лог" if on else "Тестовый режим выключен")
        self.beep("test_on" if on else "test_off")

    def stats_reset(self):
        self.log_stats()
        self.stats.reset()
        log.info("Статистика обнулена")
        self.beep("calib")

    def log_stats(self):
        for line in self.stats.summary():
            log.info("  %s", line)

    # ---------- скриншоты ----------

    def screenshot(self, reason):
        """Вызывать из рабочего потока: снимок делается сразу, сохраняется в фоне."""
        sc = self.cfg.get("screenshots", {})
        if not sc.get("enabled", True) or self._sct is None or self.origin is None:
            return
        ox, oy, w, h = self.origin
        try:
            rgb = grab_rgb(self._sct, ox, oy, w, h)
        except Exception:
            return
        bars = copy.deepcopy(self.cfg["bars"])
        threading.Thread(target=self._save_shot, args=(rgb, reason, bars, sc),
                         daemon=True).start()

    def _save_shot(self, rgb, reason, bars, sc):
        import mss.tools
        try:
            folder = os.path.join(app_dir(), sc.get("folder", "screenshots"))
            os.makedirs(folder, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d_%H-%M-%S") + f".{int(time.time() * 1000) % 1000:03d}"
            path = os.path.join(folder, f"{stamp}_{safe_filename(reason)}.png")
            img = draw_bar_frames(rgb, bars)
            mss.tools.to_png(img.tobytes(), (img.shape[1], img.shape[0]), output=path)
            log.info("  скриншот: %s", os.path.relpath(path, app_dir()))
            keep = int(sc.get("keep", 200))
            if keep > 0:
                shots = sorted(f for f in os.listdir(folder) if f.lower().endswith(".png"))
                for old in shots[:-keep]:
                    os.remove(os.path.join(folder, old))
        except Exception:
            log.exception("Не удалось сохранить скриншот")

    def reload(self):
        if self.load_config():
            self.register_hotkeys()
            log.info("config.json перечитан")
            self.beep("calib")

    def stop(self):
        log.info("Выход по горячей клавише")
        self.running = False

    # ---------- запись макроса ----------

    def toggle_record(self):
        import keyboard
        if not self.recording:
            self._rec_events = []
            self._rec_held = set()
            self.recording = True
            self._rec_hook = keyboard.hook(self._on_rec_event)
            log.info("ЗАПИСЬ: нажимайте клавиши в нужном темпе. Остановить — %s.",
                     self.cfg["hotkeys"].get("record"))
            self.beep("rec_on")
            return

        self.recording = False
        if self._rec_hook is not None:
            keyboard.unhook(self._rec_hook)
            self._rec_hook = None
        ev = list(self._rec_events)
        if not ev:
            log.info("Запись остановлена — ничего не нажато.")
            self.beep("rec_off")
            return
        actions = []
        for i, (key, t) in enumerate(ev):
            delay = ev[i + 1][1] - t if i + 1 < len(ev) else self.cfg["record_last_delay"]
            actions.append([named_key(key, self.cfg.get("skill_keys", {})), round_delay(delay)])
        target = self.cfg.get("record_into") or "Записанный сценарий"
        with self.lock:
            sc = next((s for s in self.cfg["scenarios"] if s.get("name") == target), None)
            if sc is None:
                sc = {"name": target, "enabled": False, "priority": 50, "cooldown": 0,
                      "conditions": [{"event": "target_died"}], "actions": []}
                self.cfg["scenarios"].append(sc)
                log.info("Создан сценарий «%s» (выключен — включите в config.json).", target)
            sc["actions"] = actions
            self.save_config()
        log.info("Записано %d нажатий в «%s»: %s", len(actions), target,
                 ", ".join(f"{k} ({d} c)" for k, d in actions))
        self.beep("rec_off")

    def _on_rec_event(self, e):
        import keyboard
        if not self.recording:
            return
        if e.event_type == keyboard.KEY_UP:
            self._rec_held.discard(e.scan_code)
            return
        if e.scan_code in self._rec_held:  # автоповтор зажатой клавиши
            return
        self._rec_held.add(e.scan_code)
        name = key_name_from_event(e)
        if not name or name in MODIFIERS or (e.name or "").lower() in MODIFIERS:
            return
        if keyboard.is_pressed("ctrl") or keyboard.is_pressed("alt"):
            return  # это наши горячие клавиши
        if keyboard.is_pressed("shift"):
            name = "shift+" + name
        self._rec_events.append((name, e.time))
        log.info("  запись: %s", name)

    # ---------- основной цикл ----------

    def beep(self, kind, repeats=1):
        if not IS_WIN or not self.cfg.get("sound", True):
            return
        seq = BEEPS.get(kind, []) * max(1, repeats)

        def play():
            for f, d in seq:
                winsound.Beep(f, d)
        threading.Thread(target=play, daemon=True).start()

    def can_continue(self):
        if not self.running or self.paused or self.recording:
            return False
        return not self.cfg.get("only_when_window_active", True) or self.window_is_active()

    def sleep_interruptible(self, seconds):
        """Пауза внутри сценария. Экран при этом продолжает читаться,
        так что аварийная остановка срабатывает и посреди последовательности."""
        end = time.time() + seconds
        while True:
            self.poll()
            left = end - time.time()
            if left <= 0:
                return True
            if not self.can_continue():
                return False
            time.sleep(min(0.05, left))

    def pick_scenario(self, values, now):
        if now - self.last_scenario_end < float(self.cfg.get("min_gap", 0.2)):
            return None
        cands = [s for s in self.cfg.get("scenarios", [])
                 if s.get("enabled", True) and s.get("actions")]
        cands.sort(key=lambda s: s.get("priority", 100))
        for s in cands:
            conds = s.get("conditions") or []
            repeat = float(s.get("cooldown", 0) or 0)
            if not any("event" in c for c in conds):
                repeat = max(repeat, MIN_REPEAT)  # защита от нажатий каждый кадр
            if now - self.last_run.get(s.get("name"), 0) < repeat:
                continue
            if all(check_condition(c, values, self.events) for c in conds):
                return s
        return None

    def run_scenario(self, sc):
        name = sc.get("name", "?")
        dry = bool(self.cfg.get("dry_run", False))
        for c in sc["conditions"]:
            if "event" in c:
                self.events.pop(c["event"], None)
        self.last_run[name] = time.time()
        self.stats.scenarios[name] = self.stats.scenarios.get(name, 0) + 1
        self.executing = name
        log.info("▶ %s%s", name, "  [ТЕСТ — без нажатий]" if dry else "")
        self.beep("action")
        if self.cfg["screenshots"].get("on_scenario", True):
            self.screenshot(name)
        ok = True
        hold = float(self.cfg.get("key_hold", 0.05))
        skill_keys = self.cfg.get("skill_keys", {})
        try:
            for item in sc["actions"]:
                key, delay = parse_action(item)
                if not self.can_continue():
                    ok = False
                    break
                if key != "wait":
                    real = resolve_key(key, skill_keys)
                    label = key if real == key else f"{key} ({real})"
                    if real is None:
                        log.warning("  %s не задана в skill_keys — пропускаю", key)
                    elif dry:
                        log.info("  [тест] нажал бы %s, пауза %g c", label, delay)
                    else:
                        self.wait_modifiers_released()
                        press_key(real, hold)
                        log.info("  нажал %s", label)
                if not self.sleep_interruptible(delay):
                    ok = False
                    break
        finally:
            self.executing = None
            self.last_scenario_end = time.time()
            if self.no_target_since is not None:
                self.no_target_since = time.time()  # «цели нет» считаем от последней попытки
        self.last_action = f"{name}, {time.strftime('%H:%M:%S')}" + ("" if ok else " (прервано)")
        if not ok:
            reason = self.pause_reason or "пауза, выход или окно игры не активно"
            log.info("  прервано (%s)", reason)

    def poll(self, force=False):
        """Читает экран, обновляет значения, статистику, события и аварийный детектор."""
        cfg = self.cfg
        now = time.time()
        interval = float(cfg.get("check_interval", 0.1))
        if self._sct is None or (not force and now - self._last_poll < interval * 0.9):
            return
        dt = now - self._last_poll if self._last_poll else 0.0
        self._last_poll = now

        self.maybe_reload_config()
        cfg = self.cfg
        if self._reset_emergency:
            self.emergency.reset()
            self._reset_emergency = False

        origin, active = self.get_origin()
        self.origin, self.window_active = origin, active
        if origin is None:
            self.values = {}
            return
        ox, oy, _, _ = origin
        vis = cfg.get("vision", DEFAULT_CONFIG["vision"])
        vals, seen = {}, {}
        for key in BAR_KEYS:
            bar = cfg["bars"].get(key)
            if not bar_ready(bar):
                continue
            try:
                res = read_bar(self._sct, ox, oy, bar, vis)
            except Exception:
                continue  # полоска за пределами экрана
            seen[key] = res
            if res["status"] == "ok":
                vals[key] = res["value"]  # недостоверное и отсутствующее в условия не попадает
                if vis.get("learn_empty", True):
                    new = learn_empty(res["cols"], res, bar.get("empty_color") or default_empty(bar["color"]))
                    if new and new != bar.get("empty_color"):
                        bar["empty_color"] = new
                        self._learn_dirty = self._learn_dirty or now
        self.vision = seen
        if self._learn_dirty and now - self._learn_dirty > 30:
            self._learn_dirty = 0.0
            self.save_config()  # выученный цвет пустоты переживёт перезапуск

        tres = seen.get("target_hp")
        tstat = tres["status"] if tres else None
        t = vals.get("target_hp")
        if bar_ready(cfg["bars"].get("target_hp")) and tstat is not None:
            if tstat == "ok" and t > 0:
                self.no_target_since = None
                vals["no_target_sec"] = 0.0
            elif tstat in ("ok", "absent"):
                if self.no_target_since is None:
                    self.no_target_since = now
                vals["no_target_sec"] = now - self.no_target_since
            elif self.no_target_since is not None:  # unsure — не знаем, продолжаем отсчёт
                vals["no_target_sec"] = now - self.no_target_since
        self.values = vals
        if tstat == "absent" and vis.get("track_moved", True):
            self.track_target_bar(ox, oy, origin, now)
        elif tstat == "ok":
            self._track.update(cand=None, hits=0)

        if active and not self.paused and not self.recording and dt < 2:
            self.stats.active += dt

        confirm = int(cfg["dead_confirm"])
        if tstat == "ok":
            self.target_absent = 0
            if t > 0:
                self.target_alive = True
                self.zero_count = 0
                self.target_last_hp = t
            else:
                self.zero_count += 1
                if self.target_alive and self.zero_count >= confirm:
                    self.on_target_killed("HP 0, окно цели на месте")
        elif tstat == "absent":
            self.zero_count = 0
            self.target_absent += 1
            if self.target_alive and self.target_absent >= confirm:
                last = self.target_last_hp if self.target_last_hp is not None else 100
                if last < float(vis.get("kill_if_vanished_below", 15)):
                    self.on_target_killed(f"окно цели закрылось на {last:.0f}%")
                else:
                    self.on_target_lost(last)
        # unsure — ничего не решаем, ждём ясной картинки
        ttl = float(cfg["event_ttl"])
        self.events = {k: v for k, v in self.events.items() if now - v <= ttl}

        # Аварийный детектор — только когда игра на экране и чтение достоверно
        hp = vals.get("my_hp")
        if hp is not None and active and not self.paused:
            em = cfg["emergency"]
            reason = self.emergency.update(
                hp, now,
                on_death=bool(em.get("on_death", True)),
                confirm=int(cfg["dead_confirm"]),
                drop_percent=float(em.get("hp_drop_percent", 0)),
                drop_seconds=float(em.get("hp_drop_seconds", 3)),
            )
            if reason:
                self.emergency_stop(reason)

    def on_target_killed(self, how):
        self.target_alive = False
        self.target_last_hp = None
        # на паузе убийство считаем, но сценарий по нему потом не запускаем
        if not self.paused and not self.recording:
            self.events["target_died"] = time.time()
        self.stats.kills += 1
        log.info("Цель убита (#%d) — %s", self.stats.kills, how)
        if self.cfg["screenshots"].get("on_kill", False):
            self.screenshot(f"убийство {self.stats.kills}")

    def on_target_lost(self, last):
        self.target_alive = False
        self.target_last_hp = None
        if not self.paused and not self.recording:
            self.events["target_lost"] = time.time()
        self.stats.lost += 1
        log.info("Цель потеряна, не убита (HP было %.0f%%) — моба перехватили, сняли цель или он убежал", last)
        if self.cfg["screenshots"].get("on_kill", False):
            self.screenshot("цель потеряна")

    def track_target_bar(self, ox, oy, origin, now):
        """Окно цели могли передвинуть: ищем полоску той же длины рядом с прежним местом.
        Новое место принимаем, только если нашли его 2 раза подряд в одной точке."""
        tr = self._track
        if now - tr["last"] < 1.0:
            return
        tr["last"] = now
        bar = self.cfg["bars"]["target_hp"]
        n = bar["x2"] - bar["x1"] + 1
        rx, ry = 160, 100
        _, _, ww, wh = origin
        x0, y0 = max(0, bar["x1"] - rx), max(0, bar["y"] - ry)
        x1, y1 = min(ww, bar["x2"] + rx + 1), min(wh, bar["y"] + ry + 1)
        if x1 - x0 < n + 2 or y1 <= y0:
            return
        try:
            region = grab_rgb(self._sct, ox + x0, oy + y0, x1 - x0, y1 - y0)
        except Exception:
            return
        others = [self.cfg["bars"][k] for k in BAR_KEYS if k != "target_hp" and bar_ready(self.cfg["bars"][k])]
        cands = []
        for (x, y) in find_moved_bar(region, bar, n):
            ax, ay = x + x0, y + y0
            if any(abs(ay - o["y"]) <= 6 and ax <= o["x2"] + 6 and ax + n >= o["x1"] - 6 for o in others):
                continue  # это наша собственная полоска HP/MP
            if abs(ax - bar["x1"]) <= 1 and abs(ay - bar["y"]) <= 1:
                continue
            cands.append((ax, ay))
        if not cands:
            tr.update(cand=None, hits=0)
            return
        # полоска обычно 2–6 строк: берём точку посередине группы
        cands.sort(key=lambda c: (abs(c[0] - bar["x1"]) + abs(c[1] - bar["y"])))
        ax = cands[0][0]
        ys = sorted(c[1] for c in cands if c[0] == ax)
        best = (ax, ys[len(ys) // 3])
        if tr["cand"] and abs(tr["cand"][0] - best[0]) <= 1 and abs(tr["cand"][1] - best[1]) <= 2:
            tr["hits"] += 1
        else:
            tr.update(cand=best, hits=1)
        if tr["hits"] >= 2:
            dx, dy = best[0] - bar["x1"], best[1] - bar["y"]
            with self.lock:
                bar["x1"] += dx
                bar["x2"] += dx
                bar["y"] += dy
                self.save_config()
            tr.update(cand=None, hits=0)
            log.info("Окно цели сдвинуто на (%+d, %+d) — полоска найдена на новом месте", dx, dy)
            self.beep("calib")

    def tick(self):
        self.poll(force=True)
        if self.origin is None or self.paused or self.recording:
            return
        if self.cfg.get("only_when_window_active", True) and not self.window_active:
            return
        if self.manual_request and time.time() - self.manual_request_time > 15:
            log.info("Проверка «%s» отменена: окно игры так и не стало активным", self.manual_request)
            self.manual_request = None
        if self.manual_request:
            name, self.manual_request = self.manual_request, None
            sc = next((s for s in self.cfg.get("scenarios", []) if s.get("name") == name), None)
            if sc:
                self.run_scenario(sc)
            return
        sc = self.pick_scenario(self.values, time.time())
        if sc:
            self.run_scenario(sc)

    def worker(self):
        import mss
        with mss.mss() as sct:
            self._sct = sct
            while self.running:
                t0 = time.time()
                try:
                    self.tick()
                except Exception:
                    log.exception("Ошибка в основном цикле")
                    time.sleep(1)
                interval = float(self.cfg.get("check_interval", 0.1))
                time.sleep(max(0.01, interval - (time.time() - t0)))
            self._sct = None

    # ---------- для оверлея ----------

    def status_text(self):
        if self.recording:
            state = "ЗАПИСЬ"
        elif self.paused:
            state = f"АВАРИЯ: {self.pause_reason}" if self.pause_reason else "ПАУЗА"
        elif self.executing:
            state = f"выполняю: {self.executing}"
        else:
            state = "работает"
        if self.origin is None:
            win = "игра не найдена"
        elif not self.cfg.get("window_title", "").strip():
            win = "весь экран"
        elif self.window_active:
            win = "игра активна"
        else:
            win = "игра не активна, жду"

        def pct(k):
            r = self.vision.get(k)
            if r is None:
                return "  —"
            if r["status"] == "absent":
                return " нет"
            if r["status"] == "unsure":
                return "  ? "
            return f"{r['value']:3.0f}%"

        test = "  [ТЕСТ]" if self.cfg.get("dry_run") else ""
        return "\n".join([
            f"HP Watcher · {state}{test}",
            f"Окно: {win}",
            f"Цель HP: {pct('target_hp')}",
            f"Моё HP:  {pct('my_hp')}   MP: {pct('my_mp')}",
            self.stats.short(),
            self.keys_line(),
            f"Калибровка: {BAR_NAMES[BAR_KEYS[self.calib_index]]}",
            f"Последнее: {self.last_action}",
        ])

    def keys_line(self):
        sk = self.cfg.get("skill_keys", {})
        names = {"attack": "атака", "next_target": "след. цель"}
        parts = [f"{names.get(n, n)} {str(k).upper()}" for n, k in sk.items() if k]
        return "Кнопки: " + (", ".join(parts) if parts else "не заданы")

    def status_colors(self):
        """Фон окна статуса: красный — авария, жёлтый — тестовый режим."""
        if self.paused and self.pause_reason:
            return "#7a1414"
        if self.cfg.get("dry_run"):
            return "#5a4a10"
        return "#1d1f24"


# ============================================================
#  Оверлей: окно статуса + рамки вокруг считываемых линий
# ============================================================

class Overlay:
    KEY = "#ff00fe"  # этот цвет становится прозрачным
    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000

    def __init__(self, app):
        import tkinter as tk
        self.app = app
        self.tk = tk
        self.show = bool(app.cfg.get("overlay", True))
        self.root = tk.Tk()
        self.root.title("HP Watcher")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.9)
        self.root.configure(bg="#1d1f24")
        x, y = app.cfg.get("status_window_pos", [20, 200])
        self.root.geometry(f"+{int(x)}+{int(y)}")
        self.label = tk.Label(self.root, text="", justify="left", anchor="w",
                              font=("Consolas", 10), fg="#e6e6e6", bg="#1d1f24",
                              padx=10, pady=6, cursor="fleur")
        self.label.pack(fill="x")
        self.label.bind("<ButtonPress-1>", self._drag_start)
        self.label.bind("<B1-Motion>", self._drag_move)
        self.label.bind("<ButtonRelease-1>", self._drag_end)
        self.label.bind("<Double-Button-1>", lambda e: app.toggle_pause())

        self.btns = tk.Frame(self.root, bg="#1d1f24")
        self.btns.pack(fill="x", padx=6, pady=(0, 6))
        self.buttons = []
        for text, cmd in (("⚙ Настройки", app.open_settings),
                          ("⏸ Пауза", lambda: threading.Thread(target=app.toggle_pause, daemon=True).start()),
                          ("Тест", lambda: threading.Thread(target=app.toggle_dry_run, daemon=True).start())):
            b = tk.Label(self.btns, text=text, font=("Segoe UI", 9), fg="#e6e6e6", bg="#30343c",
                         padx=8, pady=2, cursor="hand2")
            b.pack(side="left", padx=2)
            b.bind("<Button-1>", lambda e, cmd=cmd: cmd())
            b.bind("<Enter>", lambda e, b=b: b.config(bg="#475060"))
            b.bind("<Leave>", lambda e, b=b: b.config(bg="#30343c"))
            self.buttons.append(b)
        self.root.update_idletasks()
        self._style(self.root, self.WS_EX_TOOLWINDOW | self.WS_EX_NOACTIVATE)
        if not self.show:
            self.root.withdraw()  # окно статуса выключено, но настройки по Ctrl+Alt+S работают

        self.frames = None
        if self.show and app.cfg.get("overlay_frames", True):
            self.vx, self.vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
            vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
            self.frames = tk.Toplevel(self.root)
            self.frames.overrideredirect(True)
            self.frames.attributes("-topmost", True)
            self.frames.configure(bg=self.KEY)
            self.frames.attributes("-transparentcolor", self.KEY)
            self.frames.geometry(f"{vw}x{vh}+{self.vx}+{self.vy}")
            self.canvas = tk.Canvas(self.frames, bg=self.KEY, highlightthickness=0)
            self.canvas.pack(fill="both", expand=True)
            self.frames.update_idletasks()
            self._style(self.frames, self.WS_EX_LAYERED | self.WS_EX_TRANSPARENT |
                        self.WS_EX_TOOLWINDOW | self.WS_EX_NOACTIVATE)
        self._last_topmost = 0.0

    def _hwnd(self, w):
        return user32.GetParent(w.winfo_id()) or w.winfo_id()

    def _style(self, w, flags):
        hwnd = self._hwnd(w)
        style = user32.GetWindowLongW(hwnd, self.GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, self.GWL_EXSTYLE, style | flags)

    def _keep_on_top(self):
        # HWND_TOPMOST без активации, чтобы не забирать фокус у игры
        if not self.show:
            return
        for w in (self.root, self.frames):
            if w is not None:
                user32.SetWindowPos(self._hwnd(w), wt.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _drag_end(self, e):
        with self.app.lock:
            self.app.cfg["status_window_pos"] = [self.root.winfo_x(), self.root.winfo_y()]
            self.app.save_config()

    def _draw_frames(self):
        c = self.canvas
        c.delete("all")
        app = self.app
        if app.origin is None:
            return
        ox, oy = app.origin[0] - self.vx, app.origin[1] - self.vy
        current = BAR_KEYS[app.calib_index]
        for key in BAR_KEYS:
            bar = app.cfg["bars"].get(key)
            if not bar_ready(bar):
                continue
            x1, x2, y = ox + bar["x1"], ox + bar["x2"], oy + bar["y"]
            color = OVERLAY_COLORS[key]
            width = 2 if key == current else 1
            # рамка не задевает саму считываемую линию (y)
            c.create_rectangle(x1 - 5, y - 5, x2 + 5, y + 5, outline=color, width=width)
            r = app.vision.get(key)
            if r is None:
                text = BAR_NAMES[key]
            elif r["status"] == "ok":
                text = f"{BAR_NAMES[key]} {r['value']:.0f}%"
            elif r["status"] == "unsure":
                text = f"{BAR_NAMES[key]} ? ({r['agree']:.0f}%)"
            else:
                text = f"{BAR_NAMES[key]}: нет"
            c.create_text(x2 + 10, y, text=text, anchor="w", fill=color, font=("Consolas", 9, "bold"))

    def _refresh(self):
        if not self.app.running:
            self.root.destroy()
            return
        while self.app.ui_requests:
            req = self.app.ui_requests.popleft()
            if req == "settings":
                try:
                    import settings_ui
                    settings_ui.SettingsWindow.open(self.root, self.app)
                except Exception:
                    log.exception("Не удалось открыть настройки")
        bg = self.app.status_colors()
        self.label.config(text=self.app.status_text(), bg=bg)
        self.root.configure(bg=bg)
        self.btns.configure(bg=bg)
        self.buttons[1].config(text="▶ Продолжить" if self.app.paused else "⏸ Пауза")
        self.buttons[2].config(text="Тест: вкл" if self.app.cfg.get("dry_run") else "Тест: выкл")
        if self.frames is not None:
            self._draw_frames()
        now = time.time()
        if now - self._last_topmost > 2:
            self._keep_on_top()
            self._last_topmost = now
        self.root.after(100, self._refresh)

    def run(self):
        self.root.after(100, self._refresh)
        self.root.mainloop()


# ============================================================

def setup_logging():
    log.setLevel(logging.INFO)
    fh = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s"))
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
    log.addHandler(fh)
    log.addHandler(ch)


def main():
    if not IS_WIN:
        print("HP Watcher работает только в Windows.")
        return 1
    set_dpi_awareness()
    setup_logging()
    app = App()
    app.load_config(first=True)
    if app.cfg.get("require_admin", True) and "--no-admin" not in sys.argv:
        if relaunch_as_admin():
            return 0

    import keyboard
    import pydirectinput
    pydirectinput.PAUSE = 0
    pydirectinput.FAILSAFE = False

    app.register_hotkeys()
    app.print_help()
    if not any(bar_ready(b) for b in app.cfg["bars"].values()):
        log.info("Полоски ещё не настроены. Зайдите в игру, нажмите %s (привязка окна), "
                 "возьмите моба в цель и нажмите %s (автопоиск).",
                 app.cfg["hotkeys"]["bind_window"], app.cfg["hotkeys"]["auto_find"])

    if app.cfg.get("dry_run"):
        log.info("Включён ТЕСТОВЫЙ РЕЖИМ — клавиши не нажимаются (%s — выключить).",
                 app.cfg["hotkeys"].get("dry_run"))
    if not bar_ready(app.cfg["bars"]["my_hp"]):
        log.info("Аварийная пауза не работает, пока не откалибровано «моё HP».")

    threading.Thread(target=app.worker, daemon=True).start()
    app.beep("start")

    try:
        Overlay(app).run()
    except Exception:
        log.exception("Оверлей не запустился, работаю без него")
    while app.running:
        time.sleep(0.2)
    time.sleep(0.3)
    keyboard.unhook_all()
    log.info("Итоги:")
    app.log_stats()
    log.info("Выход.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        input("\nОшибка. Нажмите Enter, чтобы закрыть окно...")
        sys.exit(1)
