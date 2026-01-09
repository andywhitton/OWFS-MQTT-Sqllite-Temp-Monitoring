"""
SQLite service for recording readings and creating time-aligned summaries.

Configuration taken from owtemp.ini [DB] section (path from OW_INI_PATH env or default 'owtemp.ini'):

[DB]
db enabled = True
db path = readings.db
db scale = C            # 'C' or 'F' - affects stored temp/AF units (rawtemp always stored as received)
summary periods = (5, 15, 60, 1440)
max entries per summary = 2000
autovacuum = True
"""
from typing import Optional, List, Tuple, Dict, Any
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
import os
import configparser
import logging

from services.general import GENERAL_INI, LOG_LEVEL #system wide functions and constants


DEFAULT_DB = "readings.db"

logger = logging.getLogger("onewire.backend")
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
logger.setLevel(LOG_LEVEL)

def _parse_periods(value: str) -> List[int]:
    if not value:
        return [5, 15, 60, 1440]
    v = value.strip()
    # remove surrounding parentheses/brackets
    if v.startswith("(") and v.endswith(")"):
        v = v[1:-1]
    parts = [p.strip() for p in v.split(",") if p.strip()]
    out = []
    for p in parts:
        try:
            out.append(int(p))
        except Exception:
            continue
    return sorted(set(out))


class SQLiteService:
    def __init__(self, db_path: Optional[str] = None, ini_path: Optional[str] = None) -> None:
        """
        Read configuration from owtemp.ini [DB] (or provided ini_path).
        If db enabled is False the service will be a no-op for writes.
        """
        self._lock = threading.RLock()
        self.ini_path = ini_path or os.environ.get("OW_INI_PATH", GENERAL_INI)

        # load DB config
        cfg = configparser.ConfigParser()
        try:
            cfg.read(self.ini_path)
        except Exception:
            cfg = configparser.ConfigParser()

        db_cfg = cfg["DB"] if cfg.has_section("DB") else {}
        self.db_enabled = False
        try:
            self.db_enabled = str(db_cfg.get("db enabled", "False")).strip().lower() in ("1", "true", "yes", "on")
        except Exception:
            self.db_enabled = False

        self.db_path = db_path or db_cfg.get("db path", DEFAULT_DB)
        # db scale: controls stored AF/temp units (temp column)
        self.db_scale = (db_cfg.get("db scale", "C") or "C").strip().upper()
        if self.db_scale not in ("C", "F"):
            self.db_scale = "C"

        self.summary_periods = _parse_periods(db_cfg.get("summary periods", "(5,15,60,1440)"))
        try:
            self.max_entries_per_summary = int(db_cfg.get("max entries per summary", "2000"))
        except Exception:
            self.max_entries_per_summary = 2000
        try:
            self.autovacuum = str(db_cfg.get("autovacuum", "True")).strip().lower() in ("1", "true", "yes", "on")
        except Exception:
            self.autovacuum = True

        # connection
        self._conn: Optional[sqlite3.Connection] = None
        if self.db_enabled:
            self._open_and_prepare()

    def _open_and_prepare(self) -> None:
        with self._lock:
            if self._conn:
                return
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
            cur = self._conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            # readings table
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    as_at TEXT NOT NULL,         -- ISO8601 UTC
                    rawtemp REAL,
                    AF REAL,
                    temp REAL
                );
                """
            )
            # summaries: include period_start to identify time window uniquely
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    summary_mins INTEGER NOT NULL,
                    period_start TEXT NOT NULL,  -- ISO8601 UTC start of the period
                    avg_temp REAL,
                    UNIQUE(device_id, summary_mins, period_start)
                );
                """
            )
            self._conn.commit()
            logger.info("SQlite database %s opened.", self.db_path)


    def close(self) -> None:
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def clear_database(self) -> None:
        """
        Delete all rows from readings and summaries tables and attempt to VACUUM the database.
        No-op if DB disabled.
        """
        if not self.db_enabled:
            return
        with self._lock:
            try:
                cur = self._conn.cursor()
                # Remove rows from both tables
                cur.execute("DELETE FROM readings;")
                cur.execute("DELETE FROM summaries;")
                self._conn.commit()
                # Try to reclaim space; VACUUM may require exclusive access so ignore errors
                try:
                    cur.execute("VACUUM;")
                except Exception:
                    pass
            except Exception:
                # don't raise; log handled by caller if needed
                pass

    def add_reading(self, device_id: str, as_at: datetime, rawtemp: float, AF: float, temp: float) -> None:
        """
        Insert a reading. If DB disabled this is a no-op.
        rawtemp: the measured Celsius temperature (always stored as provided).
        AF: adjustment factor (in Celsius). If DB scale == 'F', AF and temp are converted to Fahrenheit
        for storage in the temp/AF columns (temp column holds value in configured scale).
        """
        if not self.db_enabled:
            return
        if as_at.tzinfo is None:
            as_at = as_at.replace(tzinfo=timezone.utc)
        iso = as_at.astimezone(timezone.utc).isoformat()

        store_AF = AF
        store_temp = temp
        store_rawtemp = rawtemp
        try:
            if self.db_scale == "F":
                store_AF = _c_to_f(float(AF))
                store_temp = _c_to_f(float(temp))
                store_rawtemp = _c_to_f(float(rawtemp))
        except Exception:
            # fallback to provided values
            store_AF = AF
            store_temp = temp
            store_rawtemp = rawtemp

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO readings (device_id, as_at, rawtemp, AF, temp) VALUES (?, ?, ?, ?, ?)",
                (device_id, iso, store_rawtemp, store_AF, store_temp),
            )

    def _floor_dt(self, dt: datetime, mins: int) -> datetime:
        """
        Floor dt to nearest lower multiple of mins minutes (aligned to hour).
        dt treated as UTC.
        """
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
        minute = (dt.minute // mins) * mins
        return dt.replace(minute=minute, second=0, microsecond=0)

    def create_summaries(self, ref_dt: Optional[datetime] = None) -> List[Tuple[str, int, str, float]]:
        """
        Create summaries for configured summary_periods for the most-recent completed
        period aligned to each period boundary based on ref_dt (or now).
        Inserts only summaries not already present.

        Returns list of inserted summaries as tuples (device_id, summary_mins, period_start_iso, avg_temp)
        """
        inserted = []
        if ref_dt is None:
            #ref_dt = datetime.utcnow().replace(tzinfo=timezone.utc)
            ref_dt = datetime.now(timezone.utc)
        else:
            if ref_dt.tzinfo is None:
                ref_dt = ref_dt.replace(tzinfo=timezone.utc)
            ref_dt = ref_dt.astimezone(timezone.utc)

        with self._lock:
            cur = self._conn.cursor()
            for mins in self.summary_periods:
                # compute most recent completed period_end aligned to mins
                period_end = self._floor_dt(ref_dt, mins)
                period_start = period_end - timedelta(minutes=mins)
                period_start_iso = period_start.isoformat()
                period_end_iso = period_end.isoformat()

                # compute per-device averages from readings.temp; the stored temp already respects db_scale
                cur.execute(
                    """
                    SELECT device_id, AVG(temp) as avg_temp
                    FROM readings
                    WHERE as_at >= ? AND as_at < ?
                    GROUP BY device_id
                    """,
                    (period_start_iso, period_end_iso),
                )
                rows = cur.fetchall()
                for device_id, avg_temp in rows:
                    # check if summary exists
                    cur.execute(
                        "SELECT 1 FROM summaries WHERE device_id = ? AND summary_mins = ? AND period_start = ?",
                        (device_id, mins, period_start_iso),
                    )
                    if cur.fetchone():
                        continue
                    cur.execute(
                        "INSERT INTO summaries (device_id, summary_mins, period_start, avg_temp) VALUES (?, ?, ?, ?)",
                        (device_id, mins, period_start_iso, float(avg_temp)),
                    )
                    inserted.append((device_id, mins, period_start_iso, float(avg_temp)))

                # autovacuum / pruning: remove summaries older than cutoff = now - (mins * max_entries_per_summary) minutes
                if self.autovacuum and self.max_entries_per_summary > 0:
                    try:
                        now_utc = datetime.now(timezone.utc)
                        cutoff = now_utc - timedelta(minutes=mins * self.max_entries_per_summary)
                        cutoff_iso = cutoff.isoformat()
                        cur.execute(
                            "DELETE FROM summaries WHERE summary_mins = ? AND period_start < ?",
                            (mins, cutoff_iso),
                        )
                    except Exception:
                        # ignore pruning errors
                        pass

            self._conn.commit()
        return inserted

    def list_summary_periods(self) -> List[int]:
        """
        Return configured summary periods usable with `get_dataset`.
        Includes 0 to represent raw readings.

        Example return: [0, 5, 15, 60, 1440]
        """
        # Ensure unique, sorted and include 0
        periods = sorted(set(self.summary_periods))
        if 0 not in periods:
            periods.insert(0, 0)
        return periods

    def get_last_summary_time(self, mins: int) -> Optional[datetime]:
        """Return the latest period_start for a given summary interval, or None."""
        if not self.db_enabled:
            return None
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT period_start FROM summaries WHERE summary_mins = ? ORDER BY period_start DESC LIMIT 1",
                (mins,),
            )
            r = cur.fetchone()
            if not r:
                return None
            dt = datetime.fromisoformat(r[0])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt

    def list_devices_in_period(self, start: datetime, end: datetime) -> List[str]:
        """Return distinct device_ids that have readings in [start, end)."""
        if not self.db_enabled:
            return []
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT DISTINCT device_id FROM readings WHERE as_at >= ? AND as_at < ?",
                (start.isoformat(), end.isoformat()),
            )
            return [r[0] for r in cur.fetchall()]

    def get_dataset(self,
                    summary_mins: int = 0,
                    from_dt: Optional[datetime] = None,
                    to_dt: Optional[datetime] = None,
                    device_id: str = '%') -> List[Dict[str, Any]]:
        """
        Return dataset for a given summary level and date range.

        - summary_mins == 0 returns raw readings.
        - summary_mins in configured summary_periods returns summaries for that level.

        Defaults:
        - For summary_mins == 0 or summary_mins < 1440 (sub-day): return latest 24 hours when from/to not provided.
        - For summary_mins >= 1440 (daily): return latest 30 days when from/to not provided.

        Returned rows:
        - readings: dicts with keys: device_id, as_at (ISO), rawtemp, AF, temp
        - summaries: dicts with keys: device_id, period_start (ISO), summary_mins, avg_temp

        device_id accepts SQL LIKE patterns (default '%' returns all devices).
        """
        if not self.db_enabled:
            return []

        #now = datetime.utcnow().replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)

        # validate summary level
        if summary_mins != 0 and summary_mins not in self.summary_periods:
            raise ValueError(f"Unsupported summary level: {summary_mins}")

        # default ranges
        if from_dt is None or to_dt is None:
            if summary_mins == 0 or (summary_mins != 0 and summary_mins < 1440):
                # default last 24 hours
                to_dt = to_dt or now
                from_dt = from_dt or (to_dt - timedelta(days=1))
            else:
                # daily summaries default last 30 days
                to_dt = to_dt or now
                from_dt = from_dt or (to_dt - timedelta(days=30))

        # normalize timezone to UTC ISO strings
        if from_dt.tzinfo is None:
            from_dt = from_dt.replace(tzinfo=timezone.utc)
        else:
            from_dt = from_dt.astimezone(timezone.utc)
        if to_dt.tzinfo is None:
            to_dt = to_dt.replace(tzinfo=timezone.utc)
        else:
            to_dt = to_dt.astimezone(timezone.utc)

        from_iso = from_dt.isoformat()
        to_iso = to_dt.isoformat()

        with self._lock:
            cur = self._conn.cursor()
            if summary_mins == 0:
                # raw readings
                cur.execute(
                    """
                    SELECT device_id, as_at, temp
                    FROM readings
                    WHERE as_at >= ? AND as_at < ? AND device_id LIKE ?
                    ORDER BY as_at ASC
                    """,
                    (from_iso, to_iso, device_id),
                )
                rows = cur.fetchall()
                result: List[Dict[str, Any]] = []
                for dev_id, as_at, temp in rows:
                    result.append({
                        "device_id": dev_id,
                        "as_at": as_at,
                        "temp": temp
                    })
                return result
            else:
                # summaries at summary_mins level
                cur.execute(
                    """
                    SELECT device_id, period_start, avg_temp
                    FROM summaries
                    WHERE summary_mins = ? AND period_start >= ? AND period_start < ? AND device_id LIKE ?
                    ORDER BY period_start ASC
                    """,
                    (summary_mins, from_iso, to_iso, device_id),
                )
                rows = cur.fetchall()
                result = []
                for dev_id, period_start, avg_temp in rows:
                    result.append({
                        "device_id": dev_id,
                        "as_at": period_start,
                        "temp": avg_temp
                    })
                return result