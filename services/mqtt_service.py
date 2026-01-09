"""
MQTT wrapper around paho-mqtt for simple publish/subscribe.
"""
from pickle import FALSE
from typing import Optional
from xmlrpc.client import Boolean
import paho.mqtt.client as paho
import configparser
import os

from services.general import _c_to_f, GENERAL_INI,LOG_LEVEL
import logging

logger = logging.getLogger("onewire.backend")
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
logger.setLevel(LOG_LEVEL)

class MQTTService:
    def __init__(self) -> None:
        self._client: Optional[paho.Client] = None
        self._mqtt_scale: str = 'C'

        # MQTT base topic
        self.__mqtt_base: str = ''

    def run(self) -> None:
        logger.debug("mqtt service instantiated")

    def connect(self) -> Boolean:
        # Read configuration
        try:
            cfg = configparser.ConfigParser()
            cfg.read(GENERAL_INI)  # owtemp.ini for general settings
        except Exception:
            logger.exception("Failed to read ini %s", GENERAL_INI)
            return


        try:
            if cfg.has_section('MQTT'):
                if not cfg['MQTT'].getboolean('mqtt enabled', fallback=False):
                    logger.debug("MQTT not enabled in config")
                    return False

                self.__mqtt_base = cfg['MQTT'].get('mqtt base', '').strip()
                mqtt_host = cfg['MQTT'].get('mqtt host')
                mqtt_port = cfg['MQTT'].getint('mqtt port', fallback=1883)
                mqtt_callback_api_version = cfg['MQTT'].getint('mqtt callback api version', fallback=1)

                # allow per-MQTT reporting scale: fall back to REPORTING.scale
                self._mqtt_scale = cfg['MQTT'].get('mqtt scale', None).upper()
                if self._mqtt_scale is None:
                    self._mqtt_scale = cfg['REPORTING'].get('scale', 'C').upper() if cfg.has_section('REPORTING') else 'C'
                if self._mqtt_scale not in ('C', 'F'):
                    self._mqtt_scale = 'C'
        except Exception:
            logger.exception("Error checking MQTT configuration")

        cb_api_ver = paho.CallbackAPIVersion.VERSION2 if mqtt_callback_api_version == 2 else paho.CallbackAPIVersion.VERSION1

        # Create MQTT client
        self._client = paho.Client(client_id=mqtt_host, callback_api_version=cb_api_ver, protocol=paho.MQTTv311)
        
        # Resolve username/password:
        # 1) environment variables (e.g. loaded from .env via systemd EnvironmentFile)
        # 2) owtemp.ini [MQTT] section
        env_user = os.getenv('MQTT_USERNAME')
        env_pass = os.getenv('MQTT_PASSWORD')

        cfg_user = ''
        cfg_pass = ''
        try:
            if cfg.has_section('MQTT'):
                cfg_user = cfg['MQTT'].get('mqtt username', cfg['MQTT'].get('username', ''))
                cfg_pass = cfg['MQTT'].get('mqtt password', cfg['MQTT'].get('password', ''))
        except Exception:
            cfg_user = ''
            cfg_pass = ''

        username = env_user if env_user is not None and env_user != '' else (cfg_user or '')
        password = env_pass if env_pass is not None and env_pass != '' else (cfg_pass or '')

        # Set username and password if provided
        if username:
            # paho accepts password as None or empty string
            self._client.username_pw_set(username, password or None)

        try:
            # Connect to MQTT broker
            self._client.connect(mqtt_host, mqtt_port, 60)
            # Start the network loop
            self._client.loop_start()
            logger.info("MQTT connected (base=%s, report-scale=%s)", self.__mqtt_base, self._mqtt_scale)
            return True
        except Exception as ex:
            logger.error("Failed to connect to MQTT broker: %s", ex)
            self._client = None
            return False

    def publish(self, topic: str, payload: str) -> None:
        if self._client:
            self._client.publish(topic, payload)

    def publish_temperature(self, device_id: str, alias: str, temp_c: float) -> None:
        """
        Publish temperature to MQTT. Convert to F if mqtt scale configured as 'F'.
        temp_c is the temperature in Celsius (internal storage).
        """
        if self._client:
            # Convert according to MQTT reporting scale
            reported_value = temp_c
            try:
                if self._mqtt_scale == 'F':
                    reported_value = _c_to_f(float(temp_c))
            except Exception:
                # fallback to original Celsius value
                reported_value = temp_c

            # Build topic: mqtt_base / device_id / alias / Temperature
            base = self.__mqtt_base or ''
            topic = f"{self.__mqtt_base}/{device_id}/{alias}/Temperature".replace('//', '/')
            try:
                self.publish(topic, str(reported_value))
            except Exception:
                logger.exception("Failed to publish MQTT for device %s", device_id)

    def subscribe(self, topic: str) -> None:
        if self._client:
            self._client.subscribe(topic)

    def stop(self) -> None:
        if self._client:
            self._client.loop_stop()
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None