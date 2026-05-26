# OT Lab App (v2-dev)

Docker-first OT/ICS simulation framework for Modbus/TCP experiments with:

- OpenPLC (PLC)
- FUXA (HMI)
- Web platform (analysis + scenario execution)
- Monitor runtime based on `tshark` (packet capture + protocol dissection)

Official web interface (v1 production):
[https://otlab.salmazo.org](https://otlab.salmazo.org)

## Run v2 locally

From repo root:

```bash
docker compose build
docker compose up -d
```

Open:

- Web: [http://localhost:8000](http://localhost:8000)
- OpenPLC: [http://localhost:8081](http://localhost:8081)
- FUXA: [http://localhost:1881](http://localhost:1881)

## v2 structure

- `app.py`: FastAPI backend and session/state orchestration
- `scripts/tshark_runtime.py`: monitor runtime (`tshark` + inline Modbus proxy)
- `docker-compose.yml`: OT + DMZ lab topology
- `docker/`: FUXA image customization
- `scripts/v2_seed/`: initial ST/FUXA seed artifacts
- `static/` + `templates/`: web UI
- `studies/`: research notes/evidence
