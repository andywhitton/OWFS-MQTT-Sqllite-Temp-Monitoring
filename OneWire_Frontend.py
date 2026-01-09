#from operator import truediv
#from pickle import TRUE
import threading
import configparser
from time import sleep
# from pynput import keyboard
import os
#import platform
#import sys
#import subprocess
import requests
from sshkeyboard import listen_keyboard, stop_listening
from datetime import datetime #, timedelta

# Replace direct import of backend thread with HTTP API client
# from OneWire_Backend import poll_device_thread

# Configuration
inifilename = 'ow_frontend.ini'  # name of the ini file
# Read configuration
config = configparser.ConfigParser()
# Read frontend INI immediately so API_BASE can be derived from it
config.read(inifilename)
api_host = config.get('OW_API', 'api_host', fallback=os.getenv('ONEWIRE_API_HOST', '127.0.0.1'))
api_port = config.get('OW_API', 'api_port', fallback=os.getenv('ONEWIRE_API_PORT', '8000'))
# Construct API base URL
API_BASE = f"http://{api_host}:{api_port}"  # base URL of the FastAPI backend

# Global variables
outputmsgtocli = True  # Flag to indicate if messages should be printed to cli
keypressed = ''
stop = False  # A flag to indicate processes should stop
interactive = False
devices_cache = []  # local cache of devices fetched from API
refresh = 20  # display updated temperatures every x seconds
ticks = 8  # parts of a second to check for key entry
report_temp_change_on_reconnect = False
host = ''
scale = 'C'
config = configparser.ConfigParser()
stop = False

# Keyboard listener state
_listener_thread: threading.Thread | None = None
_listener_lock = threading.Lock()

STATE_NEW = 'New'
STATE_RECONNECTED = 'Reconnected'
STATE_DISCONNECTED = 'Disconnected'
STATE_CONNECTED = 'Connected'


# --- API helpers ------------------------------------------------------------
def api_status() -> dict:
    try:
        r = requests.get(f"{API_BASE}/status", timeout=3)
        r.raise_for_status()
        return r.json()
    except Exception as ex:
        return {"error": str(ex)}


def api_get_devices(scale: str = 'C') -> list:
    """
    Retrieve devices from backend; request temperature scale via query param.
    scale: 'C' or 'F' (defaults to 'C')
    """
    try:
        params = {'scale': scale.upper()} if scale else {}
        r = requests.get(f"{API_BASE}/devices", params=params, timeout=5)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []


def api_set_alias(device: str, alias: str | None) -> dict:
    try:
        payload = {"device": device, "alias": alias}
        r = requests.post(f"{API_BASE}/alias", json=payload, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as ex:
        return {"error": str(ex)}


def api_get_summary_periods() -> list:
    """
    Query backend for configured summary periods (includes 0).
    """
    try:
        r = requests.get(f"{API_BASE}/summaryperiods", timeout=5)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []


def api_clear_db() -> dict:
    """
    Call the backend endpoint to clear the SQLite database.
    """
    try:
        r = requests.post(f"{API_BASE}/cleardb", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as ex:
        return {"error": str(ex)}


# --- Group API helpers ------------------------------------------------------
def api_get_groups() -> list:
    try:
        r = requests.get(f"{API_BASE}/groups", timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


def api_create_group(name: str) -> dict:
    try:
        r = requests.post(f"{API_BASE}/groups", json={"name": name}, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as ex:
        return {"error": str(ex)}


def api_rename_group(index: int, name: str) -> dict:
    try:
        r = requests.put(f"{API_BASE}/groups/{index}", json={"name": name}, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as ex:
        return {"error": str(ex)}


def api_delete_group(index: int) -> dict:
    try:
        r = requests.delete(f"{API_BASE}/groups/{index}", timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as ex:
        return {"error": str(ex)}


# --- UI / local logic (refactored to use API) -------------------------------

def request_exit_program():
    global stop
    global ticks

    print("\nShutting down...")
    stop = True

    # Ensure keyboard listener is stopped
    try:
        stop_listening()
    except Exception:
        pass
  


def press(key):
    global interactive
    global stop
    if key == 'm':
        interactive = True
        # stop the background listener so display_menu can take over input
        try:
            stop_listening()
        except Exception:
            pass
        sleep(0.1)
        display_menu()
    if key == 'q':
        interactive = False
        request_exit_program()
        stop = True
        try:
            stop_listening()
        except Exception:
            pass


def display_menu():
    global interactive
    # stop reporting temperatures when in menu
    global outputmsgtocli
    temp_outputmsgtocli = outputmsgtocli
    outputmsgtocli = False

    while True:
        print('\nMenu')
        print('a - Set device alias')
        print('r - Reset device alias')
        print('l - List device data')
        print('g - manage Groups')
        print('o - Toggle report device status to console')
        print('d - Delete database')
        print('x - exit menu')

        choice = input("Select an option (arlgodx): ")

        match choice:
            case 'a':
                set_device_alias()
            case 'g':
                manage_groups()
            case 'r':
                reset_device_alias()
            case 'o':
                temp_outputmsgtocli = not temp_outputmsgtocli
                print(f'Device temperature reporting: {temp_outputmsgtocli}')
            case 'l':
                list_device_data()
            case 'd':
                # Confirm destructive action
                confirm = input("This deletes all data in the database. To continue type the word DELETE?: ").strip()
                if confirm == "DELETE":
                    resp = api_clear_db()
                    if isinstance(resp, dict) and resp.get('cleared'):
                        print("Database cleared.")
                    else:
                        err = resp.get('error') if isinstance(resp, dict) else None
                        reason = resp.get('reason') if isinstance(resp, dict) else None
                        if err:
                            print(f"Failed to clear database: {err}")
                        elif reason:
                            print(f"Did not clear database: {reason}")
                        else:
                            print("Failed to clear database or no confirmation from server.")
                else:
                    print("Aborted database delete.")
            case 'x':
                interactive = False  # restart keyboard listening
                list_devices(False) # list devices
                break
            case _:
                print("Invalid choice. Please try again.")

    outputmsgtocli = temp_outputmsgtocli


def print_menu_options():
    print('Menu')
    print('m - Show menu')
    print('q - Exit')


# Set a device's alias using API
def set_device_alias():
    global devices_cache
    devices = devices_cache
    if not devices:
        print("No devices available")
        return
    for idx, d in enumerate(devices, start=1):
        print(f"{idx}: {d.get('ID')} - {d.get('alias')} - {d.get('temp')}")
    device_to_edit = int(input('Enter reference of device to set alias, 0 to abort: '))
    if 0 < device_to_edit <= len(devices):
        new_alias = input('Enter new alias.  Blank to abort: ')
        if new_alias != '':
            device = devices[device_to_edit - 1]['device']
            resp = api_set_alias(device, new_alias)
            if 'error' in resp:
                print(f"Failed to set alias: {resp['error']}")
            else:
                print("Alias update requested")
                # refresh cache immediately
                refresh_devices_cache()


# Reset a device's alias using API (alias=null)
def reset_device_alias():
    global devices_cache
    devices = devices_cache
    if not devices:
        print("No devices available")
        return
    for idx, d in enumerate(devices, start=1):
        print(f"{idx}: {d.get('ID')} - {d.get('alias')}")
    device_to_edit = int(input('Enter reference of device to reset alias, 0 to abort: '))
    if 0 < device_to_edit <= len(devices):
        device = devices[device_to_edit - 1]['device']
        resp = api_set_alias(device, None)
        if 'error' in resp:
            print(f"Failed to reset alias: {resp['error']}")
        else:
            print("Alias reset requested")
            refresh_devices_cache()


# Set the reporting adjustment factors to equalise group reporting (uses local ini updates)
def equalise_device_af(g: int):
    """
    Prompt user and request backend to equalise group g.
    Frontend retains user interaction and reporting; backend performs AF calculations and writes the ini.
    """
    global devices_cache
    inp = input('Are you sure you wish to equalise the devices (y/n)?')
    if inp != 'y':
        return

    try:
        r = requests.post(f"{API_BASE}/groups/{g}/equalise", timeout=10)
        r.raise_for_status()
        resp = r.json()
    except Exception as ex:
        print(f"Failed to request equalise operation: {ex}")
        return

    if 'error' in resp:
        print(f"Equalise failed: {resp['error']}")
        return

    result = resp.get('result', {})
    count = result.get('count', 0)
    avg = result.get('avg', None)
    if count == 0:
        print(f"No devices found in group {g} to equalise.")
        return

    print(f"Equalised {count} devices in group {g}. New AFs written to ini. Group average raw temp: {avg:.4f}")
    # Refresh device cache to show updated AFs and temps
    refresh_devices_cache()


def toggle_reporting():
    global outputmsgtocli
    outputmsgtocli = not outputmsgtocli
    print(f'Reporting temperatures to console: {outputmsgtocli}')


# --- Updated manage_groups using API ---------------------------------------
def manage_groups():
    global outputmsgtocli
    temp_outputmsgtocli = outputmsgtocli
    outputmsgtocli = False

    inp = ''
    while inp != 'x':
        groups = api_get_groups()
        i = len(groups)
        print('MANAGE GROUPS')
        print('1 - Rename')
        print('2 - Create')
        print('3 - Delete')
        print('4 - Calibrate temperatures')
        print('x - Exit')

        # Display current groups
        print('\nCurrent groups:')
        for g in groups:
            print(f"{g.get('index')}: {g.get('name')}")

        inp = input('Select option: ')[-1:]  # get last character

        match inp:
            case '1':
                g = input('Enter group reference: ')
                g = int(g)
                if 0 < g <= i:
                    groupname = input('Enter updated group name: ')
                    resp = api_rename_group(g, groupname)
                    if 'error' in resp:
                        print(f"Rename failed: {resp['error']}")
                    else:
                        print("Group renamed")
                else:
                    print("Invalid group reference")
            case '2':
                groupname = input('Enter new group name: ')
                resp = api_create_group(groupname)
                if 'error' in resp:
                    print(f"Create failed: {resp['error']}")
                else:
                    print(f"Added group {groupname} (index {resp.get('created')})")
            case '3':
                g = input('Enter group reference to delete: ')
                g = int(g)
                if g < 0 or g > i:
                    print('Must enter a valid group reference')
                else:
                    if g == 1:
                        print("You can't delete Group 1")
                    else:
                        resp = api_delete_group(g)
                        if 'error' in resp:
                            print(f"Delete failed: {resp['error']}")
                        else:
                            print('Group deleted')
                            # refresh device cache to reflect group reassignments
                            refresh_devices_cache()
            case '4':
                g = input('Enter group reference to calibrate: ')
                g = int(g)
                if g < 0 or g > i:
                    print('Must enter a valid group reference')
                else:
                    equalise_device_af(g)
            case 'x':
                pass
            case _:
                print("Invalid choice. Please try again.")

    print('Exited group management')
    outputmsgtocli = temp_outputmsgtocli


def list_groups():
    """
    Query backend for groups and display them.
    Returns the number of groups.
    """
    groups = api_get_groups()
    if not groups:
        print("No groups defined")
        return 0

    print('%-*s %-*s' % (3, 'Ref', 10, 'GROUP'))
    print('%-*s %-*s' % (3, '===', 10, '====='))
    for g in groups:
        idx = g.get('index')
        name = g.get('name')
        print('%-*s %-*s' % (3, str(idx), 10, name))
    return len(groups)


def update_ini():
    with open(inifilename, 'w') as configfile:
        config.write(configfile)


def find(lst, key, value):
    for i, dic in enumerate(lst):
        if dic[key] == value:
            return i
    return -1


# Report new devices (console)
def report_device_statechange(device):
    if device['state'] == STATE_NEW:
        state = 'connected'
    elif device['state'] == STATE_RECONNECTED:
        state = 'reconnected'
    elif device['state'] == STATE_DISCONNECTED:
        state = 'disconected'
    elif device['state'] == STATE_CONNECTED:
        state = 'connected'
    else:
        state = 'unknown state'

    if outputmsgtocli is True:
        print(device['ID'] + ' ' + state + ' at ' +
              datetime.strftime(datetime.fromisoformat(device['presentat']), "%Y-%m-%d %H:%M:%S") + ' as ' +
              device['alias'] + ' of type ' +
              device['type'] + ' : AF of %*.3f' % (7, device.get('AF', 0.0)))


def list_devices_callback():
    if interactive is False:
        list_devices()
    return


def refresh_devices_cache():
    global devices_cache
    # Determine default scale from frontend ini (REPORTING.scale) default C
    try:
        config.read(inifilename)
        frontend_scale = config['REPORTING'].get('scale', 'C').strip().upper() if config.has_section('REPORTING') else 'C'
    except Exception:
        frontend_scale = 'C'
    if frontend_scale not in ('C', 'F'):
        frontend_scale = 'C'

    devices_cache = api_get_devices(scale=frontend_scale)


def list_devices(refresh_first: bool = True):
    if refresh_first:
        refresh_devices_cache()

    if not devices_cache:
        print('No devices connected')
        return

    # Sort devices alphabetically by alias, then by ID (both case-insensitive)
    sorted_devices = sorted(
        devices_cache,
        key=lambda d: (str(d.get('alias', '')).lower(), str(d.get('ID', '')).lower())
    )

    max_device_length = max(len(d.get('device', '')) for d in sorted_devices) + 1
    max_alias_length = max(len(d.get('alias', '')) for d in sorted_devices) + 1
    max_temp_length = max(len('Temperature'), max(len(str(d.get('temp', ''))) for d in sorted_devices)) + 1
    ref = 0

    print('\n%-*s %-*s %-*s %*s %-*s' % (3, 'Ref', max_device_length, 'ID', max_alias_length, 'Alias',
                                       max_temp_length, 'Temperature', 8, 'At'))
    print('%-*s %-*s %-*s %*s %-*s' % (3, '===', max_device_length, '==', max_alias_length, '=====',
                                       max_temp_length, '===========', 8, '=='))
    for d in sorted_devices:
        ref += 1
        temp = 0 if d.get('state') == STATE_DISCONNECTED else d.get('temp', 0)

        presentat = d.get('presentat', '')
        try:
            at_str = datetime.fromisoformat(presentat).strftime("%H:%M:%S") if presentat else ''
        except Exception:
            at_str = str(presentat)

        # Use get(...) to avoid KeyError if fields missing
        print('%-*s %-*s %-*s %*.3f %-*s' % (
            3,
            str(ref),
            max_device_length,
            d.get('ID', ''),
            max_alias_length,
            d.get('alias', ''),
            max_temp_length,
            float(temp) if temp is not None else 0.0,
            8,
            at_str
        ))
    return


# ****************** CODE STARTS *****************

def _run_listener():
    try:
        listen_keyboard(on_press=press)
    except Exception:
        pass


def start_keyboard_listener():
    global _listener_thread
    with _listener_lock:
        if _listener_thread is None or not _listener_thread.is_alive():
            _listener_thread = threading.Thread(target=_run_listener, daemon=True, name="keyboard-listener")
            _listener_thread.start()


def list_device_data():
    """
    Interactive flow:
     - show available devices
     - let user pick one
     - ask for summary period (0 for raw readings)
     - request /tempdata?summary_mins=...&device_id=...
     - display date and temperature
    """
    global devices_cache
    refresh_devices_cache()
    if not devices_cache:
        print("No devices available")
        return

    for idx, d in enumerate(devices_cache, start=1):
        print(f"{idx}: {d.get('ID')} - {d.get('alias')}")
    try:
        sel = int(input("Select device reference (0 to abort): "))
    except Exception:
        print("Invalid selection")
        return
    if sel <= 0 or sel > len(devices_cache):
        print("Aborted")
        return

    device = devices_cache[sel - 1]
    device_id = device.get('ID') or device.get('device')

    # Ask for summary period
    # Fetch configured summary periods from backend and present sorted options (excluding 0)
    try:
        periods = api_get_summary_periods()
        # Ensure integers and sort
        valid_periods = sorted([int(p) for p in periods if isinstance(p, (int, float)) or (isinstance(p, str) and p.isdigit())])
        # remove 0 for display
        display_periods = [p for p in valid_periods if p != 0]
    except Exception:
        display_periods = []

    display_list = ", ".join(str(p) for p in display_periods) if display_periods else ""
    prompt = f"Enter summary period in minutes (0 = raw readings{', or ' + display_list if display_list else ''}) [0]: "
    period_input = input(prompt).strip()
    if period_input == '':
        summary_mins = 0
    else:
        try:
            summary_mins = int(period_input)
        except Exception:
            print("Invalid period")
            return

    # Validate against available periods if we got a list
    if display_periods and summary_mins != 0 and summary_mins not in valid_periods:
        print("Requested summary period not in available options")
        return

    # Call backend API
    try:
        params = {'summary_mins': summary_mins, 'device_id': device_id}
        r = requests.get(f"{API_BASE}/tempdata", params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as ex:
        print(f"Failed to fetch device data: {ex}")
        return

    if not data:
        print("No data available for that selection.")
        return

    # Display results: date / temp
    print("\n{:<20} {:>10}".format("Date", "Temperature"))
    print("-" * 32)
    for row in data:
        as_at = row.get('as_at')
        temp = row.get('temp')
        try:
            dstr = datetime.fromisoformat(as_at).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            dstr = str(as_at)
        try:
            tval = float(temp)
            tstr = f"{tval:.3f}"
        except Exception:
            tstr = str(temp)
        print(f"{dstr:<20} {tstr:>10}")
    input("\nPress Enter to continue...")

def main():
    print('One Wire Temperature monitor (API frontend)')
    print('===========================================')
    print('')

    global devices_cache
    global stop

    # Configuration
    config.sections()
    config.read(inifilename)

    # Get config variables (locals)
    if config.has_section('REPORTING'):
        try:
            report_temp_change_on_reconnect = config['REPORTING'].getboolean('report temp change on reconnect')
        except Exception:
            pass
        try:
            _scale = config['REPORTING']['scale']
            if _scale in ('C', 'F'):
                global scale
                scale = _scale
        except Exception:
            pass

    # Get the refresh interval to use in seconds else use the default
    if config.has_option('GENERAL', 'refresh'):
        _refresh = config['GENERAL'].getint('refresh')
        if _refresh != 0:
            global refresh
            refresh = _refresh

    # Get the tick interval for responsiveness
    if config.has_option('GENERAL', 'ticks'):
        _ticks = config['GENERAL'].getint('ticks')
        if _ticks != 0:
            global ticks
            ticks = _ticks

    print('Refresh interval is : ' + str(refresh) + ' seconds')
    print(f'Reporting temperatures in {scale}')

    # Make sure groups are present
    if config.has_section('GROUPS') is False:
        config['GROUPS'] = {}  # Create GROUPS section
        config['GROUPS']['GROUP 1'] = 'DEFAULT'  # Create first group
        update_ini()

    # Ensure backend is running (request start via API)
    s = api_status()
    if isinstance(s, dict) and not s.get('running'):
        print("Backend not responding")
        stop = True

    # Show menu options
    if stop == False:
        print_menu_options()

    # Main loop: poll device list periodically and allow interactive menu
    try:
        while stop is False:
            if not interactive:
                print("\nFetching device list...")
                list_devices()
            # ensure listener running
            if not interactive and not stop:
                start_keyboard_listener()
            # Sleep in small steps
            for _ in range(int(refresh * ticks)):
                if stop:
                    break
                sleep(1 / ticks)
    except KeyboardInterrupt:
        request_exit_program()

    print("\nFinished")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        request_exit_program()
