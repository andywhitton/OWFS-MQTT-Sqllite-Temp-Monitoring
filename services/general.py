import logging

#Constants
DEVICES_INI = 'ow_devices.ini'# groups and per-device AF storage
GENERAL_INI = 'owtemp.ini'  # config file for OneWire_Backend

LOG_LEVEL = logging.DEBUG  # Default log level

# Convert Celsius to Fahrenheit
def _c_to_f(celsius: float) -> float:
    return (celsius * 9.0 / 5.0) + 32.0

