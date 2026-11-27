# ASUS K95VJ Power Configuration (`power-config/`)

Hardware-tailored, battery-only power management setup for **ASUSTeK K95VJ** running **Linux Mint 22.3 (Ubuntu Noble)**, designed to keep **100% native full performance on AC** while applying safe automatic power savings when unplugged.

## Directory File Index

| File | Purpose |
| :--- | :--- |
| `01-asus-k95vj.conf` | TLP drop-in profile (`/etc/tlp.d/01-asus-k95vj.conf`) configuring AC (`schedutil`, Turbo ON, Runtime PM `on`, HDD APM `254`, Wi-Fi/Audio power save OFF) vs. Battery (`powersave`, Turbo OFF, 2.0 GHz cap, Runtime PM `auto` including GPU Root Port `00:01.0`, safe SATA/HDD savings, and explicit USB denylist for `32c2:0066` 2.4G Wireless receiver). |
| `apply_power_tuning.sh` | Idempotent deployment and management script that installs TLP, applies the drop-in config, enables `nouveau` runtime PM capability (honoring TLP AC/BAT rules), links `/usr/local/bin/powertool`, and supports `--status` and `--revert`. |
| `README.md` | Documentation for the `power-config/` directory and usage instructions. |

## Usage

```bash
# Apply comprehensive power configuration (requires sudo)
sudo ./power-config/apply_power_tuning.sh

# Check current AC/Battery, CPU, GPU, and TLP state
./power-config/apply_power_tuning.sh --status

# Revert cleanly back to default power-profiles-daemon
sudo ./power-config/apply_power_tuning.sh --revert
```
