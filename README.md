# WattWatch: Persistent Intel RAPL Power Consumption Tracker & CLI Tool

WattWatch is a lightweight, automated Linux power-tracking solution that measures processor energy consumption using the Intel Running Average Power Limit (RAPL) sysfs interface (`/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj`).

---

## Directory File Index

This directory contains the source code, service definitions, automated installation tooling, and test suites for WattWatch:

| File | Purpose |
| :--- | :--- |
| `power_logger.py` | Daemon script sampling Intel RAPL `energy_uj` at regular intervals (every 2.0 seconds), handling counter wrap-around/overflow, and appending real-time power metrics (`YYYY-MM-DD HH:MM:SS,power_watts`) to `/var/log/power_history.csv`. |
| `powertool.py` | Interactive CLI application providing real-time single-line wattage streaming (`\r`) and historical range queries (Average Watts, total Joules, total kWh, and duration) across logged timestamps. |
| `powertool` | Executable symlink to `powertool.py` enabling direct invocation in the terminal. |
| `power-logger.service` | Systemd service unit configuring `power_logger.py` as an automated background daemon with `Restart=always` and automatic restart delays. |
| `install.sh` | Deployment script installing the daemon into `/opt/power-tracker/`, registering `/usr/local/bin/powertool`, enabling sysfs permissions via `tmpfiles.d`, and activating the systemd service. |
| `test_wattwatch.py` | Comprehensive unit and integration test suite covering counter overflow calculations, CSV log parsing, metric formulas, and hardware sysfs readability. |
| `.gitignore` | Git ignore rules excluding Python bytecode caches (`__pycache__`). |
| `README.md` | Directory documentation explaining file roles, architectural details, and operational instructions. |

---

## Architecture & Math Formulas

### 1. Real-Time Power Calculation
Intel RAPL exposes cumulative energy in microjoules ($\mu J$). Real-time wattage is calculated as:
$$\text{Watts} = \frac{\Delta \text{Energy}_{\mu J}}{1{,}000{,}000 \times \Delta t_{\text{seconds}}}$$

### 2. Counter Wrap-Around / Overflow Handling
The 32-bit hardware counter wraps around when `curr_uj < last_uj`. The maximum energy range is read from `/sys/class/powercap/intel-rapl/intel-rapl:0/max_energy_range_uj` (defaulting to 65,532,610,987 $\mu J$ if absent). When wrap-around occurs:
$$\Delta \text{Energy}_{\mu J} = (\text{max\_range}_{\mu J} - \text{last}_{\mu J}) + \text{curr}_{\mu J}$$

### 3. Historical Range Query Metrics
Between a given `[start_dt, end_dt]`:
- **Average Power**: $\bar{W} = \frac{1}{N} \sum_{i=1}^N W_i$
- **Total Sampled Duration**: $\text{TotalSeconds} = (t_{\text{last}} - t_{\text{first}})$
- **Total Energy (Joules)**: $\text{Joules} = \bar{W} \times \text{TotalSeconds}$
- **Total Energy (kWh)**: $\text{kWh} = \frac{\text{Joules}}{3{,}600{,}000}$

---

## Quick Start & Usage

### Running Tests
```bash
python3 -m unittest -v test_wattwatch.py
```

### Deploying the Systemd Daemon and CLI Tool
```bash
sudo ./install.sh
```

### Launching the CLI Tool
```bash
# Interactive menu (Live Monitor or Historical Range Query):
powertool

# Stream live wattage directly:
powertool live

# Query historical range directly:
powertool query --start "2026-10-10 22:00:00" --end "2026-10-10 22:30:00"
```
