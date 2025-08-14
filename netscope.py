#!/usr/bin/env python3
# NetScope v2.0.11

import argparse
import curses
import importlib.util
import ipaddress
import json
import os
import platform
import socket
import sys
import time
from pathlib import Path
import atexit

import psutil

VERSION = "2.0.11"

# -----------------------------------------------------------------------------
# Performance knobs
# -----------------------------------------------------------------------------
IDLE_MS = 80               # baseline input wait (keeps CPU low)
SCROLL_BOOST_MS = 250      # after a scroll key, use a lower timeout for this long
SCROLL_MS = 15             # boosted timeout during scroll (snappy keys)
PROC_SORT_DEFAULT = "cpu"  # default sort for processes
CONN_KIND = os.environ.get("NETSCOPE_CONN_KIND", "tcp")  # tcp|tcp4|tcp6

# -----------------------------------------------------------------------------
# Theming (default "blue"; external themes in /etc/netscope/themes/*.json)
# Pairs: 1 text, 2 title/primary, 4 border/header, 5-8 accents, 9 muted
# -----------------------------------------------------------------------------

THEME_DIR = "/etc/netscope/themes"

def _osc_set_default_bg(hex_color: str):
    # OSC 11: set terminal default background (truecolor)
    try:
        h = hex_color.lstrip("#")
        if len(h) == 3:
            h = "".join(c*2 for c in h)
        elif len(h) == 8:
            h = h[:6]
        seq = f"\x1b]11;#{h}\x07"
        os.write(sys.stdout.fileno(), seq.encode("ascii", "ignore"))
    except Exception:
        pass

def _osc_reset_default_bg():
    # OSC 111: reset terminal default background
    try:
        os.write(sys.stdout.fileno(), b"\x1b]111\x07")
    except Exception:
        pass

class ThemeManager:
    def __init__(self):
        self.current = "blue"
        self._ids = {"bg":16,"fg":17,"accent":18,"accent2":19,"accent3":20,"accent4":21,"accent5":22,"muted":23}
        atexit.register(self._on_exit_reset_bg)

    @staticmethod
    def _hex_norm(h: str) -> str:
        h = h.lstrip("#").strip()
        if len(h) == 3:
            h = "".join(c*2 for c in h)
        if len(h) == 8:
            h = h[:6]
        return h.lower()

    @staticmethod
    def _hex_to_rgb(hex_color: str):
        h = ThemeManager._hex_norm(hex_color)
        return int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)

    @staticmethod
    def _hex_to_1000(hex_color: str):
        r,g,b = ThemeManager._hex_to_rgb(hex_color)
        return r*1000//255, g*1000//255, b*1000//255

    @staticmethod
    def _nearest_xterm256(hex_color: str) -> int:
        # Map hex to closest 256-color index (cube vs grayscale)
        r,g,b = ThemeManager._hex_to_rgb(hex_color)
        levels = [0,95,135,175,215,255]
        def quant(v): return min(range(6), key=lambda i: abs(levels[i]-v))
        ri, gi, bi = quant(r), quant(g), quant(b)
        cube_idx = 16 + 36*ri + 6*gi + bi
        cube_rgb = (levels[ri], levels[gi], levels[bi])
        gray_index = 232 + max(0, min(23, int(round(((r+g+b)//3 - 8)/10))))
        gray_val = 8 + (gray_index-232)*10
        gray_rgb = (gray_val, gray_val, gray_val)
        def dist(a,b,c, x,y,z): return (a-x)**2 + (b-y)**2 + (c-z)**2
        return cube_idx if dist(r,g,b,*cube_rgb) <= dist(r,g,b,*gray_rgb) else gray_index

    @staticmethod
    def _supports_truecolor() -> bool:
        # Respect NETSCOPE_TRUECOLOR=1/0 for forcing behavior
        force = os.environ.get("NETSCOPE_TRUECOLOR")
        if force == "1": return True
        if force == "0": return False
        ct = (os.environ.get("COLORTERM") or "").lower()
        term = (os.environ.get("TERM") or "").lower()
        if "truecolor" in ct or "24bit" in ct: return True
        if term.endswith("-direct"): return True
        # tmux path: if TERM has -256color inside tmux, OSC 11 still works
        if os.environ.get("TMUX") and ("-256color" in term or term.endswith("-direct")):
            return True
        return False

    def available(self):
        names = ["blue"]
        try:
            for p in sorted(Path(THEME_DIR).glob("*.json")):
                names.append(p.stem)
        except Exception:
            pass
        return names

    def _load_theme_json(self, name):
        path = Path(THEME_DIR) / f"{name}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return {str(k).lower(): v for k, v in data.items()}
        except Exception:
            return None

    def _extract_palette(self, t):
        palette = [None]*6
        if isinstance(t.get("palette"), list):
            p = [c for c in t["palette"] if isinstance(c, str)]
            for i in range(min(6, len(p))):
                palette[i] = p[i]
        accents = {
            "bg": t.get("bg"),
            "fg": t.get("fg") or (palette[5] if palette[5] else None),
            "accent": t.get("accent") or (palette[0] if palette[0] else None),
            "accent2": t.get("accent2") or (palette[1] if palette[1] else None),
            "accent3": t.get("accent3") or (palette[2] if palette[2] else None),
            "accent4": t.get("accent4") or (palette[3] if palette[3] else None),
            "accent5": t.get("accent5") or (palette[4] if palette[4] else None),
            "muted": t.get("muted") or (palette[3] if palette[3] else None),
        }
        defd = {"bg":"#001b4d","fg":"#eaeaea","accent":"#ffd75f","accent2":"#ff5f5f","accent3":"#5fff87","accent4":"#5f87ff","accent5":"#af87ff","muted":"#87d7ff"}
        for k,v in accents.items():
            accents[k] = v if (isinstance(v,str) and v.startswith("#")) else defd[k]
        return accents

    def _on_exit_reset_bg(self):
        _osc_reset_default_bg()

    def apply(self, stdscr, name=None):
        """
        - 'blue' theme: classic white-on-blue background via curses.
        - custom theme:
            * if truecolor: set OSC 11 bg to exact hex, use -1 bg in pairs
            * else: try init_color; if not possible, approximate with xterm-256
        """
        curses.start_color()
        curses.use_default_colors()
        if name:
            self.current = name
        name = self.current

        def set_pairs_fg_bg(fg, bg):
            # Initialize pairs 1..9 with given fg/bg (ints or -1)
            for pid, fgcol, bgcol in (
                (1, fg["fg"], bg),
                (2, fg["accent"], bg),
                (4, fg["accent"], bg),
                (5, fg["accent2"], bg),
                (6, fg["accent3"], bg),
                (7, fg["accent4"], bg),
                (8, fg["accent5"], bg),
                (9, fg["muted"],  bg),
            ):
                curses.init_pair(pid, fgcol, bgcol)

        if name == "blue":
            _osc_reset_default_bg()
            for pid in (1,2,4,5,6,7,8,9):
                curses.init_pair(pid, curses.COLOR_WHITE, curses.COLOR_BLUE)
        else:
            t = self._load_theme_json(name)
            if not t:
                # Fallback to blue if theme file missing
                for pid in (1,2,4,5,6,7,8,9):
                    curses.init_pair(pid, curses.COLOR_WHITE, curses.COLOR_BLUE)
            else:
                p = self._extract_palette(t)
                if self._supports_truecolor():
                    # Set terminal default background to theme hex
                    _osc_set_default_bg(p["bg"])
                    # Foreground/accent colors: try true custom; else xterm256 approximate; bg=-1 (default)
                    can_custom = curses.can_change_color() and (getattr(curses, "COLORS", 0) >= 24)
                    if can_custom:
                        for key in ("fg","accent","accent2","accent3","accent4","accent5","muted"):
                            curses.init_color(self._ids[key], *self._hex_to_1000(p[key]))
                        set_pairs_fg_bg(
                            {"fg":self._ids["fg"],"accent":self._ids["accent"],"accent2":self._ids["accent2"],
                             "accent3":self._ids["accent3"],"accent4":self._ids["accent4"],"accent5":self._ids["accent5"],
                             "muted":self._ids["muted"]},
                            -1
                        )
                    else:
                        # Approximate with xterm-256 for fg/accent, bg uses terminal default (-1) which is OSC 11 truecolor
                        fg = {
                            "fg":     self._nearest_xterm256(p["fg"]),
                            "accent": self._nearest_xterm256(p["accent"]),
                            "accent2":self._nearest_xterm256(p["accent2"]),
                            "accent3":self._nearest_xterm256(p["accent3"]),
                            "accent4":self._nearest_xterm256(p["accent4"]),
                            "accent5":self._nearest_xterm256(p["accent5"]),
                            "muted":  self._nearest_xterm256(p["muted"]),
                        }
                        set_pairs_fg_bg(fg, -1)
                else:
                    # No truecolor: try custom RGB palette; else fall back to nearest 256 bg
                    can_custom = curses.can_change_color() and (getattr(curses, "COLORS", 0) >= 24)
                    if can_custom:
                        for key in ("bg","fg","accent","accent2","accent3","accent4","accent5","muted"):
                            curses.init_color(self._ids[key], *self._hex_to_1000(p[key]))
                        set_pairs_fg_bg(
                            {"fg":self._ids["fg"],"accent":self._ids["accent"],"accent2":self._ids["accent2"],
                             "accent3":self._ids["accent3"],"accent4":self._ids["accent4"],"accent5":self._ids["accent5"],
                             "muted":self._ids["muted"]},
                            self._ids["bg"]
                        )
                    elif getattr(curses, "COLORS", 0) >= 256:
                        bg = self._nearest_xterm256(p["bg"])
                        fg = {
                            "fg":     self._nearest_xterm256(p["fg"]),
                            "accent": self._nearest_xterm256(p["accent"]),
                            "accent2":self._nearest_xterm256(p["accent2"]),
                            "accent3":self._nearest_xterm256(p["accent3"]),
                            "accent4":self._nearest_xterm256(p["accent4"]),
                            "accent5":self._nearest_xterm256(p["accent5"]),
                            "muted":  self._nearest_xterm256(p["muted"]),
                        }
                        set_pairs_fg_bg(fg, bg)
                    else:
                        # Very limited terminal: keep classic blue to ensure readability
                        for pid in (1,2,4,5,6,7,8,9):
                            curses.init_pair(pid, curses.COLOR_WHITE, curses.COLOR_BLUE)

        if stdscr is not None:
            stdscr.bkgd(curses.color_pair(1))
            stdscr.refresh()
        return self.current

THEME = ThemeManager()

# -----------------------------------------------------------------------------
# Splash banner (NetScope wordmark only)
# -----------------------------------------------------------------------------

NETSCOPE_SPLASH_ASCII = [
    "ooooo      ooo               .    .oooooo..o                                          ",
    "`888b.     `8'             .o8   d8P'    `Y8                                          ",
    " 8 `88b.    8   .ooooo.  .o888oo Y88bo.       .ooooo.   .ooooo.  oo.ooooo.   .ooooo.  ",
    " 8   `88b.  8  d88' `88b   888    `\"Y8888o.  d88' `\"Y8 d88' `88b  888' `88b d88' `88b ",
    " 8     `88b.8  888ooo888   888        `\"Y88b 888       888   888  888   888 888ooo888 ",
    " 8       `888  888    .o   888 . oo     .d8P 888   .o8 888   888  888   888 888    .o ",
    "o8o        `8  `Y8bod8P'   \"888\" 8\"\"88888P'  `Y8bod8P' `Y8bod8P'  888bod8P' `Y8bod8P' ",
    "                                                                  888                 ",
    "                                                                 o888o                "
]

# -----------------------------------------------------------------------------
# ASCII art for System Info (from /etc/netscope/ascii_art.py)
# -----------------------------------------------------------------------------

def load_ascii_art_dict():
    path = "/etc/netscope/ascii_art.py"
    try:
        spec = importlib.util.spec_from_file_location("netscope_ascii", path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[attr-defined]
            art = getattr(mod, "ascii_art_dict", {})
            if isinstance(art, dict):
                return {str(k).lower(): v for k, v in art.items()}
    except Exception:
        pass
    return {}

ASCII_ART_DICT = load_ascii_art_dict()

def detect_distro_key():
    osr = {}
    try:
        with open("/etc/os-release","r",encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k,v = line.strip().split("=",1)
                    osr[k] = v.strip().strip('"')
    except Exception:
        pass
    cands = []
    if osr.get("ID"): cands.append(osr["ID"].lower())
    if osr.get("ID_LIKE"): cands.extend([p.strip().lower() for p in osr["ID_LIKE"].split()])
    if osr.get("NAME"):
        name = osr["NAME"].lower()
        for key in ASCII_ART_DICT.keys():
            if key in name: cands.append(key)
    for c in cands:
        if c in ASCII_ART_DICT: return c
    for alias in ("debian","ubuntu","arch","fedora","centos","alpine"):
        if alias in ASCII_ART_DICT: return alias
    if ASCII_ART_DICT:
        return next(iter(ASCII_ART_DICT.keys()))
    return None

# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------

def format_bytes(size):
    if size is None: return "N/A"
    if size < 1024: return f"{size} B"
    if size < 1024**2: return f"{size//1024} KB"
    if size < 1024**3: return f"{size//(1024**2)} MB"
    return f"{size//(1024**3)} GB"

def compress_ipv6(addr):
    try:
        ip = ipaddress.ip_address(addr)
        return ip.compressed if ip.version == 6 else addr
    except Exception:
        return addr

def _pad(text, width, align='left'):
    s = "" if text is None else str(text)
    if len(s) > width: return s[:max(0,width)]
    return s.rjust(width) if align=='right' else s.ljust(width)

# -----------------------------------------------------------------------------
# Per-tick caches (speed + lower CPU)
# -----------------------------------------------------------------------------

_proc_meta_cache = {}
_proc_meta_tick = -1
def _proc_meta(pid, tick):
    global _proc_meta_cache, _proc_meta_tick
    if _proc_meta_tick != tick:
        _proc_meta_cache = {}
        _proc_meta_tick = tick
    if pid in _proc_meta_cache:
        return _proc_meta_cache[pid]
    name = "N/A"; user = "N/A"
    try:
        p = psutil.Process(pid)
        name = p.name()
        user = p.username()
    except Exception:
        pass
    _proc_meta_cache[pid] = (name, user)
    return _proc_meta_cache[pid]

_pid_io_cache = {}
_pid_io_tick = -1
def _proc_io(pid, tick):
    global _pid_io_cache, _pid_io_tick
    if _pid_io_tick != tick:
        _pid_io_cache = {}
        _pid_io_tick = tick
    if pid in _pid_io_cache:
        return _pid_io_cache[pid]
    class D: read_bytes = 0; write_bytes = 0
    try:
        io = psutil.Process(pid).io_counters()
    except Exception:
        io = D()
    _pid_io_cache[pid] = io
    return io

# -----------------------------------------------------------------------------
# Connection snapshot (lean; fill details lazily)
# Each row: (laddr_str, raddr_str, status_str, pid_int)
# -----------------------------------------------------------------------------

_conn_tick = -1
_conn_snapshot = {"EST": [], "LIS": []}

def _snapshot_connections(tick):
    global _conn_tick, _conn_snapshot
    if _conn_tick == tick:
        return _conn_snapshot
    _conn_tick = tick
    est = []; lis = []
    try:
        for c in psutil.net_connections(kind=CONN_KIND):
            status = c.status
            if status not in (psutil.CONN_ESTABLISHED, psutil.CONN_LISTEN):
                continue
            laddr = f"{compress_ipv6(c.laddr.ip)}:{c.laddr.port}" if c.laddr else "N/A"
            raddr = f"{compress_ipv6(c.raddr.ip)}:{c.raddr.port}" if c.raddr else "N/A"
            pid = int(c.pid) if c.pid else 0
            row = (laddr, raddr, "ESTABLISHED" if status == psutil.CONN_ESTABLISHED else "LISTEN", pid)
            if status == psutil.CONN_ESTABLISHED":
                est.append(row)
            else:
                lis.append(row)
    except Exception:
        pass
    _conn_snapshot = {"EST": est, "LIS": lis}
    return _conn_snapshot

def list_connections(kind_status, tick):
    snap = _snapshot_connections(tick)
    return snap["EST"] if kind_status == "ESTABLISHED" else snap["LIS"]

def _format_conn_row(base_row, tick):
    laddr, raddr, status, pid = base_row
    if pid:
        name, user = _proc_meta(pid, tick)
        io = _proc_io(pid, tick)
        sent = format_bytes(io.write_bytes)
        recv = format_bytes(io.read_bytes)
    else:
        name = user = "N/A"
        sent = recv = "N/A"
    return [laddr, raddr, status, str(pid) if pid else "N/A", name, user, sent, recv]

# -----------------------------------------------------------------------------
# Data providers (processes/system info)
# -----------------------------------------------------------------------------

def get_system_info_lines():
    lines = []
    u = platform.uname()
    lines += [
        f"System: {u.system}",
        f"Node Name: {u.node}",
        f"Release: {u.release}",
        f"Version: {u.version}",
        f"Machine: {u.machine}",
        f"CPU Cores: {psutil.cpu_count(logical=False)}",
        f"CPU Threads: {psutil.cpu_count(logical=True)}",
    ]
    try:
        freq = psutil.cpu_freq()
        if freq: lines.append(f"CPU Frequency: {freq.current:.2f} MHz")
    except Exception: pass
    mem = psutil.virtual_memory()
    lines.append(f"Total Memory: {format_bytes(mem.total)}")
    try:
        d = psutil.disk_usage("/")
        lines.append(f"Disk Usage: {d.percent}% of {format_bytes(d.total)}")
    except Exception: pass
    try:
        ifs = psutil.net_if_addrs()
        if ifs: lines.append(f"Network Interfaces: {', '.join(ifs.keys())}")
    except Exception: pass
    return lines

def process_table():
    rows = []
    for proc in psutil.process_iter(["pid","username","nice","memory_info","status","cpu_percent","memory_percent","name","create_time"]):
        try:
            p = proc.info
            up = time.time() - p["create_time"]
            m, s = divmod(int(up), 60); h, m = divmod(m, 60)
            tstr = f"{h:02}:{m:02}:{s:02}"
            rows.append([
                str(p["pid"]),
                p.get("username","N/A"),
                str(p.get("nice","N/A")),
                format_bytes(p["memory_info"].vms),
                format_bytes(p["memory_info"].rss),
                format_bytes(getattr(p["memory_info"], "shared", 0)) if hasattr(p["memory_info"], "shared") else "N/A",
                p.get("status","N/A"),
                f'{p.get("cpu_percent",0):.1f}',
                f'{p.get("memory_percent",0):.1f}',
                tstr,
                p.get("name","N/A"),
            ])
        except Exception:
            continue
    return rows

# -----------------------------------------------------------------------------
# UI helpers
# -----------------------------------------------------------------------------

MIN_WIDTH = 120

def border_title(win, title):
    win.attron(curses.color_pair(4))
    win.border(0)
    win.attroff(curses.color_pair(4))
    try:
        win.addstr(0, 2, title, curses.color_pair(2) | curses.A_BOLD)
    except curses.error:
        pass

def draw_hline(win, y, x, width):
    try:
        win.hline(y, x, curses.ACS_HLINE, max(1, width))
    except curses.error:
        pass

def draw_table_header(win, y, x, cols, col_colors, sep=" "):
    cx = x
    for i, (hdr, w, _align) in enumerate(cols):
        color = curses.color_pair(col_colors[i % len(col_colors)]) | curses.A_BOLD
        try: win.addstr(y, cx, _pad(hdr, w), color)
        except curses.error: pass
        cx += w
        if i < len(cols)-1:
            try: win.addstr(y, cx, sep, curses.color_pair(9))
            except curses.error: pass
            cx += len(sep)

def draw_table_row(win, y, x, values, cols, col_colors, sep=" ", selected=False):
    cx = x
    for i, (val, (hdr, w, align)) in enumerate(zip(values, cols)):
        color = curses.color_pair(col_colors[i % len(col_colors)])
        if selected: color |= curses.A_REVERSE
        try: win.addstr(y, cx, _pad(val, w, align), color)
        except curses.error: pass
        cx += w
        if i < len(cols)-1:
            try: win.addstr(y, cx, sep, curses.color_pair(9))
            except curses.error: pass
            cx += len(sep)

# Column specs (fixed widths + single-space separators)
CONN_COLS = [
    ("Local Address", 25, 'left'),
    ("Remote Address", 25, 'left'),
    ("Status",        10, 'left'),
    ("PID",            6,  'right'),
    ("Program",       18,  'left'),
    ("User",          16,  'left'),
    ("Sent",          10,  'right'),
    ("Recv",          10,  'right'),
]
PROC_COLS = [
    ("PID",     6,  'right'),
    ("USER",    12, 'left'),
    ("NI",      3,  'right'),
    ("VIRT",    10, 'right'),
    ("RES",     10, 'right'),
    ("SHR",     10, 'right'),
    ("STATUS",  10, 'left'),
    ("CPU%",    5,  'right'),
    ("MEM%",    5,  'right'),
    ("TIME+",   8,  'right'),
    ("Command", 30, 'left'),
]

# With default "blue", pairs 2..9 are also white/blue, so the menu and tables are monochrome.
CONN_COLORS = [2,5,6,7,8,9,5,6]
PROC_COLORS = [2,5,6,7,8,9,5,6,7,8,9]

# Small helper to flip timeout for smooth scrolling without high CPU
def _boost_timeout(stdscr, boosting, boost_until):
    now = time.time()
    if boosting and now >= boost_until:
        stdscr.timeout(IDLE_MS)
        return False, 0.0
    return boosting, boost_until

# -----------------------------------------------------------------------------
# Screens
# -----------------------------------------------------------------------------

# Splash (menu colors come from active theme; monochrome on "blue")
def screen_splash(stdscr):
    stdscr.clear()
    THEME.apply(stdscr)
    stdscr.timeout(IDLE_MS)
    h, w = stdscr.getmaxyx()
    while w < MIN_WIDTH:
        stdscr.clear()
        try:
            stdscr.addstr(0, 0, f"Please resize your window to at least {MIN_WIDTH} columns.", curses.color_pair(1) | curses.A_BOLD)
        except curses.error:
            pass
        stdscr.refresh(); time.sleep(0.25)
        h, w = stdscr.getmaxyx()

    title = "NetScope"
    menu = ["1. System Info", "2. Established Connections", "3. Listening Connections", "4. Both", "5. Running Processes", "6. Exit"]
    sel = 0
    menu_colors = [2,5,6,7,8,9]

    def draw_menu():
        stdscr.erase()
        stdscr.bkgd(curses.color_pair(1))
        border_title(stdscr, f"{title} {VERSION}")

        # Draw the splash ASCII AFTER the background so it persists
        h, w = stdscr.getmaxyx()
        logo_top = max(2, h//6)
        ascii_attr = curses.color_pair(7) | curses.A_BOLD
        for i, line in enumerate(NETSCOPE_SPLASH_ASCII):
            x = max(0, (w - len(line)) // 2)
            try:
                stdscr.addstr(logo_top + i, x, line, ascii_attr)
            except curses.error:
                pass

        # Menu below the splash
        menu_top = logo_top + len(NETSCOPE_SPLASH_ASCII) + 2
        for idx, item in enumerate(menu):
            x = max(0, (w - len(item)) // 2)
            base = curses.color_pair(menu_colors[idx % len(menu_colors)])
            attr = (base | curses.A_BOLD | curses.A_REVERSE) if idx == sel else (base | curses.A_BOLD)
            try:
                stdscr.addstr(menu_top + idx, x, item, attr)
            except curses.error:
                pass
        stdscr.refresh()

    draw_menu()
    while True:
        ch = stdscr.getch()
        if ch == -1: continue
        if ch == ord('t'):
            theme_dialog(stdscr); stdscr.clear(); THEME.apply(stdscr); draw_menu(); continue
        if ch in (curses.KEY_UP, ord('k')): sel = (sel - 1) % len(menu); draw_menu()
        elif ch in (curses.KEY_DOWN, ord('j')): sel = (sel + 1) % len(menu); draw_menu()
        elif ch in (10, 13, curses.KEY_ENTER): return sel + 1
        elif ch in (ord('1'), ord('2'), ord('3'), ord('4'), ord('5'), ord('6')): return int(chr(ch))
        elif ch in (ord('q'), 27): return 6

# System Info
def screen_system_info(stdscr, interval):
    curses.curs_set(0)
    stdscr.timeout(IDLE_MS)

    distro_key = detect_distro_key()
    distro_art = ASCII_ART_DICT.get(distro_key, [])

    last_tick = 0
    while True:
        now = time.time()
        if now - last_tick >= interval:
            last_tick = now
            _render_system_info(stdscr, distro_art)
        ch = stdscr.getch()
        if ch == -1: continue
        if ch in (curses.KEY_BACKSPACE, curses.KEY_LEFT, 127): return
        if ch in (ord('q'), 27): raise SystemExit
        if ch == ord('t'): theme_dialog(stdscr); last_tick = 0

def _render_system_info(stdscr, distro_art):
    stdscr.erase()
    stdscr.bkgd(curses.color_pair(1))
    border_title(stdscr, "System Info (Backspace/Left = Back, t = Theme, q = Quit)")
    h, w = stdscr.getmaxyx()
    info = get_system_info_lines()

    block = []
    if distro_art:
        block.extend(distro_art); block.append("")
    block.extend(info)

    max_w = max((len(line) for line in block), default=0)
    start_y = max(1, (h - len(block)) // 2)
    start_x = max(1, (w - max_w) // 2)

    y = start_y
    for line in block:
        if line == "":
            y += 1; continue
        color = curses.color_pair(2) | curses.A_BOLD if (distro_art and line in distro_art) else curses.color_pair(1)
        try: stdscr.addstr(y, start_x, line[:max(1, w - start_x - 1)], color)
        except curses.error: pass
        y += 1
    stdscr.refresh()

# Connections (Established/Listening) — immediate fetch + atomic render (no flicker)
def screen_connections(stdscr, interval, mode):
    stdscr.timeout(IDLE_MS)
    start_idx = 0
    boosting = False
    boost_until = 0.0

    # Immediate fetch & paint
    tick = (_conn_tick + 1) if _conn_tick >= 0 else 1
    rows = list_connections("ESTABLISHED" if mode == "Established" else "LISTEN", tick)
    _render_connections(stdscr, rows, start_idx, mode, tick)
    last = time.time()

    while True:
        boosting, boost_until = _boost_timeout(stdscr, boosting, boost_until)
        now = time.time()
        if now - last >= interval:
            last = now; tick += 1
            rows = list_connections("ESTABLISHED" if mode == "Established" else "LISTEN", tick)
            _render_connections(stdscr, rows, start_idx, mode, tick)

        ch = stdscr.getch()
        if ch == -1: continue
        if ch in (curses.KEY_BACKSPACE, curses.KEY_LEFT, 127): return
        if ch in (ord('q'), 27): raise SystemExit
        if ch == ord('t'): theme_dialog(stdscr); last = 0; _render_connections(stdscr, rows, start_idx, mode, tick)
        elif ch in (curses.KEY_UP,):
            start_idx = max(0, start_idx - 1)
            _render_connections(stdscr, rows, start_idx, mode, tick)
            stdscr.timeout(SCROLL_MS); boosting = True; boost_until = time.time() + (SCROLL_BOOST_MS/1000.0)
        elif ch in (curses.KEY_DOWN, ord('j')):
            max_lines = max(1, stdscr.getmaxyx()[0] - 6)
            start_idx = min(max(0, len(rows) - max_lines), start_idx + 1)
            _render_connections(stdscr, rows, start_idx, mode, tick)
            stdscr.timeout(SCROLL_MS); boosting = True; boost_until = time.time() + (SCROLL_BOOST_MS/1000.0)
        elif ch == ord('?'):
            _popup_help(stdscr, [
                " Established/Listening Connections ",
                "",
                " Up/Down        : scroll",
                " t              : theme dialog",
                " Backspace/Left : back to menu",
                " q              : quit",
            ])

def _render_connections(stdscr, rows, start_idx, mode, tick):
    stdscr.erase()
    stdscr.bkgd(curses.color_pair(1))
    border_title(stdscr, f"{mode} Connections (Backspace/Left = Back, t = Theme, q = Quit)")
    h, w = stdscr.getmaxyx()
    y = 2; x = 2
    draw_table_header(stdscr, y, x, CONN_COLS, CONN_COLORS, sep=" ")
    draw_hline(stdscr, y+1, 1, w-2)
    max_lines = max(1, h - (y + 3))
    visible = rows[start_idx:start_idx+max_lines]
    for i, base_row in enumerate(visible):
        vals = _format_conn_row(base_row, tick)
        draw_table_row(stdscr, y+2+i, x, vals, CONN_COLS, CONN_COLORS, sep=" ", selected=False)
    stdscr.refresh()

# Both (two panes; bottom nearly touches outer border) — also immediate paint
def screen_both(stdscr, interval):
    stdscr.timeout(IDLE_MS)
    boosting = False
    boost_until = 0.0

    # Immediate snapshot + rows
    tick = (_conn_tick + 1) if _conn_tick >= 0 else 1
    snap = _snapshot_connections(tick)
    est_rows = snap["EST"]; lis_rows = snap["LIS"]
    est_idx = 0; lis_idx = 0
    active = "EST"
    last = time.time()

    layout_key = None
    top = bottom = None

    def layout():
        nonlocal top, bottom, layout_key
        stdscr.erase()
        stdscr.bkgd(curses.color_pair(1))
        border_title(stdscr, "Both Connections (Tab = Switch, t = Theme, Back = Menu, q = Quit)")
        h, w = stdscr.getmaxyx()
        inner_h = h - 2
        inner_w = w - 2
        sep = 1
        top_h = max(5, (inner_h - sep) // 2)
        bot_h = max(5, inner_h - sep - top_h)
        top    = curses.newwin(top_h, inner_w, 1, 1);              top.bkgd(curses.color_pair(1))
        bottom = curses.newwin(bot_h, inner_w, 1 + top_h + sep, 1); bottom.bkgd(curses.color_pair(1))
        layout_key = (h, w)

    def render():
        # Top
        top.erase()
        border_title(top, "Established" + (" [ACTIVE]" if active=="EST" else ""))
        y = 2; x = 1
        draw_table_header(top, y, x, CONN_COLS, CONN_COLORS, sep=" ")
        draw_hline(top, y+1, 1, top.getmaxyx()[1]-2)
        max_est = max(0, top.getmaxyx()[0] - (y + 3))
        for i, base_row in enumerate(est_rows[est_idx:est_idx+max_est]):
            vals = _format_conn_row(base_row, tick)
            draw_table_row(top, y+2+i, x, vals, CONN_COLS, CONN_COLORS, sep=" ", selected=False)
        top.noutrefresh()

        # Bottom
        bottom.erase()
        border_title(bottom, "Listening" + (" [ACTIVE]" if active=="LIS" else ""))
        y = 2; x = 1
        draw_table_header(bottom, y, x, CONN_COLS, CONN_COLORS, sep=" ")
        draw_hline(bottom, y+1, 1, bottom.getmaxyx()[1]-2)
        max_lis = max(0, bottom.getmaxyx()[0] - (y + 3))
        for i, base_row in enumerate(lis_rows[lis_idx:lis_idx+max_lis]):
            vals = _format_conn_row(base_row, tick)
            draw_table_row(bottom, y+2+i, x, vals, CONN_COLS, CONN_COLORS, sep=" ", selected=False)
        bottom.noutrefresh()
        curses.doupdate()

    layout()
    render()

    while True:
        boosting, boost_until = _boost_timeout(stdscr, boosting, boost_until)
        now = time.time()
        h, w = stdscr.getmaxyx()
        if layout_key != (h, w):
            layout(); render()

        if now - last >= interval:
            last = now; tick += 1
            snap = _snapshot_connections(tick)
            est_rows = snap["EST"]; lis_rows = snap["LIS"]
            render()

        ch = stdscr.getch()
        if ch == -1: continue
        if ch in (curses.KEY_BACKSPACE, curses.KEY_LEFT, 127): return
        if ch in (ord('q'), 27): raise SystemExit
        if ch == ord('t'): theme_dialog(stdscr); last = 0; layout(); render()
        elif ch == ord('\t'): active = "LIS" if active == "EST" else "EST"; render()
        elif ch in (curses.KEY_UP,):
            if active == "EST": est_idx = max(0, est_idx - 1)
            else: lis_idx = max(0, lis_idx - 1)
            render(); stdscr.timeout(SCROLL_MS); boosting = True; boost_until = time.time() + (SCROLL_BOOST_MS/1000.0)
        elif ch in (curses.KEY_DOWN,):
            if active == "EST":
                max_est = max(0, top.getmaxyx()[0] - 5)
                est_idx = min(max(0, len(est_rows) - max_est), est_idx + 1)
            else:
                max_lis = max(0, bottom.getmaxyx()[0] - 5)
                lis_idx = min(max(0, len(lis_rows) - max_lis), lis_idx + 1)
            render(); stdscr.timeout(SCROLL_MS); boosting = True; boost_until = time.time() + (SCROLL_BOOST_MS/1000.0)

# Help popup
def _popup_help(stdscr, lines):
    h, w = stdscr.getmaxyx()
    ph = len(lines) + 2
    pw = max(len(x) for x in lines) + 4
    y = (h - ph)//2; x = (w - pw)//2
    win = curses.newwin(ph, pw, y, x)
    win.bkgd(curses.color_pair(1))
    border_title(win, "Help")
    for i, line in enumerate(lines):
        try: win.addstr(1+i, 2, line, curses.color_pair(2))
        except curses.error: pass
    win.refresh()
    win.getch()

# Kill confirm
def confirm_kill(stdscr, name, pid):
    msg = f"Terminate '{name}' (PID {pid})? (y/n)"
    h, w = stdscr.getmaxyx()
    win_w = min(max(40, len(msg)+4), w-4); win_h = 5
    y = (h - win_h)//2; x = (w - win_w)//2
    win = curses.newwin(win_h, win_w, y, x)
    win.bkgd(curses.color_pair(1)); border_title(win, "Confirm")
    try: win.addstr(2, 2, msg, curses.color_pair(2))
    except curses.error: pass
    win.refresh()
    win.timeout(IDLE_MS)
    while True:
        ch = win.getch()
        if ch in (ord('y'), ord('Y')): return True
        if ch in (ord('n'), ord('N'), 27): return False

# Processes
def screen_processes(stdscr, interval):
    """
    Running Processes Screen commands:
      Up/Down Arrows or j: Scroll through the list of processes. (k reserved for kill)
      k: Kill the selected process (with confirmation).
      s: Search for a process.
      n: Find next match in search.
      c: Sort processes by CPU usage.
      m: Sort processes by Memory usage.
      ?: Show this help menu.
      Left Arrow or Backspace: Return to the main menu.
      q: Quit the application.
    """
    stdscr.timeout(IDLE_MS)
    last = 0
    rows = []
    start_idx = 0
    sel = 0
    search_term = None
    sort_mode = PROC_SORT_DEFAULT  # 'cpu' or 'mem'
    boosting = False
    boost_until = 0.0

    def sort_rows(data):
        if sort_mode == "cpu":
            return sorted(data, key=lambda r: float(r[7] or 0.0), reverse=True)
        else:
            return sorted(data, key=lambda r: float(r[8] or 0.0), reverse=True)

    def render():
        _render_processes(stdscr, rows, start_idx, sel, sort_mode)

    while True:
        boosting, boost_until = _boost_timeout(stdscr, boosting, boost_until)

        now = time.time()
        if now - last >= interval:
            last = now
            rows = sort_rows(process_table())
            render()

        ch = stdscr.getch()
        if ch == -1:
            continue
        if ch in (curses.KEY_BACKSPACE, curses.KEY_LEFT, 127):
            return
        if ch in (ord('q'), 27):
            raise SystemExit
        if ch == ord('t'):
            theme_dialog(stdscr); last = 0; render()
        elif ch in (curses.KEY_UP,):
            sel = max(0, sel - 1); start_idx = min(start_idx, sel)
            render(); stdscr.timeout(SCROLL_MS); boosting = True; boost_until = time.time() + (SCROLL_BOOST_MS/1000.0)
        elif ch in (curses.KEY_DOWN, ord('j')):
            sel = min(max(0, len(rows)-1), sel + 1)
            h, w = stdscr.getmaxyx()
            max_lines = max(1, h - 6)
            if sel >= start_idx + max_lines:
                start_idx = sel - max_lines + 1
            render(); stdscr.timeout(SCROLL_MS); boosting = True; boost_until = time.time() + (SCROLL_BOOST_MS/1000.0)
        elif ch == ord('k'):
            try:
                pid = int(rows[sel][0]); pname = rows[sel][10]
                if confirm_kill(stdscr, pname, pid):
                    try: psutil.Process(pid).terminate()
                    except Exception: pass
                    last = 0
                    rows = sort_rows(process_table())
                    render()
            except Exception:
                pass
        elif ch == ord('s'):
            curses.echo()
            h, w = stdscr.getmaxyx()
            prompt = "Search process name: "
            width = min(w-4, 72)
            win = curses.newwin(3, width, h-4, 2)
            win.bkgd(curses.color_pair(1)); border_title(win, "Search")
            try: win.addstr(1, 2, prompt, curses.color_pair(2))
            except curses.error: pass
            win.refresh(); win.timeout(5000)
            try: term = win.getstr(1, 2+len(prompt), width - (4+len(prompt))).decode("utf-8")
            except Exception: term = ""
            curses.noecho()
            search_term = term.strip() or None
            if search_term:
                for i, r in enumerate(rows):
                    if search_term.lower() in r[10].lower():
                        sel = i; start_idx = max(0, sel - 2); break
                render()
        elif ch == ord('n') and search_term:
            start = sel + 1; found = None
            for i in range(start, len(rows)):
                if search_term.lower() in rows[i][10].lower():
                    found = i; break
            if found is not None:
                sel = found
                h, w = stdscr.getmaxyx()
                max_lines = max(1, h - 6)
                if sel >= start_idx + max_lines:
                    start_idx = sel - max_lines + 1
                render()
        elif ch == ord('c'):
            sort_mode = "cpu"; rows = sort_rows(rows); render()
        elif ch == ord('m'):
            sort_mode = "mem"; rows = sort_rows(rows); render()
        elif ch == ord('?'):
            _popup_help(stdscr, [
                " Running Processes ",
                "",
                " Up/Down Arrows or j: Scroll through the list of processes.",
                " k: Kill the selected process (with confirmation).",
                " s: Search for a process.",
                " n: Find next match in search.",
                " c: Sort processes by CPU usage.",
                " m: Sort processes by Memory usage.",
                " ?: Show this help menu.",
                " Left Arrow or Backspace: Return to the main menu.",
                " q: Quit the application.",
            ])

def _render_processes(stdscr, rows, start_idx, sel, sort_mode):
    stdscr.erase()
    stdscr.bkgd(curses.color_pair(1))
    border_title(stdscr, f"Running Processes (sort: {sort_mode.upper()} | c/m • k:Kill • s:Search • n:Next • ?:Help)")
    h, w = stdscr.getmaxyx()
    y = 2; x = 1
    draw_table_header(stdscr, y, x, PROC_COLS, PROC_COLORS, sep=" ")
    draw_hline(stdscr, y+1, 1, w-2)
    max_lines = max(1, h - 6)
    for i, r in enumerate(rows[start_idx:start_idx+max_lines]):
        draw_table_row(stdscr, y+2+i, x, r, PROC_COLS, PROC_COLORS, sep=" ", selected=(start_idx+i)==sel)
    stdscr.refresh()

# Theme dialog
def theme_dialog(stdscr):
    opts = THEME.available()
    sel = opts.index(THEME.current) if THEME.current in opts else 0
    h, w = stdscr.getmaxyx()
    win_h = len(opts) + 6
    title = "Select Theme (Enter = Apply, Esc = Cancel)"
    win_w = max(len(title)+4, max(len(o) for o in opts)+10)
    y = (h - win_h)//2; x = (w - win_w)//2
    win = curses.newwin(win_h, win_w, y, x); win.keypad(True)
    win.timeout(IDLE_MS)

    def paint():
        win.bkgd(curses.color_pair(1))
        border_title(win, "Themes")
        try: win.addstr(1, 2, title, curses.color_pair(2) | curses.A_BOLD)
        except curses.error: pass
        for i, o in enumerate(opts):
            marker = "▶ " if i == sel else "  "
            attr = curses.color_pair(2) | (curses.A_BOLD if i == sel else 0)
            try: win.addstr(3+i, 2, f"{marker}{o}", attr)
            except curses.error: pass
        win.refresh()

    paint()
    while True:
        ch = win.getch()
        if ch in (curses.KEY_UP, ord('k')): sel = (sel - 1) % len(opts); paint()
        elif ch in (curses.KEY_DOWN, ord('j')): sel = (sel + 1) % len(opts); paint()
        elif ch in (10, 13, curses.KEY_ENTER): THEME.apply(stdscr, opts[sel]); break
        elif ch in (27, ord('q')): break

# Main / CLI
def show_help():
    print(f"""
NetScope {VERSION} - Network and Process Monitoring Tool

Usage:
  netscope.py [options]

Options:
  -d <seconds>        Update interval (default: 3)
  -t, --theme <name>  Theme to load from /etc/netscope/themes (default blue)
  -h                  Show this help
  -v                  Show version

Controls (global):
  t                   Open theme dialog
  q                   Quit
  ← / Backspace       Back to menu (from a screen)
""".strip())

def run(stdscr, interval, initial_theme):
    curses.curs_set(0)
    os.environ.setdefault("ESCDELAY", "25")
    THEME.apply(stdscr, initial_theme)
    while True:
        sel = screen_splash(stdscr)
        if sel == 1: screen_system_info(stdscr, interval)
        elif sel == 2: screen_connections(stdscr, interval, "Established")
        elif sel == 3: screen_connections(stdscr, interval, "Listening")
        elif sel == 4: screen_both(stdscr, interval)
        elif sel == 5: screen_processes(stdscr, interval)
        elif sel == 6: break

def main():
    parser = argparse.ArgumentParser(add_help=False, description=f"NetScope {VERSION}")
    parser.add_argument("-d", type=int, default=3, help="Update interval in seconds (default 3)")
    parser.add_argument("-t", "--theme", default="blue", help="Theme name from /etc/netscope/themes (default blue)")
    parser.add_argument("-h", action="store_true", help="Show help")
    parser.add_argument("-v", action="store_true", help="Show version")
    args = parser.parse_args()

    if args.h: show_help(); sys.exit(0)
    if args.v: print(f"NetScope {VERSION}"); sys.exit(0)

    try:
        curses.wrapper(lambda stdscr: run(stdscr, max(1, int(args.d)), args.theme))
    finally:
        _osc_reset_default_bg()

if __name__ == "__main__":
    main()
