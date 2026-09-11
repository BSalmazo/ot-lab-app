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

Developers: `make install`, `make test`, `make lint`. The `scripts/liscere_*.py` files are shims kept so
older command lines keep working; the code is in `liscere/` (programs) and `otlab_core/` (engine).
