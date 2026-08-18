# NI-9213 Continuous Thermocouple Logger

A small Linux-based continuous logger for NI cDAQ + NI-9213 Type-K
thermocouples.

## Architecture

Type-K thermocouples -> NI-9213 -> cDAQ -> NI-DAQmx -> Python -> SQLite + CSV

The web interface is read-only and shows the latest value from every configured
channel.

## 1. Check the NI hardware first

Install NI-DAQmx for Desktop Linux before installing the Python package.

Then check whether Linux sees the device:

```bash
nilsdev
```

and/or:

```bash
nidaqmxconfig
```

Do NOT assume the device is `cDAQ1Mod1`. Use the identifier reported by Linux.

For example, if Linux reports:

```text
cDAQ6
cDAQ6Mod1
```

then use:

```yaml
device: "cDAQ6Mod1"
```

in `config.yaml`.

## 2. Check the channels

A four-channel example is included. Extend it to all channels on the NI-9213:

```yaml
channels:
  - name: "T01"
    physical: "ai0"
    description: "Thermocouple 1"
```

The NI-9213 has 16 thermocouple input channels. If multiple NI-9213 modules
are installed, use the module/channel identifier appropriate to each module.

## 3. Install Python environment

From this directory:

```bash
sudo apt update
sudo apt install python3 python3-venv
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
```

The NI-DAQmx driver is NOT installed by pip. The `nidaqmx` package is the
Python API layer and requires the NI-DAQmx driver to already be installed.

## 4. Test the logger manually

Before using systemd:

```bash
./.venv/bin/python logger.py
```

You should see temperatures every 5 seconds.

Stop with:

```text
Ctrl+C
```

Data is written to:

```text
data/measurements.sqlite3
data/csv/YYYY-MM-DD.csv
data/logger.log
```

## 5. Start the web interface

In another terminal:

```bash
./.venv/bin/python webapp.py
```

Then open:

```text
http://LOGGER-IP:8080
```

The API is available at:

```text
http://LOGGER-IP:8080/api/latest
```

## 6. Install as services

Copy the project to `/opt`:

```bash
sudo mkdir -p /opt/ni-tc-logger
sudo cp -a . /opt/ni-tc-logger/
sudo chown -R YOUR_USERNAME:YOUR_USERNAME /opt/ni-tc-logger
```

Edit both service files and replace:

```text
YOUR_USERNAME
```

with the Linux username that should run the logger.

Then:

```bash
sudo cp ni-tc-logger.service /etc/systemd/system/
sudo cp ni-tc-web.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now ni-tc-logger.service
sudo systemctl enable --now ni-tc-web.service
```

Check:

```bash
systemctl status ni-tc-logger
systemctl status ni-tc-web
```

Follow the logger:

```bash
journalctl -u ni-tc-logger -f
```

## Important design decisions

### Local data survives LTE failure

The logger does not depend on internet access. SQLite and CSV are written
locally. LTE is only needed for remote access.

### Automatic DAQ recovery

If the DAQ task throws an exception, the task is closed and recreated after
10 seconds.

### Automatic restart after power loss

systemd starts the logger after boot and restarts it if the Python process
dies.

### SQLite + CSV

SQLite is the primary database. Daily CSV files provide a simple portable
export.

## Recommended next improvements

For a real long-term measurement campaign, add:

- automatic daily database backup
- disk-space monitoring
- temperature alarms
- thermocouple open-circuit detection
- watchdog hardware/software
- NTP time synchronization
- Tailscale/WireGuard for remote access
- optional historical graphs
- configuration through the web interface
- measurement metadata / campaign ID
