#!/bin/bash

# Script to copy updated OneWire Backend files to /opt/onewire/ and restart the service

echo "Updating OneWire Backend..."
sudo cp -v --update /home/andy/OneWireToMQTT/OneWire_Backend.py /opt/onewire/
sudo cp -v --update /home/andy/OneWireToMQTT/owtemp.ini /opt/onewire/
sudo cp -v --update /home/andy/OneWireToMQTT/ow_devices.ini /opt/onewire/
sudo cp -v --update /home/andy/OneWireToMQTT/favicon.ico /opt/onewire/

sudo cp -v --update /home/andy/OneWireToMQTT/services/* /opt/onewire/services/

# Restart the service to apply changes
echo Restarting OneWire service...
sudo systemctl daemon-reload
sudo systemctl restart onewire
echo OneWire service restarted

echo "OneWire Backend updated"
