# NetScope 2.0.11

NetScope is a powerful network and process monitoring tool inspired by htop and netstat. It lets you inspect established and listening connections, browse running processes, and interact with them in a fast, keyboard-friendly UI. New in this release: a theming system with truecolor backgrounds (OSC 11) when supported, automatic 256-color/16-color fallbacks, and runtime theme switching (t). Themes can be supplied as JSON in /etc/netscope/themes/*.json; the default blue theme remains available out of the box.

## Features

- **Established Connections**: View all established network connections.
- **Listening Connections**: View all listening network connections.
- **Both Connections View**: View both established and listening connections side-by-side.
- **Running Processes**: View and interact with running processes. Highlight processes and perform actions using simple keyboard controls.
- **Process Search**: Press `'s'` to search for running processes using wildcards, and `'n'` to navigate to the next match.
- **Process Sorting**: Sort processes by CPU usage (`'c'`) or memory usage (`'m'`).
- **Kill Processes with Confirmation**: Press `'k'` to kill the selected process with a confirmation prompt to prevent accidental termination.
- **Help Menus**: Press `'?'` to display context-specific help menus with key bindings and navigation instructions.
- **Interactive Commands**: Navigate and interact with the application using intuitive keyboard commands.
- **Smooth Scrolling**: Efficient and smooth scrolling through lists of connections and processes.
- **Mac OS X Support**: Now supports Mac OS X (tested on Intel chips). Requires `sudo` privileges.
- **Enhanced System Info**: Displays detailed system information including OS, Host, Kernel, Uptime, Packages, Shell, Resolution, Terminal, CPU, GPU, Memory, Swap, Disk Usage, Local IP, and Locale.

## Requirements

- Python 3.x
- `psutil` library
- `curses` library

## Screenshot

### NetScope in action, running the Tokyo Night theme

<img src="https://raw.githubusercontent.com/yodabytz/netscope/refs/heads/main/Screenshot-1.jpg?raw=true" width="600">
<img src="https://raw.githubusercontent.com/yodabytz/netscope/refs/heads/main/Screenshot-2.jpg?raw=true" width="600">
<img src="https://raw.githubusercontent.com/yodabytz/netscope/refs/heads/main/Screenshot-3.jpg?raw=true" width="600">




## Installation Steps

1. **Clone the Repository**:
    ```sh
    git clone https://github.com/yodabytz/netscope.git
    cd netscope
    ```

2. **Install Dependencies**:
    ```sh
    pip install psutil
    ```

3. **Create Directories and Move Files**:
    ```sh
    sudo mkdir -p /etc/netscope/themes
    sudo cp ascii_art.py /etc/netscope/
    sudo cp -r themes /etc/netscope/
    ```

4. **Move the Script to `/usr/bin`**:
    ```sh
    sudo cp netscope.py /usr/bin/netscope
    sudo chmod +x /usr/bin/netscope
    ```
    *For Mac OS X, place the script in `/usr/local/bin` instead.*

## Usage

Run the tool by typing:
```sh
netscope

# Optional arguments:

-d <seconds>: Set the update interval in seconds (default is 3 seconds).
-h: Show the help message with usage instructions.
-v: Show version information.

Examples:
netscope -d 5   # Update every 5 seconds
netscope -h     # Display help message
netscope -v     # Display version information
```

## Controls
# General Navigation
```
Up/Down Arrows or k/j: Navigate through lists and menu options.
Enter or Return: Select a menu option.
Left Arrow or Backspace: Return to the main menu from sub-screens.
q: Quit the application from any screen.
?: Show context-specific help menus with key bindings and instructions.

Established and Listening Connections Screens
Up/Down Arrows or k/j: Scroll through the list of connections.
Left Arrow or Backspace: Return to the main menu.
q: Quit the application.

Both Connections Screen
Tab: Switch between Established and Listening sections.
Up/Down Arrows or k/j: Scroll through the connections in the active section.
Left Arrow or Backspace: Return to the main menu.
?: Show help menu with key bindings.
q: Quit the application.

Running Processes Screen
Up/Down Arrows or k/j: Scroll through the list of processes.
k: Kill the selected process (with confirmation prompt).
s: Search for a process using wildcards (e.g., *python*).
n: Find the next match for the search term.
c: Sort processes by CPU usage (descending).
m: Sort processes by Memory usage (descending).
Left Arrow or Backspace: Return to the main menu.
?: Show help menu with key bindings.
q: Quit the application.
```

## License
This project is licensed under the MIT License. See the LICENSE file for details.

Author
NetScope 2.0 is developed by Yodabytz.

Contributions, issues, and feedback are welcome!


