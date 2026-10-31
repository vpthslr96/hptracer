# -*- coding: utf-8 -*-
"""
Окно настроек HP Watcher.

Вкладки:
  Охота          — сценарии: когда срабатывать, как часто, доп. условия, шаги «клавиша → пауза»
  Кнопки панели  — именованные клавиши (@attack, @next_target, свои)
  Полоски        — привязка окна, автопоиск, допуск цвета, живые значения
  Общие          — тестовый режим, интервалы, аварийная пауза, скриншоты, горячие клавиши
"""

import copy
import json
import time
import tkinter as tk
from tkinter import messagebox, ttk

import hp_watcher as hw

# ------------------------------------------------------------
#  Чистая логика (тестируется без окна)
# ------------------------------------------------------------

TRIGGERS = [
    ("kill", "После убийства цели"),
    ("lost", "Цель потеряна (не убита)"),
    ("combat", "Во время боя (цель жива)"),
    ("no_target", "Нет цели"),
    ("timer", "По таймеру / по условию"),
]
TRIGGER_LABEL = dict(TRIGGERS)
TRIGGER_BY_LABEL = {v: k for k, v in TRIGGERS}
TRIGGER_HINT = {
    "kill": "Один раз после каждого убийства. Обычно: следующая цель → атака, подобрать дроп.",
    "lost": "Окно цели закрылось, а HP было заметным: моба перехватили, сняли цель Esc, моб убежал. "
            "Обычно: следующая цель → атака.",
    "combat": "Пока у цели есть HP, повторяется с указанным интервалом. Для атакующих скиллов.",
    "no_target": "Если цели нет указанное время: next target не нашёл моба, моба перехватили.",
    "timer": "Каждые N секунд, если выполнены доп. условия (или всегда, если условий нет). "
             "Для баффов, банок, саммонов. Банка: условие «Моё HP < 40». "
             "Бафф вне боя: условие «Цели нет ≥ 1».",
}
REPEAT_HINT = {
    "kill": "0 — после каждого убийства",
    "lost": "0 — после каждой потери",
    "combat": "например 5 — скилл раз в 5 с",
    "no_target": "не чаще, чем раз в столько секунд",
    "timer": "1200 = раз в 20 минут",
}

VALUE_LABELS = [
    ("target_hp", "HP цели, %"),
    ("my_hp", "Моё HP, %"),
    ("my_mp", "Моё MP, %"),
    ("no_target_sec", "Цели нет, сек"),
    ("target_stuck_sec", "Моб без урона, сек"),
]
VALUE_LABEL = dict(VALUE_LABELS)
VALUE_BY_LABEL = {v: k for k, v in VALUE_LABELS}
OPS = ["<", "<=", ">", ">=", "==", "!="]
VALUE_SHORT = {"target_hp": "HP цели", "my_hp": "моё HP", "my_mp": "моё MP", "no_target_sec": "нет цели, с",
               "target_stuck_sec": "моб без урона, с"}


def describe_conds(conds):
    return ", ".join(f"{VALUE_SHORT.get(c.get('value'), c.get('value'))} {c.get('op')} {fnum(c.get('than')):g}"
                     for c in conds)

SKILL_HINTS = {"attack": "Атака", "next_target": "Следующая цель (/nexttarget)",
               "pickup": "Поднять (подбор дропа)", "potion": "Банка HP"}

HOTKEY_LABELS = [
    ("settings", "Окно настроек"),
    ("clicker_toggle", "Автокликер вкл/выкл"),
    ("pause", "Пауза / продолжить"),
    ("dry_run", "Тестовый режим вкл/выкл"),
    ("run_now", "Проверить «После убийства моба»"),
    ("record", "Запись макроса"),
    ("bind_window", "Привязать окно игры"),
    ("auto_find", "Автопоиск полосок"),
    ("calib_next_bar", "Выбрать полоску для калибровки"),
    ("calib_left", "Левый край полоски"),
    ("calib_right", "Правый край полоски"),
    ("stats_reset", "Статистика и обнуление"),
    ("reload", "Перечитать config.json"),
    ("exit", "Выход"),
]

COMMON_KEYS = (["f%d" % i for i in range(1, 13)] + list("1234567890") + ["-", "="]
               + ["esc", "enter", "space", "tab"] + list("qwertyuiopasdfghjklzxcvbnm"))

TK_KEYSYMS = {
    "escape": "esc", "return": "enter", "kp_enter": "enter", "space": "space", "tab": "tab",
    "backspace": "backspace", "delete": "delete", "insert": "insert", "home": "home", "end": "end",
    "prior": "pageup", "next": "pagedown", "up": "up", "down": "down", "left": "left", "right": "right",
    "minus": "-", "equal": "=", "grave": "`", "bracketleft": "[", "bracketright": "]",
    "semicolon": ";", "comma": ",", "period": ".", "slash": "/", "backslash": "\\",
    "kp_add": "add", "kp_subtract": "subtract", "kp_multiply": "multiply", "kp_divide": "divide",
    "pause": "pause", "caps_lock": "capslock", "scroll_lock": "scrolllock",
}
MOD_KEYSYMS = {"control_l": "ctrl", "control_r": "ctrl", "alt_l": "alt", "alt_r": "alt",
               "shift_l": "shift", "shift_r": "shift", "meta_l": "alt", "meta_r": "alt"}


def split_conditions(conds):
    """conditions из конфига -> (тип, параметр типа, доп. условия)."""
    conds = list(conds or [])
    for i, c in enumerate(conds):
        if c.get("event") == "target_died":
            return "kill", 0, conds[:i] + conds[i + 1:]
        if c.get("event") == "target_lost":
            return "lost", 0, conds[:i] + conds[i + 1:]
    for i, c in enumerate(conds):
        if c.get("value") == "target_hp" and c.get("op") == ">" and float(c.get("than", 0)) == 0:
            return "combat", 0, conds[:i] + conds[i + 1:]
    for i, c in enumerate(conds):
        if c.get("value") == "no_target_sec" and c.get("op") in (">=", ">"):
            # «Нет цели» — только если это единственное такое условие и нет события
            rest = conds[:i] + conds[i + 1:]
            return "no_target", float(c.get("than", 4)), rest
    return "timer", 0, conds


def build_conditions(trigger, param, extras):
    if trigger == "kill":
        head = [{"event": "target_died"}]
    elif trigger == "lost":
        head = [{"event": "target_lost"}]
    elif trigger == "combat":
        head = [{"value": "target_hp", "op": ">", "than": 0}]
    elif trigger == "no_target":
        head = [{"value": "no_target_sec", "op": ">=", "than": float(param)}]
    else:
        head = []
    return head + [dict(c) for c in extras]


def key_token(text):
    """Из «@attack  (F1)» берём «@attack»; пробелы и регистр убираем."""
    text = (text or "").strip()
    return text.split()[0].lower() if text else ""


def tk_key_name(keysym):
    k = (keysym or "").lower()
    if k in MOD_KEYSYMS:
        return None
    if k in TK_KEYSYMS:
        return TK_KEYSYMS[k]
    if len(k) >= 2 and k[0] == "f" and k[1:].isdigit():
        return k
    if k.startswith("kp_") and k[3:].isdigit():
        return k[3:]
    if len(k) == 1:
        return k
    return None


def combo_name(mods, keysym):
    main = tk_key_name(keysym)
    if not main:
        return None
    order = [m for m in ("ctrl", "alt", "shift") if m in mods]
    return "+".join(order + [main])


MOUSE_CHOICES = ["click:left", "click:right", "click:middle", "dblclick:left", "click:left*3"]
BUTTON_LABELS = [("left", "Левая (ЛКМ)"), ("right", "Правая (ПКМ)"), ("middle", "Средняя (СКМ)")]


def check_key(key, skill_keys, mapping):
    """Пустая строка — ок, иначе текст ошибки."""
    if not key:
        return "пустая клавиша"
    if key in ("wait", "wait_target", "find_target"):
        return ""
    if hw.parse_mouse(key):
        return ""
    if key.startswith(("click", "dblclick", "move")):
        return "непонятный шаг мыши (примеры: click:left, click:right@120,340, click:left*5)"
    if key.startswith("@"):
        if not skill_keys.get(key[1:]):
            return f"кнопка {key} не задана на вкладке «Кнопки панели»"
        key = str(skill_keys[key[1:]]).lower()
    bad = [p for p in key.split("+") if p.strip() not in mapping]
    return f"неизвестная клавиша {', '.join(bad)}" if bad else ""


CLS_COLORS = {0: (235, 120, 40), 1: (70, 200, 100), 2: (120, 120, 135)}  # чужое, заливка, пусто


def vision_rgb(res, width=640, top_h=12, strip_h=6):
    """Увеличенная полоса, которую сняла программа, и под ней — как она поняла каждый столбец.
    Белая черта — найденная граница заливки."""
    import numpy as np
    band = np.asarray(res.get("band"))
    if band.ndim != 3 or band.shape[1] == 0:
        return None
    h, n, _ = band.shape
    sx = max(1, width // n)
    top = np.repeat(band, max(1, top_h // h), axis=0)
    cls = np.asarray(res.get("cls", np.zeros(n)), dtype=int)
    strip = np.array([CLS_COLORS.get(int(c), (0, 0, 0)) for c in cls], dtype=np.uint8)[None, :, :]
    strip = np.repeat(strip, strip_h, axis=0)
    gap = np.full((2, n, 3), 30, np.uint8)
    img = np.concatenate([top.astype(np.uint8), gap, strip], axis=0)
    img = np.repeat(img, sx, axis=1)
    if res.get("status") in ("ok", "unsure"):
        x = min(img.shape[1] - 2, int(res.get("split", 0)) * sx)
        img[:, max(0, x - 1):x + 1] = 255
    return img


def vision_text(res):
    if res is None:
        return "нет данных — игра не найдена или полоска не настроена"
    st = res.get("status")
    if st == "ok":
        return f"{res['value']:.0f}%   ·   достоверно ({res['agree']:.0f}% согласия)"
    if st == "unsure":
        return f"НЕДОСТОВЕРНО — согласие {res['agree']:.0f}%, значение не используется"
    return f"полоски нет на экране (похожих столбцов {res['coverage']:.0f}%)"


def png_b64(img):
    import base64
    import mss.tools
    data = mss.tools.to_png(img.tobytes(), (img.shape[1], img.shape[0]))
    return base64.b64encode(data).decode("ascii")


def fnum(v, default=0.0):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------
#  Вспомогательные виджеты
# ------------------------------------------------------------

class ScrollFrame(ttk.Frame):
    def __init__(self, parent, height=200):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, height=height)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")
        for w in (self.canvas, self.inner):
            w.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
            w.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120), "units")

    def clear(self):
        for w in self.inner.winfo_children():
            w.destroy()


def key_capture(parent, callback):
    """Маленькое окно «нажмите клавишу». callback(имя) — например «f3» или «ctrl+1»."""
    win = tk.Toplevel(parent)
    win.title("Клавиша")
    win.transient(parent)
    win.resizable(False, False)
    ttk.Label(win, text="Нажмите клавишу или сочетание…", padding=(24, 18, 24, 8)).pack()
    ttk.Button(win, text="Отмена", command=win.destroy).pack(pady=(0, 14))
    mods = set()

    def down(e):
        k = (e.keysym or "").lower()
        if k in MOD_KEYSYMS:
            mods.add(MOD_KEYSYMS[k])
            return "break"
        name = combo_name(mods, e.keysym)
        if name:
            callback(name)
            win.destroy()
        return "break"

    def up(e):
        k = (e.keysym or "").lower()
        if k in MOD_KEYSYMS:
            mods.discard(MOD_KEYSYMS[k])
        return "break"

    win.bind("<KeyPress>", down)
    win.bind("<KeyRelease>", up)
    win.update_idletasks()
    x = parent.winfo_rootx() + parent.winfo_width() // 2 - win.winfo_width() // 2
    y = parent.winfo_rooty() + parent.winfo_height() // 2 - win.winfo_height() // 2
    win.geometry(f"+{max(0, x)}+{max(0, y)}")
    try:
        win.grab_set()
    except tk.TclError:
        pass
    win.focus_force()
    return win


# ------------------------------------------------------------
#  Окно настроек
# ------------------------------------------------------------

class SettingsWindow:
    instance = None

    @classmethod
    def open(cls, root, app):
        if cls.instance is not None and cls.instance.win.winfo_exists():
            w = cls.instance.win
            w.deiconify()
            w.lift()
            w.focus_force()
            return cls.instance
        cls.instance = cls(root, app)
        return cls.instance

    def __init__(self, root, app):
        self.root = root
        self.app = app
        self.cfg = copy.deepcopy(app.cfg)
        # список показываем в порядке реального приоритета (выше — важнее), порядок сохраняем при равенстве
        self.cfg["scenarios"] = sorted(self.cfg.get("scenarios", []), key=lambda s: s.get("priority", 100))
        self.saved_snapshot = self._snapshot()
        self.sel = None          # индекс выбранного сценария
        self._loading = False    # не писать в модель, пока заполняем поля
        self._recording = None

        self.win = tk.Toplevel(root)
        self.win.title(f"HP Watcher {hw.VERSION} — настройки")
        self.win.geometry("1060x700")
        self.win.minsize(960, 600)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        try:
            ttk.Style(self.win).theme_use("vista")
        except tk.TclError:
            pass

        nb = ttk.Notebook(self.win)
        nb.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self.tab_hunt = ttk.Frame(nb, padding=8)
        self.tab_keys = ttk.Frame(nb, padding=8)
        self.tab_bars = ttk.Frame(nb, padding=8)
        self.tab_general = ttk.Frame(nb, padding=8)
        self.tab_clicker = ttk.Frame(nb, padding=8)
        nb.add(self.tab_hunt, text="  Охота  ")
        nb.add(self.tab_keys, text="  Кнопки панели  ")
        nb.add(self.tab_clicker, text="  Кликер и ввод  ")
        nb.add(self.tab_bars, text="  Полоски  ")
        nb.add(self.tab_general, text="  Общие  ")
        self.nb = nb

        bottom = ttk.Frame(self.win, padding=8)
        bottom.pack(fill="x")
        self.msg = ttk.Label(bottom, text="", foreground="#a33")
        self.msg.pack(side="left", fill="x", expand=True)
        ttk.Button(bottom, text="Закрыть", command=self.close).pack(side="right")
        ttk.Button(bottom, text="Сохранить и закрыть", command=lambda: self.save(close=True)).pack(side="right", padx=4)
        ttk.Button(bottom, text="Сохранить", command=self.save).pack(side="right")

        self.gvars = []  # (путь, переменная, тип) — поля вкладок «Полоски» и «Общие»
        self.build_keys_tab()
        self.build_hunt_tab()
        self.build_bars_tab()
        self.build_clicker_tab()
        self.build_general_tab()
        self.refresh_scen_list(select=0)
        self._tick()
        self.win.focus_force()

    # ---------- общее ----------

    def _snapshot(self):
        return json.dumps(self.cfg, sort_keys=True, ensure_ascii=False)

    def dirty(self):
        self.collect_general()
        return self._snapshot() != self.saved_snapshot

    def mapping(self):
        return hw.known_keys()

    def skill_keys(self):
        return self.cfg.setdefault("skill_keys", {})

    def key_choices(self):
        sk = self.skill_keys()
        named = [f"@{n}  ({str(k).upper()})" for n, k in sk.items() if k]
        return named + COMMON_KEYS + MOUSE_CHOICES + ["wait", "wait_target", "find_target"]

    def validate(self):
        errors = []
        mapping = self.mapping()
        names = [s.get("name", "").strip() for s in self.cfg["scenarios"]]
        for i, s in enumerate(self.cfg["scenarios"]):
            name = names[i] or f"#{i + 1}"
            if not names[i]:
                errors.append(f"Сценарий #{i + 1}: нет названия")
            elif names.count(names[i]) > 1:
                errors.append(f"Сценарий «{name}»: название повторяется")
            for j, (k, _) in enumerate(hw.parse_action(a) for a in s.get("actions", [])):
                err = check_key(k, self.skill_keys(), mapping) if mapping else ""
                if err:
                    errors.append(f"«{name}», шаг {j + 1}: {err}")
            if s.get("enabled", True) and not s.get("actions"):
                errors.append(f"«{name}»: включён, но шагов нет")
        ck = self.cfg.get("clicker", {})
        if ck.get("enabled") and self.v_point_mode.get() == "point" and not ck.get("point"):
            errors.append("Автокликер: выбрана «в точке», но точка не задана")
        if ck.get("enabled") and ck.get("mode") == "hold" and check_key(ck.get("hold_key", ""), {}, mapping):
            errors.append(f"Автокликер: клавиша зажатия — {check_key(ck.get('hold_key', ''), {}, mapping)}")
        for n, k in self.skill_keys().items():
            if not n or " " in n:
                errors.append(f"Кнопка панели «{n}»: имя без пробелов, латиницей")
            elif k and mapping and check_key(str(k).lower(), {}, mapping):
                errors.append(f"Кнопка @{n}: {check_key(str(k).lower(), {}, mapping)}")
        return errors

    def save(self, close=False):
        self.collect_general()
        errors = self.validate()
        if errors:
            self.msg.config(text=errors[0] + (f"  (и ещё {len(errors) - 1})" if len(errors) > 1 else ""))
            messagebox.showerror("Не сохранено", "Исправьте:\n\n" + "\n".join(errors[:15]), parent=self.win)
            return False
        for i, s in enumerate(self.cfg["scenarios"]):
            s["priority"] = (i + 1) * 10  # выше в списке — важнее
        new = copy.deepcopy(self.cfg)
        # Калибровку могли поменять горячими клавишами, пока окно открыто — берём свежую,
        # из окна переносим только допуск цвета.
        live = copy.deepcopy(self.app.cfg["bars"])
        for k, bar in live.items():
            bar["tolerance"] = self.cfg["bars"].get(k, {}).get("tolerance", bar.get("tolerance", 60))
        new["bars"] = live
        new["status_window_pos"] = self.app.cfg.get("status_window_pos", new.get("status_window_pos"))
        self.app.apply_config(new)
        self.cfg = copy.deepcopy(self.app.cfg)
        self.saved_snapshot = self._snapshot()
        self.msg.config(text="Сохранено " + time.strftime("%H:%M:%S"), foreground="#286b2e")
        if close:
            self.destroy()
        return True

    def close(self):
        if self.dirty():
            r = messagebox.askyesnocancel("Несохранённые изменения", "Сохранить изменения?", parent=self.win)
            if r is None:
                return
            if r and not self.save():
                return
        self.destroy()

    def destroy(self):
        self.stop_recording()
        SettingsWindow.instance = None
        self.win.destroy()

    def _tick(self):
        if not self.win.winfo_exists():
            return
        self.update_bars_live()
        self.update_clicker_live()
        self.win.after(300, self._tick)

    # ========================================================
    #  Вкладка «Охота»
    # ========================================================

    def build_hunt_tab(self):
        t = self.tab_hunt
        t.columnconfigure(1, weight=1)
        t.rowconfigure(0, weight=1)

        left = ttk.Frame(t)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 10))
        ttk.Button(left, text="⚡ Быстрая настройка охоты…", command=self.open_hunt_wizard).pack(fill="x", pady=(0, 6))
        ttk.Label(left, text="Сценарии (выше — важнее)").pack(anchor="w")
        cols = ("on", "name", "when", "every")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=16, selectmode="browse")
        for c, text, w in (("on", "", 26), ("name", "Название", 150), ("when", "Когда", 160), ("every", "Повтор", 64)):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor="w", stretch=False)
        self.tree.pack(fill="y", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-Button-1>", self.on_toggle_click)
        bar = ttk.Frame(left)
        bar.pack(fill="x", pady=(6, 0))
        for text, cmd in (("+ Новый", self.scen_add), ("Копия", self.scen_copy), ("Удалить", self.scen_delete),
                          ("▲", lambda: self.scen_move(-1)), ("▼", lambda: self.scen_move(1))):
            ttk.Button(bar, text=text, command=cmd, width=8 if len(text) > 1 else 3).pack(side="left", padx=1)

        self.empty_label = ttk.Label(t, text="Выберите сценарий слева или создайте новый.", foreground="#777")
        self.ed = ttk.Frame(t)
        self.ed.grid(row=0, column=1, sticky="nsew")
        self.ed.columnconfigure(1, weight=1)
        e = self.ed

        self.v_name = tk.StringVar()
        self.v_enabled = tk.BooleanVar()
        self.v_trigger = tk.StringVar()
        self.v_param = tk.StringVar()
        self.v_repeat = tk.StringVar()

        r = 0
        ttk.Label(e, text="Название").grid(row=r, column=0, sticky="w", pady=3)
        top = ttk.Frame(e)
        top.grid(row=r, column=1, sticky="ew")
        top.columnconfigure(0, weight=1)
        ttk.Entry(top, textvariable=self.v_name).grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(top, text="Включён", variable=self.v_enabled).grid(row=0, column=1, padx=10)
        ttk.Button(top, text="▶ Проверить в игре", command=self.scen_test).grid(row=0, column=2)

        r += 1
        ttk.Label(e, text="Когда").grid(row=r, column=0, sticky="w", pady=3)
        when = ttk.Frame(e)
        when.grid(row=r, column=1, sticky="w")
        ttk.Combobox(when, textvariable=self.v_trigger, values=[l for _, l in TRIGGERS],
                     state="readonly", width=28).pack(side="left")
        self.param_box = ttk.Frame(when)
        ttk.Label(self.param_box, text="  уже").pack(side="left")
        ttk.Spinbox(self.param_box, textvariable=self.v_param, from_=0.5, to=600, increment=0.5,
                    width=6).pack(side="left", padx=4)
        ttk.Label(self.param_box, text="сек").pack(side="left")

        r += 1
        self.hint = ttk.Label(e, text="", foreground="#555", wraplength=500, justify="left")
        self.hint.grid(row=r, column=1, sticky="w", pady=(0, 6))
        e.bind("<Configure>", lambda ev: self.hint.config(wraplength=max(300, ev.width - 140)))

        r += 1
        ttk.Label(e, text="Повторять").grid(row=r, column=0, sticky="w", pady=3)
        rep = ttk.Frame(e)
        rep.grid(row=r, column=1, sticky="w")
        ttk.Label(rep, text="не чаще, чем раз в").pack(side="left")
        ttk.Spinbox(rep, textvariable=self.v_repeat, from_=0, to=36000, increment=0.5, width=8).pack(side="left", padx=4)
        ttk.Label(rep, text="сек").pack(side="left")
        r += 1
        self.repeat_hint = ttk.Label(e, text="", foreground="#555")
        self.repeat_hint.grid(row=r, column=1, sticky="w")

        r += 1
        ttk.Separator(e).grid(row=r, column=0, columnspan=2, sticky="ew", pady=6)
        r += 1
        ttk.Label(e, text="Доп. условия").grid(row=r, column=0, sticky="nw", pady=3)
        cbox = ttk.Frame(e)
        cbox.grid(row=r, column=1, sticky="ew")
        self.cond_rows = ttk.Frame(cbox)
        self.cond_rows.pack(fill="x")
        ttk.Button(cbox, text="+ Условие", command=self.cond_add).pack(anchor="w", pady=(2, 0))

        r += 1
        ttk.Separator(e).grid(row=r, column=0, columnspan=2, sticky="ew", pady=6)
        r += 1
        ttk.Label(e, text="Шаги").grid(row=r, column=0, sticky="nw", pady=3)
        e.rowconfigure(r, weight=1)
        sbox = ttk.Frame(e)
        sbox.grid(row=r, column=1, sticky="nsew")
        hdr = ttk.Frame(sbox)
        hdr.pack(fill="x")
        ttk.Label(hdr, text="№", width=3).pack(side="left")
        ttk.Label(hdr, text="Клавиша", width=24).pack(side="left", padx=(4, 0))
        ttk.Label(hdr, text="Пауза после, сек").pack(side="left", padx=(36, 0))
        self.steps = ScrollFrame(sbox, height=190)
        self.steps.pack(fill="both", expand=True)
        sb = ttk.Frame(sbox)
        sb.pack(fill="x", pady=(4, 0))
        ttk.Button(sb, text="+ Нажатие", command=lambda: self.step_add(["f1", 0.3])).pack(side="left")
        ttk.Button(sb, text="+ Пауза", command=lambda: self.step_add(["wait", 1.0])).pack(side="left", padx=4)
        ttk.Button(sb, text="+ Клик", command=lambda: self.step_add(["click:left", 0.2])).pack(side="left")
        ttk.Button(sb, text="+ Ждать цель", command=lambda: self.step_add(["wait_target", 2.0])).pack(side="left", padx=4)
        self.rec_btn = ttk.Button(sb, text="⏺ Записать с клавиатуры", command=self.toggle_recording)
        self.rec_btn.pack(side="left")
        self.rec_label = ttk.Label(sb, text="", foreground="#a33")
        self.rec_label.pack(side="left", padx=8)

        for v in (self.v_name, self.v_enabled, self.v_trigger, self.v_param, self.v_repeat):
            v.trace_add("write", lambda *a: self.editor_changed())

    # ---------- быстрая настройка охоты ----------

    def open_hunt_wizard(self):
        p = {**hw.HUNT_DEFAULTS, **(self.cfg.get("hunt") or {})}
        sk = self.skill_keys()
        for k in ("attack", "next_target", "pickup", "potion"):
            if sk.get(k):
                p[k] = sk[k]
        win = tk.Toplevel(self.win)
        win.title("Быстрая настройка охоты")
        win.transient(self.win)
        win.resizable(False, False)
        f = ttk.Frame(win, padding=14)
        f.pack(fill="both", expand=True)
        f.columnconfigure(3, weight=1)  # лишняя ширина строк-«пролётов» уходит в колонку подсказок
        ttk.Label(f, wraplength=520, justify="left", text=(
            "Цикл: взять цель → бежать и бить → убил → подобрать дроп → следующая цель… "
            "Плюс банка, когда HP ниже порога. Укажите клавиши, на которых это стоит у вас на панели."
        )).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))
        v = {}

        def key_row(r, name, text, hint=""):
            ttk.Label(f, text=text).grid(row=r, column=0, sticky="w", pady=3)
            v[name] = tk.StringVar(value=str(p.get(name) or ""))
            ttk.Entry(f, textvariable=v[name], width=10).grid(row=r, column=1, sticky="w")
            ttk.Button(f, text="⌨", width=3, command=lambda: key_capture(win, v[name].set)).grid(row=r, column=2, padx=4)
            if hint:
                ttk.Label(f, text=hint, foreground="#555").grid(row=r, column=3, sticky="w")

        def num_row(r, name, text, lo, hi, step, unit):
            row = ttk.Frame(f)
            row.grid(row=r, column=0, columnspan=4, sticky="w", pady=2, padx=(20, 0))
            ttk.Label(row, text=text).pack(side="left")
            v[name] = tk.StringVar(value=f"{fnum(p.get(name)):g}")
            ttk.Spinbox(row, textvariable=v[name], from_=lo, to=hi, increment=step, width=6).pack(side="left", padx=4)
            ttk.Label(row, text=unit).pack(side="left")

        key_row(1, "attack", "Атака", "«Атака» из окна действий (Alt+C)")
        key_row(2, "next_target", "Взять цель", "/nexttarget или макрос с /target Имя моба")
        tm = ttk.Frame(f)
        tm.grid(row=3, column=0, columnspan=4, sticky="w", pady=(10, 0))
        ttk.Label(tm, text="Как брать цель:").pack(side="left")
        v["target_mode"] = tk.StringVar(value=p.get("target_mode", "nexttarget"))
        ttk.Radiobutton(tm, text="клавишей, без мыши (рекомендуется)", value="nexttarget",
                        variable=v["target_mode"]).pack(side="left", padx=(8, 0))
        ttk.Radiobutton(tm, text="зрением (клик мышью)", value="vision",
                        variable=v["target_mode"]).pack(side="left", padx=(8, 0))
        tt = ttk.Frame(f)
        tt.grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))
        ttk.Button(tt, text="Проверить поиск мобов (через 3 с)",
                   command=lambda: self.countdown(scan_label, "Переключитесь в игру", lambda: self.test_scan(scan_label))
                   ).pack(side="left")
        scan_label = ttk.Label(tt, text="", foreground="#286b2e", wraplength=360, justify="left")
        scan_label.pack(side="left", padx=8)
        num_row(5, "search_after", "Брать цель, если её нет", 0.5, 30, 0.5, "сек (и сразу после запуска)")
        num_row(6, "attack_repeat", "Повторять атаку в бою раз в", 0.5, 30, 0.5, "сек")
        num_row(7, "stuck_after", "Моб не получает урон — сменить цель через", 0, 60, 1, "сек (0 — не менять)")
        key_row(8, "pickup", "Подбор дропа", "«Поднять»; пусто — не подбирать")
        num_row(9, "pickup_times", "После убийства нажать", 0, 20, 1, "раз")
        num_row(10, "pickup_delay", "с паузой", 0.1, 5, 0.1, "сек (персонаж подбегает к дропу)")
        key_row(11, "potion", "Банка HP", "пусто — не пить")
        num_row(12, "potion_hp", "Пить, если моё HP ниже", 1, 99, 5, "%")
        num_row(13, "potion_cooldown", "Не чаще, чем раз в", 1, 600, 1, "сек (откат банки)")
        v["pause_on_hp_drop"] = tk.BooleanVar(value=bool(p.get("pause_on_hp_drop")))
        ttk.Checkbutton(f, variable=v["pause_on_hp_drop"], text="Пауза при резком падении HP"
                        ).grid(row=14, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(f, text="выкл. — пьём банку; пауза на смерть остаётся", foreground="#555"
                  ).grid(row=14, column=3, sticky="w", pady=(6, 0))
        warn = ttk.Label(f, text="", foreground="#a33", wraplength=520, justify="left")
        warn.grid(row=16, column=0, columnspan=4, sticky="w", pady=(8, 0))
        if not hw.bar_ready(self.app.cfg["bars"].get("my_hp")):
            warn.config(text="Полоска «моё HP» не настроена — банка не сработает. Настройте её на вкладке «Полоски».")
        ttk.Label(f, foreground="#555", wraplength=520, justify="left", text=(
            "Сценарии охоты («Банка HP», «После убийства моба», «После потери цели», «Застрял на мобе», "
            "«Атака в бою», «Взять цель») будут заменены и встанут наверх списка. Остальные ваши сценарии останутся ниже."
        )).grid(row=15, column=0, columnspan=4, sticky="w", pady=(8, 0))
        btns = ttk.Frame(f)
        btns.grid(row=17, column=0, columnspan=4, sticky="e", pady=(12, 0))

        def apply():
            q = {}
            mapping = self.mapping()
            for name in ("attack", "next_target", "pickup", "potion"):
                k = key_token(v[name].get())
                if not k and name in ("attack", "next_target"):
                    warn.config(text="Укажите клавиши атаки и следующей цели.")
                    return
                if k and check_key(k, {}, mapping):
                    warn.config(text=f"{k}: {check_key(k, {}, mapping)}")
                    return
                q[name] = k
            for name, lo, hi in (("search_after", 0.5, 30), ("attack_repeat", 0.5, 30), ("stuck_after", 0, 60),
                                 ("pickup_times", 0, 20),
                                 ("pickup_delay", 0.1, 5), ("potion_hp", 1, 99), ("potion_cooldown", 1, 600)):
                q[name] = min(hi, max(lo, fnum(v[name].get(), hw.HUNT_DEFAULTS[name])))
            q["pickup_times"] = int(q["pickup_times"])
            q["pause_on_hp_drop"] = bool(v["pause_on_hp_drop"].get())
            q["target_mode"] = v["target_mode"].get()
            hw.apply_hunt_preset(self.cfg, q)
            self.refresh_gvars()
            win.destroy()
            self.render_skill_keys()
            self.refresh_scen_list(select=0)
            if self.save():
                self.msg.config(text="Охота настроена и сохранена. Переключитесь в игру — программа сама возьмёт цель.",
                                foreground="#286b2e")

        ttk.Button(btns, text="Отмена", command=win.destroy).pack(side="right")
        ttk.Button(btns, text="Применить и сохранить", command=apply).pack(side="right", padx=6)
        win.update_idletasks()
        x = self.win.winfo_rootx() + (self.win.winfo_width() - win.winfo_width()) // 2
        y = self.win.winfo_rooty() + 40
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
        try:
            win.grab_set()
        except tk.TclError:
            pass
        self._hunt_win, self._hunt_vars, self._hunt_apply = win, v, apply

    def test_scan(self, label):
        """Ищет имена мобов на текущем экране игры и сохраняет картинку с найденным в screenshots."""
        import mss
        try:
            with mss.mss() as sct:
                rgb, cands = self.app.scan_targets(sct)
        except Exception as e:
            label.config(text=f"Ошибка: {e}", foreground="#a33")
            return
        if rgb is None:
            label.config(text="Окно игры не найдено — привяжите его на вкладке «Полоски»", foreground="#a33")
            return
        tc = self.app.cfg.get("targeting", {})
        offs = tc.get("click_offsets", [22, 45, 70])
        pts = [(int((b[0] + b[2]) / 2), b[3] + int(offs[0])) for b in cands[:5]]
        img = hw.draw_boxes(rgb, cands, (0, 255, 0), pts)
        folder = hw.os.path.join(hw.app_dir(), self.app.cfg.get("screenshots", {}).get("folder", "screenshots"))
        try:
            import mss.tools
            hw.os.makedirs(folder, exist_ok=True)
            path = hw.os.path.join(folder, "проверка_поиска_мобов.png")
            mss.tools.to_png(img.tobytes(), (img.shape[1], img.shape[0]), output=path)
            where = f" Картинка: {hw.os.path.relpath(path, hw.app_dir())}"
        except Exception:
            where = ""
        if cands:
            b = cands[0]
            label.config(text=f"Найдено мобов: {len(cands)}. Ближайший — имя в ({b[0]}, {b[1]}), клик будет в "
                              f"({pts[0][0]}, {pts[0][1]}).{where}", foreground="#286b2e")
        else:
            label.config(text="Имён мобов не найдено. Включите в игре показ имён монстров." + where, foreground="#a33")

    # ---------- список ----------

    def scen_row(self, s):
        trig, param, extras = split_conditions(s.get("conditions"))
        if trig == "kill":
            when = "После убийства"
        elif trig == "lost":
            when = "Цель потеряна"
        elif trig == "combat":
            when = "В бою"
        elif trig == "no_target":
            when = f"Нет цели ≥ {param:g} с"
        else:
            when = "Всегда" if not extras else ""
        if extras:
            when = (when + ", " if when else "") + describe_conds(extras)
        cd = fnum(s.get("cooldown", 0))
        every = "—" if cd == 0 else (f"{cd / 60:g} мин" if cd >= 120 else f"{cd:g} с")
        return ("✓" if s.get("enabled", True) else "—", s.get("name", ""), when, every)

    def refresh_scen_list(self, select=None):
        self.tree.delete(*self.tree.get_children())
        for i, s in enumerate(self.cfg["scenarios"]):
            self.tree.insert("", "end", iid=str(i), values=self.scen_row(s))
        if select is not None and self.cfg["scenarios"]:
            select = max(0, min(select, len(self.cfg["scenarios"]) - 1))
            self.tree.selection_set(str(select))
            self.tree.see(str(select))
        elif not self.cfg["scenarios"]:
            self.sel = None
            self.load_editor()

    def update_scen_row(self):
        if self.sel is not None and self.tree.exists(str(self.sel)):
            self.tree.item(str(self.sel), values=self.scen_row(self.cfg["scenarios"][self.sel]))

    def on_select(self, _=None):
        cur = self.tree.selection()
        self.sel = int(cur[0]) if cur else None
        self.load_editor()

    def on_toggle_click(self, e):
        if self.tree.identify_column(e.x) == "#1" and self.sel is not None:
            self.v_enabled.set(not self.v_enabled.get())

    def scen_add(self):
        self.cfg["scenarios"].append({"name": self.unique_name("Новый сценарий"), "enabled": True,
                                      "priority": 100, "cooldown": 5,
                                      "conditions": build_conditions("combat", 0, []),
                                      "actions": [["f3", 0.5]]})
        self.refresh_scen_list(select=len(self.cfg["scenarios"]) - 1)

    def scen_copy(self):
        if self.sel is None:
            return
        s = copy.deepcopy(self.cfg["scenarios"][self.sel])
        s["name"] = self.unique_name(s.get("name", "Сценарий") + " (копия)")
        self.cfg["scenarios"].insert(self.sel + 1, s)
        self.refresh_scen_list(select=self.sel + 1)

    def scen_delete(self):
        if self.sel is None:
            return
        name = self.cfg["scenarios"][self.sel].get("name")
        if not messagebox.askyesno("Удалить", f"Удалить сценарий «{name}»?", parent=self.win):
            return
        del self.cfg["scenarios"][self.sel]
        self.refresh_scen_list(select=self.sel)

    def scen_move(self, d):
        i = self.sel
        if i is None or not (0 <= i + d < len(self.cfg["scenarios"])):
            return
        sc = self.cfg["scenarios"]
        sc[i], sc[i + d] = sc[i + d], sc[i]
        self.refresh_scen_list(select=i + d)

    def unique_name(self, base):
        names = {s.get("name") for s in self.cfg["scenarios"]}
        if base not in names:
            return base
        n = 2
        while f"{base} {n}" in names:
            n += 1
        return f"{base} {n}"

    def scen_test(self):
        if self.sel is None:
            return
        if self.dirty() and not self.save():
            return
        name = self.cfg["scenarios"][self.sel].get("name")
        self.app.request_run(name)
        self.msg.config(text=f"Переключитесь в игру в течение 15 секунд — выполню «{name}»",
                        foreground="#286b2e")

    # ---------- редактор ----------

    def current(self):
        return self.cfg["scenarios"][self.sel] if self.sel is not None else None

    def load_editor(self):
        s = self.current()
        self._loading = True
        try:
            if not s:
                self.ed.grid_remove()
                self.empty_label.grid(row=0, column=1, sticky="n", pady=40)
                return
            self.empty_label.grid_remove()
            self.ed.grid()
            trig, param, extras = split_conditions(s.get("conditions"))
            self._extras = [dict(c) for c in extras]
            self.v_name.set(s.get("name", ""))
            self.v_enabled.set(bool(s.get("enabled", True)))
            self.v_trigger.set(TRIGGER_LABEL[trig])
            self.v_param.set(f"{param or 4:g}")
            self.v_repeat.set(f"{fnum(s.get('cooldown', 0)):g}")
            self.update_trigger_ui(trig)
            self.render_conds()
            self.render_steps()
        finally:
            self._loading = False

    def update_trigger_ui(self, trig):
        if trig == "no_target":
            self.param_box.pack(side="left")
        else:
            self.param_box.pack_forget()
        self.hint.config(text=TRIGGER_HINT[trig])
        self.repeat_hint.config(text=REPEAT_HINT[trig])

    def editor_changed(self):
        if self._loading or self.current() is None:
            return
        s = self.current()
        trig = TRIGGER_BY_LABEL.get(self.v_trigger.get(), "timer")
        s["name"] = self.v_name.get().strip()
        s["enabled"] = bool(self.v_enabled.get())
        s["cooldown"] = max(0.0, fnum(self.v_repeat.get(), s.get("cooldown", 0)))
        s["conditions"] = build_conditions(trig, max(0.0, fnum(self.v_param.get(), 4)), self._extras)
        self.update_trigger_ui(trig)
        self.update_scen_row()

    # ---------- доп. условия ----------

    def render_conds(self):
        for w in self.cond_rows.winfo_children():
            w.destroy()
        if not self._extras:
            ttk.Label(self.cond_rows, text="нет — срабатывает только по «Когда»", foreground="#777").pack(anchor="w")
        for i, c in enumerate(self._extras):
            row = ttk.Frame(self.cond_rows)
            row.pack(fill="x", pady=1)
            vv = tk.StringVar(value=VALUE_LABEL.get(c.get("value"), VALUE_LABELS[1][1]))
            vo = tk.StringVar(value=c.get("op", "<"))
            vt = tk.StringVar(value=f"{fnum(c.get('than', 0)):g}")
            ttk.Combobox(row, textvariable=vv, values=[l for _, l in VALUE_LABELS], state="readonly",
                         width=16).pack(side="left")
            ttk.Combobox(row, textvariable=vo, values=OPS, state="readonly", width=4).pack(side="left", padx=4)
            ttk.Spinbox(row, textvariable=vt, from_=0, to=10000, increment=1, width=7).pack(side="left")
            ttk.Button(row, text="✕", width=3, command=lambda i=i: self.cond_del(i)).pack(side="left", padx=4)

            def upd(*_, i=i, vv=vv, vo=vo, vt=vt):
                self._extras[i] = {"value": VALUE_BY_LABEL.get(vv.get(), "my_hp"), "op": vo.get(),
                                   "than": fnum(vt.get())}
                self.editor_changed()
            for v in (vv, vo, vt):
                v.trace_add("write", upd)

    def cond_add(self):
        if self.current() is None:
            return
        self._extras.append({"value": "my_mp", "op": ">", "than": 10})
        self.render_conds()
        self.editor_changed()

    def cond_del(self, i):
        del self._extras[i]
        self.render_conds()
        self.editor_changed()

    # ---------- шаги ----------

    def render_steps(self):
        self.steps.clear()
        s = self.current()
        if s is None:
            return
        acts = s.setdefault("actions", [])
        choices = self.key_choices()
        if not acts:
            ttk.Label(self.steps.inner, text="Шагов нет. Добавьте нажатие или запишите с клавиатуры.",
                      foreground="#777").pack(anchor="w", pady=4)
        for i, a in enumerate(acts):
            key, delay = hw.parse_action(a)
            acts[i] = [key, delay]
            row = ttk.Frame(self.steps.inner)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=f"{i + 1}.", width=3).grid(row=0, column=0)
            shown = next((c for c in choices if key_token(c) == key), key)
            vk = tk.StringVar(value=shown)
            vd = tk.StringVar(value=f"{delay:g}")
            ttk.Combobox(row, textvariable=vk, values=choices, width=18).grid(row=0, column=1, padx=(4, 0))
            ttk.Button(row, text="⌨", width=3,
                       command=lambda vk=vk: key_capture(self.win, vk.set)).grid(row=0, column=2, padx=2)
            ttk.Button(row, text="◎", width=3,
                       command=lambda vk=vk: self.pick_point(lambda p, vk=vk: vk.set(self.with_point(vk.get(), p)))
                       ).grid(row=0, column=3)
            ttk.Spinbox(row, textvariable=vd, from_=0, to=600, increment=0.05, width=6).grid(row=0, column=4, padx=(8, 4))
            for c, (text, cmd) in enumerate((("▲", lambda i=i: self.step_move(i, -1)),
                                             ("▼", lambda i=i: self.step_move(i, 1)),
                                             ("✕", lambda i=i: self.step_del(i))), start=5):
                ttk.Button(row, text=text, width=2, command=cmd).grid(row=0, column=c, padx=1)
            err = ttk.Label(row, text="", foreground="#a33", wraplength=380, justify="left")
            err.grid(row=1, column=1, columnspan=7, sticky="w")

            def upd(*_, i=i, vk=vk, vd=vd, err=err):
                k = key_token(vk.get())
                acts[i] = [k, max(0.0, fnum(vd.get(), 0))]
                e = check_key(k, self.skill_keys(), self.mapping())
                ms = hw.parse_mouse(k)
                if k in ("wait_target", "find_target") and not e:
                    err.config(text=("ждать новую живую цель, но не дольше указанного времени; "
                                     "не появилась — остальные шаги пропускаются") if k == "wait_target" else
                               ("взять цель (как настроено в «Быстрой настройке охоты»: зрением или /nexttarget) "
                                "и дождаться её; не вышло — остальные шаги пропускаются"), foreground="#555")
                    err.grid()
                    return
                if e:
                    err.config(text="⚠ " + e, foreground="#a33")
                elif ms:
                    err.config(text="мышь: " + hw.describe_mouse(ms)
                               + ("" if ms["point"] else " — там, где курсор (◎ — выбрать точку)"),
                               foreground="#555")
                if e or ms:
                    err.grid()
                else:
                    err.grid_remove()
            vk.trace_add("write", upd)
            vd.trace_add("write", upd)
            upd()

    # ---------- точки для мыши ----------

    def with_point(self, text, point):
        """Ставит точку в шаг: мышиный шаг получает @x,y, клавиша превращается в клик ЛКМ в точке."""
        ms = hw.parse_mouse(key_token(text))
        if ms is None:
            ms = {"kind": "click", "button": "left", "count": 1, "point": None}
        ms["point"] = point
        return hw.mouse_spec(**ms)

    def countdown(self, label, text, then, n=3):
        """Обратный отсчёт таймерами окна (без фоновых потоков — tkinter их не любит)."""
        if not self.win.winfo_exists():
            return
        if n > 0:
            label.config(text=f"{text}… {n}", foreground="#286b2e")
            self.win.after(1000, lambda: self.countdown(label, text, then, n - 1))
        else:
            then()

    def pick_point(self, callback):
        """Через 3 секунды запоминает позицию курсора относительно окна игры."""
        def done():
            origin, _ = self.app.get_origin()
            if origin is None:
                self.msg.config(text="Окно игры не найдено — привяжите его на вкладке «Полоски»", foreground="#a33")
                return
            x, y = hw.cursor_pos()
            p = (x - origin[0], y - origin[1])
            if not (0 <= p[0] < origin[2] and 0 <= p[1] < origin[3]):
                self.msg.config(text=f"Курсор вне окна игры ({p[0]}, {p[1]}) — попробуйте ещё раз", foreground="#a33")
                return
            callback(p)
            self.msg.config(text=f"Точка ({p[0]}, {p[1]}) в окне игры запомнена", foreground="#286b2e")
        self.countdown(self.msg, "Наведите курсор на нужное место в игре", done)

    def step_add(self, act):
        s = self.current()
        if s is None:
            return
        s.setdefault("actions", []).append(list(act))
        self.render_steps()
        self.steps.canvas.update_idletasks()
        self.steps.canvas.yview_moveto(1.0)

    def step_del(self, i):
        del self.current()["actions"][i]
        self.render_steps()

    def step_move(self, i, d):
        acts = self.current()["actions"]
        if 0 <= i + d < len(acts):
            acts[i], acts[i + d] = acts[i + d], acts[i]
            self.render_steps()

    # ---------- запись ----------

    def toggle_recording(self):
        if self._recording is None:
            self.start_recording()
        else:
            self.stop_recording(apply=True)

    def start_recording(self):
        if self.current() is None:
            return
        try:
            import keyboard
        except Exception:
            messagebox.showerror("Запись", "Запись недоступна: нет модуля keyboard", parent=self.win)
            return
        rec = {"events": [], "held": set(), "hook": None, "scen": self.sel}

        def on_event(e):
            if e.event_type == keyboard.KEY_UP:
                rec["held"].discard(e.scan_code)
                return
            if e.scan_code in rec["held"]:
                return
            rec["held"].add(e.scan_code)
            name = hw.key_name_from_event(e)
            if not name or name in hw.MODIFIERS or (e.name or "").lower() in hw.MODIFIERS:
                return
            if keyboard.is_pressed("ctrl") or keyboard.is_pressed("alt"):
                return
            if keyboard.is_pressed("shift"):
                name = "shift+" + name
            rec["events"].append((name, e.time))

        rec["hook"] = keyboard.hook(on_event)
        self._recording = rec
        self.app.recording = True  # чтобы утилита сама ничего не нажимала во время записи
        self.rec_btn.config(text="⏹ Остановить запись")
        self._rec_update()

    def _rec_update(self):
        if self._recording is None or not self.win.winfo_exists():
            return
        ev = self._recording["events"]
        self.rec_label.config(text=f"Запись… нажмите клавиши в нужном темпе ({len(ev)}): "
                              + " ".join(k for k, _ in ev[-8:]))
        self.win.after(150, self._rec_update)

    def stop_recording(self, apply=False):
        rec, self._recording = self._recording, None
        if rec is None:
            return
        try:
            import keyboard
            keyboard.unhook(rec["hook"])
        except Exception:
            pass
        self.app.recording = False
        if not self.win.winfo_exists():
            return
        self.rec_btn.config(text="⏺ Записать с клавиатуры")
        self.rec_label.config(text="")
        ev = rec["events"]
        if not apply or not ev or rec["scen"] != self.sel:
            return
        if self.current().get("actions") and not messagebox.askyesno(
                "Запись", f"Записано нажатий: {len(ev)}. Заменить ими текущие шаги?", parent=self.win):
            return
        last = fnum(self.cfg.get("record_last_delay", 0.5), 0.5)
        acts = []
        for i, (k, t) in enumerate(ev):
            d = ev[i + 1][1] - t if i + 1 < len(ev) else last
            acts.append([hw.named_key(k, self.skill_keys()), hw.round_delay(d)])
        self.current()["actions"] = acts
        self.render_steps()

    # ========================================================
    #  Вкладка «Кнопки панели»
    # ========================================================

    def build_keys_tab(self):
        t = self.tab_keys
        ttk.Label(t, wraplength=880, justify="left", text=(
            "Назовите клавиши панели скиллов, и в сценариях можно будет выбирать их по имени (@attack). "
            "Если перенесёте скилл на другую клавишу, достаточно поменять её здесь, а не во всех сценариях.\n"
            "attack и next_target используются в готовых сценариях «После убийства моба» и «Поиск цели». "
            "Поставьте на панель «Атаку» и «Следующую цель» из окна «Действия» (Alt+C)."
        )).pack(anchor="w", pady=(0, 8))
        hdr = ttk.Frame(t)
        hdr.pack(fill="x")
        ttk.Label(hdr, text="Имя (латиницей)", width=22).pack(side="left")
        ttk.Label(hdr, text="Клавиша", width=18).pack(side="left", padx=(6, 0))
        self.keys_box = ScrollFrame(t, height=360)
        self.keys_box.pack(fill="both", expand=True)
        ttk.Button(t, text="+ Добавить кнопку", command=self.skill_add).pack(anchor="w", pady=6)
        self.render_skill_keys()

    def render_skill_keys(self):
        self.keys_box.clear()
        sk = self.skill_keys()
        self._skill_rows = []
        self._row_names = list(sk.keys())
        for name, key in list(sk.items()):
            row = ttk.Frame(self.keys_box.inner)
            row.pack(fill="x", pady=2)
            vn = tk.StringVar(value=name)
            vk = tk.StringVar(value=str(key))
            ttk.Entry(row, textvariable=vn, width=24).pack(side="left")
            ttk.Combobox(row, textvariable=vk, values=COMMON_KEYS, width=14).pack(side="left", padx=(6, 0))
            ttk.Button(row, text="⌨", width=3, command=lambda vk=vk: key_capture(self.win, vk.set)).pack(side="left", padx=2)
            hint = ttk.Label(row, text=SKILL_HINTS.get(name, ""), foreground="#555")
            ttk.Button(row, text="✕", width=3, command=lambda vn=vn: self.skill_del(vn.get())).pack(side="left", padx=2)
            hint.pack(side="left", padx=8)
            self._skill_rows.append((vn, vk))
            for v in (vn, vk):
                v.trace_add("write", lambda *a: self.skill_changed())

    def skill_changed(self):
        new = {}
        for i, (vn, vk) in enumerate(self._skill_rows):
            n = vn.get().strip()
            if not n:
                continue  # имя стёрто, пока печатают новое — помним старое имя строки
            new[n] = key_token(vk.get())
            o = self._row_names[i]
            if o and o != n:
                # переименование: @старое -> @новое во всех сценариях
                for s in self.cfg["scenarios"]:
                    for a in s.get("actions", []):
                        if isinstance(a, list) and a and a[0] == "@" + o:
                            a[0] = "@" + n
                self._row_names[i] = n
        self.cfg["skill_keys"] = new
        if self.current() is not None:
            self.render_steps()

    def skill_add(self):
        sk = self.skill_keys()
        n, i = "skill", 1
        while f"{n}{i}" in sk:
            i += 1
        sk[f"{n}{i}"] = "f3"
        self.render_skill_keys()

    def skill_del(self, name):
        used = [s.get("name") for s in self.cfg["scenarios"]
                if any(hw.parse_action(a)[0] == "@" + name for a in s.get("actions", []))]
        if used and not messagebox.askyesno(
                "Удалить", f"@{name} используется в: {', '.join(used)}.\nВсё равно удалить?", parent=self.win):
            return
        self.skill_keys().pop(name, None)
        self.render_skill_keys()
        if self.current() is not None:
            self.render_steps()

    # ========================================================
    #  Вкладка «Полоски»
    # ========================================================

    def build_bars_tab(self):
        t = self.tab_bars
        hk = self.cfg["hotkeys"]
        top = ttk.LabelFrame(t, text="Окно игры", padding=(8, 4))
        top.pack(fill="x")
        self.v_title = tk.StringVar(value=self.cfg.get("window_title", ""))
        ttk.Label(top, text="Заголовок содержит:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.v_title, width=40).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Button(top, text="Привязать (через 3 с)", command=lambda: self.delayed("bind")).grid(row=0, column=2)
        ttk.Button(top, text="Автопоиск полосок (через 3 с)",
                   command=lambda: self.delayed("auto")).grid(row=0, column=3, padx=6)
        self.delay_label = ttk.Label(top, text="", foreground="#286b2e")
        self.delay_label.grid(row=1, column=0, columnspan=4, sticky="w")
        ttk.Label(top, foreground="#555", wraplength=980, justify="left", text=(
            "Нажмите кнопку и за 3 с переключитесь в игру. Для автопоиска возьмите моба с полным HP, свои HP/MP "
            f"тоже полные. Вручную: {hk.get('calib_next_bar', '').upper()} — выбрать полоску, курсор на левый край → "
            f"{hk.get('calib_left', '').upper()}, на правый → {hk.get('calib_right', '').upper()}."
        )).grid(row=2, column=0, columnspan=4, sticky="w", pady=(2, 0))

        box = ttk.LabelFrame(t, text="Полоски", padding=(8, 4))
        box.pack(fill="x", pady=(6, 0))
        for c, text in enumerate(("", "Положение", "Заливка / пусто", "Допуск цвета")):
            ttk.Label(box, text=text, foreground="#555").grid(row=0, column=c, sticky="w", padx=6)
        self.bar_widgets = {}
        for r, key in enumerate(hw.BAR_KEYS, start=1):
            ttk.Label(box, text=hw.BAR_NAMES[key], width=10).grid(row=r, column=0, sticky="w", padx=6, pady=1)
            pos = ttk.Label(box, text="", width=22)
            pos.grid(row=r, column=1, sticky="w", padx=6)
            sws = ttk.Frame(box)
            sws.grid(row=r, column=2, sticky="w", padx=6)
            sw = tk.Label(sws, text="     ", relief="solid", bd=1)
            sw.pack(side="left")
            sw_e = tk.Label(sws, text="     ", relief="solid", bd=1)
            sw_e.pack(side="left", padx=(3, 0))
            tol = ttk.Frame(box)
            tol.grid(row=r, column=3, sticky="w", padx=6)
            vt = tk.IntVar(value=int(self.cfg["bars"][key].get("tolerance", 60)))
            ttk.Scale(tol, from_=10, to=140, variable=vt, length=200).pack(side="left")
            tl = ttk.Label(tol, text=str(vt.get()), width=4)
            tl.pack(side="left", padx=4)

            def upd(*_, key=key, vt=vt, tl=tl):
                v = int(float(vt.get()))
                tl.config(text=str(v))
                self.cfg["bars"][key]["tolerance"] = v
            vt.trace_add("write", upd)
            self.bar_widgets[key] = {"pos": pos, "sw": sw, "sw_e": sw_e}

        seen = ttk.LabelFrame(t, text="Что видит программа (вживую)", padding=(8, 4))
        seen.pack(fill="x", pady=6)
        legend = ttk.Frame(seen)
        legend.pack(fill="x", pady=(0, 4))
        ttk.Label(legend, text="Сверху — снятые пиксели (увеличено), снизу — как поняты столбцы:",
                  foreground="#555").pack(side="left")
        for cls, text in ((1, "заливка"), (2, "пусто"), (0, "чужое")):
            r_, g_, b_ = CLS_COLORS[cls]
            tk.Label(legend, text="   ", bg=f"#{r_:02x}{g_:02x}{b_:02x}").pack(side="left", padx=(6, 2))
            ttk.Label(legend, text=text, foreground="#555").pack(side="left")
        ttk.Label(legend, text="  │ белая черта — граница", foreground="#555").pack(side="left")
        for key in hw.BAR_KEYS:
            row = ttk.Frame(seen)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=hw.BAR_NAMES[key], width=10).pack(side="left", anchor="n")
            right = ttk.Frame(row)
            right.pack(side="left", fill="x", expand=True)
            img = tk.Label(right, bg="#202226", width=80, height=1, anchor="w")
            img.pack(anchor="w")
            txt = ttk.Label(right, text="—")
            txt.pack(anchor="w")
            self.bar_widgets[key].update(img=img, txt=txt, photo=None)

        vbox = ttk.LabelFrame(t, text="Зрение", padding=(8, 4))
        vbox.pack(fill="x")
        c1, c2, c3 = ttk.Frame(vbox), ttk.Frame(vbox), ttk.Frame(vbox)
        c1.pack(side="left", anchor="n")
        c2.pack(side="left", anchor="n", padx=16)
        c3.pack(side="left", anchor="n", fill="x", expand=True)
        self._g_number(c1, ["vision", "band"], "Строк пикселей (медиана)", 1, 9, 2, "", int, width=24)
        self._g_number(c1, ["vision", "min_confidence"], "Мин. достоверность", 50, 100, 1, "%", int, width=24)
        self._g_number(c2, ["vision", "kill_if_vanished_below"], "Окно закрылось ниже — убийство",
                       0, 100, 1, "%", int, width=30)
        self._g_check(c2, ["vision", "track_moved"], "Находить сдвинутое окно цели")
        self._g_check(c1, ["vision", "learn_empty"], "Уточнять цвет пустоты на лету")
        ttk.Label(c3, wraplength=240, justify="left", foreground="#555", text=(
            "Много оранжевого на чистой полоске — увеличьте допуск цвета. Пустое окно цели или фон "
            "рядом читаются как полоска — уменьшите. «Недостоверно» — картинка противоречивая, "
            "по ней программа ничего не делает."
        )).pack(anchor="w")

    def delayed(self, what):
        def run():
            try:
                if what == "bind":
                    self.app.bind_window()
                else:
                    self.app.auto_find()
            finally:
                self.after_calibration()
        self.countdown(self.delay_label, "Переключитесь в игру", run)

    def after_calibration(self):
        if not self.win.winfo_exists():
            return
        self.v_title.set(self.app.cfg.get("window_title", ""))
        for k, bar in self.app.cfg["bars"].items():
            tol = self.cfg["bars"].get(k, {}).get("tolerance", bar.get("tolerance", 60))
            self.cfg["bars"][k] = copy.deepcopy(bar)
            self.cfg["bars"][k]["tolerance"] = tol
        self.delay_label.config(text="Готово — проверьте рамки на экране и значения ниже. Подробности в консоли.")

    def update_bars_live(self):
        bars = self.app.cfg["bars"]
        vision = getattr(self.app, "vision", {}) or {}
        for key, w in self.bar_widgets.items():
            b = bars.get(key, {})
            if hw.bar_ready(b):
                w["pos"].config(text=f"x {b['x1']}…{b['x2']}, y {b['y']}", foreground="")
                fill = b.get("color", [0, 0, 0])
                empty = b.get("empty_color") or hw.default_empty(fill)
                w["sw"].config(bg="#%02x%02x%02x" % tuple(int(c) for c in fill))
                w["sw_e"].config(bg="#%02x%02x%02x" % tuple(int(c) for c in empty))
            else:
                w["pos"].config(text="не настроена", foreground="#a33")
                w["sw"].config(bg=self.win.cget("bg"))
                w["sw_e"].config(bg=self.win.cget("bg"))
            res = vision.get(key)
            w["txt"].config(text=vision_text(res) if hw.bar_ready(b) else "сначала настройте полоску",
                            foreground={"ok": "#286b2e", "unsure": "#a33"}.get((res or {}).get("status"), "#555"))
            img = vision_rgb(res) if res else None
            if img is None:
                w["img"].config(image="", width=80, height=1)
                w["photo"] = None
                continue
            try:
                photo = tk.PhotoImage(data=png_b64(img))
            except Exception:
                continue
            w["img"].config(image=photo, width=img.shape[1], height=img.shape[0])
            w["photo"] = photo  # держим ссылку, иначе картинка пропадёт

    # ========================================================
    #  Вкладка «Кликер и ввод»
    # ========================================================

    def build_clicker_tab(self):
        t = self.tab_clicker
        inp = self.cfg.setdefault("input", copy.deepcopy(hw.DEFAULT_CONFIG["input"]))
        ck = self.cfg.setdefault("clicker", copy.deepcopy(hw.DEFAULT_CONFIG["clicker"]))

        f = ttk.LabelFrame(t, text="Способ ввода (клавиши сценариев и клики)", padding=(8, 4))
        f.pack(fill="x")
        self.v_backend = tk.StringVar(value=inp.get("backend", "sendinput"))
        ttk.Radiobutton(f, text="SendInput — обычный, ничего ставить не нужно", value="sendinput",
                        variable=self.v_backend).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Radiobutton(f, text="Драйвер Interception — нажатия от имени настоящих клавиатуры и мыши",
                        value="interception", variable=self.v_backend).grid(row=1, column=0, columnspan=3, sticky="w")
        ttk.Label(f, text="interception.dll:").grid(row=2, column=0, sticky="w", padx=(22, 4))
        self.v_dll = tk.StringVar(value=inp.get("interception_dll", ""))
        ttk.Entry(f, textvariable=self.v_dll, width=50).grid(row=2, column=1, sticky="w")
        ttk.Button(f, text="Обзор…", command=self.browse_dll).grid(row=2, column=2, padx=4)
        ttk.Button(f, text="Проверить драйвер", command=self.check_driver).grid(row=2, column=3)
        self.input_label = ttk.Label(f, text="", foreground="#286b2e")
        self.input_label.grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))
        ttk.Label(f, foreground="#555", wraplength=980, justify="left", text=(
            "Установка драйвера (один раз): скачайте Interception (github.com/oblitum/Interception, Releases), "
            "в командной строке от имени администратора выполните «command line installer\\install-interception.exe "
            "/install» и перезагрузите компьютер. Файл library\\x64\\interception.dll положите рядом с HPWatcher.exe "
            "(или укажите путь выше). Пустое поле — искать рядом с программой. Если драйвер не загрузится, "
            "программа сама вернётся к SendInput."
        )).grid(row=4, column=0, columnspan=4, sticky="w", pady=(2, 0))

        f = ttk.LabelFrame(t, text="Автокликер", padding=(8, 4))
        f.pack(fill="x", pady=8)
        left = ttk.Frame(f)
        left.pack(side="left", anchor="n")
        right = ttk.Frame(f)
        right.pack(side="left", anchor="n", padx=24)

        self._g_check(left, ["clicker", "enabled"], "Автокликер включён")
        self.v_mode = tk.StringVar(value=ck.get("mode", "toggle"))
        hk = self.cfg["hotkeys"].get("clicker_toggle", "").upper()
        ttk.Label(left, text="Когда кликать:").pack(anchor="w", pady=(6, 0))
        ttk.Radiobutton(left, text=f"Вкл/выкл горячей клавишей {hk} или кнопкой «Кликер»",
                        value="toggle", variable=self.v_mode).pack(anchor="w")
        hold = ttk.Frame(left)
        hold.pack(anchor="w")
        ttk.Radiobutton(hold, text="Пока зажата клавиша", value="hold", variable=self.v_mode).pack(side="left")
        self.v_hold_key = tk.StringVar(value=ck.get("hold_key", "x"))
        ttk.Entry(hold, textvariable=self.v_hold_key, width=8).pack(side="left", padx=4)
        ttk.Button(hold, text="⌨", width=3, command=lambda: key_capture(self.win, self.v_hold_key.set)).pack(side="left")
        ttk.Radiobutton(left, text=f"Пока цель жива (кликер включён {hk})", value="target",
                        variable=self.v_mode).pack(anchor="w")

        self.v_button = tk.StringVar(value=dict(BUTTON_LABELS).get(ck.get("button", "left"), BUTTON_LABELS[0][1]))
        row = ttk.Frame(right)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Кнопка", width=24).pack(side="left")
        ttk.Combobox(row, textvariable=self.v_button, values=[l for _, l in BUTTON_LABELS], state="readonly",
                     width=14).pack(side="left")
        self._g_number(right, ["clicker", "cps"], "Кликов в секунду", 0.5, 50, 1, "", float, width=24)
        self._g_number(right, ["clicker", "jitter"], "Разброс интервала", 0, 90, 5, "%", int, width=24)
        hrow = ttk.Frame(right)
        hrow.pack(fill="x", pady=2)
        ttk.Label(hrow, text="Держать кнопку, мс", width=24).pack(side="left")
        lo, hi = (list(ck.get("hold_ms") or [8, 20]) + [20])[:2]
        self.v_hold_lo, self.v_hold_hi = tk.StringVar(value=f"{lo:g}"), tk.StringVar(value=f"{hi:g}")
        ttk.Spinbox(hrow, textvariable=self.v_hold_lo, from_=1, to=500, increment=1, width=5).pack(side="left")
        ttk.Label(hrow, text="–").pack(side="left", padx=2)
        ttk.Spinbox(hrow, textvariable=self.v_hold_hi, from_=1, to=500, increment=1, width=5).pack(side="left")

        prow = ttk.Frame(t)
        prow.pack(fill="x", pady=(0, 6))
        ttk.Label(prow, text="Куда кликать:").pack(side="left", padx=(8, 8))
        self.v_point_mode = tk.StringVar(value="point" if ck.get("point") else "cursor")
        ttk.Radiobutton(prow, text="где курсор", value="cursor", variable=self.v_point_mode,
                        command=self.clicker_point_changed).pack(side="left")
        ttk.Radiobutton(prow, text="в точке", value="point", variable=self.v_point_mode,
                        command=self.clicker_point_changed).pack(side="left", padx=(8, 0))
        self.point_label = ttk.Label(prow, text="")
        self.point_label.pack(side="left", padx=(8, 0))
        ttk.Button(prow, text="Выбрать точку (3 с)", command=lambda: self.pick_point(self.set_clicker_point)
                   ).pack(side="left", padx=8)
        self.clicker_point_changed()

        self.clicker_label = ttk.Label(t, text="", foreground="#286b2e")
        self.clicker_label.pack(anchor="w")
        self._g_check(t, ["clicker", "return_cursor"],
                      "После клика в точку из шага сценария возвращать курсор на место")
        ttk.Label(t, foreground="#555", wraplength=980, justify="left", text=(
            "Кликер не кликает на паузе, во время записи, пока выполняется сценарий и когда окно игры не активно. "
            "В тестовом режиме только пишет в лог. Клики мышью можно добавлять и в шаги сценариев на вкладке «Охота»: "
            "«+ Клик», затем ◎, чтобы выбрать точку в окне игры."
        )).pack(anchor="w", pady=(6, 0))

    def browse_dll(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(parent=self.win, title="interception.dll",
                                       filetypes=[("interception.dll", "*.dll"), ("Все файлы", "*.*")])
        if p:
            self.v_dll.set(p)

    def check_driver(self):
        cfg = {"backend": "interception", "interception_dll": self.v_dll.get().strip()}
        try:
            b = hw.InterceptionBackend(cfg["interception_dll"] or None)
            text, color = f"Драйвер работает: {b.status}. Нажмите «Сохранить», чтобы включить.", "#286b2e"
            b.close()
        except Exception as e:
            text, color = f"Драйвер не работает: {e}", "#a33"
        self.input_label.config(text=text, foreground=color)
        self._driver_checked = time.time()

    def set_clicker_point(self, p):
        self.cfg["clicker"]["point"] = [int(p[0]), int(p[1])]
        self.v_point_mode.set("point")
        self.clicker_point_changed()

    def clicker_point_changed(self):
        ck = self.cfg["clicker"]
        if self.v_point_mode.get() == "cursor":
            self._saved_point = ck.get("point") or getattr(self, "_saved_point", None)
            ck["point"] = None
            self.point_label.config(text="(курсор не двигается)", foreground="#555")
        else:
            if not ck.get("point") and getattr(self, "_saved_point", None):
                ck["point"] = self._saved_point
            pt = ck.get("point")
            self.point_label.config(text=f"({pt[0]}, {pt[1]}) в окне игры" if pt else "точка не выбрана",
                                    foreground="" if pt else "#a33")

    def update_clicker_live(self):
        if not hasattr(self, "clicker_label"):
            return
        app = self.app
        ck = app.cfg.get("clicker", {})
        state = app.clicker_state if ck.get("enabled") else "выключен"
        self.clicker_label.config(text=f"Кликер сейчас: {state}   ·   кликов за сессию: {app.stats.clicks}")
        if time.time() - getattr(self, "_driver_checked", 0) > 8:
            err = getattr(app, "input_error", None)
            self.input_label.config(text=f"Сейчас: {app.input_status}" + (f" — {err}" if err else ""),
                                    foreground="#a33" if err else "#286b2e")

    def collect_clicker(self):
        if not hasattr(self, "v_backend"):
            return
        inp = self.cfg.setdefault("input", {})
        inp["backend"] = self.v_backend.get()
        inp["interception_dll"] = self.v_dll.get().strip()
        ck = self.cfg.setdefault("clicker", {})
        ck["mode"] = self.v_mode.get()
        ck["hold_key"] = key_token(self.v_hold_key.get()) or "x"
        ck["button"] = {l: k for k, l in BUTTON_LABELS}.get(self.v_button.get(), "left")
        lo = min(500, max(1, fnum(self.v_hold_lo.get(), 8)))
        hi = min(500, max(1, fnum(self.v_hold_hi.get(), 20)))
        ck["hold_ms"] = [int(min(lo, hi)), int(max(lo, hi))]
        if self.v_point_mode.get() == "cursor":
            ck["point"] = None

    # ========================================================
    #  Вкладка «Общие»
    # ========================================================

    def _g_get(self, path):
        d = self.cfg
        for p in path[:-1]:
            d = d.setdefault(p, {})
        return d.get(path[-1])

    def _g_check(self, parent, path, text):
        v = tk.BooleanVar(value=bool(self._g_get(path)))
        ttk.Checkbutton(parent, text=text, variable=v).pack(anchor="w", pady=2)
        self.gvars.append((path, v, bool))

    def _g_number(self, parent, path, text, lo, hi, step, unit="", kind=float, width=34):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text=text, width=width).pack(side="left")
        cur = self._g_get(path)
        v = tk.StringVar(value=f"{cur:g}" if cur is not None else "")
        ttk.Spinbox(row, textvariable=v, from_=lo, to=hi, increment=step, width=7).pack(side="left")
        ttk.Label(row, text=unit, foreground="#555").pack(side="left", padx=4)
        self.gvars.append((path, v, (kind, lo, hi)))

    def build_general_tab(self):
        t = self.tab_general
        cols = ttk.Frame(t)
        cols.pack(fill="both", expand=True)
        left = ttk.Frame(cols)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        right = ttk.Frame(cols)
        right.pack(side="left", fill="both", expand=True)

        check, number = self._g_check, self._g_number

        f = ttk.LabelFrame(left, text="Работа", padding=8)
        f.pack(fill="x")
        check(f, ["dry_run"], "Тестовый режим — ничего не нажимать, только писать в лог")
        check(f, ["only_when_window_active"], "Нажимать, только когда окно игры активно")
        check(f, ["sound"], "Звуковые сигналы")
        check(f, ["overlay_frames"], "Рамки вокруг полосок на экране (после перезапуска)")
        number(f, ["check_interval"], "Проверять экран каждые", 0.03, 1, 0.01, "сек")
        number(f, ["dead_confirm"], "Цель мертва после N проверок подряд", 1, 20, 1, "", int)
        number(f, ["key_hold"], "Держать клавишу нажатой", 0.01, 0.5, 0.01, "сек")
        number(f, ["min_gap"], "Пауза между сценариями", 0, 5, 0.05, "сек")

        f = ttk.LabelFrame(left, text="Аварийная пауза", padding=8)
        f.pack(fill="x", pady=8)
        check(f, ["emergency", "on_death"], "При смерти персонажа")
        number(f, ["emergency", "hp_drop_percent"], "При падении HP на (0 — выкл.)", 0, 100, 5, "%", int)
        number(f, ["emergency", "hp_drop_seconds"], "…за время", 0.5, 30, 0.5, "сек")

        f = ttk.LabelFrame(left, text="Скриншоты", padding=8)
        f.pack(fill="x")
        check(f, ["screenshots", "enabled"], "Сохранять скриншоты в папку screenshots")
        check(f, ["screenshots", "on_scenario"], "…при каждом сценарии")
        check(f, ["screenshots", "on_emergency"], "…при аварийной паузе")
        check(f, ["screenshots", "on_kill"], "…при каждом убийстве")
        number(f, ["screenshots", "keep"], "Хранить последних", 10, 5000, 10, "шт.", int)

        f = ttk.LabelFrame(right, text="Горячие клавиши (работают из любого окна)", padding=8)
        f.pack(fill="both", expand=True)
        for name, text in HOTKEY_LABELS:
            row = ttk.Frame(f)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=text, width=30).pack(side="left")
            v = tk.StringVar(value=self.cfg["hotkeys"].get(name, ""))
            ttk.Entry(row, textvariable=v, width=14).pack(side="left")
            ttk.Button(row, text="⌨", width=3, command=lambda v=v: key_capture(self.win, v.set)).pack(side="left", padx=2)
            self.gvars.append((["hotkeys", name], v, str))
        ttk.Label(f, foreground="#555", wraplength=380, justify="left", text=(
            "Лучше оставлять Ctrl+Alt+…, чтобы не пересекаться с клавишами игры."
        )).pack(anchor="w", pady=(6, 0))

    def refresh_gvars(self):
        """Поля вкладок заново берут значения из self.cfg (после быстрой настройки)."""
        for path, var, kind in self.gvars:
            cur = self._g_get(path)
            if kind is bool:
                var.set(bool(cur))
            elif kind is str:
                var.set(cur or "")
            else:
                var.set(f"{cur:g}" if isinstance(cur, (int, float)) else "")

    def collect_general(self):
        if not hasattr(self, "gvars"):
            return
        for path, v, kind in self.gvars:
            d = self.cfg
            for p in path[:-1]:
                d = d.setdefault(p, {})
            if kind is bool:
                d[path[-1]] = bool(v.get())
            elif kind is str:
                d[path[-1]] = v.get().strip().lower()
            else:
                typ, lo, hi = kind
                val = min(hi, max(lo, fnum(v.get(), d.get(path[-1], lo) or lo)))
                d[path[-1]] = int(round(val)) if typ is int else round(val, 3)
        if hasattr(self, "v_title"):
            self.cfg["window_title"] = self.v_title.get().strip()
        self.collect_clicker()
