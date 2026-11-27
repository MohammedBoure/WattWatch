#!/usr/bin/env python3
"""
WattWatch CLI Power Management Utility (Intel RAPL)
Provides:
  1. Live real-time power draw monitoring (Watts).
  2. Historical range queries calculating total consumed energy (kWh / Joules)
     and average wattage between two user-supplied timestamps.
"""

import os
import sys
import glob
import time
import argparse
from datetime import datetime
from typing import List, Tuple, Optional, Dict, Any

DEFAULT_ENERGY_PATH = "/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj"
DEFAULT_MAX_RANGE_PATH = "/sys/class/powercap/intel-rapl/intel-rapl:0/max_energy_range_uj"
DEFAULT_LOG_PATH = "/var/log/power_history.csv"
FALLBACK_MAX_RANGE_UJ = 65532610987
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


# ============================================================================
# Helpers: Sysfs Reading & Energy Calculation
# ============================================================================

def read_energy_uj(energy_path: str = DEFAULT_ENERGY_PATH) -> int:
    """Read energy counter in microjoules from Intel RAPL sysfs."""
    try:
        with open(energy_path, "r") as f:
            return int(f.read().strip())
    except PermissionError:
        raise PermissionError(
            f"Permission denied reading '{energy_path}'. "
            "Please check file permissions or run with appropriate privileges."
        )
    except FileNotFoundError:
        raise FileNotFoundError(
            f"RAPL interface '{energy_path}' not found. "
            "Please ensure Intel RAPL kernel modules (intel_rapl_msr/powercap) are loaded."
        )


def read_max_energy_range_uj(max_range_path: str = DEFAULT_MAX_RANGE_PATH) -> int:
    """Read maximum counter range before overflow from Intel RAPL sysfs."""
    try:
        with open(max_range_path, "r") as f:
            return int(f.read().strip())
    except Exception:
        return FALLBACK_MAX_RANGE_UJ


def compute_watts(
    curr_uj: int, last_uj: int, delta_seconds: float, max_range_uj: int
) -> float:
    """
    Calculate real-time power in Watts from microjoule delta and elapsed time.
    Handles counter overflow/wrap-around when curr_uj < last_uj.
    """
    if delta_seconds <= 0:
        return 0.0

    if curr_uj >= last_uj:
        delta_uj = curr_uj - last_uj
    else:
        # Counter wrap-around
        delta_uj = (max_range_uj - last_uj) + curr_uj

    return (delta_uj / 1_000_000.0) / delta_seconds


def _read_sysfs_str(path: str) -> Optional[str]:
    """Read and strip a sysfs text file, returning None if unavailable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def _read_sysfs_int(path: str) -> Optional[int]:
    """Read an integer from a sysfs file, returning None if unavailable."""
    val = _read_sysfs_str(path)
    if val is None:
        return None
    try:
        return int(val)
    except ValueError:
        return None


def find_rapl_domain_by_name(
    target_name: str, base_dir: str = "/sys/class/powercap"
) -> Optional[Tuple[str, str]]:
    """
    Find a readable Intel RAPL domain directory matching target_name (e.g. 'psys').
    Returns (energy_uj_path, max_energy_range_uj_path) or None.
    """
    for domain_dir in sorted(glob.glob(os.path.join(base_dir, "intel-rapl*"))):
        name = _read_sysfs_str(os.path.join(domain_dir, "name"))
        if name == target_name:
            energy_file = os.path.join(domain_dir, "energy_uj")
            max_file = os.path.join(domain_dir, "max_energy_range_uj")
            if os.access(energy_file, os.R_OK):
                return energy_file, max_file
    return None


def read_power_supply_info(
    power_supply_dir: str = "/sys/class/power_supply",
) -> Dict[str, Any]:
    """
    Inspect /sys/class/power_supply for Mains (AC) and Battery (BAT*) metrics.
    Computes battery wattage from power_now (uW) or current_now (uA) * voltage_now (uV).
    """
    info: Dict[str, Any] = {
        "ac_online": None,
        "ac_power_watts": None,
        "battery_name": None,
        "battery_status": None,
        "battery_watts": None,
        "battery_capacity": None,
    }

    if not os.path.isdir(power_supply_dir):
        return info

    for entry in sorted(os.listdir(power_supply_dir)):
        dev_dir = os.path.join(power_supply_dir, entry)
        if not os.path.isdir(dev_dir):
            continue

        supply_type = _read_sysfs_str(os.path.join(dev_dir, "type"))
        if supply_type == "Mains":
            online = _read_sysfs_int(os.path.join(dev_dir, "online"))
            if online is not None:
                info["ac_online"] = bool(online)
            ac_power_uw = _read_sysfs_int(os.path.join(dev_dir, "power_now"))
            if ac_power_uw is not None and ac_power_uw > 0:
                info["ac_power_watts"] = ac_power_uw / 1_000_000.0

        elif supply_type == "Battery" and info["battery_name"] is None:
            present = _read_sysfs_int(os.path.join(dev_dir, "present"))
            if present == 0:
                continue

            info["battery_name"] = entry
            info["battery_status"] = _read_sysfs_str(os.path.join(dev_dir, "status"))
            info["battery_capacity"] = _read_sysfs_int(os.path.join(dev_dir, "capacity"))

            power_uw = _read_sysfs_int(os.path.join(dev_dir, "power_now"))
            if power_uw is not None:
                info["battery_watts"] = abs(power_uw) / 1_000_000.0
            else:
                current_ua = _read_sysfs_int(os.path.join(dev_dir, "current_now"))
                voltage_uv = _read_sysfs_int(os.path.join(dev_dir, "voltage_now"))
                if current_ua is not None and voltage_uv is not None:
                    info["battery_watts"] = (
                        abs(current_ua) * abs(voltage_uv)
                    ) / 1_000_000_000_000.0

    return info


def resolve_device_power(
    cpu_watts: float,
    ps_info: Dict[str, Any],
    psys_watts: Optional[float] = None,
) -> Tuple[Optional[float], str]:
    """
    Determine total device power consumption (Watts) and a concise source label.
    Priority:
      1. Battery discharging power (actual full-device draw on battery)
      2. Mains/AC power_now (if exposed by hardware)
      3. Intel RAPL 'psys' domain (SoC/platform power, if exposed)
    Returns (total_watts_or_None, status_label).
    """
    bat_status = ps_info.get("battery_status")
    bat_watts = ps_info.get("battery_watts")
    bat_name = ps_info.get("battery_name") or "BAT"
    bat_cap = ps_info.get("battery_capacity")

    if bat_status == "Discharging" and bat_watts is not None:
        cap_str = f" {bat_cap}%" if bat_cap is not None else ""
        return bat_watts, f"{bat_name}{cap_str}"

    if ps_info.get("ac_power_watts") is not None:
        return ps_info["ac_power_watts"], "AC"

    if psys_watts is not None:
        return psys_watts, "psys"

    if ps_info.get("ac_online") or bat_status in ("Charging", "Full", "Not charging"):
        detail_parts = ["On AC"]
        if bat_status == "Charging" and bat_watts is not None:
            detail_parts.append(f"Charging {bat_watts:.2f}W")
        elif bat_status:
            detail_parts.append(bat_status)
        if bat_cap is not None:
            detail_parts.append(f"{bat_cap}%")
        return None, ", ".join(detail_parts)

    return None, "No full-device sensor"


def format_live_status_line(
    cpu_watts: float,
    total_watts: Optional[float],
    source_label: str,
    now_str: str,
    sample_count: int,
) -> str:
    """Format the single-line (\\r) live monitor output."""
    if total_watts is not None:
        other_watts = max(0.0, total_watts - cpu_watts)
        return (
            f"\r[Live] Device Total: {total_watts:6.2f} W "
            f"(CPU: {cpu_watts:5.2f} W | Other: {other_watts:5.2f} W) "
            f"[{source_label}] | {now_str} | #{sample_count}  "
        )
    return (
        f"\r[Live] Device Total: N/A ({source_label}) | "
        f"CPU: {cpu_watts:5.2f} W | {now_str} | #{sample_count}  "
    )


# ============================================================================
# Feature 1: Live Real-Time Power Draw Monitoring
# ============================================================================

def run_live_view(
    energy_path: str = DEFAULT_ENERGY_PATH,
    max_range_path: str = DEFAULT_MAX_RANGE_PATH,
    interval: float = 1.0,
    power_supply_dir: str = "/sys/class/power_supply",
):
    """
    Stream live real-time full-device and CPU power draw updates on a single line (\\r).
    Exits gracefully on Ctrl+C.
    """
    psys_paths = find_rapl_domain_by_name("psys")
    initial_ps = read_power_supply_info(power_supply_dir)

    print("\n" + "=" * 68)
    print("      WattWatch: Live Full-Device & CPU Power Monitor")
    print("=" * 68)
    print(f"CPU RAPL interface : {energy_path}")
    if initial_ps["battery_name"]:
        print(
            f"Power Supply       : {initial_ps['battery_name']} "
            f"({initial_ps['battery_status'] or 'Unknown'})"
        )
    if psys_paths:
        print(f"Platform RAPL      : {psys_paths[0]} (psys)")
    if (
        initial_ps.get("ac_online")
        and initial_ps.get("ac_power_watts") is None
        and not psys_paths
    ):
        print(
            "Note               : On AC without wall-power/psys sensor; "
            "unplug AC to measure full-device battery draw."
        )
    print(f"Refresh interval   : {interval:.1f} second(s)")
    print("Streaming live power draw below. Press Ctrl+C to stop.\n")

    try:
        max_range_uj = read_max_energy_range_uj(max_range_path)
        last_uj = read_energy_uj(energy_path)
        last_psys_uj = read_energy_uj(psys_paths[0]) if psys_paths else None
        psys_max_range = (
            read_max_energy_range_uj(psys_paths[1]) if psys_paths else None
        )
        last_time = time.time()
    except Exception as e:
        print(f"\n[Error] Unable to initialize live monitoring: {e}", file=sys.stderr)
        return

    sample_count = 0
    try:
        while True:
            time.sleep(interval)
            curr_uj = read_energy_uj(energy_path)
            curr_psys_uj = read_energy_uj(psys_paths[0]) if psys_paths else None
            curr_time = time.time()
            delta_seconds = curr_time - last_time

            cpu_watts = compute_watts(curr_uj, last_uj, delta_seconds, max_range_uj)
            psys_watts = None
            if (
                psys_paths
                and curr_psys_uj is not None
                and last_psys_uj is not None
                and psys_max_range is not None
            ):
                psys_watts = compute_watts(
                    curr_psys_uj, last_psys_uj, delta_seconds, psys_max_range
                )

            ps_info = read_power_supply_info(power_supply_dir)
            total_watts, source_label = resolve_device_power(
                cpu_watts=cpu_watts, ps_info=ps_info, psys_watts=psys_watts
            )

            sample_count += 1
            now_str = datetime.now().strftime("%H:%M:%S")
            status_line = format_live_status_line(
                cpu_watts=cpu_watts,
                total_watts=total_watts,
                source_label=source_label,
                now_str=now_str,
                sample_count=sample_count,
            )
            sys.stdout.write(status_line)
            sys.stdout.flush()

            last_uj = curr_uj
            last_psys_uj = curr_psys_uj
            last_time = curr_time

    except KeyboardInterrupt:
        print("\n\n[Info] Live monitoring terminated cleanly.")


# ============================================================================
# Feature 2: Historical Range Query & Calculations
# ============================================================================

def parse_log_file(log_path: str) -> List[Tuple[datetime, float]]:
    """
    Parse /var/log/power_history.csv into a list of (datetime, watts) tuples.
    Skips invalid/empty lines or headers gracefully.
    """
    if not os.path.exists(log_path):
        raise FileNotFoundError(
            f"Log file '{log_path}' does not exist. "
            "Ensure the power-logger service is running and writing logs."
        )

    records: List[Tuple[datetime, float]] = []
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue

            parts = line.split(",")
            if len(parts) != 2:
                continue

            dt_str, watts_str = parts[0].strip(), parts[1].strip()
            try:
                dt = datetime.strptime(dt_str, DATETIME_FORMAT)
                watts = float(watts_str)
                records.append((dt, watts))
            except ValueError:
                # Silently skip header lines or malformed entries
                continue

    return records


def calculate_range_metrics(
    records: List[Tuple[datetime, float]],
    start_dt: datetime,
    end_dt: datetime,
) -> Optional[dict]:
    """
    Filter records within [start_dt, end_dt] and calculate:
      - Average power consumption (Watts)
      - Total energy consumed in Joules (Watts * TotalSeconds)
      - Total energy consumed in Kilowatt-hours (Joules / 3,600,000)
      - Total sampled duration (in minutes and seconds)
    """
    # Filter rows within inclusive range [start_dt, end_dt]
    filtered = [r for r in records if start_dt <= r[0] <= end_dt]

    if not filtered:
        return None

    # Sort filtered records by datetime just in case
    filtered.sort(key=lambda x: x[0])

    num_samples = len(filtered)
    watts_values = [w for _, w in filtered]
    avg_watts = sum(watts_values) / num_samples

    first_dt = filtered[0][0]
    last_dt = filtered[-1][0]
    total_seconds = (last_dt - first_dt).total_seconds()

    # Total energy consumed in Joules = Watts * TotalSeconds
    total_joules = avg_watts * total_seconds

    # Total energy consumed in Kilowatt-hours = Joules / 3,600,000
    total_kwh = total_joules / 3_600_000.0

    duration_minutes = int(total_seconds // 60)
    duration_seconds = int(total_seconds % 60)

    return {
        "num_samples": num_samples,
        "first_dt": first_dt,
        "last_dt": last_dt,
        "total_seconds": total_seconds,
        "duration_minutes": duration_minutes,
        "duration_seconds": duration_seconds,
        "avg_watts": avg_watts,
        "total_joules": total_joules,
        "total_kwh": total_kwh,
    }


def display_range_report(
    start_dt: datetime,
    end_dt: datetime,
    metrics: Optional[dict],
    all_records: List[Tuple[datetime, float]],
):
    """Format and print the historical analysis report."""
    print("\n" + "=" * 62)
    print("         WattWatch: Historical Power Consumption Report")
    print("=" * 62)
    print(f"Requested Start : {start_dt.strftime(DATETIME_FORMAT)}")
    print(f"Requested End   : {end_dt.strftime(DATETIME_FORMAT)}")
    print("-" * 62)

    if metrics is None:
        print("[!] No log entries found within the specified date range.")
        if all_records:
            earliest = min(r[0] for r in all_records).strftime(DATETIME_FORMAT)
            latest = max(r[0] for r in all_records).strftime(DATETIME_FORMAT)
            print(f"    Available log range: {earliest} to {latest}")
            print(f"    Total log entries in file: {len(all_records)}")
        else:
            print("    The log file currently contains 0 valid entries.")
        print("=" * 62 + "\n")
        return

    duration_str = (
        f"{metrics['duration_minutes']} minute(s) and {metrics['duration_seconds']} second(s) "
        f"({metrics['total_seconds']:.1f}s total)"
    )

    print(f"Total Samples Recorded : {metrics['num_samples']}")
    print(f"First Sample Timestamp : {metrics['first_dt'].strftime(DATETIME_FORMAT)}")
    print(f"Last Sample Timestamp  : {metrics['last_dt'].strftime(DATETIME_FORMAT)}")
    print(f"Sampled Duration       : {duration_str}")
    print("-" * 62)
    print(f"Average Power Draw     : {metrics['avg_watts']:.2f} Watts")
    print(f"Total Energy (Joules)  : {metrics['total_joules']:.2f} J")
    print(f"Total Energy (kWh)     : {metrics['total_kwh']:.6f} kWh")
    print("=" * 62 + "\n")


def prompt_for_datetime(prompt_text: str) -> Optional[datetime]:
    """
    Prompt user for a datetime string and validate it against DATETIME_FORMAT.
    Allows user to type 'q' or 'cancel' to abort.
    """
    while True:
        try:
            user_input = input(prompt_text).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None

        if not user_input or user_input.lower() in ("q", "quit", "cancel"):
            return None

        try:
            return datetime.strptime(user_input, DATETIME_FORMAT)
        except ValueError:
            print(
                f"[Error] Invalid format '{user_input}'. "
                f"Expected '{DATETIME_FORMAT}' (e.g. 2026-10-10 14:30:00)."
            )


def run_historical_query(
    log_path: str = DEFAULT_LOG_PATH,
    start_str: Optional[str] = None,
    end_str: Optional[str] = None,
):
    """
    Perform historical query on power log file. Prompts interactively if
    start_str or end_str are omitted.
    """
    print("\n" + "=" * 62)
    print("         WattWatch: Historical Energy Range Query")
    print("=" * 62)
    print(f"Target log file: {log_path}\n")

    try:
        records = parse_log_file(log_path)
    except Exception as err:
        print(f"[Error] {err}\n", file=sys.stderr)
        return

    # Parse or prompt for Start DateTime
    start_dt: Optional[datetime] = None
    if start_str:
        try:
            start_dt = datetime.strptime(start_str.strip(), DATETIME_FORMAT)
        except ValueError:
            print(
                f"[Error] Invalid start date '{start_str}'. Format must be '{DATETIME_FORMAT}'.",
                file=sys.stderr,
            )
            return
    else:
        start_dt = prompt_for_datetime("Enter Start Date & Time (YYYY-MM-DD HH:MM:SS) [or 'q' to abort]: ")
        if start_dt is None:
            print("[Info] Query aborted by user.\n")
            return

    # Parse or prompt for End DateTime
    end_dt: Optional[datetime] = None
    if end_str:
        try:
            end_dt = datetime.strptime(end_str.strip(), DATETIME_FORMAT)
        except ValueError:
            print(
                f"[Error] Invalid end date '{end_str}'. Format must be '{DATETIME_FORMAT}'.",
                file=sys.stderr,
            )
            return
    else:
        end_dt = prompt_for_datetime("Enter End Date & Time   (YYYY-MM-DD HH:MM:SS) [or 'q' to abort]: ")
        if end_dt is None:
            print("[Info] Query aborted by user.\n")
            return

    # Validate logical range
    if start_dt > end_dt:
        print(
            f"[Error] Start timestamp ({start_dt}) cannot be after End timestamp ({end_dt}).\n",
            file=sys.stderr,
        )
        return

    # Compute and display report
    metrics = calculate_range_metrics(records, start_dt, end_dt)
    display_range_report(start_dt, end_dt, metrics, records)


# ============================================================================
# Interactive CLI Menu
# ============================================================================

def interactive_menu(
    energy_path: str = DEFAULT_ENERGY_PATH,
    max_range_path: str = DEFAULT_MAX_RANGE_PATH,
    log_path: str = DEFAULT_LOG_PATH,
):
    """Interactive top-level navigation menu."""
    while True:
        print("\n" + "=" * 62)
        print("          WattWatch: Intel RAPL Power Management Tool")
        print("=" * 62)
        print("  [1] Live View              - Real-time power draw (Watts)")
        print("  [2] Historical Range Query - Energy consumption (kWh / Joules)")
        print("  [3] Exit")
        print("=" * 62)

        try:
            choice = input("Select an option [1-3]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break

        if choice == "1":
            run_live_view(energy_path=energy_path, max_range_path=max_range_path)
        elif choice == "2":
            run_historical_query(log_path=log_path)
        elif choice in ("3", "q", "quit", "exit"):
            print("Goodbye.")
            break
        else:
            print("[Error] Invalid selection. Please enter 1, 2, or 3.")


# ============================================================================
# Main Entry Point & Argument Parsing
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="WattWatch - Intel RAPL Power Tracking & Analysis CLI Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Subcommand: live
    live_parser = subparsers.add_parser("live", help="Stream live real-time wattage")
    live_parser.add_argument(
        "--energy-path",
        default=DEFAULT_ENERGY_PATH,
        help="Path to RAPL energy_uj sysfs file",
    )
    live_parser.add_argument(
        "--max-range-path",
        default=DEFAULT_MAX_RANGE_PATH,
        help="Path to RAPL max_energy_range_uj sysfs file",
    )
    live_parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Sampling interval in seconds (default: 1.0)",
    )

    # Subcommand: query / history
    query_parser = subparsers.add_parser(
        "query", aliases=["history"], help="Historical range power query"
    )
    query_parser.add_argument(
        "--log-path",
        default=DEFAULT_LOG_PATH,
        help="Path to CSV log file (default: /var/log/power_history.csv)",
    )
    query_parser.add_argument(
        "--start",
        default=None,
        help="Start datetime (YYYY-MM-DD HH:MM:SS)",
    )
    query_parser.add_argument(
        "--end",
        default=None,
        help="End datetime (YYYY-MM-DD HH:MM:SS)",
    )

    args = parser.parse_args()

    if args.command == "live":
        run_live_view(
            energy_path=args.energy_path,
            max_range_path=args.max_range_path,
            interval=args.interval,
        )
    elif args.command in ("query", "history"):
        run_historical_query(
            log_path=args.log_path,
            start_str=args.start,
            end_str=args.end,
        )
    else:
        # Default behavior: run interactive menu
        interactive_menu()


if __name__ == "__main__":
    main()
