# NetScope 1.0

NetScope is a network monitoring tool that displays established and listening network connections along with additional details such as the process name, PID, user, data sent, and data received.

## Features

- Display established network connections
- Display listening network connections
- Show local and remote addresses, status, PID, program name, user, data sent, and data received
- Real-time updates with minimal CPU load
- User-friendly terminal interface

## Screenshot
![alt Scrrenshot of NetScope in action](https://raw.githubusercontent.com/yodabytz/netscope/main/netscope1.0.png?raw=true)

## Installation

To install the required dependencies, use `pip`:

```sh
pip install psutil curses

## Usage
Run the netscope.py script:
python netscope.py

## Controls
Up Arrow: Scroll up through the connections list
Down Arrow: Scroll down through the connections list
Tab: Switch between Established and Listening connections
q: Quit the application

