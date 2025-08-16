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
import re
import textwrap
import subprocess
import shutil
import getpass
from typing import Optional

# ---- psutil import (graceful fallback if missing) ---------------------------
try:
    import psutil  # type: ignore
    PSUTIL_OK = True
except Exception:
    psutil = None  # type: ignore
    PSUTIL_OK = False

VERSION = "2.0.11"

# -----------------------------------------------------------------------------
# Performance knobs
# -----------------------------------------------------------------------------
IDLE_MS = 80
SCROLL_BOOST_MS = 250
SCROLL_MS = 15
PROC_SORT_DEFAULT = "cpu"
CONN_KIND = os.environ.get("NETSCOPE_CONN_KIND", "tcp")

# -----------------------------------------------------------------------------
# Theming
# -----------------------------------------------------------------------------

THEME_DIR = "/etc/netscope/themes"

def _osc_set_default_bg(hex_color: str):
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
        force = os.environ.get("NETSCOPE_TRUECOLOR")
        if force == "1": return True
        if force == "0": return False
        ct = (os.environ.get("COLORTERM") or "").lower()
        term = (os.environ.get("TERM") or "").lower()
        if "truecolor" in ct or "24bit" in ct: return True
        if term.endswith("-direct"): return True
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
        curses.start_color()
        curses.use_default_colors()
        if name:
            self.current = name
        name = self.current

        def set_pairs_fg_bg(fg, bg):
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
                for pid in (1,2,4,5,6,7,8,9):
                    curses.init_pair(pid, curses.COLOR_WHITE, curses.COLOR_BLUE)
            else:
                p = self._extract_palette(t)
                if self._supports_truecolor():
                    _osc_set_default_bg(p["bg"])
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
                        for pid in (1,2,4,5,6,7,8,9):
                            curses.init_pair(pid, curses.COLOR_WHITE, curses.COLOR_BLUE)

        if stdscr is not None:
            stdscr.bkgd(curses.color_pair(1))
            stdscr.refresh()
        return self.current

THEME = ThemeManager()

# -----------------------------------------------------------------------------
# Splash wordmark
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
# ASCII art import (from ascii_art.py)
# -----------------------------------------------------------------------------

ASCII_COLOR_LAYERS_FN = None
ASCII_CENTER_FN = None

def _load_ascii_from_path(path: str, modname_suffix: str):
    try:
        if not path or not os.path.exists(path):
            return None, None, None
        spec = importlib.util.spec_from_file_location(f"netscope_ascii_{modname_suffix}", path)
        if not spec or not spec.loader:
            return None, None, None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[attr-defined]
        art = getattr(mod, "ascii_art_dict", None)
        if not isinstance(art, dict):
            return None, None, None
        # normalize keys to lowercase so "Darwin" matches "darwin"
        norm = {str(k).lower(): v for k, v in art.items()}
        color_fn = getattr(mod, "color_layers_for", None)
        center_fn = getattr(mod, "centered_ascii", None)
        return norm, (color_fn if callable(color_fn) else None), (center_fn if callable(center_fn) else None)
    except Exception:
        return None, None, None

def load_ascii_art_dict():
    candidates = []
    envp = os.environ.get("NETSCOPE_ASCII_PATH")
    if envp: candidates.append(envp)
    candidates += [
        "/etc/netscope/ascii_art.py",
        str((Path(__file__).parent / "ascii_art.py").resolve()),
        "./ascii_art.py",
        "/usr/local/etc/netscope/ascii_art.py",
        "/usr/local/share/netscope/ascii_art.py",
        "/opt/homebrew/etc/netscope/ascii_art.py",
        "/opt/homebrew/share/netscope/ascii_art.py",
        str(Path.home() / ".config/netscope/ascii_art.py"),
        "/opt/netscope/ascii_art.py",
        "/Library/Application Support/NetScope/ascii_art.py",
        "/mnt/data/ascii_art.py",
    ]
    seen = set()
    idx = 0
    for p in candidates:
        if not p or p in seen:
            continue
        seen.add(p)
        art, cfn, ctf = _load_ascii_from_path(p, str(idx))
        idx += 1
        if art:
            globals()["ASCII_COLOR_LAYERS_FN"] = cfn
            globals()["ASCII_CENTER_FN"] = ctf
            return art
    return {}

ASCII_ART_DICT = load_ascii_art_dict()

# -----------------------------------------------------------------------------
# Distro detection + robust key resolution
# -----------------------------------------------------------------------------

def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())

def _strip_linux(s: str) -> str:
    return _norm(s).replace("linux","")

def _best_key_match(token: str, keys_lower):
    t = token.lower()
    if t in keys_lower:
        return keys_lower[t]
    tn = _strip_linux(t)
    for k_norm, original in ((_strip_linux(k), v) for k, v in keys_lower.items()):
        if tn == k_norm:
            return original
    for k, original in keys_lower.items():
        if t in k or k in t or tn in _strip_linux(k) or _strip_linux(k) in tn:
            return original
    return None

def detect_distro_key():
    keys_lower = { k.lower(): k for k in ASCII_ART_DICT.keys() }

    force = os.environ.get("NETSCOPE_FORCE_DISTRO", "").strip().lower()
    if force and keys_lower:
        match = _best_key_match(force, keys_lower)
        if match:
            return match

    try:
        sysname = (platform.system() or "").lower()
    except Exception:
        sysname = ""

    # --- macOS: hard-prefer 'darwin'
    if sysname in ("darwin", "mac", "macos", "osx", "mac os", "mac os x"):
        for tok in ("darwin","macos","osx","apple","mac","mac os","mac os x"):
            if tok in keys_lower:
                return tok
        return None

    # Windows
    if sysname.startswith("win"):
        for tok in ("windows","microsoft","win"):
            m = _best_key_match(tok, keys_lower)
            if m: return m

    # Linux via /etc/os-release
    osr = {}
    try:
        with open("/etc/os-release","r",encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k,v = line.strip().split("=",1)
                    osr[k] = v.strip().strip('"')
    except Exception:
        pass

    if keys_lower:
        candidates = []
        for key in ("ID","VARIANT_ID"):
            if osr.get(key):
                candidates.append(osr[key])
        if osr.get("ID_LIKE"):
            candidates.extend([p for p in osr["ID_LIKE"].replace(",", " ").split() if p])
        for key in ("NAME","PRETTY_NAME"):
            if osr.get(key):
                candidates.append(osr[key])

        expanded = []
        for c in candidates:
            cl = c.lower()
            expanded.append(cl)
            if "ubuntu" in cl: expanded += ["ubuntu","debian"]
            if "debian" in cl: expanded += ["debian"]
            if "arch" in cl:   expanded += ["arch","arch linux"]
            if "manjaro" in cl:expanded += ["manjaro","arch"]
            if "fedora" in cl: expanded += ["fedora"]
            if "rhel" in cl or "red hat" in cl or "redhat" in cl: expanded += ["rhel","redhat","centos"]
            if "centos" in cl: expanded += ["centos","rhel","redhat"]
            if "rocky" in cl:  expanded += ["rocky","rhel","centos"]
            if "alma" in cl:   expanded += ["almalinux","rhel","centos"]
            if "suse" in cl or "opensuse" in cl:
                expanded += ["opensuse","suse","tumbleweed","leap"]
            if "alpine" in cl: expanded += ["alpine"]
            if "gentoo" in cl: expanded += ["gentoo"]
            if "mint" in cl:   expanded += ["mint","ubuntu","debian"]
            if "kali" in cl:   expanded += ["kali","debian"]
            if "elementary" in cl: expanded += ["elementary","ubuntu"]
            if "pop" in cl:    expanded += ["pop","pop-os","ubuntu"]
            if "budgie" in cl: expanded += ["ubuntu budgie","ubuntu"]
            if "nixos" in cl:  expanded += ["nixos"]
            if "void" in cl:   expanded += ["void"]
            if "endeavour" in cl: expanded += ["endeavour","arch"]
            if "zorin" in cl:  expanded += ["zorin","ubuntu"]

        keys_lower_map = { k.lower(): k for k in ASCII_ART_DICT.keys() }
        for tok in expanded + candidates:
            m = _best_key_match(tok, keys_lower_map)
            if m:
                return m

    return None

def resolve_logo_key(primary_key: Optional[str]) -> Optional[str]:
    if not ASCII_ART_DICT:
        return None
    keys_lower = { k.lower(): k for k in ASCII_ART_DICT.keys() }

    if isinstance(primary_key, str):
        pk = primary_key.lower()
        if pk in ASCII_ART_DICT:
            return pk

    sysname = (platform.system() or "").lower()
    if sysname in ("darwin","mac","macos","osx","mac os","mac os x"):
        for tok in ("darwin","macos","osx","apple","mac","mac os","mac os x"):
            if tok in keys_lower:
                return tok

    if isinstance(primary_key, str):
        m = _best_key_match(primary_key, keys_lower)
        if m:
            return m.lower()

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

def format_bytes_mib(num):
    try:
        return f"{int(num / (1024*1024))}MiB"
    except Exception:
        return "N/A"

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
# System helpers (fallbacks when psutil is missing)
# -----------------------------------------------------------------------------

def _cmd_out(args, text=True, shell=False, timeout=0.5):
    """Safe command execution with reasonable timeout"""
    try:
        res = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=text, shell=shell, timeout=timeout)
        return res.stdout or ""
    except Exception:
        return ""

def _has_cmd(name):
    return shutil.which(name) is not None

def _cpu_counts():
    if PSUTIL_OK:
        try:
            return (psutil.cpu_count(logical=False) or 0, psutil.cpu_count(logical=True) or 0)  # type: ignore[attr-defined]
        except Exception:
            pass
    threads = os.cpu_count() or 0
    return (threads or 0, threads or 0)

def _cpu_freq_mhz():
    if PSUTIL_OK:
        try:
            f = psutil.cpu_freq()  # type: ignore[attr-defined]
            if f: return f.current
        except Exception:
            pass
    if platform.system() == "Darwin":
        out = _cmd_out(["sysctl","-n","hw.cpufrequency"])
        try:
            hz = int(out.strip())
            return hz/1_000_000.0
        except Exception:
            return None
    return None

def _mem_total_and_available():
    if PSUTIL_OK:
        try:
            vm = psutil.virtual_memory()  # type: ignore[attr-defined]
            return (vm.total, getattr(vm, "available", None))
        except Exception:
            pass
    sysname = platform.system()
    if sysname == "Darwin":
        try:
            tot = int(_cmd_out(["sysctl","-n","hw.memsize"]).strip())
        except Exception:
            tot = None
        try:
            vm = _cmd_out(["vm_stat"])
            m = re.search(r"page size of (\d+) bytes", vm)
            pz = int(m.group(1)) if m else 4096
            def pages(key):
                m = re.search(rf"{key}:\s+(\d+)\.", vm)
                return int(m.group(1)) if m else 0
            avail = (pages("Pages free") + pages("Pages speculative")) * pz
        except Exception:
            avail = None
        return (tot or 0, avail)
    elif sysname == "Linux":
        try:
            data = {}
            with open("/proc/meminfo","r") as f:
                for ln in f:
                    if ":" in ln:
                        k,v = ln.split(":",1)
                        data[k.strip()] = v.strip()
            def kb(name):
                s = data.get(name,"0 kB").split()[0]
                return int(s)*1024
            tot = kb("MemTotal")
            avail = kb("MemAvailable") if "MemAvailable" in data else None
            return (tot, avail)
        except Exception:
            return (0, None)
    else:
        return (0, None)

def _disk_usage_root():
    if PSUTIL_OK:
        try:
            d = psutil.disk_usage("/")  # type: ignore[attr-defined]
            return (d.used, d.total, d.percent)
        except Exception:
            pass
    try:
        du = shutil.disk_usage("/")
        used = du.total - du.free
        pct = int(round(used * 100.0 / du.total)) if du.total else 0
        return (used, du.total, pct)
    except Exception:
        return (0, 0, 0)

def _boot_time():
    if PSUTIL_OK:
        try:
            return psutil.boot_time()  # type: ignore[attr-defined]
        except Exception:
            pass
    sysname = platform.system()
    if sysname == "Darwin":
        out = _cmd_out(["sysctl","-n","kern.boottime"])
        m = re.search(r"sec\s*=\s*(\d+)", out)
        if m: return float(m.group(1))
    elif sysname == "Linux":
        try:
            with open("/proc/uptime","r") as f:
                up = float(f.read().split()[0])
                return time.time() - up
        except Exception:
            pass
    return time.time()

# -----------------------------------------------------------------------------
# Per-tick caches
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
    if PSUTIL_OK:
        try:
            p = psutil.Process(pid)  # type: ignore[attr-defined]
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
    if PSUTIL_OK:
        try:
            io = psutil.Process(pid).io_counters()  # type: ignore[attr-defined]
        except Exception:
            io = D()
    else:
        io = D()
    _pid_io_cache[pid] = io
    return io

# -----------------------------------------------------------------------------
# Connections snapshot
# -----------------------------------------------------------------------------

_conn_tick = -1
_conn_snapshot = {"EST": [], "LIS": []}

def _snapshot_connections(tick):
    global _conn_tick, _conn_snapshot
    if _conn_tick == tick:
        return _conn_snapshot
    _conn_tick = tick
    est = []; lis = []
    if PSUTIL_OK:
        try:
            for c in psutil.net_connections(kind=CONN_KIND):  # type: ignore[attr-defined]
                status = c.status
                if status not in ("ESTABLISHED", "LISTEN"):
                    continue
                laddr = f"{compress_ipv6(c.laddr.ip)}:{c.laddr.port}" if c.laddr else "N/A"
                raddr = f"{compress_ipv6(c.raddr.ip)}:{c.raddr.port}" if c.raddr else "N/A"
                pid = int(c.pid) if c.pid else 0
                row = (laddr, raddr, "ESTABLISHED" if status == "ESTABLISHED" else "LISTEN", pid)
                if status == "ESTABLISHED":
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
    if pid and PSUTIL_OK:
        name, user = _proc_meta(pid, tick)
        io = _proc_io(pid, tick)
        sent = format_bytes(io.write_bytes)
        recv = format_bytes(io.read_bytes)
    else:
        name = user = "N/A"
        sent = recv = "N/A"
    return [laddr, raddr, status, str(pid) if pid else "N/A", name, user, sent, recv]

# -----------------------------------------------------------------------------
# System info functions (FIXED)
# -----------------------------------------------------------------------------

def _count_packages_robust(sysname_lower: str) -> str:
    """Robust package counting like neofetch"""
    try:
        if sysname_lower == "darwin":
            total = 0
            # Homebrew packages
            if _has_cmd("brew"):
                out = _cmd_out(["brew", "list", "--formula"], timeout=3.0)
                if out:
                    total += len([l for l in out.splitlines() if l.strip()])
                out = _cmd_out(["brew", "list", "--cask"], timeout=3.0)
                if out:
                    total += len([l for l in out.splitlines() if l.strip()])
            # MacPorts
            if _has_cmd("port"):
                out = _cmd_out(["port", "installed"], timeout=3.0)
                if out:
                    total += len([l for l in out.splitlines() if l.strip() and not l.startswith("The following")])
            return str(total) if total > 0 else "0"
            
        elif sysname_lower == "linux":
            # Debian/Ubuntu - dpkg
            if _has_cmd("dpkg-query"):
                out = _cmd_out(["dpkg-query", "-f", "${binary:Package}\n", "-W"], timeout=3.0)
                if out:
                    return str(len([l for l in out.splitlines() if l.strip()]))
            
            # Red Hat/Fedora/CentOS - rpm
            if _has_cmd("rpm"):
                out = _cmd_out(["rpm", "-qa"], timeout=3.0)
                if out:
                    return str(len([l for l in out.splitlines() if l.strip()]))
            
            # Arch Linux - pacman
            if _has_cmd("pacman"):
                out = _cmd_out(["pacman", "-Qq"], timeout=3.0)
                if out:
                    return str(len([l for l in out.splitlines() if l.strip()]))
            
            # Alpine - apk
            if _has_cmd("apk"):
                out = _cmd_out(["apk", "info"], timeout=3.0)
                if out:
                    return str(len([l for l in out.splitlines() if l.strip()]))
            
            # Gentoo - qlist
            if _has_cmd("qlist"):
                out = _cmd_out(["qlist", "-I"], timeout=3.0)
                if out:
                    return str(len([l for l in out.splitlines() if l.strip()]))
            
            # OpenSUSE - zypper
            if _has_cmd("zypper"):
                out = _cmd_out(["zypper", "se", "--installed-only"], timeout=3.0)
                if out:
                    return str(len([l for l in out.splitlines() if l.strip() and "|" in l]) - 2)  # Remove header lines
            
            # Flatpak
            flatpak_count = 0
            if _has_cmd("flatpak"):
                out = _cmd_out(["flatpak", "list"], timeout=2.0)
                if out:
                    flatpak_count = len([l for l in out.splitlines() if l.strip() and "\t" in l])
            
            # Snap
            snap_count = 0
            if _has_cmd("snap"):
                out = _cmd_out(["snap", "list"], timeout=2.0)
                if out:
                    snap_count = len([l for l in out.splitlines() if l.strip() and not l.startswith("Name")]) - 1
            
            total = flatpak_count + snap_count
            return str(total) if total > 0 else "0"
            
        return "0"
    except Exception:
        return "0"

def _get_cpu_info_robust():
    """Robust CPU detection like neofetch"""
    try:
        sysname = platform.system()
        
        if sysname == "Darwin":
            # Try sysctl first
            cpu_brand = _cmd_out(["sysctl", "-n", "machdep.cpu.brand_string"], timeout=1.0)
            if cpu_brand:
                return cpu_brand.strip()
            
            # Fallback to system_profiler
            out = _cmd_out(["system_profiler", "SPHardwareDataType"], timeout=3.0)
            for line in out.splitlines():
                if "Processor Name:" in line:
                    return line.split(":", 1)[1].strip()
                elif "Chip:" in line:
                    return line.split(":", 1)[1].strip()
            
        elif sysname == "Linux":
            # Read /proc/cpuinfo
            try:
                with open("/proc/cpuinfo", "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if line.startswith("model name"):
                            cpu = line.split(":", 1)[1].strip()
                            # Clean up common CPU name patterns
                            cpu = re.sub(r'\s+', ' ', cpu)  # Multiple spaces to single
                            cpu = re.sub(r'\(R\)', '', cpu)  # Remove (R)
                            cpu = re.sub(r'\(TM\)', '', cpu)  # Remove (TM)
                            return cpu
            except Exception:
                pass
            
            # Fallback to lscpu
            out = _cmd_out(["lscpu"], timeout=2.0)
            for line in out.splitlines():
                if line.startswith("Model name:"):
                    return line.split(":", 1)[1].strip()
        
        # Ultimate fallback
        cpu = platform.processor()
        return cpu if cpu and cpu != "" else "Unknown CPU"
        
    except Exception:
        return "Unknown CPU"

def _get_gpu_info_robust():
    """Robust GPU detection like neofetch"""
    try:
        sysname = platform.system()
        
        if sysname == "Darwin":
            out = _cmd_out(["system_profiler", "SPDisplaysDataType"], timeout=3.0)
            gpus = []
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("Chipset Model:"):
                    gpu = line.split(":", 1)[1].strip()
                    if gpu not in gpus:
                        gpus.append(gpu)
            return ", ".join(gpus) if gpus else "Unknown GPU"
            
        elif sysname == "Linux":
            gpus = []
            
            # Try lspci first
            if _has_cmd("lspci"):
                out = _cmd_out(["lspci", "-mm"], timeout=2.0)
                for line in out.splitlines():
                    if any(word in line.lower() for word in ['vga', '3d', 'display']):
                        # Parse lspci -mm format
                        parts = [p.strip('"') for p in line.split('"') if p.strip('"')]
                        if len(parts) >= 3:
                            gpu = f"{parts[2]} {parts[3]}" if len(parts) > 3 else parts[2]
                            gpus.append(gpu)
            
            # Try reading from /proc/driver/nvidia/version for NVIDIA
            try:
                with open("/proc/driver/nvidia/version", "r") as f:
                    nvidia_info = f.read()
                    if "NVIDIA" in nvidia_info:
                        for line in nvidia_info.splitlines():
                            if "NVIDIA" in line and "Driver Version" in line:
                                gpus.append("NVIDIA GPU")
                                break
            except Exception:
                pass
            
            # Try reading from /sys/class/drm
            try:
                import glob
                for card in glob.glob("/sys/class/drm/card*/device/vendor"):
                    try:
                        with open(card, "r") as f:
                            vendor = f.read().strip()
                        card_path = card.replace("vendor", "device")
                        with open(card_path, "r") as f:
                            device = f.read().strip()
                        if vendor == "0x1002":  # AMD
                            gpus.append("AMD GPU")
                        elif vendor == "0x10de":  # NVIDIA
                            gpus.append("NVIDIA GPU")
                        elif vendor == "0x8086":  # Intel
                            gpus.append("Intel GPU")
                    except Exception:
                        continue
            except Exception:
                pass
            
            return ", ".join(set(gpus)) if gpus else "Unknown GPU"
        
        return "Unknown GPU"
        
    except Exception:
        return "Unknown GPU"

def _get_resolution_robust():
    """Robust resolution detection like neofetch"""
    try:
        sysname = platform.system()
        
        if sysname == "Darwin":
            out = _cmd_out(["system_profiler", "SPDisplaysDataType"], timeout=3.0)
            resolutions = []
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("Resolution:"):
                    res = line.replace("Resolution:", "").strip()
                    if res not in resolutions:
                        resolutions.append(res)
            return ", ".join(resolutions) if resolutions else "Unknown"
            
        elif sysname == "Linux":
            resolutions = []
            
            # Try xrandr first
            if _has_cmd("xrandr") and os.environ.get("DISPLAY"):
                out = _cmd_out(["xrandr", "--current"], timeout=2.0)
                for line in out.splitlines():
                    if " connected" in line and "*" in line:
                        match = re.search(r"(\d{3,5})x(\d{3,5})", line)
                        if match:
                            res = f"{match.group(1)}x{match.group(2)}"
                            if res not in resolutions:
                                resolutions.append(res)
            
            # Try reading from /sys/class/drm if xrandr fails
            if not resolutions:
                try:
                    import glob
                    for mode_file in glob.glob("/sys/class/drm/card*/modes"):
                        try:
                            with open(mode_file, "r") as f:
                                modes = f.read().strip().split('\n')
                                if modes and modes[0]:
                                    resolutions.append(modes[0])
                                    break
                        except Exception:
                            continue
                except Exception:
                    pass
            
            # Try wayland/weston methods
            if not resolutions and _has_cmd("weston-info"):
                out = _cmd_out(["weston-info"], timeout=2.0)
                for line in out.splitlines():
                    if "mode:" in line:
                        match = re.search(r"(\d+)x(\d+)", line)
                        if match:
                            res = f"{match.group(1)}x{match.group(2)}"
                            if res not in resolutions:
                                resolutions.append(res)
            
            return ", ".join(resolutions) if resolutions else "Unknown"
        
        return "Unknown"
        
    except Exception:
        return "Unknown"

def _get_shell_info_robust():
    """Get shell information with version like neofetch"""
    try:
        shell_path = os.environ.get("SHELL") or os.environ.get("ComSpec") or ""
        shell_name = os.path.basename(shell_path) if shell_path else ""
        
        if not shell_name:
            # Try to detect from parent process
            try:
                parent_pid = os.getppid()
                if PSUTIL_OK:
                    parent = psutil.Process(parent_pid)
                    shell_name = parent.name()
            except Exception:
                pass
        
        if not shell_name:
            return "Unknown"
            
        # Get version for common shells
        version = ""
        try:
            if shell_name in ("bash", "zsh", "fish", "ksh", "dash"):
                out = _cmd_out([shell_name, "--version"], timeout=1.0)
                if out:
                    first_line = out.splitlines()[0]
                    # Extract version number
                    version_match = re.search(r'(\d+\.\d+(?:\.\d+)?)', first_line)
                    if version_match:
                        version = version_match.group(1)
            elif shell_name == "tcsh":
                out = _cmd_out([shell_name, "--version"], timeout=1.0)
                if out:
                    version_match = re.search(r'tcsh (\d+\.\d+)', out)
                    if version_match:
                        version = version_match.group(1)
        except Exception:
            pass
        
        return f"{shell_name} {version}".strip() if version else shell_name
        
    except Exception:
        return "Unknown"

# Global cache for system info to avoid repeated expensive calls
_SYSTEM_INFO_CACHE = None
_LOGO_CACHE = None

def initialize_system_cache():
    """Pre-load system information at startup to avoid delays later"""
    global _SYSTEM_INFO_CACHE, _LOGO_CACHE
    
    try:
        sysname = platform.system()
        arch = platform.architecture()[0] if isinstance(platform.architecture(), tuple) else "64bit"

        # OS info with better fallbacks
        if sysname == "Linux":
            os_name = "Linux"
            try:
                with open("/etc/os-release", "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("PRETTY_NAME="):
                            os_name = line.split("=", 1)[1].strip().strip('"')
                            break
                        elif line.startswith("NAME=") and os_name == "Linux":
                            os_name = line.split("=", 1)[1].strip().strip('"')
            except Exception:
                # Fallback: try other methods
                try:
                    if os.path.exists("/etc/debian_version"):
                        os_name = "Debian"
                    elif os.path.exists("/etc/redhat-release"):
                        with open("/etc/redhat-release", "r") as f:
                            os_name = f.read().strip()
                    elif os.path.exists("/etc/arch-release"):
                        os_name = "Arch Linux"
                except Exception:
                    pass
            os_line = f"{arch} {os_name}"
        elif sysname == "Darwin":
            try:
                # Get macOS version
                out = _cmd_out(["sw_vers", "-productVersion"], timeout=0.5)
                version = out.strip() if out else ""
                if version:
                    os_line = f"{arch} macOS {version}"
                else:
                    os_line = f"{arch} macOS"
            except Exception:
                os_line = f"{arch} macOS"
        elif sysname.startswith("Win"):
            os_line = f"{arch} Windows {platform.release()}"
        else:
            os_line = f"{arch} {sysname}"

        # Basic system info with better error handling
        try:
            u = platform.uname()
            kernel_line = f"{u.machine} {u.system} {u.release}"
        except Exception:
            kernel_line = "Unknown"
        
        # Shell info with version
        shell_line = _get_shell_info_robust()
        
        # Desktop environment with better detection
        if sysname == "Darwin":
            de_line = "Aqua"
            wm_line = "Quartz Compositor"
        elif sysname == "Linux":
            de_line = (os.environ.get("XDG_CURRENT_DESKTOP") or 
                      os.environ.get("DESKTOP_SESSION") or 
                      os.environ.get("GDMSESSION") or "Unknown")
            wm_line = (os.environ.get("XDG_SESSION_DESKTOP") or 
                      os.environ.get("XDG_CURRENT_WM") or 
                      os.environ.get("GDMSESSION") or "Unknown")
        else:
            de_line = "Explorer" if sysname.startswith("Win") else "Unknown"
            wm_line = "DWM" if sysname.startswith("Win") else "Unknown"

        # User info
        try:
            uhost = f"{getpass.getuser()}@{socket.gethostname()}"
        except Exception:
            uhost = "user@hostname"

        # More expensive operations with better fallbacks
        pkgs = _count_packages_robust(sysname.lower())
        cpu_line = _get_cpu_info_robust()
        gpu_line = _get_gpu_info_robust()
        res_line = _get_resolution_robust()

        _SYSTEM_INFO_CACHE = {
            "UH": uhost,
            "OS": os_line,
            "KERNEL": kernel_line,
            "PKGS": pkgs,
            "SHELL": shell_line,
            "RES": res_line,
            "DE": de_line,
            "WM": wm_line,
            "CPU": cpu_line,
            "GPU": gpu_line,
        }
        
        # Pre-load logo data
        detected_key = detect_distro_key()
        resolved_key = resolve_logo_key(detected_key)
        _LOGO_CACHE = _prepare_logo_data(resolved_key)
        
    except Exception:
        # Ultimate fallback
        _SYSTEM_INFO_CACHE = {
            "UH": f"{getpass.getuser()}@{socket.gethostname()}",
            "OS": f"{platform.architecture()[0]} {platform.system()}",
            "KERNEL": platform.release(),
            "PKGS": "N/A",
            "SHELL": os.path.basename(os.environ.get("SHELL", "N/A")),
            "RES": "N/A",
            "DE": "N/A",
            "WM": "N/A",
            "CPU": platform.processor() or "CPU",
            "GPU": "N/A",
        }

def _get_dynamic_info():
    """Get frequently changing system info"""
    try:
        # Uptime
        try:
            up = time.time() - _boot_time()
            d = int(up // 86400); h = int((up % 86400)//3600); m = int((up % 3600)//60)
            if d > 0:
                uptime = f"{d}d {h}h {m}m"
            elif h > 0:
                uptime = f"{h}h {m}m"
            else:
                uptime = f"{m}m"
        except Exception:
            uptime = "N/A"

        # Disk usage
        try:
            used, total, pct = _disk_usage_root()
            disk = f"{format_bytes(used)} / {format_bytes(total)} ({pct}%)"
        except Exception:
            disk = "N/A"

        # RAM usage
        try:
            if PSUTIL_OK:
                vm = psutil.virtual_memory()  # type: ignore[attr-defined]
                used = vm.total - vm.available
                ram = f"{format_bytes_mib(used)} / {format_bytes_mib(vm.total)}"
            else:
                tot, avail = _mem_total_and_available()
                if tot and avail is not None:
                    used = max(0, tot - avail)
                    ram = f"{format_bytes_mib(used)} / {format_bytes_mib(tot)}"
                elif tot:
                    ram = f"{format_bytes_mib(tot)} total"
                else:
                    ram = "N/A"
        except Exception:
            ram = "N/A"

        return {
            "UPTIME": uptime,
            "DISK": disk,
            "RAM": ram,
        }
    except Exception:
        return {
            "UPTIME": "N/A",
            "DISK": "N/A", 
            "RAM": "N/A",
        }

def _get_complete_system_info(static_info):
    """Combine static and dynamic info efficiently"""
    dynamic = _get_dynamic_info()
    
    return [
        static_info["UH"],
        f"OS: {static_info['OS']}",
        f"Kernel: {static_info['KERNEL']}",
        f"Uptime: {dynamic['UPTIME']}",
        f"Packages: {static_info['PKGS']}",
        f"Shell: {static_info['SHELL']}",
        f"Resolution: {static_info['RES']}",
        f"DE: {static_info['DE']}",
        f"WM: {static_info['WM']}",
        f"WM Theme: {THEME.current}",
        f"Font: {os.environ.get('TERMINAL_FONT') or 'N/A'}",
        f"Disk: {dynamic['DISK']}",
        f"CPU: {static_info['CPU']}",
        f"GPU: {static_info['GPU']}",
        f"RAM: {dynamic['RAM']}",
    ]

def _prepare_logo_data(resolved_key):
    """Prepare logo data efficiently"""
    if not resolved_key or not ASCII_ART_DICT.get(resolved_key):
        return None
        
    distro_art = ASCII_ART_DICT[resolved_key]
    distro_layers = None
    
    try:
        if ASCII_COLOR_LAYERS_FN:
            layers = ASCII_COLOR_LAYERS_FN(resolved_key)
            if isinstance(layers, list) and layers:
                distro_layers = layers
    except Exception:
        pass
        
    if not distro_layers:
        try:
            distro_layers = _enhanced_policy_color_layers(resolved_key, distro_art)
        except Exception:
            distro_layers = None
    
    return (distro_art, distro_layers)

def _enhanced_policy_color_layers(distro_key, lines):
    """Enhanced color policy with special handling for multi-color logos like Darwin"""
    if not lines:
        return []
    
    key_lower = distro_key.lower() if distro_key else ""
    
    # Special handling for Darwin/Apple - rainbow stripes
    if key_lower in ("darwin", "macos", "osx", "apple"):
        # Use the rainbow stripe pattern from the ASCII art module
        rainbow_colors = ['accent3', 'accent', 'accent2', 'accent2', 'accent5', 'accent4']  # Green, Yellow, Orange, Red, Purple, Blue
        layered = []
        for i, line in enumerate(lines):
            if line.strip():  # Skip empty lines
                color = rainbow_colors[i % len(rainbow_colors)]
            else:
                color = 'fg'
            layered.append((color, [line]))
        return layered
    
    # Special handling for Debian - light with red accent
    elif key_lower == "debian":
        # Most of the logo is light (fg/muted), with red accent in the center area
        layered = []
        total_lines = len(lines)
        center_start = total_lines // 3
        center_end = 2 * total_lines // 3
        
        for i, line in enumerate(lines):
            if center_start <= i <= center_end and line.strip():
                # Center portion gets red accent
                color = 'accent2'
            elif line.strip():
                # Rest is light colored
                color = 'fg'
            else:
                color = 'fg'
            layered.append((color, [line]))
        return layered
    
    # Standard single-color mappings for other distros
    color_map = {
        'arch': 'accent4', 'archlinux': 'accent4', 'arch linux': 'accent4',
        'ubuntu': 'accent2', 'ubuntu budgie': 'accent2',
        'fedora': 'accent4',
        'mint': 'accent3', 'linux mint': 'accent3',
        'manjaro': 'accent3',
        'kali': 'accent4', 'kali linux': 'accent4',
        'redhat': 'accent2', 'rhel': 'accent2', 'red hat': 'accent2',
        'gentoo': 'accent5',
        'slackware': 'accent4',
        'elementary': 'accent4',
        'opensuse': 'accent3', 'suse': 'accent3'
    }
    
    color = color_map.get(key_lower, 'accent')
    return [(color, list(lines))]

def get_screenfetch_info_lines():
    """Legacy compatibility function"""
    return get_complete_system_info()

def get_complete_system_info():
    """Get complete system information combining static and dynamic data"""
    global _SYSTEM_INFO_CACHE
    
    # Initialize cache if not already done
    if _SYSTEM_INFO_CACHE is None:
        initialize_system_cache()
    
    # Get dynamic info
    dynamic = _get_dynamic_info()
    
    return [
        _SYSTEM_INFO_CACHE["UH"],
        f"OS: {_SYSTEM_INFO_CACHE['OS']}",
        f"Kernel: {_SYSTEM_INFO_CACHE['KERNEL']}",
        f"Uptime: {dynamic['UPTIME']}",
        f"Packages: {_SYSTEM_INFO_CACHE['PKGS']}",
        f"Shell: {_SYSTEM_INFO_CACHE['SHELL']}",
        f"Resolution: {_SYSTEM_INFO_CACHE['RES']}",
        f"DE: {_SYSTEM_INFO_CACHE['DE']}",
        f"WM: {_SYSTEM_INFO_CACHE['WM']}",
        f"WM Theme: {THEME.current}",
        f"Font: {os.environ.get('TERMINAL_FONT') or 'N/A'}",
        f"Disk: {dynamic['DISK']}",
        f"CPU: {_SYSTEM_INFO_CACHE['CPU']}",
        f"GPU: {_SYSTEM_INFO_CACHE['GPU']}",
        f"RAM: {dynamic['RAM']}",
    ]

# Legacy compatibility functions
def _compute_screenfetch_static():
    """Legacy compatibility"""
    global _SYSTEM_INFO_CACHE
    if _SYSTEM_INFO_CACHE is None:
        initialize_system_cache()
    return _SYSTEM_INFO_CACHE

def _disk_line():
    """Legacy compatibility"""
    return _get_dynamic_info()["DISK"]

def _ram_line():
    """Legacy compatibility"""
    return _get_dynamic_info()["RAM"]

def _uptime_line():
    """Legacy compatibility"""
    return _get_dynamic_info()["UPTIME"]

# -----------------------------------------------------------------------------
# ANSI handling + width for centering
# -----------------------------------------------------------------------------

_ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')

def _strip_ansi(s: str) -> str:
    try:
        return _ANSI_RE.sub('', s)
    except Exception:
        return s

_SGR_TO_THEME = {
    30: "muted", 31: "accent2", 32: "accent3", 33: "accent", 34: "accent4", 35: "accent5", 36: "accent4", 37: "fg",
    90: "muted", 91: "accent2", 92: "accent3", 93: "accent", 94: "accent4", 95: "accent5", 96: "accent4", 97: "fg",
}
_BG_SGR = {40,41,42,43,44,45,46,47,100,101,102,103,104,105,106,107}

def _sgr_to_theme_name(code: int, is_bg=False):
    if is_bg:
        base = code - (10 if code >= 100 else 0)
        return _SGR_TO_THEME.get(base, "fg")
    return _SGR_TO_THEME.get(code, "fg")

def _render_ansi_line(win, y, x, text, pair_map, fallback_pair_id):
    i = 0
    cur_fg = None
    cur_bg = None
    maxx = win.getmaxyx()[1]
    cx = x
    while i < len(text) and cx < maxx-1:
        if text[i] == '\x1b':
            m = _ANSI_RE.match(text, i)
            if m:
                seq = m.group(0)
                if seq.endswith('m'):
                    body = seq[2:-1]
                    parts = [p for p in body.split(';') if p != ""]
                    if not parts:
                        cur_fg = cur_bg = None
                    else:
                        for p in parts:
                            try:
                                code = int(p)
                            except ValueError:
                                continue
                            if code == 0:
                                cur_fg = cur_bg = None
                            elif 30 <= code <= 37 or 90 <= code <= 97:
                                cur_fg = _sgr_to_theme_name(code, is_bg=False)
                            elif code in _BG_SGR:
                                cur_bg = _sgr_to_theme_name(code, is_bg=True)
                i = m.end()
                continue
        ch = text[i]
        if ch == '\t':
            ch = ' '
        if ch == ' ' and cur_bg:
            pid = pair_map.get(cur_bg, fallback_pair_id)
            try: win.addch(y, cx, '█', curses.color_pair(pid) | curses.A_BOLD)
            except curses.error: pass
            cx += 1; i += 1; continue
        pid = pair_map.get(cur_fg or "fg", fallback_pair_id)
        try: win.addch(y, cx, ch, curses.color_pair(pid) | (curses.A_BOLD if pid != 1 else 0))
        except curses.error: pass
        cx += 1; i += 1

# -----------------------------------------------------------------------------
# Wrapping helpers for info lines
# -----------------------------------------------------------------------------

def _wrap_info_lines(lines, target_width):
    out = []
    maxw = max(20, target_width)
    for line in lines:
        if not line or ":" not in line:
            for chunk in textwrap.wrap(line, width=maxw, break_long_words=False, break_on_hyphens=False):
                out.append(chunk)
            continue
        key, rest = line.split(":", 1)
        key = key.strip()
        rest = rest.lstrip()
        indent = len(key) + 2
        body_width = max(10, maxw - indent)
        parts = textwrap.wrap(rest, width=body_width, break_long_words=False, break_on_hyphens=False)
        if not parts:
            out.append(f"{key}:")
            continue
        out.append(f"{key}: {parts[0]}")
        for p in parts[1:]:
            out.append(" " * indent + p)
    return out

# -----------------------------------------------------------------------------
# UI helpers
# -----------------------------------------------------------------------------

MIN_WIDTH = 120
INNER_LEFT_PAD = 2

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
CONN_COLORS = [2,5,6,7,8,9,5,6]
PROC_COLORS = [2,5,6,7,8,9,5,6,7,8,9]

def _boost_timeout(stdscr, boosting, boost_until):
    now = time.time()
    if boosting and now >= boost_until:
        stdscr.timeout(IDLE_MS)
        return False, 0.0
    return boosting, boost_until

# -----------------------------------------------------------------------------
# Screens
# -----------------------------------------------------------------------------

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

        h, w = stdscr.getmaxyx()
        logo_top = max(2, h//6)
        ascii_attr = curses.color_pair(7) | curses.A_BOLD
        for i, line in enumerate(NETSCOPE_SPLASH_ASCII):
            x = max(0, (w - len(line)) // 2)
            try:
                stdscr.addstr(logo_top + i, x, line, ascii_attr)
            except curses.error:
                pass

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
    stdscr.timeout(50)

    detected_key = detect_distro_key()
    resolved_key = resolve_logo_key(detected_key)
    
    logo_data = _prepare_logo_data(resolved_key)
    
    last_dynamic_update = 0
    last_render_hash = None
    
    current_info = get_complete_system_info()
    _render_system_info_optimized(stdscr, logo_data, current_info)
    last_render_hash = hash(tuple(current_info))
    
    while True:
        now = time.time()
        
        if now - last_dynamic_update >= interval:
            last_dynamic_update = now
            
            current_info = get_complete_system_info()
            current_hash = hash(tuple(current_info))
            
            if current_hash != last_render_hash:
                _render_system_info_optimized(stdscr, logo_data, current_info)
                last_render_hash = current_hash
                
        ch = stdscr.getch()
        if ch == -1: 
            continue
        if ch in (curses.KEY_BACKSPACE, curses.KEY_LEFT, 127): 
            return
        if ch in (ord('q'), 27): 
            raise SystemExit
        if ch == ord('t'): 
            theme_dialog(stdscr)
            # Refresh system cache when theme changes
            global _SYSTEM_INFO_CACHE
            _SYSTEM_INFO_CACHE = None
            initialize_system_cache()
            last_render_hash = None
            last_dynamic_update = 0

def _render_system_info_optimized(stdscr, logo_data, info_lines):
    """Optimized system info rendering with improved performance"""
    stdscr.erase()
    stdscr.bkgd(curses.color_pair(1))
    border_title(stdscr, "System Info (Backspace/Left = Back, t = Theme, q = Quit)")
    
    h, w = stdscr.getmaxyx()
    pair_map = {'fg':1, 'accent':2, 'accent2':5, 'accent3':6, 'accent4':7, 'accent5':8, 'muted':9}
    
    # Process logo data
    logo_lines = []
    logo_colors = []
    ansi_present = False
    
    if logo_data:
        distro_art, distro_layers = logo_data
        if distro_art:
            ansi_present = any('\x1b[' in ln for ln in distro_art if isinstance(ln, str))
        
        if distro_layers:
            for color_name, lines in distro_layers:
                pair_id = pair_map.get(color_name, 2)
                for line in lines:
                    logo_lines.append(line)
                    logo_colors.append(pair_id)
        elif distro_art:
            # Fallback to default coloring
            logo_lines = list(distro_art)
            logo_colors = [2] * len(logo_lines)
    else:
        logo_lines = ["[ Logo not found ]"]
        logo_colors = [9]
    
    # Calculate dimensions
    vis_logo_widths = [len(_strip_ansi(line)) for line in logo_lines]
    logo_w = max(vis_logo_widths) if vis_logo_widths else 0
    
    # Process info lines with wrapping
    target_info_w = min(max(logo_w, 60), max(20, w - 4))
    info_wrapped = _wrap_info_lines(info_lines, target_info_w)
    vis_info_widths = [len(line) for line in info_wrapped]
    info_w = max(vis_info_widths) if vis_info_widths else 0
    
    max_w = max(logo_w, info_w)
    total_height = len(logo_lines) + 1 + len(info_wrapped)
    start_y = max(1, (h - total_height) // 2)
    start_x = max(1, (w - max_w) // 2)
    
    # Render logo
    y = start_y
    max_render_width = max(1, w - start_x - 1)
    
    for line, color_pair in zip(logo_lines, logo_colors):
        if y >= h - 1:
            break
        
        # Handle ANSI sequences or plain text
        if ansi_present and '\x1b[' in line:
            try:
                _render_ansi_line(stdscr, y, start_x, line, pair_map, fallback_pair_id=color_pair)
            except Exception:
                # Fallback to safe rendering
                safe = _strip_ansi(line)[:max_render_width]
                try: 
                    stdscr.addnstr(y, start_x, safe, max_render_width, curses.color_pair(color_pair) | curses.A_BOLD)
                except curses.error: 
                    pass
        else:
            # Plain text rendering
            safe_line = line[:max_render_width]
            attr = curses.color_pair(color_pair) | (curses.A_BOLD if color_pair != 1 else 0)
            try: 
                stdscr.addnstr(y, start_x, safe_line, max_render_width, attr)
            except curses.error: 
                pass
        y += 1
    
    y += 1  # Space between logo and info
    
    # Render info lines
    HEAD_PAIR = 2
    VAL_PAIR = 1
    
    for line in info_wrapped:
        if y >= h - 1:
            break
            
        if not line.strip():
            y += 1
            continue
            
        if line.startswith(" "):
            # Continuation line
            safe_line = line[:max_render_width]
            try: 
                stdscr.addnstr(y, start_x, safe_line, max_render_width, curses.color_pair(VAL_PAIR))
            except curses.error: 
                pass
        elif ":" in line:
            # Key-value pair
            key, rest = line.split(":", 1)
            key = key.strip()
            rest = rest.lstrip()
            
            # Render key
            try: 
                stdscr.addnstr(y, start_x, key, max_render_width, curses.color_pair(HEAD_PAIR) | curses.A_BOLD)
            except curses.error: 
                pass
            
            # Render separator
            x2 = start_x + len(key)
            if x2 < w - 2:
                try: 
                    stdscr.addnstr(y, x2, ": ", max(1, w - x2 - 1), curses.color_pair(HEAD_PAIR) | curses.A_BOLD)
                except curses.error: 
                    pass
                
                # Render value
                x3 = x2 + 2
                if x3 < w - 1 and rest:
                    safe_rest = rest[:max(1, w - x3 - 1)]
                    try: 
                        stdscr.addnstr(y, x3, safe_rest, max(1, w - x3 - 1), curses.color_pair(VAL_PAIR))
                    except curses.error: 
                        pass
        else:
            # Plain line
            safe_line = line[:max_render_width]
            try: 
                stdscr.addnstr(y, start_x, safe_line, max_render_width, curses.color_pair(VAL_PAIR))
            except curses.error: 
                pass
        y += 1
    
    stdscr.refresh()

# Connections
def screen_connections(stdscr, interval, mode):
    stdscr.timeout(IDLE_MS)
    start_idx = 0
    boosting = False
    boost_until = 0.0

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

# Both panes
def screen_both(stdscr, interval):
    stdscr.timeout(IDLE_MS)
    boosting = False
    boost_until = 0.0

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
        top.erase()
        border_title(top, "Established" + (" [ACTIVE]" if active=="EST" else ""))
        y = 2; x = INNER_LEFT_PAD
        draw_table_header(top, y, x, CONN_COLS, CONN_COLORS, sep=" ")
        draw_hline(top, y+1, 1, top.getmaxyx()[1]-2)
        max_est = max(0, top.getmaxyx()[0] - (y + 3))
        for i, base_row in enumerate(est_rows[est_idx:est_idx+max_est]):
            vals = _format_conn_row(base_row, tick)
            draw_table_row(top, y+2+i, x, vals, CONN_COLS, CONN_COLORS, sep=" ", selected=False)
        top.noutrefresh()

        bottom.erase()
        border_title(bottom, "Listening" + (" [ACTIVE]" if active=="LIS" else ""))
        y = 2; x = INNER_LEFT_PAD
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

# Confirm kill
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
def _process_table_via_ps():
    rows = []
    cmd = ["ps","-axo","pid=,user=,ni=,vsz=,rss=,stat=,pcpu=,pmem=,etime=,comm="]
    out = _cmd_out(cmd, timeout=1)
    for ln in out.splitlines():
        try:
            parts = ln.strip().split(None, 10)
            if len(parts) < 10:
                continue
            pid, user, ni, vsz_k, rss_k, stat, pcpu, pmem, etime, comm = parts[:10]
            VIRT = format_bytes(int(vsz_k)*1024)
            RES  = format_bytes(int(rss_k)*1024)
            SHR  = "N/A"
            STATUS = stat
            CPU = f"{float(pcpu):.1f}"
            MEM = f"{float(pmem):.1f}"
            TIMEP = etime
            rows.append([pid, user, ni, VIRT, RES, SHR, STATUS, CPU, MEM, TIMEP, comm])
        except Exception:
            continue
    return rows

def process_table():
    if not PSUTIL_OK:
        return _process_table_via_ps()
    rows = []
    for proc in psutil.process_iter(["pid","username","nice","memory_info","status","cpu_percent","memory_percent","name","create_time"]):  # type: ignore[attr-defined]
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

def screen_processes(stdscr, interval):
    stdscr.timeout(IDLE_MS)
    start_idx = 0
    sel_idx = 0
    sort_col = PROC_SORT_DEFAULT
    boosting = False
    boost_until = 0.0

    def get_sort_key(row):
        if sort_col == "cpu":
            try: return float(row[7])
            except: return 0.0
        elif sort_col == "mem":
            try: return float(row[8])
            except: return 0.0
        elif sort_col == "pid":
            try: return int(row[0])
            except: return 0
        else:
            return row[0]

    rows = process_table()
    rows.sort(key=get_sort_key, reverse=True)
    _render_processes(stdscr, rows, start_idx, sel_idx, sort_col)
    last = time.time()

    while True:
        boosting, boost_until = _boost_timeout(stdscr, boosting, boost_until)
        now = time.time()
        if now - last >= interval:
            last = now
            rows = process_table()
            rows.sort(key=get_sort_key, reverse=True)
            sel_idx = min(sel_idx, len(rows)-1) if rows else 0
            _render_processes(stdscr, rows, start_idx, sel_idx, sort_col)

        ch = stdscr.getch()
        if ch == -1: continue
        if ch in (curses.KEY_BACKSPACE, curses.KEY_LEFT, 127): return
        if ch in (ord('q'), 27): raise SystemExit
        if ch == ord('t'): theme_dialog(stdscr); last = 0; _render_processes(stdscr, rows, start_idx, sel_idx, sort_col)
        elif ch in (curses.KEY_UP, ord('k')):
            sel_idx = max(0, sel_idx - 1)
            max_lines = max(1, stdscr.getmaxyx()[0] - 6)
            if sel_idx < start_idx: start_idx = sel_idx
            _render_processes(stdscr, rows, start_idx, sel_idx, sort_col)
            stdscr.timeout(SCROLL_MS); boosting = True; boost_until = time.time() + (SCROLL_BOOST_MS/1000.0)
        elif ch in (curses.KEY_DOWN, ord('j')):
            sel_idx = min(len(rows)-1, sel_idx + 1) if rows else 0
            max_lines = max(1, stdscr.getmaxyx()[0] - 6)
            if sel_idx >= start_idx + max_lines: start_idx = sel_idx - max_lines + 1
            _render_processes(stdscr, rows, start_idx, sel_idx, sort_col)
            stdscr.timeout(SCROLL_MS); boosting = True; boost_until = time.time() + (SCROLL_BOOST_MS/1000.0)
        elif ch == ord('c'): sort_col = "cpu"; rows.sort(key=get_sort_key, reverse=True); _render_processes(stdscr, rows, start_idx, sel_idx, sort_col)
        elif ch == ord('m'): sort_col = "mem"; rows.sort(key=get_sort_key, reverse=True); _render_processes(stdscr, rows, start_idx, sel_idx, sort_col)
        elif ch == ord('p'): sort_col = "pid"; rows.sort(key=get_sort_key, reverse=True); _render_processes(stdscr, rows, start_idx, sel_idx, sort_col)
        elif ch == ord('x') and rows and sel_idx < len(rows):
            row = rows[sel_idx]
            pid, name = row[0], row[10]
            if confirm_kill(stdscr, name, pid):
                try:
                    if PSUTIL_OK:
                        psutil.Process(int(pid)).terminate()  # type: ignore[attr-defined]
                    else:
                        os.kill(int(pid), 15)
                except Exception:
                    pass
            _render_processes(stdscr, rows, start_idx, sel_idx, sort_col)
        elif ch == ord('?'):
            _popup_help(stdscr, [
                " Process Manager ",
                "",
                " Up/Down        : navigate",
                " c              : sort by CPU",
                " m              : sort by Memory",
                " p              : sort by PID",
                " x              : terminate process",
                " t              : theme dialog",
                " Backspace/Left : back to menu",
                " q              : quit",
            ])

def _render_processes(stdscr, rows, start_idx, sel_idx, sort_col):
    stdscr.erase()
    stdscr.bkgd(curses.color_pair(1))
    border_title(stdscr, f"Processes (Sort: {sort_col.upper()}) (c=CPU, m=MEM, p=PID, x=Kill, ?=Help)")
    h, w = stdscr.getmaxyx()
    y = 2; x = 2
    draw_table_header(stdscr, y, x, PROC_COLS, PROC_COLORS, sep=" ")
    draw_hline(stdscr, y+1, 1, w-2)
    max_lines = max(1, h - (y + 3))
    visible = rows[start_idx:start_idx+max_lines]
    for i, row in enumerate(visible):
        selected = (start_idx + i) == sel_idx
        draw_table_row(stdscr, y+2+i, x, row, PROC_COLS, PROC_COLORS, sep=" ", selected=selected)
    stdscr.refresh()

# -----------------------------------------------------------------------------
# Legacy basic info (kept for completeness)
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
    ]
    cores, threads = _cpu_counts()
    lines += [
        f"CPU Cores: {cores if cores else 'N/A'}",
        f"CPU Threads: {threads if threads else 'N/A'}",
    ]
    freq = _cpu_freq_mhz()
    if freq:
        lines.append(f"CPU Frequency: {freq:.2f} MHz")

    tot, _ = _mem_total_and_available()
    lines.append(f"Total Memory: {format_bytes(tot) if tot else 'N/A'}")

    used, total, pct = _disk_usage_root()
    if total:
        lines.append(f"Disk Usage: {pct}% of {format_bytes(total)}")

    if PSUTIL_OK:
        try:
            ifs = psutil.net_if_addrs()  # type: ignore[attr-defined]
            if ifs: lines.append(f"Network Interfaces: {', '.join(ifs.keys())}")
        except Exception:
            pass
    else:
        if _has_cmd("ifconfig"):
            out = _cmd_out(["bash","-lc","ifconfig -a | grep -E '^[a-zA-Z0-9]+' -o | xargs echo"], shell=True, timeout=1)
            if out.strip():
                names = [n for n in out.strip().split() if n]
                if names:
                    lines.append(f"Network Interfaces: {', '.join(names)}")
    return lines

# -----------------------------------------------------------------------------
# Theme dialog
# -----------------------------------------------------------------------------

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
        elif ch in (10, 13, curses.KEY_ENTER): 
            THEME.apply(stdscr, opts[sel])
            # Clear cache to refresh theme-dependent info
            global _SYSTEM_INFO_CACHE
            if _SYSTEM_INFO_CACHE:
                _SYSTEM_INFO_CACHE = None
            initialize_system_cache()
            break
        elif ch in (27, ord('q')): break

# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

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
    
    # Initialize system cache at startup for fast loading
    initialize_system_cache()
    
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
