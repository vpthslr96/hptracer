# -*- coding: utf-8 -*-
"""Охота как машина состояний (библиотека transitions).

    выкл ──start──▶ поиск ──found──▶ бой ──killed──▶ подбор ──looted──▶ поиск …
                      ▲                │
                      └──lost / stuck──┘

поиск  — цели нет: берём её (зрением или /nexttarget), не вышло — повтор через search_after с.
бой    — атака при входе и повтор раз в attack_repeat с, пока моб жив.
         HP 0 (или окно цели закрылось почти на нуле) → подбор;
         окно цели закрылось на большом HP → поиск (моба перехватили, убежал);
         урон не проходит stuck_after с или бой дольше fight_max с → «застрял»: моб запоминается, цель снимается → поиск.
подбор — клавиша подбора pickup_times раз, потом поиск.

Банка HP — не состояние, а срочная проверка при каждом чтении экрана (App.poll → Hunt.urgent):
пьём и посреди поиска цели, и в бою, и во время подбора.
"""
import logging
import time

from transitions import Machine

log = logging.getLogger("hpw")
logging.getLogger("transitions").setLevel(logging.WARNING)  # её служебные строки не нужны в логе

STATE_NAMES = {"off": "выкл", "search": "поиск цели", "fight": "бой", "loot": "подбор"}


class Hunt:
    states = ["off", "search", "fight", "loot"]

    def __init__(self, app, press_key, resolve_key, defaults):
        # Функции ввода передаются явно: hp_watcher в exe запущен как __main__, и import hp_watcher
        # отсюда создал бы вторую копию модуля — без настроенного способа ввода.
        self.app = app
        self._press_key, self._resolve_key, self.defaults = press_key, resolve_key, defaults
        self.since = time.time()        # когда вошли в текущее состояние
        self.next_try = 0.0             # поиск: когда пробовать снова
        self.fails = 0                  # поиск: неудачных попыток подряд
        self.last_attack = 0.0
        self.last_potion = 0.0
        self.reason = ""                # почему перешли в текущее состояние — для лога и окна статуса
        self.machine = Machine(
            model=self, states=self.states, initial="off", auto_transitions=False, queued=True,
            after_state_change="_changed", send_event=True,
        )
        add = self.machine.add_transition
        add("start", "off", "search")
        add("stop", ["search", "fight", "loot"], "off")
        add("found", "search", "fight")
        add("killed", "fight", "loot")
        add("lost", "fight", "search")
        add("stuck", "fight", "search", before="_drop_stuck")
        add("looted", "loot", "search")
        add("aggro", "loot", "fight")   # пока подбирали, на нас напали и цель живая

    # ---------- настройки ----------

    @property
    def p(self):
        return {**self.defaults, **(self.app.cfg.get("hunt") or {})}

    def key(self, name):
        return self._resolve_key("@" + name, self.app.cfg.get("skill_keys", {}))

    def press(self, name, why=""):
        """Нажать кнопку панели (attack, pickup, potion, next_target). False — кнопка не задана."""
        real = self.key(name)
        if real is None:
            return False
        if self.app.cfg.get("dry_run"):
            log.info("  [тест] нажал бы @%s (%s)%s", name, real, why)
        else:
            self.app.wait_modifiers_released()
            self._press_key(real, float(self.app.cfg.get("key_hold", 0.05)))
            log.info("  нажал @%s (%s)%s", name, real, why)
        return True

    # ---------- переходы ----------

    def _changed(self, event):
        self.since = time.time()
        src = event.transition.source
        why = event.kwargs.get("why", "")
        self.reason = why
        name = f"охота/{STATE_NAMES[self.state]}"
        self.app.stats.scenarios[name] = self.app.stats.scenarios.get(name, 0) + 1
        log.info("Охота: %s → %s%s", STATE_NAMES[src], STATE_NAMES[self.state], f" ({why})" if why else "")
        if self.state == "search":
            self.next_try = 0.0 if src != "search" else self.next_try
            self.fails = 0
        elif self.state == "fight":
            self.last_attack = 0.0      # атака сразу при входе в бой

    def _drop_stuck(self, event):
        """Моб не получает урон: запомнить его, снять цель. Клавишей — /nexttarget возьмёт другого."""
        app = self.app
        app.stats.scenarios["охота/застрял"] = app.stats.scenarios.get("охота/застрял", 0) + 1
        app.target_alive, app._stuck = False, None  # это не «потеря цели», событие не нужно
        if app.cfg.get("targeting", {}).get("mode") == "vision":
            pt = getattr(app, "_vision_target_point", None)
            if pt:
                app.mark_bad(pt[0], pt[1], ttl=25, radius=70)
                app._vision_target_point = None
            if app.clear_target(bool(app.cfg.get("dry_run"))):
                return
        self.press("next_target", " — другой моб")

    # ---------- шаг ----------

    def event_since(self, name, t):
        ts = self.app.events.get(name)
        return ts is not None and ts >= t

    def step(self):
        """Один шаг охоты. Вызывается из App.tick (рабочий поток), когда охота включена и можно жать клавиши."""
        if self.state == "off":
            self.start(why="охота включена")
        getattr(self, "do_" + self.state)()

    def do_search(self):
        app = self.app
        if app.live_target():
            self.found(why="цель уже есть")
            return
        if time.time() < self.next_try:
            return
        app.executing = "охота: поиск цели"
        try:
            got = app.find_target(float(self.p.get("search_timeout", 2.0)), bool(app.cfg.get("dry_run")))
        finally:
            app.executing = None
        if got is None:
            return  # прервали: пауза, окно не активно
        if got or app.live_target():
            self.found(why="цель взята")
            return
        self.fails += 1
        self.next_try = time.time() + float(self.p.get("search_after", 1.0))
        if self.fails in (3, 10) or self.fails % 30 == 0:
            log.warning("Охота: цель не находится уже %d раз подряд — рядом нет мобов? "
                        "Проверьте вкладку «Полоски → Что видит программа»", self.fails)

    def do_fight(self):
        app, p, now = self.app, self.p, time.time()
        if self.event_since("target_died", self.since):
            self.killed(why="HP цели 0")
            return
        if self.event_since("target_lost", self.since):
            self.lost(why="окно цели закрылось, моб не добит")
            return
        stuck_after = float(p.get("stuck_after") or 0)
        if stuck_after > 0 and float(app.values.get("target_stuck_sec", 0)) >= max(2.0, stuck_after):
            self.stuck(why=f"урон не проходит {stuck_after:g} с")
            return
        fight_max = float(p.get("fight_max") or 0)
        if fight_max > 0 and now - self.since > fight_max:
            self.stuck(why=f"бой дольше {fight_max:g} с")
            return
        if app.live_target():
            if now - self.last_attack >= float(p.get("attack_repeat", 2.0)):
                self.press("attack", "" if self.last_attack else " — в бой")
                self.last_attack = now
        elif now - self.since > 5 and app.target_state() == "none":
            # в бой вошли, а цели так и нет (снялась без события) — ищем заново
            self.lost(why="цели нет")

    def do_loot(self):
        app, p = self.app, self.p
        times = int(p.get("pickup_times", 0)) if self.key("pickup") else 0
        app.executing = "охота: подбор"
        try:
            for i in range(times):
                self.press("pickup", f" {i + 1}/{times}")
                if not app.sleep_interruptible(float(p.get("pickup_delay", 0.5))):
                    return  # прервали — продолжим подбор, когда снова можно
                if app.live_target():
                    self.aggro(why="на нас напали")
                    return
        finally:
            app.executing = None
        self.looted(why=f"подобрал ×{times}" if times else "подбор выключен")

    # ---------- срочное: банка ----------

    def urgent(self):
        """Из App.poll после каждого чтения экрана. Только быстрые нажатия, без пауз."""
        if self.state == "off":
            return
        p, app, now = self.p, self.app, time.time()
        hp = app.values.get("my_hp")
        if hp is None or not self.key("potion"):
            return
        if hp < float(p.get("potion_hp", 50)) and now - self.last_potion >= float(p.get("potion_cooldown", 10)):
            self.last_potion = now
            app.stats.scenarios["охота/банка"] = app.stats.scenarios.get("охота/банка", 0) + 1
            self.press("potion", f" — моё HP {hp:.0f}%")

    # ---------- для окна статуса ----------

    def status(self):
        if self.state == "off":
            return "выкл"
        sec = time.time() - self.since
        extra = f", попыток {self.fails}" if self.state == "search" and self.fails else ""
        return f"{STATE_NAMES[self.state]} {sec:.0f} с{extra}"
