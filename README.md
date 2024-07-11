# NetScope 1.0

NetScope is a network monitoring application written in Python. It displays established and listening network connections along with process information, including data sent and received, process ID, program name, and user.

## Features

- Displays established and listening network connections
- Shows local and remote addresses, status, PID, program name, user, data sent, and data received
- Supports real-time updates for network data

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yodabytz/netscope.git
   cd netscope

2. ## Make the script executable:

   chmod +x netscope.py

3. ## Move the script to /usr/bin/ (you might need to use sudo for this step):

   sudo mv netscope.py /usr/bin/netscope

## Usage

Simply run the script by typing netscope in your terminal:

netscope

## Requirements

Python 3.x
psutil library
A terminal that supports color (like xterm or gnome-terminal)

## Screenshot

![alt text](https://github.com/yodabytz/netscope/main/NetScope.jpg?raw=true)

## License

This project is licensed under the MIT License - see the LICENSE file for details.
