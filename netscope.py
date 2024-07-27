#!/usr/bin/python3

import psutil
import curses
import time
import os
import platform

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

def get_all_processes():
    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'username', 'nice', 'memory_info', 'memory_percent', 'cpu_percent', 'cpu_times', 'status']):
        try:
            pid = str(proc.info['pid']).ljust(8)
            user = (proc.info['username'][:15] if proc.info['username'] else "N/A").ljust(15)  # Limit the user name length to 15 characters
            nice = str(proc.info['nice']).ljust(5) if proc.info['nice'] is not None else "N/A".ljust(5)
            memory_info = proc.info['memory_info']
            memory_percent = f"{proc.info['memory_percent']:.1f}".ljust(6) if proc.info['memory_percent'] else "N/A".ljust(6)
            cpu_percent = f"{proc.info['cpu_percent']:.1f}".ljust(6) if proc.info['cpu_percent'] is not None else "0.0".ljust(6)
            status = proc.info['status'].ljust(8) if proc.info['status'] else "N/A".ljust(8)
            virt = format_size(memory_info.vms).ljust(10) if memory_info else "N/A".ljust(10)
            res = format_size(memory_info.rss).ljust(10) if memory_info else "N/A".ljust(10)

            # SHR field fix for Mac
            if platform.system() == "Darwin":
                shr = "N/A".ljust(10)  # macOS does not provide shared memory info
            else:
                shr = format_size(getattr(memory_info, 'shared', None)).ljust(10) if memory_info else "N/A".ljust(10)

            cpu_time = f"{proc.info['cpu_times'].user:.2f}".ljust(8) if proc.info['cpu_times'] else "N/A".ljust(8)
            command = proc.info['name'][:20].ljust(20) if proc.info['name'] else "N/A".ljust(20)  # Limit command length to 20 characters
            processes.append([pid, user, nice, virt, res, shr, status, cpu_percent, memory_percent, cpu_time, command])
        except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError) as e:
            processes.append([str(proc.info.get('pid', 'N/A')).ljust(8), "N/A".ljust(15), "N/A".ljust(5), "N/A".ljust(10), "N/A".ljust(10), "N/A".ljust(10), "N/A".ljust(8), "0.0".ljust(6), "N/A".ljust(6), "N/A".ljust(8), "N/A".ljust(20)])
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
    ascii_art = [
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
    prompt = "Select an option:"
    options = ["1. Established Connections", "2. Listening Connections", "3. Both", "4. Running Processes", "5. Exit"]
    title_x = max_x // 2 - len(title) // 2
    ascii_start_y = max_y // 4
    options_y_start = max_y // 2 + 4
    prompt_x = max_x // 2 - len(prompt) // 2
    prompt_y = options_y_start - 2

    stdscr.addstr(1, title_x, title, curses.color_pair(2) | curses.A_BOLD)
    for i, line in enumerate(ascii_art):
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
        elif key in [ord('1'), ord('2'), ord('3'), ord('4'), ord('5')]:
            selected = int(chr(key)) - 1
            return selected
        elif key == ord('q'):
            return 4  # Return the index for the "Exit" option

        for idx, option in enumerate(options):
            option_x = max_x // 2 - len(option) // 2
            if idx == selected:
                stdscr.addstr(options_y_start + idx, option_x, option, curses.color_pair(2) | curses.A_BOLD)
            else:
                stdscr.addstr(options_y_start + idx, option_x, option, curses.color_pair(2))

        stdscr.refresh()

def main_screen(stdscr, selected_option):
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
    stdscr.timeout(1000)  # Increase timeout to reduce CPU usage

    established_connections = []
    listening_connections = []
    processes = []

    # Adjusted minimum width for Both screen
    min_width = 135

    def fetch_connections():
        return get_connections('ESTABLISHED'), get_connections('LISTEN')

    def fetch_processes():
        return get_all_processes()

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
            draw_table(buffer, "Established Connections", [
                conn + [format_size(io_data.get(int(conn[3].strip()), {}).get('sent', 0)), format_size(io_data.get(int(conn[3].strip()), {}).get('recv', 0))]
                for conn in established_connections if conn[3].strip().isdigit()
            ], 3, 1, max_x - 2, est_start_idx, max_lines, active_section == "ESTABLISHED")
        elif selected_option == 1:
            draw_table(buffer, "Listening Connections", [
                conn + [format_size(io_data.get(int(conn[3].strip()), {}).get('sent', 0)), format_size(io_data.get(int(conn[3].strip()), {}).get('recv', 0))]
                for conn in listening_connections if conn[3].strip().isdigit()
            ], 3, 1, max_x - 2, listen_start_idx, max_lines, active_section == "LISTEN")
        elif selected_option == 2:
            draw_table(buffer, "Established Connections", [
                conn + [format_size(io_data.get(int(conn[3].strip()), {}).get('sent', 0)), format_size(io_data.get(int(conn[3].strip()), {}).get('recv', 0))]
                for conn in established_connections if conn[3].strip().isdigit()
            ], 3, 1, max_x - 2, est_start_idx, max_lines // 2, active_section == "ESTABLISHED")
            draw_table(buffer, "Listening Connections", [
                conn + [format_size(io_data.get(int(conn[3].strip()), {}).get('sent', 0)), format_size(io_data.get(int(conn[3].strip()), {}).get('recv', 0))]
                for conn in listening_connections if conn[3].strip().isdigit()
            ], max_lines // 2 + 6, 1, max_x - 2, listen_start_idx, max_lines // 2, active_section == "LISTEN")
        elif selected_option == 3:
            draw_process_table(buffer, "Running Processes", processes, 3, 1, proc_start_idx, max_lines, proc_selected_idx)
            buffer.addstr(max_y - 2, 2, "Press 'k' to kill the selected process", curses.color_pair(2) | curses.A_BOLD)

        buffer.refresh(0, 0, 0, 0, max_y - 1, max_x - 1)

    while True:
        try:
            if selected_option == 4:
                break

            if selected_option in [0, 1, 2]:
                established_connections, listening_connections = fetch_connections()
            if selected_option == 3:
                processes = fetch_processes()

            update_display(established_connections, listening_connections, processes)
            stdscr.refresh()

            max_y, max_x = stdscr.getmaxyx()
            max_lines = max_y - 8

            key = stdscr.getch()
            if selected_option == 0:
                if key == curses.KEY_UP:
                    est_start_idx = max(est_start_idx - 1, 0)
                elif key == curses.KEY_DOWN:
                    est_start_idx = min(est_start_idx + 1, len(established_connections) - max_lines)
                elif key == ord('q'):
                    return 4  # To quit the program
                elif key in [curses.KEY_BACKSPACE, curses.KEY_LEFT, 127]:
                    return selected_option  # Navigate back to the main menu
            elif selected_option == 1:
                if key == curses.KEY_UP:
                    listen_start_idx = max(listen_start_idx - 1, 0)
                elif key == curses.KEY_DOWN:
                    listen_start_idx = min(listen_start_idx + 1, len(listening_connections) - max_lines)
                elif key == ord('q'):
                    return 4  # To quit the program
                elif key in [curses.KEY_BACKSPACE, curses.KEY_LEFT, 127]:
                    return selected_option  # Navigate back to the main menu
            elif selected_option == 3:
                if key == curses.KEY_UP:
                    proc_selected_idx = max(proc_selected_idx - 1, 0)
                    if proc_selected_idx < proc_start_idx:
                        proc_start_idx = proc_selected_idx
                elif key == curses.KEY_DOWN:
                    proc_selected_idx = min(proc_selected_idx + 1, len(processes) - 1)
                    if proc_selected_idx >= proc_start_idx + max_lines:
                        proc_start_idx = proc_selected_idx - max_lines + 1
                elif key == ord('k'):
                    try:
                        pid = int(processes[proc_selected_idx][0].strip())
                        psutil.Process(pid).terminate()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                elif key == ord('q'):
                    return 4  # To quit the program
                elif key in [curses.KEY_BACKSPACE, curses.KEY_LEFT, 127]:
                    return selected_option  # Navigate back to the main menu
            elif selected_option == 2:
                if active_section == "ESTABLISHED":
                    if key == curses.KEY_UP:
                        est_start_idx = max(est_start_idx - 1, 0)
                    elif key == curses.KEY_DOWN:
                        est_start_idx = min(est_start_idx + 1, len(established_connections) - max_lines // 2)
                    elif key == ord('q'):
                        return 4  # To quit the program
                    elif key in [curses.KEY_BACKSPACE, curses.KEY_LEFT, 127]:
                        return selected_option  # Navigate back to the main menu
                    elif key == ord('\t'):
                        active_section = "LISTEN"
                elif active_section == "LISTEN":
                    if key == curses.KEY_UP:
                        listen_start_idx = max(listen_start_idx - 1, 0)
                    elif key == curses.KEY_DOWN:
                        listen_start_idx = min(listen_start_idx + 1, len(listening_connections) - max_lines // 2)
                    elif key == ord('q'):
                        return 4  # To quit the program
                    elif key in [curses.KEY_BACKSPACE, curses.KEY_LEFT, 127]:
                        return selected_option  # Navigate back to the main menu
                    elif key == ord('\t'):
                        active_section = "ESTABLISHED"
            elif key in [curses.KEY_BACKSPACE, curses.KEY_LEFT, 127]:
                return selected_option  # Navigate back to the main menu
            elif key == ord('q'):
                return 4  # To quit the program
        except curses.error:
            pass
        except Exception as e:
            stdscr.addstr(0, 0, f"Error: {e}", curses.color_pair(2) | curses.A_BOLD)
            stdscr.refresh()
            time.sleep(1)

def main(stdscr):
    selected_option = 0
    while True:
        selected_option = splash_screen(stdscr, selected_option)
        if selected_option == 4:
            break
        selected_option = main_screen(stdscr, selected_option)

if __name__ == "__main__":
    os.environ.setdefault('ESCDELAY', '25')
    curses.wrapper(main)
