#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Comprehensive Safe Power Tuning Installer for ASUSTeK K95VJ (Linux Mint)
#
# Behavior:
#   - On AC      : Full native performance (Turbo ON, schedutil, Runtime PM ON)
#   - On Battery : Automatic power savings via TLP (Turbo OFF, Runtime PM auto,
#                  SATA/Wi-Fi/Audio savings, 2.4G Wireless mouse protected)
#
# Usage:
#   sudo ./apply_power_tuning.sh          # Install and activate
#   sudo ./apply_power_tuning.sh --status # Inspect live power & GPU state
#   sudo ./apply_power_tuning.sh --revert # Remove TLP profile & restore defaults
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TLP_CONF_SRC="${SCRIPT_DIR}/01-asus-k95vj.conf"
TLP_CONF_DST="/etc/tlp.d/01-asus-k95vj.conf"
NOUVEAU_MODPROBE_DST="/etc/modprobe.d/99-asus-k95vj-nouveau-runpm.conf"
GRUB_DROPIN_DST="/etc/default/grub.d/99-asus-k95vj-power.cfg"
POWERTOOL_LINK="/usr/local/bin/powertool"

require_root() {
    if [ "${EUID}" -ne 0 ]; then
        echo "[Error] Please run with sudo: sudo $0 $*" >&2
        exit 1
    fi
}

show_status() {
    echo "=== Power & Hardware Status ==="
    echo "AC Online (ACAD) : $(cat /sys/class/power_supply/ACAD/online 2>/dev/null || echo unknown)"
    echo "Battery Status   : $(cat /sys/class/power_supply/BAT1/status 2>/dev/null || echo unknown)"
    echo "CPU Governor     : $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo unknown)"
    echo "CPU No-Turbo     : $(cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null || echo unknown)"
    echo "CPU Max Freq kHz : $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq 2>/dev/null || echo unknown)"
    echo "GPU 01:00.0 State: $(cat /sys/bus/pci/devices/0000:01:00.0/power_state 2>/dev/null || echo unknown) / $(cat /sys/bus/pci/devices/0000:01:00.0/power/runtime_status 2>/dev/null || echo unknown) (control: $(cat /sys/bus/pci/devices/0000:01:00.0/power/control 2>/dev/null || echo unknown))"
    echo "Root 00:01.0 Ctrl: $(cat /sys/bus/pci/devices/0000:00:01.0/power/control 2>/dev/null || echo unknown)"
    echo "TLP Service      : $(systemctl is-active tlp 2>/dev/null || true)"
    echo "Thermald Service : $(systemctl is-active thermald 2>/dev/null || true)"
    if command -v tlp-stat >/dev/null 2>&1; then
        echo ""
        tlp-stat -s | head -n 25
    fi
}

apply_tuning() {
    require_root "$@"

    echo "=== [1/5] Installing TLP (replaces conflicting power-profiles-daemon) ==="
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y tlp

    echo "=== [2/5] Installing ASUS K95VJ TLP profile to ${TLP_CONF_DST} ==="
    mkdir -p /etc/tlp.d
    cp "${TLP_CONF_SRC}" "${TLP_CONF_DST}"
    chmod 644 "${TLP_CONF_DST}"

    echo "=== [3/5] Allowing Nouveau runtime PM & disabling phantom VGA-2 output ==="
    cat << 'EOF' > "${NOUVEAU_MODPROBE_DST}"
# Enable nouveau runtime PM capability; TLP controls AC (on/active) vs Battery (auto/suspend)
options nouveau runpm=1
EOF
    chmod 644 "${NOUVEAU_MODPROBE_DST}"

    mkdir -p /etc/default/grub.d
    cat << 'EOF' > "${GRUB_DROPIN_DST}"
# Disable phantom VGA-2 load-detect on GT 635M so nouveau can enter DynOff on battery
GRUB_CMDLINE_LINUX_DEFAULT="${GRUB_CMDLINE_LINUX_DEFAULT} video=VGA-2:d"
EOF
    chmod 644 "${GRUB_DROPIN_DST}"
    update-grub >/dev/null 2>&1 || true

    echo "=== [4/5] Enabling TLP and keeping thermald active ==="
    systemctl enable --now tlp.service
    systemctl enable --now thermald.service 2>/dev/null || true
    tlp start

    echo "=== [5/5] Updating system-wide powertool CLI symlink ==="
    if [ -f "${PROJECT_DIR}/powertool.py" ]; then
        chmod 755 "${PROJECT_DIR}/powertool.py"
        ln -sf "${PROJECT_DIR}/powertool.py" "${POWERTOOL_LINK}"
        echo "Linked ${POWERTOOL_LINK} -> ${PROJECT_DIR}/powertool.py"
    fi

    echo ""
    echo "=== Comprehensive Power Tuning Applied Successfully ==="
    show_status
    echo ""
    echo "[Note] Reboot once so 'options nouveau runpm=1' takes full effect for GPU suspend on battery."
    echo "[Tip]  Run 'powertool live' on battery vs AC to verify the live wattage drop."
}

revert_tuning() {
    require_root "$@"

    echo "=== Reverting ASUS K95VJ Power Tuning ==="
    rm -f "${TLP_CONF_DST}" "${NOUVEAU_MODPROBE_DST}" "${GRUB_DROPIN_DST}"
    update-grub >/dev/null 2>&1 || true
    systemctl disable --now tlp.service 2>/dev/null || true
    DEBIAN_FRONTEND=noninteractive apt-get install -y power-profiles-daemon
    systemctl enable --now power-profiles-daemon.service 2>/dev/null || true
    echo "=== Reverted cleanly to default power-profiles-daemon ==="
}

case "${1:-apply}" in
    apply)
        apply_tuning "$@"
        ;;
    --status|status)
        show_status
        ;;
    --revert|revert)
        revert_tuning "$@"
        ;;
    *)
        echo "Usage: sudo $0 [apply|--status|--revert]" >&2
        exit 1
        ;;
esac
