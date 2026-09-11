# Replay and recording

```
liscere-observe --iface eth0 ...           # live; records capture/ by default
liscere-observe --pcap file.pcapng ...     # tshark -r; records capture/ by default
liscere-observe --replay runs/<run_id> ... # from a record; no tshark; does not re-record
```

- The observer's decisions read one clock (`liscere/clock.py`). Live runs use wall time. Replay
  and pcap runs use frame time, advanced only when a frame is dispatched, so the same input gives
  byte-identical `events.jsonl` however fast the machine is.
- Replay sources are merged by `frame.time_epoch` across protocols. The observe and learn windows
  count from the capture's first frame.
- A run that stops before its input ends (no evaluable silo, `--grammar` written) still records the
  whole input, so a later replay never runs out of frames early.
- End of input: inside the observe window exits 2 with a message (shorten `--observe` or record
  longer); inside learn closes learning on what was seen; in evaluate is the normal end.
- One difference from live: the live loop ticks every 0.25 s even when the wire is silent, so a
  hold's reconstructed flats arrive spread over the silence; in replay time only advances with
  frames, so flats due by the next frame are injected together just before it. The tracker sees the
  same samples in the same order.
- `--no-record` skips the raw record; `--record` forces it on a replay run.
