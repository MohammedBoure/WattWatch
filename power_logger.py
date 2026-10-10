#!/usr/bin/env python3
"""
Intel RAPL Power Consumption Logger Daemon
Continuously reads energy counters from Intel RAPL via sysfs and logs
real-time wattage to /var/log/power_history.csv.
"""

import os
import sys
import time
import signal
import logging
import argparse
from datetime import datetime

DEFAULT_ENERGY_PATH = "/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj"
DEFAULT_MAX_RANGE_PATH = "/sys/class/powercap/intel-rapl/intel-rapl:0/max_energy_range_uj"
DEFAULT_LOG_PATH = "/var/log/power_history.csv"
DEFAULT_INTERVAL = 2.0  # seconds (2 to 3 seconds requirement)

# Fallback max energy range in uJ (~65532.6 Joules for Intel RAPL package-0)
FALLBACK_MAX_RANGE_UJ = 65532610987

# Global termination flag for clean signal handling
_running = True


def handle_shutdown_signal(signum, frame):
    """Signal handler for graceful shutdown on SIGTERM / SIGINT."""
    global _running
    _running = False
    logging.info(f"Received signal {signum}, initiating graceful shutdown...")


def read_energy_uj(energy_path: str) -> int:
    """Read cumulative energy counter in microjoules from RAPL sysfs."""
    with open(energy_path, "r") as f:
        return int(f.read().strip())


def read_max_energy_range_uj(max_range_path: str) -> int:
    """Read maximum counter range before wrap-around from RAPL sysfs."""
    try:
        with open(max_range_path, "r") as f:
            return int(f.read().strip())
    except (OSError, ValueError) as err:
        logging.warning(
            f"Could not read max_energy_range_uj from {max_range_path} ({err}). "
            f"Using fallback value: {FALLBACK_MAX_RANGE_UJ}"
        )
        return FALLBACK_MAX_RANGE_UJ


def compute_watts(
    curr_uj: int, last_uj: int, delta_seconds: float, max_range_uj: int
) -> float:
    """
    Calculate real-time power in Watts from microjoule delta and time elapsed.
    Handles counter overflow/wrap-around when curr_uj < last_uj.
    """
    if delta_seconds <= 0:
        return 0.0

    if curr_uj >= last_uj:
        delta_uj = curr_uj - last_uj
    else:
        # Counter wrapped around 0
        delta_uj = (max_range_uj - last_uj) + curr_uj
        logging.info(
            f"RAPL counter wrap-around detected: last={last_uj}, curr={curr_uj}, "
            f"max={max_range_uj}, delta={delta_uj} uJ"
        )

    # Watts = (delta_uj / 1,000,000) / delta_seconds
    power_watts = (delta_uj / 1_000_000.0) / delta_seconds
    return power_watts


def run_daemon(
    energy_path: str = DEFAULT_ENERGY_PATH,
    max_range_path: str = DEFAULT_MAX_RANGE_PATH,
    log_path: str = DEFAULT_LOG_PATH,
    interval: float = DEFAULT_INTERVAL,
):
    """Main daemon loop for power logging."""
    global _running

    # Setup signal handlers
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)

    # Ensure log directory exists
    log_dir = os.path.dirname(os.path.abspath(log_path))
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    logging.info(f"Starting Intel RAPL Power Logger Daemon")
    logging.info(f"Source: {energy_path}")
    logging.info(f"Log destination: {log_path}")
    logging.info(f"Sampling interval: {interval} seconds")

    # Initial probe of max range
    max_range_uj = read_max_energy_range_uj(max_range_path)
    logging.info(f"Configured max energy range: {max_range_uj} uJ")

    # Initial sample reading
    try:
        last_uj = read_energy_uj(energy_path)
        last_time = time.time()
    except (OSError, ValueError) as err:
        logging.error(f"Failed initial read from {energy_path}: {err}")
        sys.exit(1)

    logging.info(f"Initial energy counter: {last_uj} uJ. Entering logging loop...")

    while _running:
        time.sleep(interval)
        if not _running:
            break

        try:
            curr_uj = read_energy_uj(energy_path)
            curr_time = time.time()
            delta_seconds = curr_time - last_time

            watts = compute_watts(curr_uj, last_uj, delta_seconds, max_range_uj)

            # Record timestamp in YYYY-MM-DD HH:MM:SS format
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_line = f"{now_str},{watts:.2f}\n"

            # Append to log file and ensure readable permissions (0644)
            file_exists = os.path.exists(log_path)
            with open(log_path, "a") as f:
                if not file_exists:
                    try:
                        os.chmod(log_path, 0o644)
                    except OSError:
                        pass
                f.write(log_line)
                f.flush()

            # Update state for next tick
            last_uj = curr_uj
            last_time = curr_time

        except (OSError, ValueError) as err:
            logging.error(f"Error sampling energy reading: {err}")
            # Brief wait before retry to avoid busy loops on transient faults
            time.sleep(1.0)
        except Exception as err:
            logging.exception(f"Unexpected error in daemon loop: {err}")
            time.sleep(1.0)

    logging.info("Intel RAPL Power Logger Daemon stopped cleanly.")


def main():
    parser = argparse.ArgumentParser(
        description="Intel RAPL Power Consumption Logger Daemon"
    )
    parser.add_argument(
        "--energy-path",
        default=DEFAULT_ENERGY_PATH,
        help="Path to RAPL energy_uj sysfs file",
    )
    parser.add_argument(
        "--max-range-path",
        default=DEFAULT_MAX_RANGE_PATH,
        help="Path to RAPL max_energy_range_uj sysfs file",
    )
    parser.add_argument(
        "--log-path",
        default=DEFAULT_LOG_PATH,
        help="Path to output CSV log file",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help="Sampling interval in seconds (default: 2.0)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    run_daemon(
        energy_path=args.energy_path,
        max_range_path=args.max_range_path,
        log_path=args.log_path,
        interval=args.interval,
    )


if __name__ == "__main__":
    main()
