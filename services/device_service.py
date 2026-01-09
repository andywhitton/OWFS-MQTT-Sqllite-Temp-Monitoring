"""
DeviceService maintains device cache and provides thread-safe updates.
It now owns reading/writing the devices INI (`ow_devices.ini`) and
provides helpers so callers don't need to pass cfg/ini paths.
"""
from typing import List, Dict, Any
import configparser
from datetime import datetime
import os
import shutil
import glob
import logging

from services.general import DEVICES_INI, GENERAL_INI,LOG_LEVEL

logger = logging.getLogger("onewire.backend")
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
logger.setLevel(LOG_LEVEL)

class DeviceService:
    def __init__(self) -> None:
        self.devices: List[Dict[str, Any]] = []
        self.lock = None  # optional external lock if caller wants to provide one

    def run(self) -> None:
        logger.debug("Device service instantiated")

    def find_index(self, key: str, value: str) -> int:
        for i, d in enumerate(self.devices):
            if d.get(key) == value:
                return i
        return -1

    def upsert_device(self, device_path: str, id_: str, alias: str, type_: str, rawtemp: float,
                      now: datetime, af: float = 0.0, group=1) -> None:
        i = self.find_index('ID', id_)
        temp = rawtemp + af
        if i >= 0:
            d = self.devices[i]
            d['presentat'] = now
            d['rawtemp'] = rawtemp
            d['tempchange'] = temp - d.get('temp', temp)
            d['temp'] = temp
            d['alias'] = alias
            d['AF'] = af
            d['group'] = group
            if d.get('state') == 'New':
                d['state'] = 'Connected'
        else:
            self.devices.append({
                'device': device_path,
                'alias': alias,
                'type': type_,
                'presentat': now,
                'rawtemp': rawtemp,
                'temp': temp,
                'tempchange': 0,
                'state': 'New',
                'ID': id_,
                'AF': af,
                'group': group
            })

    # ----- Devices ini helpers (now self-contained) -----

    def _read_devices_cfg(self) -> configparser.ConfigParser:
        cfg = configparser.ConfigParser()
        # ensure file exists (will be created on write if needed)
        try:
            cfg.read(DEVICES_INI)
        except Exception:
            # return empty config on error
            cfg = configparser.ConfigParser()
        return cfg

    def _persist_devices_cfg(self, cfg: configparser.ConfigParser) -> None:
        with open(DEVICES_INI, 'w') as fh:
            cfg.write(fh)

    def list_groups(self) -> List[Dict[str, Any]]:
        """Return ordered groups as list of {index:int, name:str} (reads DEVICES_INI)."""
        cfg = self._read_devices_cfg()
        if not cfg.has_section('GROUPS'):
            return []
        return [{"index": i + 1, "name": n} for i, n in enumerate([v for _, v in cfg.items('GROUPS')])]

    def create_group(self, name: str) -> int:
        """Create a new group in DEVICES_INI and return its 1-based index."""
        cfg = self._read_devices_cfg()
        if not cfg.has_section('GROUPS'):
            cfg['GROUPS'] = {}
        current = list(cfg.items('GROUPS'))
        next_index = len(current) + 1
        cfg['GROUPS'][f'GROUP {next_index}'] = name
        self._persist_devices_cfg(cfg)
        return next_index

    def rename_group(self, index: int, new_name: str) -> None:
        """Rename an existing group in DEVICES_INI. Index is 1-based."""
        cfg = self._read_devices_cfg()
        if not cfg.has_section('GROUPS'):
            raise ValueError("No groups defined")
        count = len(list(cfg.items('GROUPS')))
        if index < 1 or index > count:
            raise ValueError("Group index out of range")
        cfg['GROUPS'][f'GROUP {index}'] = new_name
        self._persist_devices_cfg(cfg)

    def delete_group(self, devices: List[Dict[str, Any]], index: int) -> Dict[str, Any]:
        """
        Delete a group by index (1-based) from DEVICES_INI.
        Adjusts subsequent GROUP entries and updates devices in-memory.
        """
        cfg = self._read_devices_cfg()
        if index == 1:
            raise ValueError("Cannot delete Group 1 (default)")
        if not cfg.has_section('GROUPS'):
            raise ValueError("No groups defined")
        items = list(cfg.items('GROUPS'))
        count = len(items)
        if index < 1 or index > count:
            raise ValueError("Group index out of range")

        # Shift group names down to fill the deleted slot
        for i in range(index, count):
            next_name = cfg['GROUPS'].get(f'GROUP {i+1}', '')
            cfg['GROUPS'][f'GROUP {i}'] = next_name
        # remove last group entry
        cfg.remove_option('GROUPS', f'GROUP {count}')

        # Update device group assignments in memory
        updated = 0
        for d in devices:
            try:
                dev_group = int(d.get('group', 1))
            except Exception:
                dev_group = 1
            if dev_group == index:
                d['group'] = 1
                updated += 1
            elif dev_group > index:
                d['group'] = dev_group - 1
                updated += 1

        # Persist changes
        self._persist_devices_cfg(cfg)

        return {'deleted': index, 'updated': updated}

    def ensure_device_section_and_get(self, device_id: str) -> Dict[str, Any]:
        """
        Ensure DEVICE <id> section exists in DEVICES_INI and return its settings.
        Returns dict { 'AF': float, 'GROUP': int_or_str }.
        """
        cfg = self._read_devices_cfg()
        section = f'DEVICE {device_id}'
        created = False
        updated_keys = False
        if not cfg.has_section(section):
            cfg[section] = {}
            cfg[section]['GROUP'] = '1'
            cfg[section]['AF'] = '0'
            created = True
        else:
            if cfg[section].get('AF') is None:
                cfg[section]['AF'] = '0'
                updated_keys = True
            if cfg[section].get('GROUP') is None:
                cfg[section]['GROUP'] = '1'
                updated_keys = True

        if created:
            # Log discovery via caller (backend logs), but ensure persistence
            try:
                self._persist_devices_cfg(cfg)
            except Exception:
                # ignore persistence errors here
                pass
        elif updated_keys:
            try:
                self._persist_devices_cfg(cfg)
            except Exception:
                pass

        # read AF and GROUP with safe fallbacks
        af = 0.0
        try:
            af = cfg[section].getfloat('AF', fallback=0.0)
        except Exception:
            try:
                af = float(cfg[section].get('AF', '0') or 0.0)
            except Exception:
                af = 0.0
        group = 1
        try:
            gv = cfg[section].get('GROUP', fallback='1')
            try:
                group = int(gv)
            except Exception:
                group = gv
        except Exception:
            group = 1

        return {'AF': af, 'GROUP': group, 'created': created, 'updated_keys': updated_keys}

    def equalise_group(self, devices: List[Dict[str, Any]], group: int) -> Dict[str, Any]:
        """
        Equalise AF for devices in the requested group.
        Uses DEVICES_INI for persistence and creates backups there.
        """
        # read devices ini and general ini (for num_backups)
        cfg = self._read_devices_cfg()

        # Determine number of backups to retain from GENERAL_INI (fallback 10)
        num_backups = 10
        try:
            gcfg = configparser.ConfigParser()
            gcfg.read(GENERAL_INI)
            if gcfg.has_section('GENERAL') and gcfg.has_option('GENERAL', 'num_backups'):
                try:
                    num_backups = int(gcfg['GENERAL'].get('num_backups', '10'))
                except Exception:
                    num_backups = 10
        except Exception:
            num_backups = 10

        # Create backup folder and rotate backups (next to DEVICES_INI)
        ini_dir = os.path.dirname(os.path.abspath(DEVICES_INI)) or "."
        backup_dir = os.path.join(ini_dir, "backup")
        os.makedirs(backup_dir, exist_ok=True)

        # Make backup if devices ini exists
        if os.path.exists(DEVICES_INI):
            base = os.path.basename(DEVICES_INI)
            timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
            backup_name = f"{base}.{timestamp}.bak"
            backup_path = os.path.join(backup_dir, backup_name)
            try:
                shutil.copy2(DEVICES_INI, backup_path)
            except Exception:
                pass

        # Prune old backups (keep most recent num_backups)
        try:
            pattern = os.path.join(backup_dir, f"{os.path.basename(DEVICES_INI)}.*.bak")
            files = glob.glob(pattern)
            files.sort(key=lambda p: os.path.getmtime(p))
            while len(files) > num_backups:
                old = files.pop(0)
                try:
                    os.remove(old)
                except Exception:
                    pass
        except Exception:
            pass

        # Collect rawtemps for devices in group
        matches = [d for d in devices
                   if d.get('state') != 'Disconnected' and str(d.get('group')) == str(group)]

        if not matches:
            return {"count": 0, "avg": None}

        tot = 0.0
        for d in matches:
            tot += float(d.get('rawtemp', 0.0))
        avg = tot / len(matches)

        # Update AF for each matched device in devices ini
        for d in matches:
            af = avg - float(d.get('rawtemp', 0.0))
            d['AF'] = af
            section = 'DEVICE ' + str(d['ID'])
            if not cfg.has_section(section):
                cfg[section] = {}
            cfg[section]['AF'] = format(af, '.4f')

        # Persist to DEVICES_INI
        try:
            self._persist_devices_cfg(cfg)
        except Exception:
            pass

        return {"count": len(matches), "avg": avg}