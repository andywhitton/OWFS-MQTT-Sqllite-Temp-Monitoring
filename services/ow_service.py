"""
OneWire (owserver/owfs) helpers.
Maintains a module-level `ow_proxy` so callers don't have to pass the proxy around.
Small, dependency‑free functions to connect and read devices.
"""
from typing import List, Any, Optional
import pyownet as ow
import configparser
import logging

from services.general import DEVICES_INI, GENERAL_INI, LOG_LEVEL

# Module-level variables. Use `ow_connect` to initialise.
ow_proxy: Optional[Any] = None

logger = logging.getLogger("onewire.backend")
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
logger.setLevel(LOG_LEVEL)

def ow_connect() -> Any:
    """
    Create and store a pyownet proxy instance at module level and return it.
    Raises on failure.
    """
    global ow_proxy

    # Read configuration
    try:
        cfg = configparser.ConfigParser()
        cfg.read(GENERAL_INI)  # owtemp.ini for general settings
    except Exception:
        logger.exception("Failed to read ini %s", GENERAL_INI)
        return

    # Connect to owserver
    try:
        ow_host = cfg['OWSERVER'].get('host', '')
        ow_port = cfg['OWSERVER'].getint('port', fallback=4304)
    except Exception:
        logger.error("OWSERVER section missing or invalid in %s", GENERAL_INI)
        return

    try:
        # Does not support the timeout parameter; include timeout in read/write calls if needed
        ow_proxy = ow.protocol.proxy(host=ow_host, port=ow_port)
        logger.info("Connect to owserver at %s:%d", ow_host, ow_port)
    except Exception:
        logger.exception("Failed to connect to owserver at %s:%d", ow_host, ow_port)
        ow_proxy = None
        raise
    return ow_proxy


def ow_get_proxy() -> Any:
    """Return the module-level proxy or None if not connected."""
    return ow_proxy


def ow_list_devices() -> List[str]:
    """
    Return raw device paths from the ow proxy.
    If `proxy` is not supplied the module-level `ow_proxy` is used.
    Raises RuntimeError if no proxy is available.
    """
    if ow_proxy is None:
        raise RuntimeError("ow proxy is not connected - call ow_connect() first")
    return ow_proxy.dir()


def ow_read_device(device_path: str, attribute: str) -> str:
    """
    Read a device attribute from the ow proxy.
    Falls back to module-level proxy if `proxy` is not provided.
    """
    if ow_proxy is None:
        raise RuntimeError("ow proxy is not connected - call ow_connect() first")
    # ensure attribute path is concatenated correctly
    if device_path.endswith('/') and attribute.startswith('/'):
        full_path = device_path + attribute[1:]
    elif device_path.endswith('/') or attribute.startswith('/'):
        full_path = device_path + attribute
    else:
        full_path = f"{device_path}/{attribute}"
    data = ow_proxy.read(full_path)
    return data.decode('utf-8').strip() if data is not None else ''

def ow_write_device(device_path: str, attribute: str, data: str | None) -> None:
    """
    Write a device attribute to the ow proxy.
    Uses module-level proxy if `proxy` not provided.
    Data is converted by `string_to_binary`.
    """
    if ow_proxy is None:
        raise RuntimeError("ow proxy is not connected - call ow_connect() first")
    # build full path consistently
    if device_path.endswith('/') and attribute.startswith('/'):
        full_path = device_path + attribute[1:]
    elif device_path.endswith('/') or attribute.startswith('/'):
        full_path = device_path + attribute
    else:
        full_path = f"{device_path}/{attribute}"
    
    #Enable resetting of an alias back to default
    if data == None and attribute == 'alias':
        ow_proxy.write(full_path, b'')            
        logger.debug("Updated %s on device %s to Nothing", attribute, device_path)
    else:
        # pyownet expects raw bytes or binary-string depending on API;
        ow_proxy.write(full_path, data.encode('utf-8'))
        logger.debug("Updated %s on device %s to %s", attribute, device_path, data)


def ow_get_device(path: str) -> dict:
    """
    Return a device info dict for `path`.
    Uses module-level proxy if `proxy` not provided.
    Returned dict: {'alias', 'device_id', 'type', 'rawtemp'}
    """
    if ow_proxy is None:
        raise RuntimeError("ow proxy is not connected - call ow_connect() first")

    # Ensure path ends with a slash as used in other code
    if not path.endswith('/'):
        path = path + '/'

    try:
        raw = ow_proxy.read(path + 'alias')
        alias = raw.decode('utf-8') if raw is not None else ''
    except Exception:
        alias = None

    try:
        raw = ow_proxy.read(path + 'id')
        device_id = raw.decode('utf-8') if raw is not None else path.replace('/', '')
    except Exception:
        device_id = None

    try:
        raw = ow_proxy.read(path + 'type')
        dev_type = raw.decode('utf-8') if raw is not None else ''
    except Exception:
        dev_type = None

    try:
        # use timeout=0 like previous code to avoid blocking if proxy supports it
        rawtemp_raw = ow_proxy.read(path=path + 'temperature', timeout=0)
        rawtemp = float(rawtemp_raw)
    except Exception:
        rawtemp = None

    return {'alias': alias, 'device_id': device_id, 'type': dev_type, 'rawtemp': rawtemp}

