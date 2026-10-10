#!/usr/bin/env bash
set -euo pipefail

# WattWatch Installation and Deployment Script
# Deploys power_logger daemon, systemd unit, and CLI powertool utility.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Deploying WattWatch Intel RAPL Power Tracker ==="

# 1. Target directory setup
echo "[1/6] Creating /opt/power-tracker..."
mkdir -p /opt/power-tracker

# 2. Deploy daemon
echo "[2/6] Installing power_logger.py daemon..."
cp "${SCRIPT_DIR}/power_logger.py" /opt/power-tracker/power_logger.py
chmod 755 /opt/power-tracker/power_logger.py

# Deploy README in /opt/power-tracker as required by project guidelines
cat << 'EOF' > /opt/power-tracker/README.md
# Power Tracker Daemon Directory

This directory hosts the system-level daemon components for Intel RAPL power tracking.

## Files
- `power_logger.py`: Background Python daemon sampling Intel RAPL sysfs energy counters and logging wattage metrics to `/var/log/power_history.csv`.
- `README.md`: Directory overview and component documentation.
EOF

# 3. Deploy CLI tool
echo "[3/6] Installing /usr/local/bin/powertool..."
cp "${SCRIPT_DIR}/powertool.py" /usr/local/bin/powertool
chmod 755 /usr/local/bin/powertool

# 4. Configure persistent sysfs permissions
echo "[4/6] Configuring RAPL permissions..."
chmod 0444 /sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj || true
mkdir -p /etc/tmpfiles.d
cat << 'EOF' > /etc/tmpfiles.d/intel-rapl.conf
# Grant unprivileged read access to Intel RAPL energy counter for CLI powertool
z /sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj 0444 root root - -
EOF

# 5. Install systemd unit
echo "[5/6] Installing /etc/systemd/system/power-logger.service..."
cp "${SCRIPT_DIR}/power-logger.service" /etc/systemd/system/power-logger.service
chmod 644 /etc/systemd/system/power-logger.service

# 6. Reload and start systemd daemon
echo "[6/6] Reloading systemd and starting power-logger service..."
systemctl daemon-reload
systemctl enable --now power-logger.service

echo "=== Deployment Completed Successfully ==="
systemctl status power-logger.service --no-pager
