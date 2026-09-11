# Liscere passive observer

Liscere passive observer. Entry point: `scripts/liscere_observe.py`. Core: `otlab_core/`.

The legacy declarative engine (FastAPI backend, Modbus proxy, Docker/FUXA lab, web UI) has
been retired; it is preserved on the `legacy-engine-v1` git tag. There is intentionally no
web UI at present — a new one is future work.

Full documentation pending.

## Install and run

Requires Python 3.11 or newer, `tshark` on `PATH`, and [uv](https://docs.astral.sh/uv/).

```
uv sync --extra ui          # engine, terminal UI and nothing else
uv run liscere-observe --iface eth0 --observe 60 --learn 120
uv run liscere-probe --iface eth0 --duration 30
uv run liscere-observe ... 2>/dev/null | uv run liscere-ui
```

Every run writes a record under `runs/<run_id>/` (manifest, event stream, verdicts, and the raw
capture lines it consumed). A run can be repeated exactly, without tshark or the bench:

```
uv run liscere-observe --pcap capture.pcapng --observe 60 --learn 120   # from a capture file
uv run liscere-observe --replay runs/<run_id> --observe 60 --learn 120  # from a recorded run
```

Replay runs on frame time, so the same input always gives the same output.

On the Raspberry Pi the observer runs as a systemd service from a tagged release; see
[deploy/pi/README.md](deploy/pi/README.md) for install, update and rollback (one command each).

Developers: `make install`, `make test`, `make lint`. The `scripts/liscere_*.py` files are shims kept so
older command lines keep working; the code is in `liscere/` (programs) and `otlab_core/` (engine).
