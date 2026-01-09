"""
API endpoints extracted from OneWire_Backend.py.

Call `register_routes(app, dependencies...)` from OneWire_Backend after
the module-level services (_sqlite, _device_service, _backend, _mqtt_service,
logger, etc.) have been created so routes are registered with the FastAPI app.
"""
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime
from pydantic import BaseModel
from fastapi import HTTPException

# Pydantic models used by the API
class AliasUpdate(BaseModel):
    device: str
    alias: Optional[str] = None


class GroupName(BaseModel):
    name: str


def register_routes(app,
                    _sqlite,
                    _device_service,
                    get_backend: Callable[[], Optional[object]],
                    _mqtt_service,
                    logger,
                    _c_to_f,
                    GENERAL_INI,
                    DEVICES_INI):
    """
    Register API endpoints on `app`.
    Dependencies passed in so we avoid circular imports and keep OneWire_Backend
    in control of lifecycle of services.

    Note: `get_backend` is a zero-arg callable that returns the current backend
    instance. This ensures routes observe updates to the backend after startup.
    """

    def _serialize_device(device: Dict[str, Any]) -> Dict[str, Any]:
        serialized = {
            "device": device.get("device"),
            "alias": device.get("alias"),
            "type": device.get("type"),
            "ID": device.get("ID"),
            "state": device.get("state"),
            "AF": device.get("AF"),
            "group": device.get("group"),
            "temp": device.get("temp"),
            "rawtemp": device.get("rawtemp"),
            "tempchange": device.get("tempchange")
        }
        presentat = device.get("presentat")
        if isinstance(presentat, datetime):
            serialized["presentat"] = presentat.isoformat()
        else:
            serialized["presentat"] = presentat
        return serialized

    @app.get("/status")
    def api_status():
        logger.info("API call: /status")
        try:
            backend = get_backend()
            running = bool(backend is not None and getattr(backend, "is_alive", lambda: False)())
            logger.debug("Status: running=%s", running)
            return {"running": running}
        except Exception as ex:
            logger.error("Error in api_status: %s\n%s", ex, ex)
            raise HTTPException(status_code=500, detail="Failed to get status")

    @app.get("/devices", response_model=List[Dict[str, Any]])
    def api_devices(scale: Optional[str] = None):
        logger.info("API call: /devices scale=%s", scale)
        try:
            import configparser
            cfg = configparser.ConfigParser()
            cfg.read(GENERAL_INI)
            default_scale = 'C'
            try:
                if cfg.has_section('REPORTING') and cfg.has_option('REPORTING', 'scale'):
                    default_scale = cfg['REPORTING']['scale'].strip().upper()
            except Exception:
                default_scale = 'C'
            requested_scale = (scale or default_scale or 'C').upper()
            if requested_scale not in ('C', 'F'):
                logger.warning("Invalid scale requested: %s", requested_scale)
                raise HTTPException(status_code=400, detail="Invalid scale. Valid options: 'C' or 'F'.")

            backend = get_backend()
            if backend is None or not backend.is_alive():
                logger.warning("Devices requested but backend not running")
                return []

            with backend.lock:
                devices = []
                for d in list(backend.devices):
                    sd = _serialize_device(d)
                    try:
                        if requested_scale == 'F':
                            if sd.get('temp') is not None:
                                sd['temp'] = _c_to_f(float(sd['temp']))
                            if sd.get('rawtemp') is not None:
                                sd['rawtemp'] = _c_to_f(float(sd['rawtemp']))
                            if sd.get('AF') is not None:
                                sd['AF'] = _c_to_f(float(sd['AF']))
                        else:
                            if sd.get('temp') is not None:
                                sd['temp'] = float(sd['temp'])
                            if sd.get('rawtemp') is not None:
                                sd['rawtemp'] = float(sd['rawtemp'])
                            if sd.get('AF') is not None:
                                sd['AF'] = float(sd['AF'])
                    except Exception:
                        logger.exception("Conversion error for device %s", sd.get("ID"))
                    devices.append(sd)
                logger.debug("Returning %d devices", len(devices))
                return devices
        except HTTPException:
            raise
        except Exception as ex:
            logger.error("Error in api_devices: %s\n%s", ex, ex)
            raise HTTPException(status_code=500, detail="Failed to retrieve devices")

    @app.post("/alias")
    def api_set_alias(update: AliasUpdate):
        logger.info("API call: /alias device=%s alias=%s", update.device, update.alias)
        try:
            backend = get_backend()
            if backend is None or not backend.is_alive():
                logger.warning("Alias update requested but backend not running")
                raise HTTPException(status_code=503, detail="backend not running")
            try:
                backend.set_alias(update.device, update.alias)
                logger.info("Alias update requested for device %s", update.device)
            except Exception as ex:
                logger.error("Error setting alias: %s\n%s", ex, ex)
                raise HTTPException(status_code=500, detail="Failed to set alias")
            return {"status": "ok"}
        except HTTPException:
            raise
        except Exception as ex:
            logger.error("Unhandled error in api_set_alias: %s\n%s", ex, ex)
            raise HTTPException(status_code=500, detail="Failed to set alias")

    @app.get("/groups")
    def api_get_groups() -> List[Dict[str, Any]]:
        logger.info("API call: /groups (GET)")
        try:
            names = _device_service.list_groups()
            groups = [{"index": i + 1, "name": n} for i, n in enumerate(names)]
            logger.debug("Groups: %s", groups)
            return groups
        except Exception as ex:
            logger.error("Error in api_get_groups: %s\n%s", ex, ex)
            raise HTTPException(status_code=500, detail="Failed to read groups")

    @app.post("/groups")
    def api_create_group(body: GroupName):
        logger.info("API call: /groups (POST) name=%s", body.name)
        try:
            new_index = _device_service.create_group(body.name)
            logger.info("Created group %s at index %d", body.name, new_index)
            return {"created": new_index, "name": body.name}
        except Exception as ex:
            logger.error("Error in api_create_group: %s\n%s", ex, ex)
            raise HTTPException(status_code=500, detail="Failed to create group")

    @app.put("/groups/{index}")
    def api_rename_group(index: int, body: GroupName):
        logger.info("API call: /groups/%d (PUT) name=%s", index, body.name)
        try:
            _device_service.rename_group(index, body.name)
            logger.info("Renamed group %d to %s", index, body.name)
            return {"renamed": index, "name": body.name}
        except ValueError as ex:
            logger.warning("Invalid rename request: %s", ex)
            raise HTTPException(status_code=400, detail=str(ex))
        except Exception as ex:
            logger.error("Error in api_rename_group: %s\n%s", ex, ex)
            raise HTTPException(status_code=500, detail="Failed to rename group")

    @app.delete("/groups/{index}")
    def api_delete_group(index: int):
        logger.info("API call: /groups/%d (DELETE)", index)
        try:
            backend = get_backend()
            devices = []
            if backend is not None:
                with backend.lock:
                    devices = backend.devices
                    result = _device_service.delete_group(devices, index)
            else:
                result = _device_service.delete_group([], None, DEVICES_INI, index)
            logger.info("Deleted group %d result=%s", index, result)
            return {"deleted": index, "result": result}
        except ValueError as ex:
            logger.warning("Invalid delete request: %s", ex)
            raise HTTPException(status_code=400, detail=str(ex))
        except Exception as ex:
            logger.error("Error in api_delete_group: %s\n%s", ex, ex)
            raise HTTPException(status_code=500, detail="Failed to delete group")

    @app.post("/groups/{index}/equalise")
    def api_equalise_group(index: int):
        logger.info("API call: /groups/%d/equalise", index)
        try:
            backend = get_backend()
            if backend is not None and backend.is_alive():
                with backend.lock:
                    try:
                        result = _device_service.equalise_group(backend.devices, index)
                        logger.info("Equalised group %d result=%s", index, result)
                    except Exception as ex:
                        logger.error("Error during equalise operation: %s\n%s", ex, ex)
                        raise HTTPException(status_code=500, detail="Equalise operation failed")
            else:
                try:
                    result = _device_service.equalise_group([], index)
                    logger.info("Equalised group %d (backend not running) result=%s", index, result)
                except Exception as ex:
                    logger.error("Error during equalise operation (no backend): %s\n%s", ex, ex)
                    raise HTTPException(status_code=500, detail="Equalise operation failed")
            return {"status": "ok", "result": result}
        except HTTPException:
            raise
        except Exception as ex:
            logger.error("Unhandled error in api_equalise_group: %s\n%s", ex, ex)
            raise HTTPException(status_code=500, detail="Failed to equalise group")

    @app.get("/tempdata")
    def api_tempdata(summary_mins: Optional[int] = 0,
                     from_dt: Optional[str] = None,
                     to_dt: Optional[str] = None,
                     device_id: Optional[str] = '%'):
        logger.info("API call: /tempdata summary_mins=%s from=%s to=%s device=%s", summary_mins, from_dt, to_dt, device_id)
        try:
            if not _sqlite.db_enabled:
                logger.warning("DB disabled, returning empty result for /tempdata")
                return []

            fd = None
            td = None
            if from_dt:
                try:
                    fd = datetime.fromisoformat(from_dt)
                except Exception:
                    raise HTTPException(status_code=400, detail="Invalid from_dt; must be ISO8601")
            if to_dt:
                try:
                    td = datetime.fromisoformat(to_dt)
                except Exception:
                    raise HTTPException(status_code=400, detail="Invalid to_dt; must be ISO8601")

            data = _sqlite.get_dataset(summary_mins=summary_mins or 0, from_dt=fd, to_dt=td, device_id=device_id)
            return data
        except ValueError as ex:
            raise HTTPException(status_code=400, detail=str(ex))
        except HTTPException:
            raise
        except Exception as ex:
            logger.exception("Unhandled error in api_tempdata: %s", ex)
            raise HTTPException(status_code=500, detail="Failed to retrieve temperature data")

    @app.get("/summaryperiods")
    def api_get_summary_periods():
        logger.info("API call: /summaryperiods")
        try:
            if not _sqlite.db_enabled:
                logger.warning("DB disabled, returning [0]")
                return [0]
            periods = _sqlite.list_summary_periods()
            return periods
        except Exception:
            logger.exception("Failed to retrieve summary periods")
            raise HTTPException(status_code=500, detail="Failed to retrieve summary periods")

    @app.post("/cleardb")
    def api_clear_db():
        """
        Clear the SQLite database (remove all readings and summaries).
        Returns JSON indicating whether the DB was cleared.
        """
        logger.info("API call: /cleardb")
        try:
            if _sqlite is None or not _sqlite.db_enabled:
                logger.warning("Clear DB requested but DB is disabled or not available")
                return {"status": "ok", "cleared": False, "reason": "db disabled or unavailable"}
            # perform clear operation
            _sqlite.clear_database()
            logger.info("SQLite database cleared by API request")
            return {"status": "ok", "cleared": True}
        except Exception:
            logger.exception("Failed to clear sqlite database")
            raise HTTPException(status_code=500, detail="Failed to clear sqlite database")