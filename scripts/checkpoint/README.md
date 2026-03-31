# OT Lab Checkpoint Funcional (Teste Realista)

Este pacote implementa o plano de teste em 1 dia com evidencia objetiva para decidir:

- `CHECKPOINT APROVADO`, ou
- `CHECKPOINT REPROVADO`

## Escopo

Valida o comportamento fim a fim do agente:

- captura e parsing Modbus/TCP
- interface detectada (nao mostrar apenas `ALL` quando houver interface concreta)
- estado de conexao (`Active`/`Inactive`)
- alertas e excecoes
- ciclo de comandos remotos (`queued/sent/done|error`)
- estabilidade sob carga leve

Sem mudar APIs. Usa apenas:

- `GET /api/status`
- `GET /api/events`
- `GET /api/actions/modbus/commands`
- `POST /api/actions/modbus/execute`

## Arquivos

- `collect_checkpoint_data.py`: coleta snapshots periodicos da API e opcionalmente executa uma acao Modbus.
- `evaluate_checkpoint.py`: avalia CT1..CT7 e gera relatorio.
- `load_modbus_fc03.py`: gerador de carga leve (FC03) para teste de estabilidade.
- `examples/action_fc06.json`: payload exemplo para CT6.

## Fase 0: Preparacao

No host (app + agente):

```bash
cd 02_labs/modbus_lab/ot_lab_app
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

Em outro terminal do host:

```bash
cd 02_labs/modbus_lab/ot_lab_app
source .venv/bin/activate
python agent.py --server http://127.0.0.1:8000 --session-id <SESSION_ID> --iface ALL --mode MONITORING
```

No Shadow PC (host remoto real):

- instalar cliente externo (QModMaster ou script `pymodbus`)
- gerar trafego para `IP_DO_HOST:PORTA_MODBUS`

Observabilidade:

- UI aberta no host
- Wireshark/tcpdump no host

## Fases 1-5 (execucao)

### 1) Iniciar coleta de evidencias

```bash
cd 02_labs/modbus_lab/ot_lab_app
source .venv/bin/activate
python scripts/checkpoint/collect_checkpoint_data.py \
  --base-url http://127.0.0.1:8000 \
  --session-id <SESSION_ID> \
  --interval 1.0 \
  --duration 1800 \
  --output-dir artifacts/checkpoint_run
```

Opcional (CT6 com acao remota):

```bash
python scripts/checkpoint/collect_checkpoint_data.py \
  --base-url http://127.0.0.1:8000 \
  --session-id <SESSION_ID> \
  --interval 1.0 \
  --duration 1800 \
  --output-dir artifacts/checkpoint_run \
  --action-file scripts/checkpoint/examples/action_fc06.json
```

### 2) Baseline funcional (CT1/CT5)

- gerar leitura FC03/FC04 do Shadow PC para o host
- parar trafego e observar transicao para `Inactive` (janela alvo ~2s)

### 3) Interface real detectada (CT2)

- cenario local no host (esperado loopback, ex. `lo0`)
- cenario remoto vindo do Shadow PC (esperado interface fisica, ex. `en0/eth*`)

### 4) Protocolo e robustez (CT3/CT4/CT6)

- escritas reais (FC05/FC06/FC15/FC16)
- excecoes Modbus (endereco invalido/funcao nao suportada)
- reconexao (interromper e retomar cliente externo)
- comandos via UI/API

### 5) Carga leve e estabilidade (CT7)

Rodar por 5 minutos com 5-10 req/s:

```bash
python scripts/checkpoint/load_modbus_fc03.py \
  --host 127.0.0.1 \
  --port 15020 \
  --rate 8 \
  --duration 300 \
  --start-addr 0 \
  --quantity 4
```

## Fechamento: avaliacao e checkpoint

Gerar avaliacao automatica:

```bash
python scripts/checkpoint/evaluate_checkpoint.py \
  --input-dir artifacts/checkpoint_run \
  --output artifacts/checkpoint_run/REPORT.md
```

Resultado:

- `REPORT.md` com status de CT1..CT7
- `checkpoint_result.json` com decisao final

## Criterio de aprovacao

Checkpoint aprovado se:

- CT1..CT7 = `PASS`
- nenhum bug critico observado (crash, perda total de eventos, interface incorreta sistematica)

Se falhar:

- registrar bug com evidencia
- corrigir
- repetir apenas CTs afetados

