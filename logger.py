#!/usr/bin/env python3
"""
Continuous NI-9213 thermocouple logger.

Reads configured NI-DAQmx thermocouple channels, stores measurements in SQLite,
and writes daily CSV files. Designed to run continuously under systemd.

The NI-DAQmx driver itself must be installed separately. NI's nidaqmx Python
package is only the Python API layer.

Remote access achieved with tailscale with provided webapp.

ni-tc-logger, ni-tc-web and tailscaled all start automatically after booting. This is defined in systemd config files separately for all 3 instances.

useful terminal commands:
systemctl status ni-tc-web / ni-tc-logger / tailscaled
systemctl start ni-tc-web / ni-tc-logger / tailscaled
systemctl stop ni-tc-web / ni-tc-logger / tailscaled
journalctl -u ni-tc-web / ni-tc-logger / tailscaled -f
"""

from __future__ import annotations

import csv
import logging
import os
import shutil
import signal
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import nidaqmx
from nidaqmx.constants import ThermocoupleType
import yaml


CONFIG = Path(__file__).with_name("config.yaml")
STOP = threading.Event()

                                                                                #Definition of Backup and Watchdog

MIN_FREE_SPACE_GB = 10
WATCHDOG_TIMEOUT_S = 60
BACKUP_HOUR = 0
BACKUP_MINUTE = 0

def load_config():
    with CONFIG.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(log_file: Path):
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


                                                                                #Check available memory, error if less than 10gb

def check_free_space(data_dir: Path, conn=None):
    usage = shutil.disk_usage(data_dir)
    free_gb = usage.free / (1024 ** 3)

    if free_gb < MIN_FREE_SPACE_GB:
        message = f"low disk space: {free_gb: .2f} GB remaining"

        logging.warning(message)
    
        if conn is not None:
            write_event(conn, "WARNING", message)

        return False

    return True

def init_db(db_path: Path, channels):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30,)
                                                                                #WAL-mode, sql uses sqllite3-wal and sql3-shm
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS measurements (
            timestamp_utc TEXT NOT NULL,
            timestamp_local TEXT NOT NULL,
            channel TEXT NOT NULL,
            temperature_c REAL,
            status TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_measurements_time
        ON measurements(timestamp_utc)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def write_event(conn, level, message):
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO events VALUES (?, ?, ?)",
        (now.isoformat(), level, message),
    )
    conn.commit()
    logging.log(
        getattr(logging, level.upper(), logging.INFO),
        message
    )


def create_task(cfg):
    tc_type = {
        "K": ThermocoupleType.K,
        "J": ThermocoupleType.J,
        "T": ThermocoupleType.T,
        "E": ThermocoupleType.E,
        "R": ThermocoupleType.R,
        "S": ThermocoupleType.S,
        "B": ThermocoupleType.B,
        "N": ThermocoupleType.N,
    }[cfg["thermocouple_type"].upper()]

    task = nidaqmx.Task()

    for ch in cfg["channels"]:
        physical = f'{cfg["device"]}/{ch["physical"]}'
        task.ai_channels.add_ai_thrmcpl_chan(
            physical,
            name_to_assign_to_channel=ch["name"],
            min_val=float(cfg["min_temp_c"]),
            max_val=float(cfg["max_temp_c"]),
            units=nidaqmx.constants.TemperatureUnits.DEG_C,
            thermocouple_type=tc_type,
        )

    return task


def append_csv(csv_dir: Path, timestamp_local: datetime, values, channels):
    csv_dir.mkdir(parents=True, exist_ok=True)
    filename = csv_dir / f"{timestamp_local:%Y-%m-%d}.csv"
    new_file = not filename.exists()

    with filename.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(
                ["timestamp_local"] + [c["name"] for c in channels]
            )
        writer.writerow(
            [timestamp_local.isoformat(timespec="seconds")]
            + ["" if v is None else f"{v:.4f}" for v in values]
        )


def insert_measurement(conn, timestamp_utc, timestamp_local, values, channels):
    rows = []
    for ch, value in zip(channels, values):
        rows.append((
            timestamp_utc.isoformat(),
            timestamp_local.isoformat(timespec="seconds"),
            ch["name"],
            None if value is None else float(value),
            "OK" if value is not None else "ERROR",
        ))

    conn.executemany(
        "INSERT INTO measurements VALUES (?, ?, ?, ?, ?)",
        rows
    )
    conn.commit()

                                                                                                        #Watchdog definition
def watchdog(last_successful_write):
    while not STOP.is_set():
        time.sleep(10)

        elapsed = time.monotonic() - last_successful_write[0]

        if elapsed > WATCHDOG_TIMEOUT_S:
            logging.critical(
                "WATCHDOG: no successful write for %.1f seconds",
                elapsed
            )

            STOP.set()
            return

                                                                                                        #SQL-Backup (every day at 00:00)
def backup_database(db_path: Path, backup_dir: Path):
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_path = backup_dir / f"measurements_{timestamp}.sqlite3"

    source = sqlite3.connect(db_path)
    destination = sqlite3.connect(backup_path)

    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()

    logging.info("Database backup created: %s", backup_path)



def main():
    cfg = load_config()

    data_dir = Path(cfg["data_dir"])
    db_path = Path(cfg["database"])
    csv_dir = Path(cfg["csv_dir"])
    backup_dir = data_dir / "backups"
    setup_logging(Path(cfg["log_file"]))
    
    conn = init_db(db_path, cfg["channels"])

    check_free_space(data_dir, conn)

   
    write_event(conn, "INFO", "Logger starting")

    logging.info("Configured device: %s", cfg["device"])
    logging.info("Configured channels: %s", ", ".join(c["name"] for c in cfg["channels"]))
    logging.info("Sampling interval: %s s", cfg["sample_interval_s"])

    def handle_signal(signum, frame):
        logging.info("Shutdown signal received")
        STOP.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    
    task = None
    last_error = None

    last_backup_date = None
    last_disk_check = 0

    last_successful_write = [time.monotonic()]

    watchdog_thread = threading.Thread(
    target=watchdog,
    args=(last_successful_write,),
    daemon=True
    )
    watchdog_thread.start()

    try:
        while not STOP.is_set():
            try:
                now_local = datetime.now().astimezone()

                if(
                    now_local.hour == BACKUP_HOUR
                    #and now_local.minute == BACKUP_MINUTE                                      #possible to specify backup at minute 0, but unsure if stable
                    and last_backup_date != now_local.date()
                ):
                    backup_database(db_path, backup_dir)
                    last_backup_date = now_local.date()
                
                if time.monotonic() - last_disk_check > 6000:                                    #check every 6000s if enough space left
                    check_free_space(data_dir)
                    last_disk_check = time.monotonic()

                if task is None:
                    logging.info("Opening NI-DAQmx task")
                    task = create_task(cfg)
                    task.start()
                    write_event(conn, "INFO", "DAQ task started")

                values = task.read()
                if not isinstance(values, list):
                    values = [values]

                now_utc = datetime.now(timezone.utc)
                now_local = datetime.now().astimezone()

                insert_measurement(
                    conn, now_utc, now_local, values, cfg["channels"]
                )
                append_csv(csv_dir, now_local, values, cfg["channels"])

                last_successful_write[0] = time.monotonic()
                
                logging.info(
                    "T: %s",
                    " | ".join(
                        f"{c['name']}={v:.2f} °C"
                        for c, v in zip(cfg["channels"], values)
                    )
                )

                last_error = None
                STOP.wait(float(cfg["sample_interval_s"]))

            except Exception as exc:
                msg = f"DAQ error: {type(exc).__name__}: {exc}"
                if msg != last_error:
                    logging.exception(msg)
                    write_event(conn, "ERROR", msg)
                    last_error = msg

                if task is not None:
                    try:
                        task.close()
                    except Exception:
                        pass
                    task = None

                # Do not hammer a failed DAQ/device.
                STOP.wait(10)

            

    finally:
        if task is not None:
            try:
                task.stop()
            except Exception:
                pass
            task.close()

        write_event(conn, "INFO", "Logger stopped")
        conn.close()


if __name__ == "__main__":
    main()
