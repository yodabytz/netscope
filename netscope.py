#!/usr/bin/python3

import psutil
import curses
import time
import os
import platform
import re
import sys
import subprocess
import argparse
import socket

# Constants
VERSION = "2.0.04"

# Load ASCII art for system info from a separate file
ascii_art_path = '/etc/netscope/ascii_art.py'
ascii_art_dict = {}
if os.path.exists(ascii_art_path):
    with open(ascii_art_path) as f:
        exec(f.read(), globals())

# Corrected NetScope ASCII art for the splash screen
netscope_ascii_art = [
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

def show_help():
    help_text = f"""
    NetScope {VERSION} - Network and Process Monitoring Tool

    Usage: netscope [options]

    Options:
    -d <seconds>    Set the update interval in seconds (default is 3 seconds)
    -h              Show this help message
    -v              Show version information

    Controls:
    Menu Navigation:
    Up/Down Arrows or k/j: Navigate through the menu options.
    Enter or Return: Select a menu option.
    q: Quit the application from any screen.

    Established and Listening Connections Screens:
    Up/Down Arrows or k/j: Scroll through the list of connections.
    Left Arrow or Backspace: Return to the main menu.
    q: Quit the application.

    Both Connections Screen:
    Tab: Switch between Established and Listening sections.
    Up/Down Arrows or k/j: Scroll through the connections in the active section.
    ?: Show this help menu.
    Left Arrow or Backspace: Return to the main menu.
    q: Quit the application.

    Running Processes Screen:
    Up/Down Arrows or k/j: Scroll through the list of processes.
    k: Kill the selected process (with confirmation).
    s: Search for a process.
    n: Find next match in search.
    c: Sort processes by CPU usage.
    m: Sort processes by Memory usage.
    ?: Show this help menu.
    Left Arrow or Backspace: Return to the main menu.
    q: Quit the application.
    """
    print(help_text)

def get_process_name(pid):
    try:
        process = psutil.Process(pid)
        return process.name()[:20]  # Limit the process name length to 20 characters
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None

def get_process_user(pid):
    try:
        process = psutil.Process(pid)
        return process.username()[:15]  # Limit the user name length to 15 characters
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None

def format_size(bytes):
    if bytes is None:
        return "N/A"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes < 1024:
            return f"{bytes:.2f} {unit}"
        bytes /= 1024

def get_connections(status_filter):
    connections = []
    for conn in psutil.net_connections(kind='inet'):
        if conn.status == status_filter:
            laddr = f"{conn.laddr.ip}:{conn.laddr.port}".replace('::ffff:', '').ljust(25)
            raddr = f"{conn.raddr.ip}:{conn.raddr.port}".replace('::ffff:', '').ljust(25) if conn.raddr else "".ljust(25)
            status = conn.status.ljust(12)
            pid = str(conn.pid).ljust(8) if conn.pid else "None".ljust(8)
            program = get_process_name(conn.pid).ljust(20) if conn.pid else "".ljust(20)
            user = get_process_user(conn.pid).ljust(15) if conn.pid else "".ljust(15)
            connections.append([laddr, raddr, status, pid, program, user])
    return connections

# FIXED FUNCTION: Generalized process detection for all "amavisd-like" processes
def get_all_processes():
    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'exe', 'cmdline', 'username', 'nice', 'memory_info', 'memory_percent', 'cpu_percent', 'cpu_times', 'status']):
        try:
            pid = str(proc.info['pid']).ljust(8)
            user = (proc.info['username'][:15] if proc.info['username'] else "N/A").ljust(15)
            nice = str(proc.info['nice']).ljust(5) if proc.info['nice'] is not None else "N/A".ljust(5)
            memory_info = proc.info['memory_info']
            if proc.info['memory_percent'] is not None:
                memory_percent = f"{proc.info['memory_percent']:.1f}".ljust(6)
            else:
                memory_percent = "0.0".ljust(6)
            if proc.info['cpu_percent'] is not None:
                cpu_percent = f"{proc.info['cpu_percent']:.1f}".ljust(6)
            else:
                cpu_percent = "0.0".ljust(6)
            status = proc.info['status'].ljust(8) if proc.info['status'] else "N/A".ljust(8)
            virt = format_size(memory_info.vms).ljust(10) if memory_info else "N/A".ljust(10)
            res = format_size(memory_info.rss).ljust(10) if memory_info else "N/A".ljust(10)
            if platform.system() == "Darwin":
                shr = "N/A".ljust(10)
            else:
                shr = format_size(getattr(memory_info, 'shared', None)).ljust(10) if memory_info else "N/A".ljust(10)
            cpu_time = f"{proc.info['cpu_times'].user:.2f}".ljust(8) if proc.info['cpu_times'] else "N/A".ljust(8)
            # --- Begin Fix: Generalized for tricky process names ---
            proc_name = proc.info['name'] or ''
            exe = proc.info.get('exe') or ''
            cmdline = proc.info.get('cmdline') or []
            command = proc_name
            # Prefer exe if it adds context, then cmdline
            if exe and exe not in proc_name:
                command = exe.split('/')[-1]
            if cmdline and cmdline[0] and cmdline[0] not in command:
                command = cmdline[0].split('/')[-1]
            command = command[:20].ljust(20) if command else "N/A".ljust(20)
            # --- End Fix ---
            processes.append([pid, user, nice, virt, res, shr, status, cpu_percent, memory_percent, cpu_time, command])
        except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError) as e:
            processes.append([str(proc.info.get('pid', 'N/A')).ljust(8), "N/A".ljust(15), "N/A".ljust(5), "N/A".ljust(10), "N/A".ljust(10), "N/A".ljust(10), "N/A".ljust(8), "0.0".ljust(6), "0.0".ljust(6), "N/A".ljust(8), "N/A".ljust(20)])
    return processes

def draw_table(window, title, connections, start_y, start_x, width, start_idx, max_lines, active):
    title_color = curses.color_pair(2) | curses.A_BOLD if active else curses.color_pair(2)
    header_color = curses.color_pair(4)
    text_color = curses.color_pair(1)

    start_x += 1  # Add a left margin

    window.addstr(start_y, start_x, title, title_color)
    headers = ["Local Address", "Remote Address", "Status", "PID", "Program", "User", "Data Sent", "Data Recv"]
    headers_text = ' '.join(f'{header:25}' if i < 2 else f'{header:12}' if i == 2 else f'{header:8}' if i == 3 else f'{header:20}' if i == 4 else f'{header:15}' if i == 5 else f'{header:10}' for i, header in enumerate(headers))
    window.addstr(start_y + 1, start_x, headers_text, header_color)

    for i, conn in enumerate(connections[start_idx:start_idx + max_lines]):
        window.addstr(start_y + 2 + i, start_x, ' '.join(f'{str(field):25}' if j < 2 else f'{str(field):12}' if j == 2 else f'{str(field):8}' if j == 3 else f'{str(field):20}' if j == 4 else f'{str(field):15}' if j == 5 else f'{str(field):10}' for j, field in enumerate(conn)), text_color)

def draw_process_table(window, title, processes, start_y, start_x, start_idx, max_lines, selected_idx):
    title_color = curses.color_pair(2) | curses.A_BOLD
    header_color = curses.color_pair(4)
    text_color = curses.color_pair(1)
    selected_color = curses.color_pair(2) | curses.A_REVERSE

    start_x += 1  # Add a left margin

    max_y, max_x = window.getmaxyx()
    margin = 15
    table_width = max_x - margin

    window.addstr(start_y, start_x, title, title_color)
    headers = ["PID", "USER", "NI", "VIRT", "RES", "SHR", "STATUS", "CPU%", "MEM%", "TIME+", "Command"]
    headers_text = f'{headers[0]:<8}{headers[1]:<15}{headers[2]:<5}{headers[3]:<10}{headers[4]:<10}{headers[5]:<10}{headers[6]:<8} {headers[7]:<6}{headers[8]:<6}{headers[9]:<8} {headers[10]:<20}'
    window.addstr(start_y + 1, start_x, headers_text[:table_width], header_color)

    for i, proc in enumerate(processes[start_idx:start_idx + max_lines]):
        color = selected_color if i + start_idx == selected_idx else text_color
        window.addstr(start_y + 2 + i, start_x, f'{proc[0]:<8}{proc[1]:<15}{proc[2]:<5}{proc[3]:<10}{proc[4]:<10}{proc[5]:<10}{proc[6]:<8} {proc[7]:<6}{proc[8]:<6}{proc[9]:<8} {proc[10]:<20}'[:table_width], color)

def splash_screen(stdscr, selected=0):
    curses.start_color()
    curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLUE)
    curses.curs_set(0)
    max_y, max_x = stdscr.getmaxyx()

    min_width = 135  # Adjust the minimum width here

    while max_x < min_width:
        stdscr.clear()
        stdscr.addstr(0, 0, f"Please resize your window to at least {min_width} columns.", curses.color_pair(2) | curses.A_BOLD)
        stdscr.refresh()
        time.sleep(0.5)
        max_y, max_x = stdscr.getmaxyx()

    stdscr.bkgd(curses.color_pair(1))
    stdscr.clear()
    stdscr.refresh()

    title = "NetScope 2.0"
    prompt = "Select an option:"
    options = ["1. System Info", "2. Established Connections", "3. Listening Connections", "4. Both", "5. Running Processes", "6. Exit"]
    title_x = max_x // 2 - len(title) // 2
    ascii_start_y = max_y // 4
    options_y_start = max_y // 2 + 4
    prompt_x = max_x // 2 - len(prompt) // 2
    prompt_y = options_y_start - 2

    stdscr.addstr(1, title_x, title, curses.color_pair(2) | curses.A_BOLD)
    for i, line in enumerate(netscope_ascii_art):
        stdscr.addstr(ascii_start_y + i, (max_x - len(line)) // 2, line, curses.color_pair(2) | curses.A_BOLD)

    stdscr.addstr(prompt_y, prompt_x, prompt, curses.color_pair(2))

    for idx, option in enumerate(options):
        option_x = max_x // 2 - len(option) // 2
        if idx == selected:
            stdscr.addstr(options_y_start + idx, option_x, option, curses.color_pair(2) | curses.A_BOLD)
        else:
            stdscr.addstr(options_y_start + idx, option_x, option, curses.color_pair(2))

    # Draw border with app name and author
    stdscr.border(0)
    stdscr.addstr(0, 2, "NetScope 2.0", curses.color_pair(2) | curses.A_BOLD)
    stdscr.addstr(1, 2, "Written by Yodabytz", curses.color_pair(2))

    stdscr.refresh()

    while True:
        key = stdscr.getch()
        if key in [curses.KEY_ENTER, ord('\n')]:
            return selected  # Return only the selected option index
        elif key == curses.KEY_UP:
            selected = (selected - 1) % len(options)
        elif key == curses.KEY_DOWN:
            selected = (selected + 1) % len(options)
        elif key in [ord('1'), ord('2'), ord('3'), ord('4'), ord('5'), ord('6')]:
            selected = int(chr(key)) - 1
            return selected
        elif key == ord('q'):
            return 5  # Return the index for the "Exit" option

        for idx, option in enumerate(options):
            option_x = max_x // 2 - len(option) // 2
            if idx == selected:
                stdscr.addstr(options_y_start + idx, option_x, option, curses.color_pair(2) | curses.A_BOLD)
            else:
                stdscr.addstr(options_y_start + idx, option_x, option, curses.color_pair(2))

        stdscr.refresh()

def draw_system_info(window):
    # Determine if we are on macOS
    is_macos = platform.system() == "Darwin"

    # System info details
    if is_macos:
        os_name = "macOS"
        os_version = platform.mac_ver()[0]
    else:
        os_name = platform.system()
        os_version = platform.release()

    # Package counts
    packages_dpkg = 0
    if not is_macos:
        try:
            packages_dpkg = len(subprocess.check_output(['dpkg-query', '-f', '${binary:Package}\n', '-W']).decode('utf-8').splitlines())
        except (subprocess.CalledProcessError, FileNotFoundError):
            packages_dpkg = 0

    try:
        # If sqlite3 fails, fallback to counting installed formulae directly from Homebrew directories
        brew_prefix = subprocess.check_output(['brew', '--prefix'], text=True).strip()
        brew_formulae_dir = os.path.join(brew_prefix, 'Cellar')
        packages_brew = len(os.listdir(brew_formulae_dir))
    except (subprocess.CalledProcessError, FileNotFoundError):
        packages_brew = 0

    # GPU information
    if is_macos:
        try:
            gpu_info_raw = subprocess.check_output("system_profiler SPDisplaysDataType", shell=True).decode('utf-8').strip().split('\n')
            # Extract the GPU information based on the typical output structure
            gpu_info = [line.split(': ', 1)[1] for line in gpu_info_raw if 'Chipset Model' in line or 'Vendor' in line]
            gpu_text = ', '.join(gpu_info)
        except subprocess.CalledProcessError:
            gpu_text = "N/A"
    else:
        try:
            gpu_info_raw = subprocess.check_output("lspci | grep -i 'vga\\|3d\\|2d'", shell=True).decode('utf-8').strip().split('\n')
            gpu_info = [line.split(':', 2)[-1].strip() for line in gpu_info_raw]  # Extracting relevant part of GPU info
            gpu_text = gpu_info[0] if gpu_info else "N/A"
        except subprocess.CalledProcessError:
            gpu_text = "N/A"

    # Truncate GPU info to a maximum of 55 characters
    gpu_text = gpu_text[:55]  # Ensure only up to 55 characters are displayed

    # Get the current screen resolution
    if is_macos:
        try:
            screen_info = subprocess.check_output("system_profiler SPDisplaysDataType | grep Resolution", shell=True).decode('utf-8').strip()
            screen_resolution = re.search(r"Resolution: (\d+ x \d+)", screen_info).group(1)
        except Exception:
            screen_resolution = "N/A"
    else:
        try:
            if os.getenv("DISPLAY"):
                screen_info = subprocess.check_output("xdpyinfo | grep dimensions", shell=True).decode('utf-8').strip()
                screen_resolution = re.search(r"dimensions:\s+(\d+x\d+)", screen_info).group(1)
            else:
                screen_resolution = "N/A"  # Suppress error if DISPLAY is not set
        except Exception:
            screen_resolution = "N/A"

    # Get CPU information
    if is_macos:
        try:
            cpu_info = subprocess.check_output("sysctl -n machdep.cpu.brand_string", shell=True).decode('utf-8').strip()
        except subprocess.CalledProcessError:
            cpu_info = "N/A"
    else:
        try:
            cpu_info = subprocess.check_output("lscpu | grep 'Model name'", shell=True).decode('utf-8').split(':')[1].strip()
        except subprocess.CalledProcessError:
            cpu_info = "N/A"

    # Get uptime
    if is_macos:
        try:
            uptime_seconds = float(subprocess.check_output("sysctl -n kern.boottime | awk '{print $4}' | sed 's/,//'", shell=True))
            uptime_str = time.strftime('%j days, %H hours, %M mins', time.gmtime(time.time() - uptime_seconds))
        except Exception:
            uptime_str = "N/A"
    else:
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.readline().split()[0])
                uptime_str = time.strftime('%j days, %H hours, %M mins', time.gmtime(uptime_seconds))
        except Exception:
            uptime_str = "N/A"

    try:
        shell_info = os.getenv("SHELL", "N/A").split('/')[-1]
        shell_version = subprocess.check_output([shell_info, "--version"], text=True).strip().split('\n')[0]
    except (subprocess.CalledProcessError, IndexError):
        shell_version = "N/A"

    try:
        terminal = os.ttyname(sys.stdin.fileno())
    except Exception:
        terminal = "N/A"

    memory = psutil.virtual_memory()
    memory_used = format_size(memory.used)
    memory_total = format_size(memory.total)

    # Get swap information
    swap = psutil.swap_memory()
    swap_used = format_size(swap.used)
    swap_total = format_size(swap.total)

    # Get disk information
    disk_usage = psutil.disk_usage('/')
    disk_used = format_size(disk_usage.used)
    disk_total = format_size(disk_usage.total)

    # Get local IP address and its interface
    local_ip = "N/A"
    main_interface = "N/A"
    try:
        # Check for the active network interfaces
        for interface, addrs in psutil.net_if_addrs().items():
            for address in addrs:
                if address.family == socket.AF_INET and not address.address.startswith("127."):
                    local_ip = address.address
                    main_interface = interface
                    break
            if local_ip != "N/A":
                break
    except KeyError:
        pass

    # Get locale
    try:
        locale_info = subprocess.check_output("locale | grep LANG=", shell=True).decode('utf-8').strip().split('=')[-1]
    except subprocess.CalledProcessError:
        locale_info = "N/A"

    # Select appropriate ASCII art for system
    if platform.system() == "Linux":
        try:
            distro_name = subprocess.check_output(['lsb_release', '-si'], text=True).strip()
            ascii_art = ascii_art_dict.get(distro_name, ascii_art_dict.get("Debian", ["Distro ASCII art not found."]))
        except subprocess.CalledProcessError:
            ascii_art = ascii_art_dict.get("Debian", ["Distro ASCII art not found."])
    else:
        ascii_art = ascii_art_dict.get(platform.system(), ["Distro ASCII art not found."])

    system_info = [
        ("OS:", f"{os_name} {os_version} {platform.machine()}"),
        ("Host:", platform.node()),
        ("Kernel:", platform.release()),
        ("Uptime:", uptime_str),
        ("Packages:", f"{packages_dpkg} (dpkg), {packages_brew} (brew)"),
        ("Shell:", shell_version),
        ("Resolution:", screen_resolution),
        ("Terminal:", terminal),
        ("CPU:", cpu_info),
        ("GPU:", gpu_text),  # Truncated GPU info
        ("Memory:", f"{memory_used} / {memory_total}"),
        ("Swap:", f"{swap_used} / {swap_total}"),
        ("Disk (/):", f"{disk_used} / {disk_total}"),
        (f"Local IP ({main_interface}):", local_ip),
        ("Locale:", locale_info),
    ]

    max_y, max_x = window.getmaxyx()
    ascii_start_y = max_y // 4

    # Align system info to the right of the ASCII art
    ascii_art_width = max(len(line) for line in ascii_art)
    info_start_x = ascii_art_width + 5  # Add slight margin between the separator and system info

    for i, line in enumerate(ascii_art):
        if i >= max_y - ascii_start_y - 1:
            break
        window.addstr(ascii_start_y + i, 2, line, curses.color_pair(2))

    # Draw vertical line separator
    separator_x = ascii_art_width + 3  # Separator is just to the right of the ASCII art
    for i in range(ascii_start_y, ascii_start_y + len(ascii_art)):
        window.addch(i, separator_x, '|', curses.color_pair(2))  # No bold

    for i, (key, value) in enumerate(system_info):
        if i >= max_y - ascii_start_y - 1:
            break
        if key:  # If there is a key, print it
            window.addstr(ascii_start_y + i, info_start_x, f"{key:12} ", curses.color_pair(2) | curses.A_BOLD)
        window.addstr(ascii_start_y + i, info_start_x + len(key) + 2 if key else info_start_x, value, curses.color_pair(1))

def search_process(stdscr, processes, last_search_term=None, last_match_index=-1):
    curses.echo()
    max_y, max_x = stdscr.getmaxyx()
    search_win = curses.newwin(3, max_x - 4, max_y // 2 - 1, 2)
    search_win.bkgd(curses.color_pair(1))
    search_win.border(0)
    search_win.addstr(1, 2, "Search Process (use * for wildcard): ", curses.color_pair(2) | curses.A_BOLD)
    stdscr.refresh()
    search_win.refresh()
    if last_search_term is None:
        search_term = search_win.getstr(1, 38).decode('utf-8')  # Updated to start after space
    else:
        search_win.addstr(1, 38, last_search_term)
        search_win.refresh()
        search_term = last_search_term
    curses.noecho()

    matches = []
    if "*" in search_term:
        pattern = re.compile(re.escape(search_term).replace(r'\*', '.*'), re.IGNORECASE)
        matches = [idx for idx, proc in enumerate(processes) if pattern.search(proc[10].strip())]
    else:
        matches = [idx for idx, proc in enumerate(processes) if proc[10].strip().lower() == search_term.lower()]

    if not matches:
        search_win.addstr(1, 38 + len(search_term), " - App Not Found", curses.color_pair(2))
        search_win.refresh()
        time.sleep(1)
        return -1, search_term

    if last_match_index >= 0 and last_match_index in matches:
        next_match_index = matches.index(last_match_index) + 1
        if next_match_index < len(matches):
            return matches[next_match_index], search_term
        else:
            search_win.addstr(1, 38 + len(search_term), " - Starting from beginning", curses.color_pair(2))
            search_win.refresh()
            time.sleep(1)
            return matches[0], search_term
    else:
        return matches[0], search_term

def show_help_popup(stdscr, help_lines):
    height, width = stdscr.getmaxyx()
    # Calculate popup dimensions
    popup_height = len(help_lines) + 4  # extra space for border
    popup_width = max(len(line) for line in help_lines) + 4
    popup_y = (height - popup_height) // 2
    popup_x = (width - popup_width) // 2
    # Create a new window for the help popup
    help_win = curses.newwin(popup_height, popup_width, popup_y, popup_x)
    help_win.bkgd(curses.color_pair(1))
    help_win.border()
    # Add help text to the popup window
    for idx, line in enumerate(help_lines):
        help_win.addstr(idx + 1, 2, line, curses.color_pair(2))
    help_win.refresh()
    help_win.getch()
    help_win.clear()
    stdscr.refresh()

def confirm_kill_process(stdscr, process_name, pid):
    height, width = stdscr.getmaxyx()
    prompt = f"Are you sure you want to kill {process_name} (PID {pid})? (y/n)"
    prompt_width = len(prompt) + 4
    prompt_height = 5
    prompt_y = (height - prompt_height) // 2
    prompt_x = (width - prompt_width) // 2

    # Create a new window for the confirmation prompt
    confirm_win = curses.newwin(prompt_height, prompt_width, prompt_y, prompt_x)
    confirm_win.bkgd(curses.color_pair(1))
    confirm_win.border()
    confirm_win.addstr(2, 2, prompt, curses.color_pair(2))
    confirm_win.refresh()

    while True:
        key = confirm_win.getch()
        if key in [ord('y'), ord('Y')]:
            confirm_win.clear()
            confirm_win.refresh()
            return True
        elif key in [ord('n'), ord('N'), 27]:  # 27 is ESC key
            confirm_win.clear()
            confirm_win.refresh()
            return False

def main_screen(stdscr, selected_option, update_interval):
    curses.start_color()
    curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLUE)
    curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(4, curses.COLOR_YELLOW, curses.COLOR_BLACK)

    est_start_idx = 0
    listen_start_idx = 0
    proc_start_idx = 0
    proc_selected_idx = 0
    active_section = "ESTABLISHED"
    stdscr.timeout(update_interval * 1000)  # Set timeout based on update_interval

    established_connections = []
    listening_connections = []
    processes = []
    search_term = None

    # Adjusted minimum width for Both screen
    min_width = 135

    cpu_percent_index = 7
    memory_percent_index = 8
    sort_key_index = cpu_percent_index  # Default to sort by CPU usage

    def fetch_connections():
        return get_connections('ESTABLISHED'), get_connections('LISTEN')

    def fetch_processes():
        processes = get_all_processes()
        processes.sort(key=lambda x: float(x[sort_key_index].strip()), reverse=True)
        return processes

    def update_display(established_connections, listening_connections, processes):
        max_y, max_x = stdscr.getmaxyx()

        if max_x < min_width:
            stdscr.clear()
            stdscr.addstr(0, 0, f"Please resize your window to at least {min_width} columns.", curses.color_pair(2) | curses.A_BOLD)
            stdscr.refresh()
            return

        max_lines = max_y - 8

        buffer = curses.newpad(max_y, max_x)
        buffer.bkgd(curses.color_pair(1))
        buffer.erase()

        io_data = {}
        for conn in established_connections + listening_connections:
            pid = conn[3].strip()
            if pid and pid.isdigit():
                pid = int(pid)
                if pid not in io_data:
                    if platform.system() != "Darwin":
                        try:
                            p = psutil.Process(pid)
                            io_counters = p.io_counters()
                            io_data[pid] = {'sent': io_counters.write_bytes, 'recv': io_counters.read_bytes}
                        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
                            io_data[pid] = {'sent': 0, 'recv': 0}

        buffer.erase()
        buffer.bkgd(curses.color_pair(1))
        buffer.border(0)

        buffer.addstr(0, 2, "NetScope 2.0", curses.color_pair(2) | curses.A_BOLD)
        buffer.addstr(1, 2, "Written by Yodabytz", curses.color_pair(2))

        if selected_option == 0:
            draw_system_info(buffer)
        elif selected_option == 1:
            draw_table(buffer, "Established Connections", [
                conn + [format_size(io_data.get(int(conn[3].strip()), {}).get('sent', 0)), format_size(io_data.get(int(conn[3].strip()), {}).get('recv', 0))]
                for conn in established_connections if conn[3].strip().isdigit()
            ], 3, 1, max_x - 2, est_start_idx, max_lines, active_section == "ESTABLISHED")
        elif selected_option == 2:
            draw_table(buffer, "Listening Connections", [
                conn + [format_size(io_data.get(int(conn[3].strip()), {}).get('sent', 0)), format_size(io_data.get(int(conn[3].strip()), {}).get('recv', 0))]
                for conn in listening_connections if conn[3].strip().isdigit()
            ], 3, 1, max_x - 2, listen_start_idx, max_lines, active_section == "LISTEN")
        elif selected_option == 3:
            draw_table(buffer, "Established Connections", [
                conn + [format_size(io_data.get(int(conn[3].strip()), {}).get('sent', 0)), format_size(io_data.get(int(conn[3].strip()), {}).get('recv', 0))]
                for conn in established_connections if conn[3].strip().isdigit()
            ], 3, 1, max_x - 2, est_start_idx, max_lines // 2, active_section == "ESTABLISHED")
            draw_table(buffer, "Listening Connections", [
                conn + [format_size(io_data.get(int(conn[3].strip()), {}).get('sent', 0)), format_size(io_data.get(int(conn[3].strip()), {}).get('recv', 0))]
                for conn in listening_connections if conn[3].strip().isdigit()
            ], max_lines // 2 + 6, 1, max_x - 2, listen_start_idx, max_lines // 2, active_section == "LISTEN")
            # No helpline at the bottom
        elif selected_option == 4:
            draw_process_table(buffer, "Running Processes", processes, 3, 1, proc_start_idx, max_lines, proc_selected_idx)
            # Removed helpline at the bottom

        buffer.refresh(0, 0, 0, 0, max_y - 1, max_x - 1)

    while True:
        try:
            if selected_option == 5:
                break

            if selected_option in [1, 2, 3]:
                established_connections, listening_connections = fetch_connections()
            if selected_option == 4:
                processes = fetch_processes()

            update_display(established_connections, listening_connections, processes)
            stdscr.refresh()

            max_y, max_x = stdscr.getmaxyx()
            max_lines = max_y - 8

            key = stdscr.getch()
            if selected_option == 1:
                if key == curses.KEY_UP:
                    est_start_idx = max(est_start_idx - 1, 0)
                elif key == curses.KEY_DOWN:
                    est_start_idx = min(est_start_idx + 1, len(established_connections) - max_lines)
                elif key == ord('q'):
                    return 5  # To quit the program
                elif key in [curses.KEY_BACKSPACE, curses.KEY_LEFT, 127]:
                    return selected_option  # Navigate back to the main menu
            elif selected_option == 2:
                if key == curses.KEY_UP:
                    listen_start_idx = max(listen_start_idx - 1, 0)
                elif key == curses.KEY_DOWN:
                    listen_start_idx = min(listen_start_idx + 1, len(listening_connections) - max_lines)
                elif key == ord('q'):
                    return 5  # To quit the program
                elif key in [curses.KEY_BACKSPACE, curses.KEY_LEFT, 127]:
                    return selected_option  # Navigate back to the main menu
            elif selected_option == 4:
                if key == curses.KEY_UP:
                    proc_selected_idx = max(proc_selected_idx - 1, 0)
                    if proc_selected_idx < proc_start_idx:
                        proc_start_idx = proc_selected_idx
                elif key == curses.KEY_DOWN:
                    proc_selected_idx = min(proc_selected_idx + 1, len(processes) - 1)
                    if proc_selected_idx >= proc_start_idx + max_lines:
                        proc_start_idx = proc_selected_idx - max_lines + 1
                elif key == ord('k'):
                    pid = int(processes[proc_selected_idx][0].strip())
                    process_name = processes[proc_selected_idx][10].strip()
                    confirmed = confirm_kill_process(stdscr, process_name, pid)
                    if confirmed:
                        try:
                            psutil.Process(pid).terminate()
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                elif key == ord('s'):
                    proc_selected_idx, search_term = search_process(stdscr, processes)
                    if proc_selected_idx != -1:
                        proc_start_idx = max(0, proc_selected_idx - max_lines // 2)  # Center the found process in the view
                elif key == ord('n') and search_term:
                    proc_selected_idx, search_term = search_process(stdscr, processes, search_term, proc_selected_idx)
                    if proc_selected_idx != -1:
                        proc_start_idx = max(0, proc_selected_idx - max_lines // 2)  # Center the found process in the view
                elif key == ord('m'):
                    sort_key_index = memory_percent_index
                elif key == ord('c'):
                    sort_key_index = cpu_percent_index
                elif key == ord('?'):
                    # Help text specific to the Running Processes screen
                    help_lines = [
                        " Running Processes Screen Help ",
                        "",
                        " Key Bindings:",
                        " Up/Down Arrows or k/j: Scroll through the list of processes.",
                        " k - Kill the selected process (with confirmation)",
                        " s - Search for a process",
                        " n - Find next match in search",
                        " c - Sort processes by CPU usage",
                        " m - Sort processes by Memory usage",
                        " ? - Show this help menu",
                        " Left Arrow or Backspace: Return to the main menu",
                        " q - Quit the application",
                    ]
                    show_help_popup(stdscr, help_lines)
                elif key == ord('q'):
                    return 5  # To quit the program
                elif key in [curses.KEY_BACKSPACE, curses.KEY_LEFT, 127]:
                    return selected_option  # Navigate back to the main menu
            elif selected_option == 3:
                if active_section == "ESTABLISHED":
                    if key == curses.KEY_UP:
                        est_start_idx = max(est_start_idx - 1, 0)
                    elif key == curses.KEY_DOWN:
                        est_start_idx = min(est_start_idx + 1, len(established_connections) - max_lines // 2)
                    elif key == ord('\t'):
                        active_section = "LISTEN"
                    elif key == ord('?'):
                        # Help text specific to the 'Both' screen
                        help_lines = [
                            " Both Connections Screen Help ",
                            "",
                            " Key Bindings:",
                            " Tab: Switch between Established and Listening sections",
                            " Up/Down Arrows or k/j: Scroll through the connections in the active section",
                            " ? - Show this help menu",
                            " Left Arrow or Backspace: Return to the main menu",
                            " q - Quit the application",
                        ]
                        show_help_popup(stdscr, help_lines)
                    elif key == ord('q'):
                        return 5  # To quit the program
                    elif key in [curses.KEY_BACKSPACE, curses.KEY_LEFT, 127]:
                        return selected_option  # Navigate back to the main menu
                elif active_section == "LISTEN":
                    if key == curses.KEY_UP:
                        listen_start_idx = max(listen_start_idx - 1, 0)
                    elif key == curses.KEY_DOWN:
                        listen_start_idx = min(listen_start_idx + 1, len(listening_connections) - max_lines // 2)
                    elif key == ord('\t'):
                        active_section = "ESTABLISHED"
                    elif key == ord('?'):
                        # Help text specific to the 'Both' screen
                        help_lines = [
                            " Both Connections Screen Help ",
                            "",
                            " Key Bindings:",
                            " Tab: Switch between Established and Listening sections",
                            " Up/Down Arrows or k/j: Scroll through the connections in the active section",
                            " ? - Show this help menu",
                            " Left Arrow or Backspace: Return to the main menu",
                            " q - Quit the application",
                        ]
                        show_help_popup(stdscr, help_lines)
                    elif key == ord('q'):
                        return 5  # To quit the program
                    elif key in [curses.KEY_BACKSPACE, curses.KEY_LEFT, 127]:
                        return selected_option  # Navigate back to the main menu
            elif key in [curses.KEY_BACKSPACE, curses.KEY_LEFT, 127]:
                return selected_option  # Navigate back to the main menu
            elif key == ord('q'):
                return 5  # To quit the program
        except curses.error:
            pass
        except Exception as e:
            stdscr.addstr(0, 0, f"Error: {e}", curses.color_pair(2) | curses.A_BOLD)
            stdscr.refresh()
            time.sleep(1)

def main(stdscr, update_interval):
    selected_option = 0
    while True:
        selected_option = splash_screen(stdscr, selected_option)
        if selected_option == 5:
            break
        selected_option = main_screen(stdscr, selected_option, update_interval)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"NetScope {VERSION} - Network and Process Monitoring Tool", add_help=False)
    parser.add_argument("-d", type=int, default=3, help="Set the update interval in seconds (default is 3 seconds)")
    parser.add_argument("-h", action="store_true", help="Show this help message")
    parser.add_argument("-v", action="store_true", help="Show version information")

    args = parser.parse_args()

    if args.h:
        show_help()
        sys.exit(0)

    if args.v:
        print(f"NetScope {VERSION}")
        sys.exit(0)

    os.environ.setdefault('ESCDELAY', '25')
    curses.wrapper(main, args.d)
