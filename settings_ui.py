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
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import hp_watcher as hw

# ------------------------------------------------------------
#  Чистая логика (тестируется без окна)
# ------------------------------------------------------------

TRIGGERS = [
    ("kill", "После убийства цели"),
    ("combat", "Во время боя (цель жива)"),
    ("no_target", "Нет цели"),
    ("timer", "По таймеру / по условию"),
]
TRIGGER_LABEL = dict(TRIGGERS)
TRIGGER_BY_LABEL = {v: k for k, v in TRIGGERS}
TRIGGER_HINT = {
    "kill": "Один раз после каждого убийства. Обычно: следующая цель → атака, подобрать дроп.",
    "combat": "Пока у цели есть HP, повторяется с указанным интервалом. Для атакующих скиллов.",
    "no_target": "Если цели нет указанное время: next target не нашёл моба, моба перехватили.",
    "timer": "Каждые N секунд, если выполнены доп. условия (или всегда, если условий нет). "
             "Для баффов, банок, саммонов. Банка: условие «Моё HP < 40». "
             "Бафф вне боя: условие «Цели нет ≥ 1».",
}
REPEAT_HINT = {
    "kill": "0 — после каждого убийства",
    "combat": "например 5 — скилл раз в 5 с",
    "no_target": "не чаще, чем раз в столько секунд",
    "timer": "1200 = раз в 20 минут",
}

VALUE_LABELS = [
    ("target_hp", "HP цели, %"),
    ("my_hp", "Моё HP, %"),
    ("my_mp", "Моё MP, %"),
    ("no_target_sec", "Цели нет, сек"),
]
VALUE_LABEL = dict(VALUE_LABELS)
VALUE_BY_LABEL = {v: k for k, v in VALUE_LABELS}
OPS = ["<", "<=", ">", ">=", "==", "!="]
VALUE_SHORT = {"target_hp": "HP цели", "my_hp": "моё HP", "my_mp": "моё MP", "no_target_sec": "нет цели, с"}


def describe_conds(conds):
    return ", ".join(f"{VALUE_SHORT.get(c.get('value'), c.get('value'))} {c.get('op')} {fnum(c.get('than')):g}"
                     for c in conds)

SKILL_HINTS = {"attack": "Атака", "next_target": "Следующая цель (/nexttarget)"}

HOTKEY_LABELS = [
    ("settings", "Окно настроек"),
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


def check_key(key, skill_keys, mapping):
    """Пустая строка — ок, иначе текст ошибки."""
    if not key:
        return "пустая клавиша"
    if key == "wait":
        return ""
    if key.startswith("@"):
        if not skill_keys.get(key[1:]):
            return f"кнопка {key} не задана на вкладке «Кнопки панели»"
        key = str(skill_keys[key[1:]]).lower()
    bad = [p for p in key.split("+") if p.strip() not in mapping]
    return f"неизвестная клавиша {', '.join(bad)}" if bad else ""


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
        nb.add(self.tab_hunt, text="  Охота  ")
        nb.add(self.tab_keys, text="  Кнопки панели  ")
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

        self.build_keys_tab()
        self.build_hunt_tab()
        self.build_bars_tab()
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
        try:
            import pydirectinput
            return pydirectinput.KEYBOARD_MAPPING
        except Exception:
            return {}

    def skill_keys(self):
        return self.cfg.setdefault("skill_keys", {})

    def key_choices(self):
        sk = self.skill_keys()
        named = [f"@{n}  ({str(k).upper()})" for n, k in sk.items() if k]
        return named + COMMON_KEYS + ["wait"]

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
        self.rec_btn = ttk.Button(sb, text="⏺ Записать с клавиатуры", command=self.toggle_recording)
        self.rec_btn.pack(side="left")
        self.rec_label = ttk.Label(sb, text="", foreground="#a33")
        self.rec_label.pack(side="left", padx=8)

        for v in (self.v_name, self.v_enabled, self.v_trigger, self.v_param, self.v_repeat):
            v.trace_add("write", lambda *a: self.editor_changed())

    # ---------- список ----------

    def scen_row(self, s):
        trig, param, extras = split_conditions(s.get("conditions"))
        if trig == "kill":
            when = "После убийства"
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
            ttk.Spinbox(row, textvariable=vd, from_=0, to=600, increment=0.05, width=6).grid(row=0, column=3, padx=(8, 4))
            for c, (text, cmd) in enumerate((("▲", lambda i=i: self.step_move(i, -1)),
                                             ("▼", lambda i=i: self.step_move(i, 1)),
                                             ("✕", lambda i=i: self.step_del(i))), start=4):
                ttk.Button(row, text=text, width=2, command=cmd).grid(row=0, column=c, padx=1)
            err = ttk.Label(row, text="", foreground="#a33")
            err.grid(row=1, column=1, columnspan=6, sticky="w")

            def upd(*_, i=i, vk=vk, vd=vd, err=err):
                k = key_token(vk.get())
                acts[i] = [k, max(0.0, fnum(vd.get(), 0))]
                m = self.mapping()
                e = check_key(k, self.skill_keys(), m) if m else ""
                err.config(text=("⚠ " + e) if e else "")
                if e:
                    err.grid()
                else:
                    err.grid_remove()
            vk.trace_add("write", upd)
            vd.trace_add("write", upd)
            upd()

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
        top = ttk.LabelFrame(t, text="Окно игры", padding=8)
        top.pack(fill="x")
        self.v_title = tk.StringVar(value=self.cfg.get("window_title", ""))
        ttk.Label(top, text="Заголовок содержит:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.v_title, width=40).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Button(top, text="Привязать (через 3 с)", command=lambda: self.delayed("bind")).grid(row=0, column=2)
        ttk.Button(top, text="Автопоиск полосок (через 3 с)",
                   command=lambda: self.delayed("auto")).grid(row=0, column=3, padx=6)
        self.delay_label = ttk.Label(top, text="", foreground="#286b2e")
        self.delay_label.grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Label(top, foreground="#555", wraplength=880, justify="left", text=(
            "Нажмите кнопку и за 3 секунды переключитесь в игру. Для автопоиска возьмите в цель моба "
            "с полным HP, свои HP и MP тоже должны быть полными."
        )).grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))

        box = ttk.LabelFrame(t, text="Полоски", padding=8)
        box.pack(fill="x", pady=8)
        for c, text in enumerate(("", "Положение", "Цвет", "Сейчас", "Допуск цвета")):
            ttk.Label(box, text=text, foreground="#555").grid(row=0, column=c, sticky="w", padx=6)
        self.bar_widgets = {}
        for r, key in enumerate(hw.BAR_KEYS, start=1):
            ttk.Label(box, text=hw.BAR_NAMES[key]).grid(row=r, column=0, sticky="w", padx=6, pady=3)
            pos = ttk.Label(box, text="")
            pos.grid(row=r, column=1, sticky="w", padx=6)
            sw = tk.Label(box, text="      ", relief="solid", bd=1)
            sw.grid(row=r, column=2, sticky="w", padx=6)
            val = ttk.Label(box, text="—", width=8)
            val.grid(row=r, column=3, sticky="w", padx=6)
            vt = tk.IntVar(value=int(self.cfg["bars"][key].get("tolerance", 60)))
            sc = ttk.Scale(box, from_=10, to=140, variable=vt, length=220)
            sc.grid(row=r, column=4, sticky="w", padx=6)
            tl = ttk.Label(box, text=str(vt.get()), width=4)
            tl.grid(row=r, column=5, sticky="w")

            def upd(*_, key=key, vt=vt, tl=tl):
                v = int(float(vt.get()))
                tl.config(text=str(v))
                self.cfg["bars"][key]["tolerance"] = v
            vt.trace_add("write", upd)
            self.bar_widgets[key] = (pos, sw, val)

        ttk.Label(t, wraplength=880, justify="left", foreground="#555", text=(
            f"Ручная калибровка (в игре, мышью): {hk.get('calib_next_bar', '').upper()} выбрать полоску, "
            f"курсор на левый край → {hk.get('calib_left', '').upper()}, на правый край → "
            f"{hk.get('calib_right', '').upper()}. Целиться в верхнюю часть полоски, выше цифр.\n"
            "Допуск цвета: больше — надёжнее при эффектах и подсветке, но выше риск принять фон за полоску. "
            "Если HP живого моба показывает 0 — увеличьте; если пустая полоска показывает проценты — уменьшите. "
            "Значения «Сейчас» обновляются вживую."
        )).pack(anchor="w")

    def delayed(self, what):
        def run():
            for i in (3, 2, 1):
                self.win.after(0, lambda i=i: self.delay_label.config(text=f"Переключитесь в игру… {i}"))
                time.sleep(1)
            try:
                if what == "bind":
                    self.app.bind_window()
                else:
                    self.app.auto_find()
            finally:
                self.win.after(0, self.after_calibration)
        threading.Thread(target=run, daemon=True).start()

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
        for key, (pos, sw, val) in self.bar_widgets.items():
            b = bars.get(key, {})
            if hw.bar_ready(b):
                pos.config(text=f"x {b['x1']}…{b['x2']}, y {b['y']}", foreground="")
                r, g, bl = (int(c) for c in b.get("color", [0, 0, 0]))
                sw.config(bg=f"#{r:02x}{g:02x}{bl:02x}")
            else:
                pos.config(text="не настроена", foreground="#a33")
                sw.config(bg=self.win.cget("bg"))
            v = self.app.values.get(key)
            val.config(text="—" if v is None else f"{v:.0f}%")

    # ========================================================
    #  Вкладка «Общие»
    # ========================================================

    def build_general_tab(self):
        t = self.tab_general
        self.gvars = []  # (путь, переменная, тип)
        cols = ttk.Frame(t)
        cols.pack(fill="both", expand=True)
        left = ttk.Frame(cols)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        right = ttk.Frame(cols)
        right.pack(side="left", fill="both", expand=True)

        def get(path):
            d = self.cfg
            for p in path[:-1]:
                d = d.setdefault(p, {})
            return d.get(path[-1])

        def check(parent, path, text):
            v = tk.BooleanVar(value=bool(get(path)))
            ttk.Checkbutton(parent, text=text, variable=v).pack(anchor="w", pady=2)
            self.gvars.append((path, v, bool))

        def number(parent, path, text, lo, hi, step, unit="", kind=float):
            row = ttk.Frame(parent)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=text, width=34).pack(side="left")
            v = tk.StringVar(value=f"{get(path):g}" if get(path) is not None else "")
            ttk.Spinbox(row, textvariable=v, from_=lo, to=hi, increment=step, width=8).pack(side="left")
            ttk.Label(row, text=unit, foreground="#555").pack(side="left", padx=4)
            self.gvars.append((path, v, (kind, lo, hi)))

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
