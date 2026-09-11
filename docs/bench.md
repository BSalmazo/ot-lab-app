# Bench

The reference bench is a Siemens S7-1200 G2 running a five-phase batch process (fill A, heat, add
B, mix, discharge) with a WinCC Unified HMI, engineered in TIA Portal V21. Seven tags are exposed
over OPC UA: Level, Temp, ValveA, ValveB, Heater, Agitator, Outlet. The phase variable is
deliberately not exposed; the observer must infer it.

The observer runs on a Raspberry Pi Zero 2 W with tshark, on a mirror of the HMI to PLC traffic. It
has no address on that segment and injects nothing. Addresses, interface names and windows are
deployment settings (`/etc/liscere/observe.env`, `/etc/liscere/liscere.toml`) and are not published
here.

## Validated behaviour

Over about fifteen cycles on the bench, with subscriptions and per-ClientHandle attribution:

- phase sequence RISING, STABLE, RISING, STABLE, FALLING per cycle;
- held values exactly 50.0 and 80.0 during the two holds, no flicker;
- ValveB=40 written during heating is INCOHERENT; during add-B it is COHERENT.

This is the acceptance test for every change (`tests/test_hold_resampler.py`, `tests/test_replay.py`).

## Known workaround

Temperature must be excluded from state-signal discovery during learning: as a second
continuously varying signal it competes with Level for the role of state signal. Where the
exclusion is applied today is not recorded; once its flow key is known it belongs in
`liscere.toml` (`exclude_flows`), see [Configuration](config.md).

## Captures

Bench captures are archived in the private `LiscereSecurity/Bench-Data` repository as release
assets, uploaded from the Pi with one command (see the [Pi runbook](pi.md)). Their index is copied
to `experiments/captures/MANIFEST.json`.
