#!/usr/bin/env python3
"""
Top-level orchestration: config, poller thread, API endpoints.
Uses service modules for clear separation of concerns.
"""
import threading
import configparser
from time import sleep
from datetime import datetime, timezone

# FastAPI additions
from fastapi import FastAPI, HTTPException
from typing import Optional, List, Dict, Any

# logging
import logging

# Import device service and ow helpers
from services.general import GENERAL_INI, DEVICES_INI, LOG_LEVEL, _c_to_f  # system wide functions and constants
from services.device_service import DeviceService
from services.ow_service import ow_connect, ow_list_devices, ow_get_device, ow_read_device, ow_write_device
from services.mqtt_service import MQTTService
from services.sqllite_service import SQLiteService

# API routes registration
from services.api_routes import register_routes

STATE_NEW = 'New'
STATE_DISCONNECTED = 'Disconnected'
STATE_CONNECTED = 'Connected'
STATE_TBC = 'TBC'

logger = logging.getLogger("onewire.backend")
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
logger.setLevel(LOG_LEVEL)

# FastAPI app
logger.debug("Starting FastAPI")
app = FastAPI(title="OneWire Backend API", version="1.0")

class poll_device_thread(threading.Thread):
    def __init__(self):
        # Front-end accessible variables
        self.inifilename = GENERAL_INI
        self.refresh = 0
        self.ticks = 0
        self.devices = []
        self.scale = 'C'
        self.output_messages = True
        self.lock = threading.RLock()

        # Minimal internal state
        self.__stop = False

        # create thread with a readable name; do not set daemon so we can join on shutdown
        super().__init__(name="poll_device_thread", daemon=False)

    def request_stop(self) -> None:
        self.__stop = True

    def _mqtt_connect_if_enabled(self) -> None:
        _mqtt_service.mqtt_connect()

    def set_alias(self, device_path: str, alias: Optional[str]) -> None:
        """
        Set or clear alias for a device on the owserver and update in-memory cache.
        device_path: e.g. '/28.FF3A2B000000/'
        alias: new alias string or None to clear
        """
        logger.info("Request to set alias for %s -> %s", device_path, alias)
        # Write alias to owserver (best-effort)
        try:
            # ow_write_device expects string data; use empty string to clear
            ow_write_device(device_path, 'alias', alias or '')
            logger.info("Wrote alias '%s' to owserver for %s", alias or '', device_path)

            # Update in-memory cache
            with self.lock:
                for d in self.devices:
                    # device stored in 'device' field as path; match exactly or by ID if needed
                    if d.get('device') == device_path or d.get('device') == device_path.rstrip('/'):
                        d['alias'] = alias or device_path.replace('/', '')
                        logger.info("Updated in-memory alias for %s -> %s", device_path, d['alias'])
                        break
                else:
                    # not found in cache: attempt to find by ID
                    device_id = device_path.replace('/', '')
                    for d in self.devices:
                        if d.get('ID') == device_id:
                            d['alias'] = alias or device_id
                            logger.info("Updated in-memory alias for ID %s -> %s", device_id, d['alias'])
                            break
        except Exception:
            logger.exception("Failed to write alias to owserver for %s", device_path)

    def run(self) -> None:
        """
        Main polling thread:
         - load config (owtemp.ini)
         - connect to owserver
         - discover devices and update their temperatures every `refresh_interval` seconds
         - uses DEVICES_INI for group/AF storage and updates
         - publish temperatures to MQTT if enabled
        """
        logger.info("Poll thread starting")
        try:
            cfg = configparser.ConfigParser()
            cfg.read(self.inifilename)  # owtemp.ini for general settings
        except Exception:
            logger.exception("Failed to read ini %s", self.inifilename)
            return

        # Get refresh interval from [GENERAL].refresh_interval (fallback 15)
        try:
            refresh_interval = 15
            if cfg.has_section('GENERAL') and cfg.has_option('GENERAL', 'refresh_interval'):
                refresh_interval = cfg['GENERAL'].getint('refresh_interval')
        except Exception:
            refresh_interval = 15
        if refresh_interval <= 0:
            refresh_interval = 15
        self.refresh = refresh_interval
        logger.info("Polling interval set to %d seconds", self.refresh)

        if ow_connect() is None:
            logger.error("Failed to connect to owserver on startup")
            return

        _mqtt_service.connect()
        self._last_summary_run = None

        # Main loop: protect each cycle so unexpected exception doesn't kill the thread.
        while not self.__stop:
            cycle_start = datetime.now()
            try:
                # Entire cycle wrapped so unhandled errors are caught and thread continues.
                try:
                    logger.debug("Getting onewire devices")
                    paths = ow_list_devices() # get raw device paths from owserver
                    logger.debug("Found %d devices", len(paths))
                except Exception as ex:
                    logger.warning("Failed to list devices from owserver: %s. Attempting reconnect.", ex)
                    try:
                        ow_connect()
                        paths = ow_list_devices()
                        logger.debug("Found %d devices", len(paths))
                    except Exception as ex2:
                        logger.error("Reconnect failed: %s", ex2)
                        paths = []

                # Normalize paths for comparison (strip trailing slash)
                normalized_paths = {p.rstrip('/') for p in paths}

                # Immediately mark any cached device that is not already DISCONNECTED and not present in `paths`
                # as DISCONNECTED and log each such device.
                disconnected_now: List[str] = []
                with self.lock:
                    for d in self.devices:
                        dev_path = d.get('device')
                        if not dev_path:
                            continue
                        if d.get('state') != STATE_DISCONNECTED and dev_path.rstrip('/') not in normalized_paths:
                            d['state'] = STATE_DISCONNECTED
                            ident = d.get('ID') or dev_path
                            disconnected_now.append(str(ident))
                if disconnected_now:
                    logger.info("Devices disconnected (not present in ow list): %s", ", ".join(disconnected_now))

                now = datetime.now()

                dev_cfg = configparser.ConfigParser()
                try:
                    dev_cfg.read(DEVICES_INI)
                except Exception:
                    logger.exception("Failed to read devices ini %s", DEVICES_INI)
                    dev_cfg = configparser.ConfigParser()

                # Loop over device paths
                device_read_count = 0
                for path in paths:
                    if self.__stop:
                        break

                    if ow_read_device(path, 'family') != '28':
                        logger.debug("Skipping non-temperature device %s", path)
                        continue

                    device = ow_get_device(path)
                    if device["rawtemp"] is not None:
                        device_read_count += 1 # count of successfully read devices
                        device_section = 'DEVICE ' + str(device["device_id"])
                        af = 0.0
                        group = 1
                        try:
                            section_created = False
                            updated_keys = False

                            if not dev_cfg.has_section(device_section):
                                dev_cfg[device_section] = {}
                                dev_cfg[device_section]['GROUP'] = '1'
                                dev_cfg[device_section]['AF'] = '0'
                                section_created = True
                                logger.info("Discovered a new device %s", device["device_id"])
                            else:
                                if not dev_cfg[device_section].get('AF'):
                                    dev_cfg[device_section]['AF'] = '0'
                                    updated_keys = True
                                if not dev_cfg[device_section].get('GROUP'):
                                    dev_cfg[device_section]['GROUP'] = '1'
                                    updated_keys = True
                                if updated_keys:
                                    logger.info("Updated devices ini for device %s (added missing keys)", device["device_id"])

                            if section_created or updated_keys:
                                try:
                                    with open(DEVICES_INI, 'w') as fh:
                                        dev_cfg.write(fh)
                                except Exception:
                                    logger.exception("Failed to persist devices ini after adding device section for %s", device["device_id"])

                            try:
                                af = dev_cfg[device_section].getfloat('AF', fallback=0.0)
                            except Exception:
                                try:
                                    af = float(dev_cfg[device_section].get('AF', '0') or 0.0)
                                except Exception:
                                    af = 0.0
                            try:
                                group_val = dev_cfg[device_section].get('GROUP', fallback='1')
                                try:
                                    group = int(group_val)
                                except Exception:
                                    group = group_val
                            except Exception:
                                group = 1
                        except Exception:
                            logger.exception("Error processing device config for %s", device["device_id"])

                        reported_temp = device["rawtemp"] + af

                        # Update thread-local devices list (thread-safe)
                        with self.lock:
                            # find existing
                            idx = -1
                            for i, d in enumerate(self.devices):
                                if d.get('ID') == device["device_id"]:
                                    idx = i
                                    break
                            if idx >= 0:
                                prev = self.devices[idx]
                                tempchange = reported_temp - prev.get('temp', reported_temp)
                                prev.update({
                                    'device': path,
                                    'alias': device["alias"],
                                    'type': device["type"],
                                    'presentat': now,
                                    'rawtemp': device["rawtemp"],
                                    'tempchange': tempchange,
                                    'temp': reported_temp,
                                    'state': STATE_CONNECTED,   # mark connected as we successfully read it
                                    'AF': af,
                                    'group': group
                                })
                            else:
                                self.devices.append({
                                    'device': path,
                                    'alias': device["alias"],
                                    'type': device["type"],
                                    'presentat': now,
                                    'rawtemp': device["rawtemp"],
                                    'temp': reported_temp,
                                    'tempchange': 0,
                                    'state': STATE_NEW,
                                    'ID': device["device_id"],
                                    'AF': af,
                                    'group': group
                                })
                                logger.info("New connection to device %s", path)

                        # Publish temperature to MQTT if enabled (respect mqtt scale)
                        try:
                            _mqtt_service.publish_temperature(device["device_id"], device["alias"], reported_temp)
                        except Exception:
                            logger.exception("Failed to publish temperature for device %s", device["device_id"])

                        # After computing reported_temp and updating in-memory device record:
                        # Record reading to SQLite
                        try:
                            # store as UTC-aware datetime
                            _sqlite.add_reading(device["device_id"], now, device["rawtemp"], af, reported_temp)
                        except Exception:
                            logger.exception("Failed to record reading for device %s", device["device_id"])

                    else:
                        self.devices[idx]['state'] = STATE_TBC
                        logger.debug("Device %s: could not be read", path)

                # After reading all devices: detect devices that remained TBC -> became disconnected.
                tbc_ids: List[str] = []
                with self.lock:
                    for d in self.devices:
                        if d.get('state') == STATE_TBC:
                            #d['state'] = STATE_DISCONNECTED
                            ident = d.get('ID') or d.get('device') or str(d)
                            tbc_ids.append(str(ident))
                if tbc_ids:
                    logger.info("Devices could not be read this cycle: %s", ", ".join(tbc_ids))

                logger.info("Read %d device temperatures", device_read_count)

                # Summaries
                try:
                    now_utc = datetime.now(timezone.utc)
                    floored_5 = now_utc.replace(second=0, microsecond=0)
                    minute_slot = (floored_5.minute // 5) * 5
                    floored_5 = floored_5.replace(minute=minute_slot)

                    if self._last_summary_run is None or floored_5 > self._last_summary_run:
                        inserted = _sqlite.create_summaries(ref_dt=floored_5)
                        if inserted:
                            logger.info("Inserted %d summary rows at %s", len(inserted), floored_5.isoformat())
                        self._last_summary_run = floored_5
                except Exception:
                    logger.exception("Error while creating summaries")

            except Exception:
                # Catch anything unexpected in the cycle; log and continue after a short backoff.
                logger.exception("Unhandled exception in poll_device_thread main loop")
                sleep(1.0)

            # Sleep until next cycle, while being responsive to stop requests
            elapsed = (datetime.now() - cycle_start).total_seconds()
            wait = max(0, self.refresh - elapsed)
            logger.debug("Poll cycle completed in %.2fs, sleeping %.2fs", elapsed, wait)
            slept = 0.0
            while not self.__stop and slept < wait:
                step = min(1.0, wait - slept)
                sleep(step)
                slept += step

        # Cleanup if any
        if _mqtt_service is not None:
            try:
                _mqtt_service.stop()
                logger.info("MQTT client stopped")
            except Exception:
                logger.exception("Error stopping MQTT client")

        if SQLiteService is not None:
            try:
                _sqlite.close()
                logger.info("SQLite database closed")
            except Exception:
                logger.exception("Error closing SQLite service and database")
        return

    def reload_config(self) -> configparser.ConfigParser:
        cfg = configparser.ConfigParser()
        cfg.read(self.inifilename)
        return cfg

    def persist_config(self, cfg: configparser.ConfigParser) -> None:
        with open(self.inifilename, 'w') as fh:
            cfg.write(fh)


# Module-level objects (do not start the thread here; start on FastAPI startup)
logger.debug("Starting Device Services")
_device_service = DeviceService()
logger.debug("Starting MQTT Services")
_mqtt_service = MQTTService()
logger.debug("Starting SQLite Services")
_sqlite = SQLiteService()
logger.debug("Preparing backend instance")
# Do not create/start the background thread at import time.
# The FastAPI startup event will create and start the thread so it runs
# in the server worker process.
_backend: Optional[poll_device_thread] = None

# Register API routes from the extracted module
logger.debug("Registering with API services")
register_routes(app,
                _sqlite=_sqlite,
                _device_service=_device_service,
                get_backend=lambda: _backend,
                _mqtt_service=_mqtt_service,
                logger=logger,
                _c_to_f=_c_to_f,
                GENERAL_INI=GENERAL_INI,
                DEVICES_INI=DEVICES_INI)

# Start/stop handlers for the background poll thread
@app.on_event("startup")
def _on_startup() -> None:
    """
    Ensure the poll_device_thread is started when the FastAPI app starts.
    """
    global _backend
    logger.info("FastAPI startup event: ensuring poll_device_thread is running")
    try:
        if _backend is None:
            _backend = poll_device_thread()
        if not _backend.is_alive():
            _backend.start()
            logger.info("poll_device_thread started by startup event")
        else:
            logger.info("poll_device_thread already running")
    except Exception:
        logger.exception("Failed to start poll_device_thread on startup")


@app.on_event("shutdown")
def _on_shutdown() -> None:
    """Stop the poll_device_thread when FastAPI shuts down."""
    global _backend
    logger.info("FastAPI shutdown event: stopping poll_device_thread")
    try:
        if _backend is not None:
            _backend.request_stop()
            _backend.join(timeout=10)
            logger.info("poll_device_thread stopped")
    except Exception:
        logger.exception("Error stopping poll_device_thread on shutdown")
