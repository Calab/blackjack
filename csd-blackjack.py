#!/usr/bin/env python3
"""
Blackjack - a pyglet card game.

Folder layout (blackjack.py sits next to the resources folder):

    blackjack.py
    resources/
        background.png   (optional) drawn behind the table
        card-backs/      back_1.png, back_2.png, ...      card back designs
        cards_1/         2C.png ... AS.png, empty.png     a set of card faces
        cards_2/         ...                              (as many sets as you like)
        sounds/          card_flip.mp3, card_placement.mp3, illegal_move.mp3,
                         lose.mp3, shuffle.mp3, win.mp3

    The "Faces" and "Back" buttons switch to the next card-face set and the
    next card back; the choice is remembered.  (An older single
    resources/cards folder with back_dark.png still works as a fallback.)

Rules
    * 6-deck shoe, reshuffled when it runs low.
    * Blackjack pays 3:2.  Dealer stands on all 17s.
    * Hit, Stand, Double Down (on your first two cards) and Split (two cards of the
      same value, up to 4 hands; split aces get one card each).
    * Insurance is offered when the dealer shows an Ace (costs half your bet,
      pays 2:1 if the dealer has Blackjack).
    * Bets are whole dollars, $1 minimum.

Keys:  H = Hit,  S = Stand,  D = Double,  P = Split,  I / N = take / decline Insurance,
       Enter or Space = Deal

Money, win/loss statistics, the sound setting and the window size and
position are saved between runs in:
    Linux:    ~/.config/csd-blackjack/blackjack.json
    Windows:  %APPDATA%\\csd-blackjack\\blackjack.json
    macOS:    ~/Library/Application Support/csd-blackjack/blackjack.json
The image caches are kept in a "cache" folder next to that file.
"""

import json
import math
import os
import random
import re
import shutil
import sys
import time
import zlib

import pyglet
from pyglet import shapes
from pyglet.window import key, mouse

# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
APP_NAME = "csd-blackjack"
OLD_APP_NAME = "blackjack"              # settings are copied from here once, if found
START_MONEY = 500
NUM_DECKS = 6
RESHUFFLE_AT = NUM_DECKS * 52 // 4      # reshuffle when fewer cards than this remain
CHIP_VALUES = (1, 5, 25, 100)
MAX_HANDS = 4                           # most hands a player can have after splitting
DEFAULT_WINDOW = (1024, 720)
MIN_WINDOW = (800, 600)
MAX_TEXTURE_HEIGHT = 520               # card images are shrunk to this (only if Pillow is installed)
MAX_BACKGROUND_HEIGHT = None            # None = load the background at its full size
CACHE_VERSION = 3
USE_MIPMAPS = True                      # smoother scaled-down cards (set False if cards look wrong)
CARD_ASPECT = 500 / 726                 # width / height of a card, until a face set is loaded

RANKS = "23456789TJQKA"
SUITS = "CDHS"
CARD_NAMES = [r + s for r in RANKS for s in SUITS]
FACE_NAMES = ["empty"] + CARD_NAMES     # the images every cards_N folder must hold

# Colours (RGBA)
FELT = (18, 102, 58, 255)
PANEL = (9, 46, 28, 255)
PANEL_LINE = (176, 142, 52, 255)
GOLD = (255, 214, 102, 255)
WHITE = (255, 255, 255, 255)
LIGHT = (214, 232, 220, 255)
BTN = (36, 84, 62, 255)
BTN_GREEN = (30, 140, 72, 255)
BTN_RED = (170, 48, 48, 255)
BTN_DISABLED = (58, 66, 62, 255)
BTN_BORDER = (214, 232, 220, 255)
TEXT_DISABLED = (140, 148, 144, 255)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def resource_dir():
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "resources")


def config_dir(app_name=APP_NAME):
    home = os.path.expanduser("~")
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or home
    elif sys.platform == "darwin":
        base = os.path.join(home, "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return os.path.join(base, app_name)


_log_lines = []


def log(message):
    """Print a message to the terminal and keep it for blackjack.log, which is
    written to the settings folder after start-up (handy when the program is
    started from a menu or launcher, where the terminal can't be seen)."""
    print(message, file=sys.stderr)
    _log_lines.append(message)


def write_log(folder):
    try:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "blackjack.log"), "w", encoding="utf-8") as f:
            f.write(time.strftime("Started %Y-%m-%d %H:%M:%S\n"))
            f.write("\n".join(_log_lines) + "\n")
    except OSError:
        pass


def find_file(folder, filename):
    """Path of `filename` in `folder`, ignoring upper/lower case, or None."""
    exact = os.path.join(folder, filename)
    if os.path.isfile(exact):
        return exact
    try:
        for name in os.listdir(folder):
            if name.lower() == filename.lower():
                path = os.path.join(folder, name)
                if os.path.isfile(path):
                    return path
    except OSError:
        pass
    return None


def log_png_colour_info(path):
    """Note any colour settings stored in a PNG.  Image viewers apply these,
    but the game draws the raw pixel values, so they can look different."""
    pil = pil_image()
    if pil is None:
        return
    try:
        with pil.open(path) as im:           # reads the header only, not the pixels
            info = im.info
            notes = [f"mode {im.mode}"]
            if "gamma" in info:
                notes.append(f"gamma {info['gamma']} (viewers adjust brightness for this)")
            if "icc_profile" in info:
                notes.append("embedded colour profile (viewers apply it)")
            if "srgb" in info:
                notes.append("sRGB")
        log("Background colour info: " + ", ".join(notes))
    except Exception:
        pass


def fmt_money(value):
    if abs(value - round(value)) < 1e-9:
        return f"${int(round(value)):,}"
    return f"${value:,.2f}"


def set_text(label, text):
    if label.text != text:
        label.text = text


def lighten(color):
    return tuple(min(255, int(c * 1.25) + 12) for c in color[:3]) + (255,)


def safe_delete(obj):
    if obj is not None:
        try:
            obj.delete()
        except Exception:
            pass


def get_screens():
    try:
        module = getattr(pyglet, "display", None)
        if module is None:
            module = pyglet.canvas
        return list(module.get_display().get_screens())
    except Exception:
        return []


def hand_value(cards):
    """Return (best_total, is_soft) for a list of card codes like 'TC', 'AS'."""
    total = 0
    aces = 0
    for card in cards:
        rank = card[0]
        if rank == "A":
            aces += 1
            total += 11
        elif rank in "TJQK":
            total += 10
        else:
            total += int(rank)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total, aces > 0


def card_points(card):
    """Blackjack value of one card for splitting purposes (Ace = 11, face cards = 10)."""
    rank = card[0]
    if rank == "A":
        return 11
    if rank in "TJQK":
        return 10
    return int(rank)


def is_blackjack(cards):
    return len(cards) == 2 and hand_value(cards)[0] == 21


def _number(value, default, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if not math.isfinite(value) or value < minimum:
        return default
    return value


def enable_mipmaps(texture):
    """Generate mipmaps so cards drawn much smaller than the artwork stay smooth."""
    try:
        from pyglet import gl
        gl.glBindTexture(texture.target, texture.id)
        gl.glGenerateMipmap(texture.target)
        gl.glTexParameteri(texture.target, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR_MIPMAP_LINEAR)
        gl.glBindTexture(texture.target, 0)
    except Exception:
        pass


_pil_module = False                     # False = not looked for yet


def pil_image():
    """Pillow's Image module, or None.  Imported on first use (not at start-up),
    so the "Loading..." screen can appear before this work is done."""
    global _pil_module
    if _pil_module is False:
        try:
            from PIL import Image
            _pil_module = Image
        except ImportError:
            _pil_module = None
    return _pil_module


def numbered_entries(folder, pattern):
    """[(number, name, path)] for the entries of `folder` matching `pattern`
    (whose first group is a number), sorted by that number."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    found = []
    for name in names:
        match = pattern.fullmatch(name)
        if match:
            found.append((int(match.group(1)), name, os.path.join(folder, name)))
    found.sort()
    return found


def find_face_sets(root):
    """[(name, folder)] for every complete resources/cards_N folder."""
    sets = []
    for _, name, path in numbered_entries(root, re.compile(r"cards_(\d+)")):
        if not os.path.isdir(path):
            continue
        missing = [n + ".png" for n in FACE_NAMES
                   if not os.path.isfile(os.path.join(path, n + ".png"))]
        if missing:
            more = " ..." if len(missing) > 4 else ""
            log(f"Skipping card set {path}: missing {', '.join(missing[:4])}{more}")
            continue
        sets.append((name, path))
    legacy = os.path.join(root, "cards")             # the older single-folder layout
    if not sets and os.path.isdir(legacy):
        sets.append(("cards", legacy))
    return sets


def find_backs(root, face_sets):
    """[(name, path)] for resources/card-backs/back_N.png, sorted by N."""
    folder = os.path.join(root, "card-backs")
    backs = [(os.path.splitext(name)[0], path)
             for _, name, path in numbered_entries(folder, re.compile(r"back_(\d+)\.png", re.I))
             if os.path.isfile(path)]
    if not backs:                                    # older layout: cards/back_dark.png
        for _, face_folder in face_sets:
            path = os.path.join(face_folder, "back_dark.png")
            if os.path.isfile(path):
                backs.append(("back_dark", path))
                break
    return backs


def missing_file(path):
    return FileNotFoundError(2, "No such file or directory", path)


# --------------------------------------------------------------------------
# Saved data (money, statistics, settings, window geometry)
# --------------------------------------------------------------------------
class Store:
    def __init__(self):
        self.folder = config_dir()
        self.path = os.path.join(self.folder, "blackjack.json")
        self._migrate()
        self.recovered_hand = False
        self.data = {
            "money": START_MONEY,
            "won": 0,
            "lost": 0,
            "pushed": 0,
            "blackjacks": 0,
            "in_round": False,
            "sound": True,
            "card_faces": None,                 # folder name of the chosen face set
            "card_back": None,                  # file name (no .png) of the chosen back
            "window": {
                "width": DEFAULT_WINDOW[0],
                "height": DEFAULT_WINDOW[1],
                "x": None,
                "y": None,
            },
        }
        self._load()

    def _migrate(self):
        """First run with the new settings folder: bring the money, stats and
        settings over from the old folder.  (The image caches are not copied;
        they are rebuilt automatically.)"""
        old = os.path.join(config_dir(OLD_APP_NAME), "blackjack.json")
        if os.path.exists(self.path) or not os.path.isfile(old):
            return
        try:
            os.makedirs(self.folder, exist_ok=True)
            shutil.copy2(old, self.path)
        except OSError as exc:
            log(f"Could not copy the old settings from {old}: {exc}")

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return

        d = self.data
        money = _number(raw.get("money"), None)
        if money is not None:
            d["money"] = round(money * 2) / 2
        for name in ("won", "lost", "pushed", "blackjacks"):
            value = _number(raw.get(name), None)
            if value is not None:
                d[name] = int(value)
        d["sound"] = bool(raw.get("sound", True))
        d["in_round"] = bool(raw.get("in_round", False))
        for name in ("card_faces", "card_back"):
            if isinstance(raw.get(name), str):
                d[name] = raw[name]

        win = raw.get("window")
        if isinstance(win, dict):
            width = _number(win.get("width"), None, 1)
            height = _number(win.get("height"), None, 1)
            if width is not None and height is not None:
                d["window"]["width"] = int(min(width, 10000))
                d["window"]["height"] = int(min(height, 10000))
            for name in ("x", "y"):
                value = win.get(name)
                if (isinstance(value, (int, float)) and not isinstance(value, bool)
                        and math.isfinite(value)):
                    d["window"][name] = int(value)

        # The program was closed in the middle of a hand: the bet is forfeited.
        if d["in_round"]:
            d["lost"] += 1
            d["in_round"] = False
            self.recovered_hand = True
            self.save()

    def save(self):
        data = dict(self.data)
        money = data["money"]
        data["money"] = int(money) if money == int(money) else money
        try:
            os.makedirs(self.folder, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.path)
        except OSError as exc:
            print(f"Could not save game data: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------
# Images and sounds
# --------------------------------------------------------------------------
def decode_png(path, max_h):
    """Return (width, height, RGBA bytes, bottom row first) for a PNG file."""
    pil = pil_image()
    if pil is not None:
        resample = getattr(pil, "Resampling", pil).LANCZOS
        flip = getattr(pil, "Transpose", pil).FLIP_TOP_BOTTOM
        with pil.open(path) as im:
            im = im.convert("RGBA")
            if max_h and im.height > max_h:
                width = max(1, round(im.width * max_h / im.height))
                im = im.resize((width, max_h), resample)
            im = im.transpose(flip)          # pyglet wants the bottom row first
            return im.width, im.height, im.tobytes()
    image = pyglet.image.load(path)
    return image.width, image.height, image.get_data("RGBA", image.width * 4)


class ImageSet:
    """A group of images (a card-face set, one card back, the background).

    Decoding PNGs is the slow part of starting up, so the decoded pixels are
    cached in the settings folder (one cache file per group); later runs skip
    the PNG decoding."""

    def __init__(self, items, cache_path, max_h=MAX_TEXTURE_HEIGHT, opaque=False):
        self.items = list(items)            # [(name, path)]
        self.cache_path = cache_path
        self.max_h = max_h
        self.opaque = opaque                # True: ignore transparency, use the raw colours
        self.had_transparency = False
        self.textures = {}

    def _store(self, name, width, height, data):
        if self.opaque:
            count = width * height
            alpha = bytes(data[3::4])
            if alpha != b"\xff" * count:
                self.had_transparency = True
                data = bytearray(data)
                data[3::4] = b"\xff" * count
                data = bytes(data)
        texture = pyglet.image.ImageData(width, height, "RGBA", data).get_texture()
        if USE_MIPMAPS and not self.opaque:     # the background is never shrunk
            enable_mipmaps(texture)
        self.textures[name] = texture

    def _open_cache(self):
        """Return (file, entries, data_offset) for a usable cache file, else None."""
        handle = None
        try:
            handle = open(self.cache_path, "rb")
            if handle.read(4) != b"BJC1":
                raise ValueError("bad cache")
            size = int.from_bytes(handle.read(4), "little")
            header = json.loads(handle.read(size).decode("utf-8"))
            if header["version"] != CACHE_VERSION or header["max_h"] != self.max_h:
                raise ValueError("old cache")
            if not isinstance(header["entries"], dict):
                raise ValueError("bad cache")
            return handle, header["entries"], 8 + size
        except (OSError, ValueError, KeyError, TypeError):
            if handle is not None:
                handle.close()
            return None

    def _write_cache(self, stamps, collected):
        entries = {}
        chunks = []
        offset = 0
        for name, _ in self.items:
            width, height, blob = collected[name]
            entries[name] = {"stamp": stamps[name], "w": width, "h": height,
                             "off": offset, "len": len(blob)}
            chunks.append(blob)
            offset += len(blob)
        header = json.dumps({"version": CACHE_VERSION, "max_h": self.max_h,
                             "entries": entries}).encode("utf-8")
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            tmp = self.cache_path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(b"BJC1")
                f.write(len(header).to_bytes(4, "little"))
                f.write(header)
                for chunk in chunks:
                    f.write(chunk)
            os.replace(tmp, self.cache_path)
        except OSError as exc:
            log(f"Could not write the image cache {self.cache_path}: {exc}")

    def load_steps(self):
        """Generator: loads one image per step and yields after each one.

        An image is reused from the cache when its contents (size and CRC) are
        unchanged - file dates are not used, because a packaged program may
        unpack its resources with new dates on every run."""
        cache = self._open_cache()
        rebuild = cache is None
        stamps = {}
        collected = {}
        try:
            for name, path in self.items:
                with open(path, "rb") as f:      # FileNotFoundError if an image is missing
                    raw = f.read()
                stamp = [len(raw), zlib.crc32(raw)]
                loaded = False
                if cache is not None:
                    handle, entries, base = cache
                    try:
                        entry = entries.get(name)
                        if entry is not None and entry["stamp"] == stamp:
                            handle.seek(base + entry["off"])
                            blob = handle.read(entry["len"])
                            data = zlib.decompress(blob)
                            width, height = entry["w"], entry["h"]
                            if len(data) != width * height * 4:
                                raise ValueError("bad size")
                            loaded = True
                    except (OSError, ValueError, KeyError, TypeError, zlib.error):
                        handle.close()           # damaged cache: decode from the PNGs
                        cache = None
                if not loaded:
                    rebuild = True
                    width, height, data = decode_png(path, self.max_h)
                    blob = zlib.compress(data, 1)
                stamps[name] = stamp
                collected[name] = (width, height, blob)
                self._store(name, width, height, data)
                yield
        finally:
            if cache is not None:
                cache[0].close()
        if rebuild:
            self._write_cache(stamps, collected)


class Sounds:
    # The ones needed for the first hand come first; the rest load a moment later.
    NAMES = ("shuffle", "card_placement", "card_flip", "illegal_move", "win", "lose")

    def __init__(self, folder, enabled):
        self.folder = folder
        self.enabled = enabled
        self.sources = {}
        self.players = []
        self._next = 0
        self._first_error = None

    def load_next(self):
        """Load one sound file.  Returns True once every sound has been tried."""
        if self._next < len(self.NAMES):
            name = self.NAMES[self._next]
            self._next += 1
            try:
                self.sources[name] = pyglet.media.load(
                    os.path.join(self.folder, name + ".mp3"), streaming=False)
            except Exception as exc:
                if self._first_error is None:
                    self._first_error = exc
        if self._next < len(self.NAMES):
            return False
        if self._first_error is not None:
            log(f"Some sounds could not be loaded: {self._first_error}")
            if not self.sources:
                print("On Linux, pyglet needs FFmpeg to play MP3 files "
                      "(for example: sudo apt install ffmpeg).", file=sys.stderr)
        return True

    def play(self, name):
        if not self.enabled:
            return
        source = self.sources.get(name)
        if source is None:
            return
        try:
            alive = []
            for player in self.players:
                if player.playing:
                    alive.append(player)
                else:
                    safe_delete(player)
            alive.append(source.play())
            self.players = alive
        except Exception:
            pass


# --------------------------------------------------------------------------
# UI pieces
# --------------------------------------------------------------------------
class Button:
    def __init__(self, text, callback, font_size=15, color=BTN):
        self.callback = callback
        self.color = color
        self.enabled = True
        self.hover = False
        self.geom = None
        self.border = None
        self.fill = None
        self._fill_color = None
        self._label_color = WHITE
        self.label = pyglet.text.Label(
            text, font_size=font_size, weight="bold", color=WHITE,
            anchor_x="center", anchor_y="center")

    def set_text(self, text):
        self.label.text = text

    def place(self, x, y, w, h):
        geom = (round(x), round(y), round(w), round(h))
        if geom == self.geom:
            return
        self.geom = geom
        safe_delete(self.border)
        safe_delete(self.fill)
        self.border = shapes.Rectangle(x, y, w, h, color=BTN_BORDER)
        self.fill = shapes.Rectangle(x + 2, y + 2, w - 4, h - 4, color=self.color)
        self._fill_color = self.color
        self.label.x = x + w / 2
        self.label.y = y + h / 2

    def contains(self, px, py):
        if not self.geom:
            return False
        x, y, w, h = self.geom
        return x <= px <= x + w and y <= py <= y + h

    def draw(self):
        if self.fill is None:
            return
        if not self.enabled:
            fill = BTN_DISABLED
        elif self.hover:
            fill = lighten(self.color)
        else:
            fill = self.color
        if fill != self._fill_color:
            self.fill.color = fill
            self._fill_color = fill
        text_color = WHITE if self.enabled else TEXT_DISABLED
        if text_color != self._label_color:
            self.label.color = text_color
            self._label_color = text_color
        self.border.draw()
        self.fill.draw()
        self.label.draw()


class CardSprite:
    """One card on the table: a sprite that glides toward a target position."""

    def __init__(self, win, card, face_up, x, y):
        self.win = win
        self.card = card
        self.face_up = face_up
        self.x = self.tx = x
        self.y = self.ty = y
        self.sprite = pyglet.sprite.Sprite(self._texture(), x=x, y=y)
        self.refresh()

    def _texture(self):
        return self.win.face_tex[self.card] if self.face_up else self.win.back_tex

    def refresh(self):
        """Re-apply image, size and position (after a flip or a window resize)."""
        texture = self._texture()
        self.sprite.image = texture
        self.sprite.update(x=self.x, y=self.y,
                           scale_x=self.win.card_w / texture.width,
                           scale_y=self.win.card_h / texture.height)

    def snap(self):
        self.x, self.y = self.tx, self.ty
        self.sprite.update(x=self.x, y=self.y)

    def step(self, dt):
        if self.x == self.tx and self.y == self.ty:
            return
        k = 1.0 - math.exp(-dt * 14.0)
        self.x += (self.tx - self.x) * k
        self.y += (self.ty - self.y) * k
        if abs(self.tx - self.x) < 0.4 and abs(self.ty - self.y) < 0.4:
            self.x, self.y = self.tx, self.ty
        self.sprite.update(x=self.x, y=self.y)

    def draw(self):
        self.sprite.draw()

    def delete(self):
        safe_delete(self.sprite)


# --------------------------------------------------------------------------
# One hand of the player (a round has several after a split)
# --------------------------------------------------------------------------
class Hand:
    def __init__(self, bet, from_split=False):
        self.cards = []              # CardSprite list
        self.bet = bet
        self.doubled = False
        self.from_split = from_split  # 21 on a split hand is not a Blackjack
        self.done = False            # stood, busted, reached 21, or a split ace
        self.result = ""             # WIN / LOSE / PUSH once the round is settled


# --------------------------------------------------------------------------
# The game window
# --------------------------------------------------------------------------
class BlackjackWindow(pyglet.window.Window):
    _ready = False              # True once all resources are loaded and the UI is built
    _load_scheduled = False

    def __init__(self, store):
        self.store = store
        self.saved = store.data
        win = self.saved["window"]
        width, height = self._initial_size(win)
        super().__init__(width=width, height=height, caption="Blackjack", resizable=True)
        self.set_minimum_size(*MIN_WINDOW)
        self._restore_position(win, width, height)

        # Show "Loading..." straight away.  The card images are then loaded a
        # little at a time (load_tick) so the window stays responsive, and the
        # sounds are loaded after the table is already showing.
        self.load_error = None
        self.card_aspect = CARD_ASPECT
        self.background_tex = None
        self._load_gen = None
        self._load_progress = 0.0
        self._loading_bg = None
        self._loading_bar = [None, None]
        self._loading_label = pyglet.text.Label(
            "Loading...", font_size=28, weight="bold", color=WHITE,
            anchor_x="center", anchor_y="center")
        self.paint_loading_now()

    def paint_loading_now(self):
        """Put "Loading..." on screen immediately, before any slow work starts,
        instead of waiting for the event loop's first frame."""
        try:
            self.switch_to()
            for _ in range(2):
                self.dispatch_events()
                self.draw_loading()
                self.flip()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Images: card faces, card backs and the background
    # ------------------------------------------------------------------
    def cache_path(self, name):
        return os.path.join(self.store.folder, "cache", name + ".cache")

    def face_loader(self, index):
        name, folder = self.face_sets[index]
        items = [(n, os.path.join(folder, n + ".png")) for n in FACE_NAMES]
        return ImageSet(items, self.cache_path("faces-" + name))

    def back_loader(self, index):
        name, path = self.back_list[index]
        return ImageSet([("back", path)], self.cache_path("back-" + name))

    def use_faces(self, index, images):
        self.face_index = index
        self.face_tex = images.textures
        sample = self.face_tex["AS"]
        self.card_aspect = sample.width / sample.height
        self.saved["card_faces"] = self.face_sets[index][0]

    def use_back(self, index, images):
        self.back_index = index
        self.back_tex = images.textures["back"]
        self.saved["card_back"] = self.back_list[index][0]

    @staticmethod
    def saved_index(items, name):
        for i, (item_name, _) in enumerate(items):
            if item_name == name:
                return i
        return 0

    def assets_steps(self):
        """Generator for the start-up load; yields the fraction done."""
        root = resource_dir()
        self.sound_folder = os.path.join(root, "sounds")
        try:                                         # cache file from older versions
            os.remove(os.path.join(self.store.folder, "cards.cache"))
        except OSError:
            pass

        self.face_sets = find_face_sets(root)
        if not self.face_sets:
            raise missing_file(os.path.join(root, "cards_1"))
        self.back_list = find_backs(root, self.face_sets)
        if not self.back_list:
            raise missing_file(os.path.join(root, "card-backs", "back_1.png"))

        face_index = self.saved_index(self.face_sets, self.saved["card_faces"])
        back_index = self.saved_index(self.back_list, self.saved["card_back"])
        faces = self.face_loader(face_index)
        back = self.back_loader(back_index)
        log(f"Resources folder: {root}")
        log(f"Card face sets: {', '.join(n for n, _ in self.face_sets)}")
        log(f"Card backs: {', '.join(n for n, _ in self.back_list)}")
        bg_path = find_file(root, "background.png")
        background = None
        if bg_path is None:
            log(f"Background: no background.png in {root} - using the green felt")
        else:
            background = ImageSet([("background", bg_path)], self.cache_path("background"),
                                  MAX_BACKGROUND_HEIGHT, opaque=True)

        total = len(faces.items) + 1 + (4 if background else 0)
        done = 0
        for _ in faces.load_steps():
            done += 1
            yield done / total
        for _ in back.load_steps():
            done += 1
            yield done / total
        self.use_faces(face_index, faces)
        self.use_back(back_index, back)

        self.background_tex = None
        if background is not None:
            try:                                     # a bad background is not fatal
                for _ in background.load_steps():
                    pass
                self.background_tex = background.textures["background"]
                tex = self.background_tex
                log(f"Background: {bg_path} loaded ({tex.width} x {tex.height})")
                if background.had_transparency:
                    log("Background: the image has transparent parts; "
                        "they are drawn fully opaque")
                log_png_colour_info(bg_path)
            except Exception as exc:
                log(f"Background: could not load {bg_path}: {exc!r}")
            yield 1.0
        write_log(self.store.folder)

    def load_tick(self, dt):
        """Runs every frame while loading: do about 30 ms of work, then redraw."""
        if getattr(self, "has_exit", False):
            pyglet.clock.unschedule(self.load_tick)
            return
        if self._load_gen is None:
            self._load_gen = self.assets_steps()
        deadline = time.perf_counter() + 0.03
        try:
            while True:
                self._load_progress = next(self._load_gen)
                if time.perf_counter() >= deadline:
                    break
        except StopIteration:
            pyglet.clock.unschedule(self.load_tick)
            self.build_game()
        except Exception as exc:
            pyglet.clock.unschedule(self.load_tick)
            if isinstance(exc, FileNotFoundError):
                self.load_error = (f"Missing resource file: {exc.filename}\n"
                                   "The 'resources' folder (with 'cards_1', 'card-backs' "
                                   "and 'sounds') must be next to blackjack.py.")
            else:
                self.load_error = f"Could not load the card images: {exc}"
            log(self.load_error)
            write_log(self.store.folder)
            self.close()
            pyglet.app.exit()

    def sound_tick(self, dt):
        if self.sounds.load_next():
            pyglet.clock.unschedule(self.sound_tick)

    def build_game(self):
        for obj in [self._loading_bg] + self._loading_bar:
            safe_delete(obj)
        self._loading_bg = None
        self._loading_bar = [None, None]
        self.sounds = Sounds(self.sound_folder, self.saved["sound"])

        # Game state
        self.phase = "bet"          # bet | busy | insurance | player
        self.bet = 0                # the wager chosen for the next hand
        self._bet_color = WHITE
        self.dealer = []            # the dealer's CardSprite list
        self.hands = []             # the player's Hand objects (several after a split)
        self.active = 0             # index of the hand being played
        self.insurance = 0          # insurance money at stake this round
        self.turn_note = ""
        self.active_bar = None      # gold underline marking the active split hand
        self._bar_key = None
        self._hands_key = None
        self.shoe = []
        self.new_shoe()
        self.shuffle_pending = True
        self.confirming = False
        self.confirm_token = 0
        self.card_w = 100.0
        self.card_h = 145.0
        self.top_h = 64
        self.bottom_h = 112
        self.mid_y = 300
        self.shoe_x = self.shoe_y = 0
        self.dealer_y = self.player_y = 0

        if self.store.recovered_hand:
            self.msg_main = "Welcome back"
            self.msg_hint = "Your last hand was never finished, so that bet counted as a loss."
        elif int(self.money) < 1:
            self.msg_main = "Welcome back"
            self.msg_hint = self.broke_hint()
        else:
            self.msg_main = "Blackjack"
            self.msg_hint = "Place your bet, then press Deal.   Blackjack pays 3:2.   Dealer stands on 17."
        self._msg_key = None
        self.msg_bg = None

        # Text
        self.lbl_money = pyglet.text.Label("", font_size=20, weight="bold", color=GOLD,
                                           anchor_x="left", anchor_y="center")
        self.lbl_stats1 = pyglet.text.Label("", font_size=13, color=LIGHT,
                                            anchor_x="left", anchor_y="center")
        self.lbl_stats2 = pyglet.text.Label("", font_size=13, color=LIGHT,
                                            anchor_x="left", anchor_y="center")
        self.lbl_bet = pyglet.text.Label("", font_size=18, weight="bold", color=WHITE,
                                         anchor_x="center", anchor_y="center")
        self.lbl_dealer = pyglet.text.Label("", font_size=16, weight="bold", color=WHITE,
                                            anchor_x="center", anchor_y="bottom")
        self.lbl_hands = [pyglet.text.Label("", font_size=16, weight="bold", color=WHITE,
                                            anchor_x="center", anchor_y="bottom")
                          for _ in range(MAX_HANDS)]
        self.lbl_shoe = pyglet.text.Label("", font_size=11, color=LIGHT,
                                          anchor_x="left", anchor_y="bottom")
        self.lbl_msg = pyglet.text.Label("", font_size=26, weight="bold", color=WHITE,
                                         anchor_x="center", anchor_y="center")
        self.lbl_hint = pyglet.text.Label("", font_size=14, color=LIGHT,
                                          anchor_x="center", anchor_y="center")

        # Buttons
        self.btn_sound = Button("", self.toggle_sound, font_size=13)
        self.btn_clear = Button("New Player", self.clear_stats_clicked, font_size=13)
        self.chip_buttons = [Button(f"${v}", lambda v=v: self.add_bet(v)) for v in CHIP_VALUES]
        self.btn_allin = Button("All In", self.all_in)
        self.btn_clearbet = Button("Clear Bet", self.clear_bet)
        self.btn_deal = Button("DEAL", self.start_round, color=BTN_GREEN)
        self.btn_hit = Button("Hit (H)", self.action_hit)
        self.btn_stand = Button("Stand (S)", self.action_stand)
        self.btn_double = Button("Double (D)", self.action_double)
        self.btn_split = Button("Split (P)", self.action_split)
        self.btn_ins_yes = Button("Insurance (I)", self.take_insurance, color=BTN_GREEN)
        self.btn_ins_no = Button("No Insurance (N)", self.decline_insurance)
        self.btn_faces = Button("", self.next_faces, font_size=12)
        self.btn_back = Button("", self.next_back, font_size=12)
        self.top_buttons = [self.btn_sound, self.btn_clear]
        self.style_buttons = [self.btn_faces, self.btn_back]
        self.fixed_buttons = self.top_buttons + self.style_buttons   # always shown
        self.all_buttons = (self.fixed_buttons + self.chip_buttons +
                            [self.btn_allin, self.btn_clearbet, self.btn_deal,
                             self.btn_hit, self.btn_stand, self.btn_double, self.btn_split,
                             self.btn_ins_yes, self.btn_ins_no])
        self.refresh_sound_button()
        self._switch = None             # (kind, index, ImageSet, generator) while loading
        self.refresh_style_buttons()

        # Layout objects (rebuilt on every resize)
        self.bg_image = None
        self.bg_felt = self.bg_top = self.bg_bottom = None
        self.line_top = self.line_bottom = None
        self.shoe_sprites = []
        self.empty_sprites = []

        self.do_layout()
        self._ready = True
        pyglet.clock.schedule_interval(self.tick, 1 / 60.0)
        pyglet.clock.schedule_interval(self.sound_tick, 0.15)   # sounds load in the background

    # ------------------------------------------------------------------
    # Money (kept in the saved data so it is always persisted)
    # ------------------------------------------------------------------
    @property
    def money(self):
        return self.saved["money"]

    @money.setter
    def money(self, value):
        self.saved["money"] = value

    @staticmethod
    def broke_hint():
        return ("You don't have enough for a $1 bet. "
                "Click New Player to start again.")

    # ------------------------------------------------------------------
    # Window size / position memory
    # ------------------------------------------------------------------
    @staticmethod
    def _initial_size(win):
        width = max(MIN_WINDOW[0], win["width"])
        height = max(MIN_WINDOW[1], win["height"])
        screens = get_screens()
        if screens:
            biggest = max(screens, key=lambda s: s.width * s.height)
            width = min(width, biggest.width)
            height = min(height, biggest.height)
        return width, height

    def _restore_position(self, win, width, height):
        x, y = win["x"], win["y"]
        if x is None or y is None:
            return
        screens = get_screens()
        on_screen = not screens or any(
            s.x - width + 120 <= x <= s.x + s.width - 120 and
            s.y - 10 <= y <= s.y + s.height - 80
            for s in screens)
        if on_screen:
            try:
                self.set_location(x, y)
            except Exception:
                pass

    def remember_geometry(self):
        win = self.saved["window"]
        try:
            width, height = self.get_size()
            win["width"], win["height"] = int(width), int(height)
        except Exception:
            pass
        try:
            x, y = self.get_location()
            win["x"], win["y"] = int(x), int(y)
        except Exception:
            pass

    def _schedule_geometry_save(self):
        pyglet.clock.unschedule(self._geometry_save)
        pyglet.clock.schedule_once(self._geometry_save, 0.75)

    def _geometry_save(self, dt):
        self.remember_geometry()
        self.store.save()

    def on_resize(self, width, height):
        super().on_resize(width, height)
        if self._ready and width > 50 and height > 50:
            self.do_layout()
            self._schedule_geometry_save()

    def on_move(self, x, y):
        if self._ready:
            self._schedule_geometry_save()

    def on_close(self):
        self.remember_geometry()
        self.store.save()
        super().on_close()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def do_layout(self):
        W, H = self.width, self.height
        top_h, bottom_h = self.top_h, self.bottom_h

        for obj in (self.bg_image, self.bg_felt, self.bg_top, self.bg_bottom,
                    self.line_top, self.line_bottom):
            safe_delete(obj)
        self.bg_felt = shapes.Rectangle(0, 0, W, H, color=FELT)
        self.bg_image = None
        if self.background_tex is not None:
            # Shown at its real size, centred (edges that don't fit are cut off).
            # Only if it is too small to fill the window is it enlarged to cover it.
            tex = self.background_tex
            scale = max(1.0, W / tex.width, H / tex.height)
            self.bg_image = pyglet.sprite.Sprite(tex, x=(W - tex.width * scale) / 2,
                                                 y=(H - tex.height * scale) / 2)
            self.bg_image.update(scale_x=scale, scale_y=scale)
        self.bg_top = shapes.Rectangle(0, H - top_h, W, top_h, color=PANEL)
        self.bg_bottom = shapes.Rectangle(0, 0, W, bottom_h, color=PANEL)
        self.line_top = shapes.Rectangle(0, H - top_h - 2, W, 2, color=PANEL_LINE)
        self.line_bottom = shapes.Rectangle(0, bottom_h, W, 2, color=PANEL_LINE)

        # Card size from the space available
        table_h = H - top_h - bottom_h
        card_h = max(80.0, min((table_h - 170) / 2, W * 0.26, 260.0))
        self.card_h = card_h
        self.card_w = card_h * self.card_aspect
        self.dealer_y = H - top_h - 40 - card_h
        self.player_y = bottom_h + 38
        self.mid_y = (self.dealer_y + self.player_y + card_h) / 2
        self.shoe_x = W - self.card_w - 28
        self.shoe_y = self.dealer_y

        # Top panel
        self.lbl_money.x, self.lbl_money.y = 18, H - top_h / 2
        self.lbl_stats1.x, self.lbl_stats1.y = 280, H - top_h / 2 + 11
        self.lbl_stats2.x, self.lbl_stats2.y = 280, H - top_h / 2 - 11

        # Bottom panel / table labels
        self.lbl_bet.x, self.lbl_bet.y = W / 2, bottom_h - 24
        self.lbl_dealer.x, self.lbl_dealer.y = W / 2, self.dealer_y + card_h + 8
        self.lbl_shoe.x, self.lbl_shoe.y = self.shoe_x, self.shoe_y - 22
        self.lbl_msg.x = W / 2
        self.lbl_hint.x = W / 2
        self._msg_key = None
        self._hands_key = None
        self._bar_key = None

        # Shoe and empty card slots
        for sprite in self.shoe_sprites + self.empty_sprites:
            safe_delete(sprite)
        self.shoe_sprites = []
        for i in range(3):
            sprite = pyglet.sprite.Sprite(self.back_tex,
                                          x=self.shoe_x + i * 2, y=self.shoe_y - i * 2)
            sprite.update(scale_x=self.card_w / self.back_tex.width,
                          scale_y=self.card_h / self.back_tex.height)
            self.shoe_sprites.append(sprite)
        self.empty_sprites = []
        for y in (self.dealer_y, self.player_y):
            for x, yy in self.fan(2, y, W / 2, W * 0.62):
                empty = self.face_tex["empty"]
                sprite = pyglet.sprite.Sprite(empty, x=x, y=yy)
                sprite.update(scale_x=self.card_w / empty.width,
                              scale_y=self.card_h / empty.height)
                self.empty_sprites.append(sprite)

        self.layout_buttons()
        self.relayout_cards()

    def active_buttons(self):
        if self.phase == "bet":
            return self.chip_buttons + [self.btn_allin, self.btn_clearbet, self.btn_deal]
        if self.phase == "player":
            return [self.btn_hit, self.btn_stand, self.btn_double, self.btn_split]
        if self.phase == "insurance":
            return [self.btn_ins_yes, self.btn_ins_no]
        return []

    def layout_buttons(self):
        W, H = self.width, self.height
        buttons = self.active_buttons()
        if buttons:
            gap, margin = 10, 20
            if self.phase == "player":
                weights = [1.2] * len(buttons)
            elif self.phase == "insurance":
                weights = [1.7] * len(buttons)
            else:
                weights = [1.7 if b is self.btn_deal else 1.0 for b in buttons]
            avail = W - 2 * margin - gap * (len(buttons) - 1)
            unit = min(avail / sum(weights), 150)
            total = unit * sum(weights) + gap * (len(buttons) - 1)
            x = (W - total) / 2
            for button, weight in zip(buttons, weights):
                button.place(x, 16, unit * weight, 48)
                x += unit * weight + gap
        top_y = H - self.top_h / 2 - 17
        self.btn_clear.place(W - 16 - 118, top_y, 118, 34)
        self.btn_sound.place(W - 16 - 118 - 8 - 118, top_y, 118, 34)

        # Card style buttons: top-left of the table, opposite the shoe.
        style_w = max(96, min(132, W * 0.19 - 28))
        style_y = H - self.top_h - 2 - 14 - 32
        self.btn_faces.place(16, style_y, style_w, 32)
        self.btn_back.place(16, style_y - 40, style_w, 32)

    def set_phase(self, phase):
        self.phase = phase
        self.layout_buttons()

    # ------------------------------------------------------------------
    # Hands and cards
    # ------------------------------------------------------------------
    def fan(self, count, y, cx, avail):
        """Positions for `count` overlapping cards centred on cx within `avail` pixels."""
        cw = self.card_w
        if count <= 1:
            step = 0.0
        else:
            step = min(cw * 0.68, max(cw * 0.18, (avail - cw) / (count - 1)))
        x0 = cx - (cw + step * (count - 1)) / 2
        return [(x0 + i * step, y) for i in range(count)]

    def hand_slot(self, index, count):
        """(centre x, available width) for player hand `index` out of `count`."""
        if count <= 1:
            return self.width / 2, self.width * 0.62
        slot = self.width * 0.92 / count
        return self.width * 0.04 + slot * (index + 0.5), slot - 14

    def all_cards(self):
        cards = list(self.dealer)
        for hand in self.hands:
            cards.extend(hand.cards)
        return cards

    @property
    def active_hand(self):
        return self.hands[self.active]

    def arrange(self):
        slots = self.fan(len(self.dealer), self.dealer_y, self.width / 2, self.width * 0.62)
        for cs, (x, y) in zip(self.dealer, slots):
            cs.tx, cs.ty = x, y
        count = len(self.hands)
        for i, hand in enumerate(self.hands):
            cx, avail = self.hand_slot(i, count)
            slots = self.fan(len(hand.cards), self.player_y, cx, avail)
            for cs, (x, y) in zip(hand.cards, slots):
                cs.tx, cs.ty = x, y

    def relayout_cards(self):
        self.arrange()
        for cs in self.all_cards():
            cs.refresh()
            cs.snap()

    def new_shoe(self):
        self.shoe = [r + s for _ in range(NUM_DECKS) for r in RANKS for s in SUITS]
        random.shuffle(self.shoe)

    def draw_card(self):
        if not self.shoe:
            self.new_shoe()
        return self.shoe.pop()

    def deal_card(self, hand, face_up=True):
        card = self.draw_card()
        cs = CardSprite(self, card, face_up, self.shoe_x, self.shoe_y)
        hand.append(cs)
        self.arrange()
        self.sounds.play("card_placement")
        return cs

    def reveal_hole(self, sound=True):
        for cs in self.dealer:
            if not cs.face_up:
                cs.face_up = True
                cs.refresh()
                if sound:
                    self.sounds.play("card_flip")

    def clear_table(self):
        for cs in self.all_cards():
            cs.delete()
        self.dealer = []
        self.hands = []
        self.active = 0
        self.insurance = 0

    def hole_hidden(self):
        return any(not cs.face_up for cs in self.dealer)

    # ------------------------------------------------------------------
    # Timing helper
    # ------------------------------------------------------------------
    @staticmethod
    def later(delay, func):
        pyglet.clock.schedule_once(lambda dt: func(), delay)

    # ------------------------------------------------------------------
    # Betting
    # ------------------------------------------------------------------
    def reject(self, hint=None):
        self.sounds.play("illegal_move")
        if hint:
            self.msg_hint = hint

    def add_bet(self, amount):
        if self.phase != "bet":
            return
        limit = int(self.money)
        if self.bet >= limit:
            self.reject("You can't bet more than you have.")
            return
        self.bet = min(self.bet + amount, limit)

    def all_in(self):
        if self.phase != "bet":
            return
        limit = int(self.money)
        if limit < 1 or self.bet >= limit:
            self.reject("You can't bet more than you have.")
            return
        self.bet = limit

    def clear_bet(self):
        if self.phase != "bet":
            return
        self.bet = 0

    # ------------------------------------------------------------------
    # Round flow
    # ------------------------------------------------------------------
    def start_round(self):
        if self.phase != "bet":
            return
        bet = min(int(self.bet), int(self.money))
        if bet < 1:
            self.reject("Place a bet first.")
            return
        self.cancel_confirm()
        self.bet = bet
        self.money -= bet
        self.saved["in_round"] = True       # a bet is at stake; saved in case the program quits
        self.store.save()

        self.clear_table()
        self.hands = [Hand(bet)]
        self.set_phase("busy")
        self.set_message("", "")

        delay = 0.1
        if self.shuffle_pending or len(self.shoe) < RESHUFFLE_AT:
            self.new_shoe()
            self.shuffle_pending = False
            self.sounds.play("shuffle")
            self.set_message("Shuffling...", "")
            delay = 1.1

        steps = [
            lambda: self.set_message("", ""),
            lambda: self.deal_card(self.hands[0].cards, True),
            lambda: self.deal_card(self.dealer, True),
            lambda: self.deal_card(self.hands[0].cards, True),
            lambda: self.deal_card(self.dealer, False),
            self.after_deal,
        ]
        for i, step in enumerate(steps):
            self.later(delay + i * 0.4, step)

    @staticmethod
    def cards_of(hand):
        return [cs.card for cs in hand]

    # ---- insurance and Blackjacks --------------------------------------
    def after_deal(self):
        cost = self.hands[0].bet / 2
        if self.dealer[0].card[0] == "A" and self.money >= cost:
            self.btn_ins_yes.set_text(f"Insurance {fmt_money(cost)} (I)")
            self.set_phase("insurance")
            self.set_message("Insurance?",
                             f"Dealer shows an Ace. Insurance costs {fmt_money(cost)} "
                             "and pays 2:1 if the dealer has Blackjack.")
        else:
            self.check_naturals()

    def take_insurance(self):
        self.answer_insurance(True)

    def decline_insurance(self):
        self.answer_insurance(False)

    def answer_insurance(self, take):
        if self.phase != "insurance":
            return
        if take:
            cost = self.hands[0].bet / 2
            self.money -= cost
            self.insurance = cost
            self.store.save()
        self.set_phase("busy")
        self.set_message("", "")
        self.check_naturals()

    def check_naturals(self):
        player_bj = is_blackjack(self.cards_of(self.hands[0].cards))
        dealer_bj = is_blackjack(self.cards_of(self.dealer))
        if player_bj or dealer_bj:
            self.set_phase("busy")
            self.reveal_hole()
            self.later(0.8, self.settle_round)
        else:
            note = "Insurance lost.   " if self.insurance else ""
            self.begin_player_turn(note)

    # ---- the player's turn ---------------------------------------------
    def begin_player_turn(self, note=""):
        self.active = 0
        self.turn_note = note
        self.set_phase("player")
        self.show_turn_message()

    def show_turn_message(self):
        count = len(self.hands)
        main = "Your move" if count == 1 else f"Hand {self.active + 1} of {count}"
        note, self.turn_note = self.turn_note, ""
        self.set_message(main, note + "Hit (H), Stand (S), Double (D) or Split (P)")

    def check_active(self):
        """Look at the active hand after it received a card."""
        hand = self.active_hand
        total = hand_value(self.cards_of(hand.cards))[0]
        if total > 21:
            hand.done = True
            self.after_hand(0.7)
        elif total == 21:
            hand.done = True
            self.after_hand(0.5)
        else:
            self.set_phase("player")
            self.show_turn_message()

    def after_hand(self, delay=0.4):
        """The active hand is finished: move on to the next hand or to the dealer."""
        self.set_phase("busy")
        self.set_message("", "")
        self.later(delay, self.next_hand)

    def next_hand(self):
        for i in range(self.active + 1, len(self.hands)):
            if not self.hands[i].done:
                self.active = i
                if len(self.hands[i].cards) < 2:     # a split hand waits for its 2nd card
                    self.deal_card(self.hands[i].cards, True)
                self.check_active()
                return
        self.dealer_phase()

    def action_hit(self):
        if self.phase != "player":
            return
        self.deal_card(self.active_hand.cards, True)
        self.check_active()

    def action_stand(self):
        if self.phase != "player":
            return
        self.active_hand.done = True
        self.after_hand(0.3)

    def action_double(self):
        if self.phase != "player":
            return
        hand = self.active_hand
        if len(hand.cards) != 2 or self.money < hand.bet:
            self.reject("You can only double on your first two cards, and only with enough money.")
            return
        self.money -= hand.bet
        hand.bet *= 2
        hand.doubled = True
        self.store.save()
        self.set_phase("busy")
        self.set_message("", "")
        self.deal_card(hand.cards, True)
        hand.done = True
        self.after_hand(0.8)

    def can_split(self):
        if self.phase != "player":
            return False
        hand = self.active_hand
        if len(hand.cards) != 2 or len(self.hands) >= MAX_HANDS or self.money < hand.bet:
            return False
        return card_points(hand.cards[0].card) == card_points(hand.cards[1].card)

    def action_split(self):
        if self.phase != "player":
            return
        if not self.can_split():
            self.reject(f"You can split two cards of the same value (up to {MAX_HANDS} hands), "
                        "if you have enough money.")
            return
        hand = self.active_hand
        self.money -= hand.bet
        self.store.save()
        other = Hand(hand.bet, from_split=True)
        hand.from_split = True
        other.cards.append(hand.cards.pop())
        self.hands.insert(self.active + 1, other)
        self.arrange()
        self.sounds.play("card_placement")
        self.set_phase("busy")
        self.set_message("", "")

        if other.cards[0].card[0] == "A":
            # Split aces get exactly one more card each, then they are finished.
            hand.done = other.done = True
            self.later(0.4, lambda: self.deal_card(hand.cards, True))
            self.later(0.8, lambda: self.deal_card(other.cards, True))
            self.later(1.3, lambda: self.after_hand(0.0))
        else:
            # The first hand gets its second card now; the other waits for its turn.
            def deal_first():
                self.deal_card(hand.cards, True)
                self.check_active()
            self.later(0.4, deal_first)

    # ---- the dealer's turn ---------------------------------------------
    def dealer_phase(self):
        live = False
        for hand in self.hands:
            cards = self.cards_of(hand.cards)
            natural = is_blackjack(cards) and not hand.from_split
            if hand_value(cards)[0] <= 21 and not natural:
                live = True
        if live:
            self.dealer_turn()
        else:
            self.settle_round()          # every hand busted: the dealer doesn't draw

    def dealer_turn(self):
        self.reveal_hole()
        self.later(0.7, self.dealer_step)

    def dealer_step(self):
        if hand_value(self.cards_of(self.dealer))[0] < 17:
            self.deal_card(self.dealer, True)
            self.later(0.7, self.dealer_step)
        else:
            self.settle_round()

    # ---- paying out ------------------------------------------------------
    def settle_round(self):
        self.reveal_hole(sound=False)
        stats = self.saved
        dealer_cards = self.cards_of(self.dealer)
        dealer_total = hand_value(dealer_cards)[0]
        dealer_bj = is_blackjack(dealer_cards)

        results = []                     # (outcome, reason) for each hand
        net = 0
        for hand in self.hands:
            cards = self.cards_of(hand.cards)
            total = hand_value(cards)[0]
            natural = is_blackjack(cards) and not hand.from_split
            if total > 21:
                outcome, reason = "lose", "bust"
            elif natural and dealer_bj:
                outcome, reason = "push", "both_bj"
            elif natural:
                outcome, reason = "blackjack", "blackjack"
            elif dealer_bj:
                outcome, reason = "lose", "dealer_bj"
            elif dealer_total > 21:
                outcome, reason = "win", "dealer_bust"
            elif total > dealer_total:
                outcome, reason = "win", "higher"
            elif total < dealer_total:
                outcome, reason = "lose", "lower"
            else:
                outcome, reason = "push", "same"

            factor = {"blackjack": 2.5, "win": 2, "push": 1, "lose": 0}[outcome]
            self.money += hand.bet * factor
            net += hand.bet * (factor - 1)
            hand.result = {"blackjack": "BLACKJACK", "win": "WIN",
                           "push": "PUSH", "lose": "LOSE"}[outcome]
            if outcome in ("win", "blackjack"):
                stats["won"] += 1
            if outcome == "blackjack":
                stats["blackjacks"] += 1
            elif outcome == "push":
                stats["pushed"] += 1
            elif outcome == "lose":
                stats["lost"] += 1
            results.append((outcome, reason))

        insurance_text = ""
        if self.insurance:
            if dealer_bj:
                self.money += self.insurance * 3
                net += self.insurance * 2
                insurance_text = f"Insurance paid {fmt_money(self.insurance * 2)}"
            else:
                net -= self.insurance
                insurance_text = "Insurance lost"

        sound = None
        if len(results) == 1 and not self.insurance:
            outcome, reason = results[0]
            bet = self.hands[0].bet
            prefix = {"bust": "Bust!", "dealer_bust": "Dealer busts!",
                      "dealer_bj": "Dealer has Blackjack.", "both_bj": "Both have Blackjack.",
                      "lower": "Dealer wins."}.get(reason, "")
            prefix = prefix + " " if prefix else ""
            if outcome == "blackjack":
                main = f"Blackjack! You win {fmt_money(bet * 1.5)}!"
                sound = "win"
            elif outcome == "win":
                main = f"{prefix}You win {fmt_money(bet)}!"
                sound = "win"
            elif outcome == "push":
                main = f"{prefix}Push - your bet is returned."
            else:
                main = f"{prefix}You lose {fmt_money(bet)}."
                sound = "lose"
            hint = "Adjust your bet or press Deal for another hand."
        else:
            if net > 0:
                main, sound = f"You win {fmt_money(net)}!", "win"
            elif net < 0:
                main, sound = f"You lose {fmt_money(-net)}.", "lose"
            else:
                main = "You break even."
            single = {"bust": "Bust", "blackjack": "Blackjack", "win": "Hand won",
                      "lose": "Hand lost", "push": "Hand pushed"}
            parts = []
            for i, (outcome, reason) in enumerate(results):
                word = "bust" if reason == "bust" else outcome
                parts.append(f"Hand {i + 1}: {word}" if len(results) > 1 else single[word])
            if insurance_text:
                parts.append(insurance_text)
            hint = "   ".join(parts)

        stats["in_round"] = False
        self.insurance = 0
        self.store.save()
        if sound:
            self.sounds.play(sound)

        self.bet = min(self.bet, int(self.money))
        if int(self.money) < 1:
            hint = self.broke_hint()
        self.set_message(main, hint)
        self.set_phase("bet")

    # ------------------------------------------------------------------
    # Top-panel buttons
    # ------------------------------------------------------------------
    def refresh_sound_button(self):
        self.btn_sound.set_text("Sound: On" if self.sounds.enabled else "Sound: Off")

    def toggle_sound(self):
        self.sounds.enabled = not self.sounds.enabled
        self.saved["sound"] = self.sounds.enabled
        self.refresh_sound_button()
        self.store.save()

    def cancel_confirm(self, token=None):
        if token is not None and token != self.confirm_token:
            return
        self.confirming = False
        self.btn_clear.set_text("New Player")
        self.btn_clear.color = BTN

    def clear_stats_clicked(self):
        if self.phase != "bet":
            self.reject("Finish the current hand first.")
            return
        if not self.confirming:
            self.confirming = True
            self.confirm_token += 1
            token = self.confirm_token
            self.btn_clear.set_text("Confirm?")
            self.btn_clear.color = BTN_RED
            self.later(3.0, lambda: self.cancel_confirm(token))
            return

        self.cancel_confirm()
        self.saved.update(money=START_MONEY, won=0, lost=0, pushed=0,
                          blackjacks=0, in_round=False)
        self.bet = 0
        self.clear_table()
        self.set_message("New player",
                         f"Stats cleared. You start with {fmt_money(START_MONEY)}. "
                         "Place your bet, then press Deal.")
        self.store.save()

    # ------------------------------------------------------------------
    # Card style buttons (next face set / next card back)
    # ------------------------------------------------------------------
    def refresh_style_buttons(self):
        loading = self._switch[0] if self._switch else None
        faces, backs = len(self.face_sets), len(self.back_list)
        self.btn_faces.set_text("Loading..." if loading == "faces"
                                else f"Faces: {self.face_index + 1} of {faces}")
        self.btn_back.set_text("Loading..." if loading == "back"
                               else f"Back: {self.back_index + 1} of {backs}")

    def next_faces(self):
        self.start_switch("faces")

    def next_back(self):
        self.start_switch("back")

    def start_switch(self, kind):
        if self._switch is not None:
            return                                   # already loading one
        if kind == "faces":
            items, index, word = self.face_sets, self.face_index, "card face sets"
        else:
            items, index, word = self.back_list, self.back_index, "card backs"
        if len(items) < 2:
            self.reject(f"No other {word} were found in the resources folder.")
            return
        index = (index + 1) % len(items)
        images = self.face_loader(index) if kind == "faces" else self.back_loader(index)
        self._switch = (kind, index, images, images.load_steps())
        self.refresh_style_buttons()
        pyglet.clock.schedule_interval(self.switch_tick, 1 / 60.0)

    def switch_tick(self, dt):
        """Load the new images a little each frame, so the game keeps running."""
        if getattr(self, "has_exit", False):
            pyglet.clock.unschedule(self.switch_tick)
            return
        kind, index, images, steps = self._switch
        deadline = time.perf_counter() + 0.012
        try:
            while time.perf_counter() < deadline:
                next(steps)
        except StopIteration:
            pass
        except Exception as exc:
            pyglet.clock.unschedule(self.switch_tick)
            self._switch = None
            self.refresh_style_buttons()
            name = (self.face_sets if kind == "faces" else self.back_list)[index][0]
            log(f"Could not load {name}: {exc}")
            self.reject(f"Could not load {name} - see the terminal for details.")
            return
        else:
            return                                   # more to load next frame
        pyglet.clock.unschedule(self.switch_tick)
        self._switch = None
        if kind == "faces":
            self.use_faces(index, images)
        else:
            self.use_back(index, images)
        self.store.save()
        self.do_layout()                             # re-applies every card image
        self.refresh_style_buttons()
        self.sounds.play("card_flip")

    def set_message(self, main, hint=""):
        self.msg_main = main
        self.msg_hint = hint

    # ------------------------------------------------------------------
    # Per-frame updates and drawing
    # ------------------------------------------------------------------
    def tick(self, dt):
        for cs in self.all_cards():
            cs.step(dt)

    def hand_text(self, name, hand, hide_hole):
        visible = [cs.card for cs in hand if cs.face_up] if hide_hole else self.cards_of(hand)
        if not visible:
            return ""
        total, soft = hand_value(visible)
        if total > 21:
            return f"{name}: Bust ({total})"
        if not hide_hole and is_blackjack(visible):
            return f"{name}: Blackjack!"
        prefix = "Soft " if soft and len(visible) >= 2 else ""
        return f"{name}: {prefix}{total}"

    def player_hand_text(self, index, count):
        hand = self.hands[index]
        cards = self.cards_of(hand.cards)
        if not cards:
            return ""
        total, soft = hand_value(cards)
        if count == 1:
            if total > 21:
                text = f"Player: Bust ({total})"
            elif is_blackjack(cards) and not hand.from_split:
                text = "Player: Blackjack!"
            else:
                text = f"Player: {'Soft ' if soft and len(cards) >= 2 else ''}{total}"
            if hand.doubled:
                text += "   (Doubled down)"
            return text
        if total > 21:
            desc = f"Bust {total}"
        else:
            desc = f"{'Soft ' if soft and len(cards) >= 2 else ''}{total}"
        text = f"{index + 1}) {desc}  ({fmt_money(hand.bet)}{' DD' if hand.doubled else ''})"
        if hand.result:
            text += f"  {hand.result}"
        return text

    def place_hand_labels(self):
        count = max(1, len(self.hands))
        for i, label in enumerate(self.lbl_hands):
            cx, _ = self.hand_slot(i, count)
            label.x = cx
            label.y = self.bottom_h + 8
            size = 16 if count == 1 else 13
            if label.font_size != size:
                label.font_size = size

    def sync_labels(self):
        s = self.saved
        played = s["won"] + s["lost"] + s["pushed"]
        rate = f"{100 * s['won'] / played:.0f}%" if played else "-"
        set_text(self.lbl_money, f"Money: {fmt_money(self.money)}")
        set_text(self.lbl_stats1, f"Won: {s['won']}    Lost: {s['lost']}    Push: {s['pushed']}")
        set_text(self.lbl_stats2, f"Blackjacks: {s['blackjacks']}    Win rate: {rate}")

        in_play = self.phase != "bet"
        amount = sum(h.bet for h in self.hands) + self.insurance if in_play else self.bet
        bet_text = f"Bet: {fmt_money(amount)}"
        if in_play and self.insurance:
            bet_text += f"   (includes {fmt_money(self.insurance)} insurance)"
        doubled_now = in_play and any(h.doubled for h in self.hands)
        if doubled_now and len(self.hands) == 1:
            bet_text += "   -   DOUBLED DOWN"
        set_text(self.lbl_bet, bet_text)
        bet_color = GOLD if doubled_now else WHITE
        if bet_color != self._bet_color:
            self.lbl_bet.color = bet_color
            self._bet_color = bet_color

        set_text(self.lbl_dealer, self.hand_text("Dealer", self.dealer, self.hole_hidden()))
        count = len(self.hands)
        if (count, self.width) != self._hands_key:
            self._hands_key = (count, self.width)
            self.place_hand_labels()
        for i, label in enumerate(self.lbl_hands):
            set_text(label, self.player_hand_text(i, count) if i < count else "")
        set_text(self.lbl_shoe, f"Shoe: {len(self.shoe)} cards")

    def sync_buttons(self):
        limit = int(self.money)
        for button in self.chip_buttons:
            button.enabled = self.bet < limit
        self.btn_allin.enabled = limit >= 1 and self.bet < limit
        self.btn_clearbet.enabled = self.bet > 0
        self.btn_deal.enabled = self.bet >= 1
        loading = self._switch is not None
        self.btn_faces.enabled = not loading and len(self.face_sets) > 1
        self.btn_back.enabled = not loading and len(self.back_list) > 1
        if self.phase == "player" and self.hands:
            hand = self.active_hand
            self.btn_double.enabled = len(hand.cards) == 2 and self.money >= hand.bet
            self.btn_split.enabled = self.can_split()

    def sync_active_bar(self):
        """A gold line under the hand being played, when there are several."""
        show = self.phase == "player" and len(self.hands) > 1
        key_ = (show, self.active, len(self.active_hand.cards) if show else 0,
                self.width, self.height)
        if key_ == self._bar_key:
            return
        self._bar_key = key_
        safe_delete(self.active_bar)
        self.active_bar = None
        if not show:
            return
        cx, avail = self.hand_slot(self.active, len(self.hands))
        slots = self.fan(len(self.active_hand.cards), self.player_y, cx, avail)
        if slots:
            x0, x1 = slots[0][0], slots[-1][0] + self.card_w
            self.active_bar = shapes.Rectangle(x0, self.player_y - 9, x1 - x0, 4, color=GOLD)

    def sync_message(self):
        key_ = (self.msg_main, self.msg_hint, self.width, self.height)
        if key_ == self._msg_key:
            return
        self._msg_key = key_
        main, hint = self.msg_main, self.msg_hint
        set_text(self.lbl_msg, main)
        set_text(self.lbl_hint, hint)
        safe_delete(self.msg_bg)
        self.msg_bg = None
        cy = self.mid_y
        if main and hint:
            self.lbl_msg.y = cy + 14
            self.lbl_hint.y = cy - 18
            height = 72
        elif main:
            self.lbl_msg.y = cy
            height = 48
        elif hint:
            self.lbl_hint.y = cy
            height = 40
        else:
            return
        width = max(self.lbl_msg.content_width if main else 0,
                    self.lbl_hint.content_width if hint else 0) + 60
        self.msg_bg = shapes.Rectangle(self.width / 2 - width / 2, cy - height / 2,
                                       width, height, color=(0, 0, 0, 120))

    def draw_loading(self):
        self.clear()
        safe_delete(self._loading_bg)
        self._loading_bg = shapes.Rectangle(0, 0, self.width, self.height, color=FELT)
        self._loading_bg.draw()
        self._loading_label.x = self.width / 2
        self._loading_label.y = self.height / 2
        self._loading_label.draw()

        bar_w, bar_h = 260, 8
        x, y = self.width / 2 - bar_w / 2, self.height / 2 - 38
        for obj in self._loading_bar:
            safe_delete(obj)
        track = shapes.Rectangle(x, y, bar_w, bar_h, color=PANEL)
        fill = shapes.Rectangle(x, y, max(1.0, bar_w * self._load_progress), bar_h, color=GOLD)
        self._loading_bar = [track, fill]
        track.draw()
        fill.draw()

        if not self._load_scheduled:
            self._load_scheduled = True
            pyglet.clock.schedule_interval(self.load_tick, 1 / 60.0)

    def on_draw(self):
        if not self._ready:
            self.draw_loading()
            return
        self.clear()
        self.sync_labels()
        self.sync_buttons()
        self.sync_active_bar()
        self.sync_message()

        if self.bg_image is not None:
            self.bg_image.draw()                 # just the picture: no felt, no panels
        else:
            self.bg_felt.draw()
            self.bg_top.draw()
            self.bg_bottom.draw()
        self.line_top.draw()
        self.line_bottom.draw()

        self.lbl_money.draw()
        self.lbl_stats1.draw()
        self.lbl_stats2.draw()

        if not self.dealer and not self.hands:
            for sprite in self.empty_sprites:
                sprite.draw()
        for sprite in self.shoe_sprites:
            sprite.draw()
        self.lbl_shoe.draw()
        for cs in self.dealer:
            cs.draw()
        for hand in self.hands:
            for cs in hand.cards:
                cs.draw()
        if self.active_bar is not None:
            self.active_bar.draw()
        self.lbl_dealer.draw()
        for label in self.lbl_hands:
            if label.text:
                label.draw()

        if self.msg_bg is not None:
            self.msg_bg.draw()
            if self.msg_main:
                self.lbl_msg.draw()
            if self.msg_hint:
                self.lbl_hint.draw()

        self.lbl_bet.draw()
        for button in self.fixed_buttons + self.active_buttons():
            button.draw()

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------
    def on_mouse_motion(self, x, y, dx, dy):
        if not self._ready:
            return
        for button in self.all_buttons:
            button.hover = button.contains(x, y)

    def on_mouse_leave(self, x, y):
        if not self._ready:
            return
        for button in self.all_buttons:
            button.hover = False

    def on_mouse_press(self, x, y, button, modifiers):
        if not self._ready or button != mouse.LEFT:
            return
        for b in self.fixed_buttons + self.active_buttons():
            if b.contains(x, y):
                b.callback()
                return

    def on_key_press(self, symbol, modifiers):
        # (Escape deliberately does nothing, so a stray key press can't quit mid-hand.)
        if not self._ready:
            return
        if symbol == key.H:
            self.action_hit()
        elif symbol == key.S:
            self.action_stand()
        elif symbol == key.D:
            self.action_double()
        elif symbol == key.P:
            self.action_split()
        elif symbol == key.I:
            self.take_insurance()
        elif symbol == key.N:
            self.decline_insurance()
        elif symbol in (key.RETURN, key.ENTER, key.SPACE):
            self.start_round()


# --------------------------------------------------------------------------
def main():
    store = Store()
    window = BlackjackWindow(store)
    pyglet.app.run(interval=1 / 60.0)
    if window.load_error:
        sys.exit(window.load_error)


if __name__ == "__main__":
    main()
