#!/usr/bin/python3

import psutil
import curses
import time
import os

def get_process_name(pid):
    try:
        process = psutil.Process(pid)
        return process.name()
    except psutil.NoSuchProcess:
        return None

def get_process_user(pid):
    try:
        process = psutil.Process(pid)
        return process.username()
    except psutil.NoSuchProcess:
        return None

def format_size(bytes):
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
            pid = str(conn.pid).ljust(8)
            program = get_process_name(conn.pid).ljust(20) if conn.pid else "".ljust(20)
            user = get_process_user(conn.pid).ljust(15) if conn.pid else "".ljust(15)
            connections.append([laddr, raddr, status, pid, program, user])
    return connections

def draw_table(window, title, connections, start_y, start_x, width, start_idx, max_lines, active):
    title_color = curses.color_pair(2) | curses.A_BOLD if active else curses.color_pair(2)
    header_color = curses.color_pair(4)
    text_color = curses.color_pair(1)

    window.addstr(start_y, start_x, title, title_color)
    headers = ["Local Address", "Remote Address", "Status", "PID", "Program", "User", "Data Sent", "Data Recv"]
    window.addstr(start_y + 1, start_x, ' '.join(f'{header:25}' if i < 2 else f'{header:12}' if i == 2 else f'{header:8}' if i == 3 else f'{header:20}' if i == 4 else f'{header:15}' if i == 5 else f'{header:10}' for i, header in enumerate(headers)), header_color)
    
    for i, conn in enumerate(connections[start_idx:start_idx + max_lines]):
        window.addstr(start_y + 2 + i, start_x, ' '.join(f'{str(field):25}' if j < 2 else f'{str(field):12}' if j == 2 else f'{str(field):8}' if j == 3 else f'{str(field):20}' if j == 4 else f'{str(field):15}' if j == 5 else f'{str(field):10}' for j, field in enumerate(conn)), text_color)

def main(stdscr):
    # Check if the terminal supports color
    if not curses.has_colors():
        raise Exception("Your terminal does not support color. Please use a compatible terminal.")

    curses.start_color()
    
    # Initialize color pairs
    curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)  # Normal text color on blue background
    curses.init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLUE)  # Title color on blue background
    curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLUE)  # Normal text color on blue background
    curses.init_pair(4, curses.COLOR_YELLOW, curses.COLOR_BLACK)  # Header text color on black background

    est_start_idx = 0
    listen_start_idx = 0
    active_section = "ESTABLISHED"
    stdscr.timeout(100)  # Refresh every 100 ms for smoother updates

    min_width = 100  # Adjusted minimum width required to display the content properly

    established_connections = []
    listening_connections = []

    def update_display():
        nonlocal established_connections, listening_connections

        max_y, max_x = stdscr.getmaxyx()

        if max_x < min_width:
            stdscr.clear()
            stdscr.addstr(0, 0, f"Please resize your window to at least {min_width} columns.", curses.color_pair(2) | curses.A_BOLD)
            stdscr.refresh()
            return

        max_lines = (max_y - 8) // 2  # Maximum number of lines per table, adjusted for header and title

        buffer = curses.newpad(max_y, max_x)
        buffer.bkgd(' ', curses.color_pair(1))
        buffer.border(0)

        established_connections = get_connections('ESTABLISHED')
        listening_connections = get_connections('LISTEN')

        io_data = {}
        for conn in established_connections + listening_connections:
            pid = int(conn[3].strip())
            if pid:
                try:
                    p = psutil.Process(pid)
                    io_counters = p.io_counters()
                    io_data[pid] = {'sent': io_counters.write_bytes, 'recv': io_counters.read_bytes}
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    io_data[pid] = {'sent': 0, 'recv': 0}

        buffer.erase()
        buffer.bkgd(' ', curses.color_pair(1))
        buffer.border(0)

        # Display program name and author
        buffer.addstr(0, 2, "NetScope 1.0", curses.color_pair(2) | curses.A_BOLD)
        buffer.addstr(1, 2, "Written by Yodabytz", curses.color_pair(2))

        # Draw tables
        draw_table(buffer, "Established Connections", [
            conn + [format_size(io_data.get(int(conn[3].strip()), {}).get('sent', 0)), format_size(io_data.get(int(conn[3].strip()), {}).get('recv', 0))]
            for conn in established_connections
        ], 3, 1, max_x - 2, est_start_idx, max_lines, active_section == "ESTABLISHED")

        draw_table(buffer, "Listening Connections", [
            conn + [format_size(io_data.get(int(conn[3].strip()), {}).get('sent', 0)), format_size(io_data.get(int(conn[3].strip()), {}).get('recv', 0))]
            for conn in listening_connections
        ], max_lines + 6, 1, max_x - 2, listen_start_idx, max_lines, active_section == "LISTEN")

        buffer.refresh(0, 0, 0, 0, max_y - 1, max_x - 1)
    
    while True:
        try:
            update_display()
            stdscr.refresh()

            max_y, max_x = stdscr.getmaxyx()
            max_lines = (max_y - 8) // 2

            key = stdscr.getch()
            if key == curses.KEY_UP and active_section == "ESTABLISHED":
                est_start_idx = max(est_start_idx - 1, 0)
            elif key == curses.KEY_DOWN and active_section == "ESTABLISHED":
                est_start_idx = min(est_start_idx + 1, len(established_connections) - max_lines)
            elif key == curses.KEY_UP and active_section == "LISTEN":
                listen_start_idx = max(listen_start_idx - 1, 0)
            elif key == curses.KEY_DOWN and active_section == "LISTEN":
                listen_start_idx = min(listen_start_idx + 1, len(listening_connections) - max_lines)
            elif key == ord('\t'):  # Tab key to switch sections
                active_section = "LISTEN" if active_section == "ESTABLISHED" else "ESTABLISHED"
            elif key == ord('q'):
                break
        except curses.error:
            pass  # Ignore resize errors
        except Exception as e:
            stdscr.addstr(0, 0, f"Error: {e}", curses.color_pair(2) | curses.A_BOLD)
            stdscr.refresh()
            time.sleep(1)

if __name__ == "__main__":
    os.environ.setdefault('ESCDELAY', '25')  # To improve response to the escape key
    curses.wrapper(main)
