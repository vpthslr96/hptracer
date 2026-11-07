# -*- coding: utf-8 -*-
"""Симулятор охоты: проверяет цикл «нашёл моба на экране → выделил → атака → HP 0 → следующий»
на настоящем коде hp_watcher, без игры и без Windows.

Запуск:  python tests/sim_hunt.py [секунд] [сценарий]
Сценарии: basic — мобы рядом, всё штатно;
          stuck — некоторые мобы недоступны (урон не проходит);
          vanish — окно цели закрывается при смерти моба (как в части клиентов);
          aggro — пока ищем цель, моб сам нападает и выделяется.
Печатает лог и итог: сколько убито, сколько ждали без цели, сколько кликов мимо.
"""
import logging
import os
import random
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import hp_watcher as hw  # noqa: E402

W, H = 1280, 720
TBAR = dict(x1=540, x2=739, y=32)       # полоска HP цели
MYBAR = dict(x1=20, x2=169, y=30)       # моё HP
FILL, EMPTY = (190, 45, 45), (55, 22, 22)


class Mob:
    def __init__(self, gid, x, y, reachable=True):
        self.id, self.x, self.y = gid, x, y      # x,y — точка ног; имя рисуется выше
        self.hp, self.dead_at, self.reachable = 100.0, None, reachable
        self.name_w = random.choice([70, 84, 96, 110])

    @property
    def alive(self):
        return self.dead_at is None

    def body(self):  # прямоугольник, по которому клик выделяет моба
        return self.x - 25, self.y - 70, self.x + 25, self.y

    def name_box(self):
        return self.x - self.name_w // 2, self.y - 92, self.x + self.name_w // 2, self.y - 80


class Game:
    def __init__(self, mode="basic", seed=1):
        random.seed(seed)
        self.mode = mode
        self.lock = threading.RLock()
        self.mobs, self.next_id = [], 0
        self.target = None
        self.attacking = False
        self.cursor = (0, 0)
        self.move_until = 0.0
        self.t0 = time.time()
        self.kills = 0
        self.clicks = 0
        self.click_miss = 0
        self.last_spawn = 0.0
        self.noise = np.random.default_rng(seed).integers(20, 60, (H, W, 3), dtype=np.int16)
        for _ in range(4):
            self.spawn()

    def spawn(self):
        while True:
            x, y = random.randint(150, W - 150), random.randint(250, 430)
            if abs(x - W * 0.5) > 120 or abs(y - H * 0.43) > 60:
                break
        reach = not (self.mode == "stuck" and random.random() < 0.3)
        self.mobs.append(Mob(self.next_id, x, y, reach))
        self.next_id += 1

    # ---- ввод ----
    def key(self, k):
        with self.lock:
            if k == "f1":
                if self.target and self.target.alive:
                    self.attacking = True
                    self.move_until = time.time() + 0.5
            elif k == "f2":
                live = [m for m in self.mobs if m.alive and m is not self.target]
                live.sort(key=lambda m: (m.x - W / 2) ** 2 + (m.y - H * 0.43) ** 2)
                # /nexttarget в игре берёт не всегда: только моба «перед камерой»
                if live and random.random() < 0.5:
                    self.select(live[0])
            elif k == "esc":
                self.target, self.attacking = None, False

    def click(self):
        with self.lock:
            self.clicks += 1
            x, y = self.cursor
            for m in self.mobs:
                b = m.body()
                if b[0] <= x <= b[2] and b[1] <= y <= b[3]:
                    self.select(m)
                    return
            self.click_miss += 1
            self.move_until = time.time() + 0.4  # клик по земле — персонаж побежал

    def select(self, m):
        if m is not self.target:
            self.target, self.attacking = m, False

    # ---- мир ----
    def step(self):
        now = time.time()
        with self.lock:
            t = self.target
            if t and t.alive and self.attacking and now > self.move_until and t.reachable:
                t.hp = max(0.0, t.hp - 6.0 * random.uniform(0.6, 1.4))
                if t.hp <= 0:
                    t.dead_at = now
                    self.attacking = False
                    self.kills += 1
                    if self.mode == "vanish":
                        self.target = None
            # трупы исчезают через 6 с, новые мобы появляются
            self.mobs = [m for m in self.mobs if m.alive or now - m.dead_at < 6]
            if self.target is not None and self.target not in self.mobs:
                self.target = None
            if sum(m.alive for m in self.mobs) < 3 and now - self.last_spawn > 1.5:
                self.spawn()
                self.last_spawn = now
            if self.mode == "aggro" and (self.target is None or not self.target.alive) and random.random() < 0.01:
                live = [m for m in self.mobs if m.alive]
                if live:
                    self.select(random.choice(live))
                    self.attacking = True

    def frame(self):
        with self.lock:
            img = self.noise.copy()
            if time.time() < self.move_until:  # камера едет
                img = np.roll(img, int((time.time() * 400) % 40), axis=1)
            for m in self.mobs:
                x1, y1, x2, y2 = m.body()
                img[y1:y2, x1:x2] = (90, 70, 50)
                nx1, ny1, nx2, ny2 = m.name_box()
                for x in range(nx1, nx2):
                    if x % 4 in (0, 1):     # «буквы»
                        img[ny1:ny2, x] = (235, 235, 230)
            # окно цели
            img[TBAR["y"] - 22:TBAR["y"] + 8, TBAR["x1"] - 10:TBAR["x2"] + 11] = (15, 15, 20)
            if self.target is not None:
                n = TBAR["x2"] - TBAR["x1"] + 1
                k = int(round(n * self.target.hp / 100))
                ys = slice(TBAR["y"] - 3, TBAR["y"] + 4)
                img[ys, TBAR["x1"]:TBAR["x1"] + k] = FILL
                img[ys, TBAR["x1"] + k:TBAR["x2"] + 1] = EMPTY
            # моё HP — всегда 90%
            n = MYBAR["x2"] - MYBAR["x1"] + 1
            img[MYBAR["y"] - 3:MYBAR["y"] + 4, MYBAR["x1"]:MYBAR["x1"] + int(n * 0.9)] = FILL
            img[MYBAR["y"] - 3:MYBAR["y"] + 4, MYBAR["x1"] + int(n * 0.9):MYBAR["x2"] + 1] = EMPTY
            return np.clip(img, 0, 255).astype(np.uint8)


class FakeInput:
    def __init__(self, game):
        self.g = game

    def key_down(self, k):
        self.g.key(k)

    def key_up(self, k):
        pass

    def mouse_button(self, button, down):
        if down and button == "left":
            self.g.click()

    def mouse_move(self, x, y):
        self.g.cursor = (x, y)

    def close(self):
        pass


def install(game):
    def grab(sct, left, top, width, height):
        f = game.frame()
        return f[int(top):int(top) + int(height), int(left):int(left) + int(width)]
    hw.grab_rgb = grab
    hw.INPUT = FakeInput(game)
    hw.App.get_origin = lambda self: ((0, 0, W, H), True)
    hw.App.window_is_active = lambda self: True
    hw.App.wait_modifiers_released = lambda self, timeout=2.0: None
    hw.App.screenshot = lambda self, reason: None
    hw.App.save_debug_shot = lambda self, img, reason: None
    hw.App.save_config = lambda self: None
    hw.App.maybe_reload_config = lambda self: None


def make_app(target_mode="vision"):
    app = hw.App()
    cfg = app.cfg
    cfg["bars"]["target_hp"].update(TBAR, color=list(FILL), tolerance=60)
    cfg["bars"]["my_hp"].update(MYBAR, color=list(FILL), tolerance=60)
    hw.apply_hunt_preset(cfg, {**hw.HUNT_DEFAULTS, "target_mode": target_mode})
    cfg["screenshots"]["enabled"] = False
    app._sct = object()
    return app


def run(seconds=60, mode="basic", target_mode="vision"):
    game = Game(mode)
    install(game)
    app = make_app(target_mode)
    stop = time.time() + seconds
    idle = [0.0]        # сколько времени в цели не было живого моба (считает «мир», а не цикл программы)

    def world():
        last = time.time()
        while time.time() < stop:
            game.step()
            now = time.time()
            if not (game.target and game.target.alive):
                idle[0] += now - last
            last = now
            time.sleep(0.05)
    threading.Thread(target=world, daemon=True).start()
    while time.time() < stop:
        app.tick()
        time.sleep(0.1)
    idle = idle[0]
    app.running = False
    print(f"\n=== {mode}/{target_mode}: {seconds} с — убито {game.kills}, программа насчитала "
          f"{app.stats.kills}, без живой цели {idle:.1f} с, кликов {game.clicks} (мимо {game.click_miss})")
    return game, app, idle


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(relativeCreated)7.0f %(message)s")
    hw.log = logging.getLogger("hp")
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 40
    modes = sys.argv[2:] or ["basic"]
    for m in modes:
        run(secs, m)
