# Raspberry Pi runbook

The Pi runs a tagged release of the observer as a systemd service. It never runs from a branch.

## Install (once)

```
curl -fsSL https://raw.githubusercontent.com/LiscereSecurity/OT-Lab/main/deploy/pi/install.sh | sudo bash
```

Then set the interface and windows in `/etc/liscere/observe.env` and restart:

```
sudo nano /etc/liscere/observe.env
sudo systemctl restart liscere-observe
```

The installer is idempotent. It installs `tshark`, `chrony` and `logrotate`, creates the `liscere`
system user with capture rights through the `wireshark` group (no root at run time), installs
`liscere-update`, fetches the latest release into `/opt/liscere/venv`, and enables the service.

## Update (one command)

```
sudo liscere-update            # latest release
sudo liscere-update v0.2.0     # a specific release
sudo liscere-update --rollback # the previous release
```

`liscere-update` downloads the wheel and `SHA256SUMS` attached to the GitHub release, verifies the
checksum, installs, restarts the service and prints old and new versions. When `liscere-check`
exists (planned), a failing check rolls the update back automatically.

## Where things are

| What | Where |
|---|---|
| Code | `/opt/liscere/venv` (release wheels kept in `/opt/liscere/releases`) |
| Arguments | `/etc/liscere/observe.env` |
| Configuration (exclusions, pinned state signal) | `/etc/liscere/liscere.toml` |
| Run records | `/var/lib/liscere/runs/<run_id>/` (`run.json`, `events.jsonl`, `verdicts.jsonl`, `capture/`) |
| Human log | `journalctl -u liscere-observe -f` |
| Service | `systemctl status liscere-observe` |

Every live run records its raw input under `capture/`, so any bench run can be replayed exactly
elsewhere with `liscere-observe --replay <run dir>`.

## Time

`chrony` is enabled. On a lab segment without internet, add the lab NTP source in
`/etc/chrony/conf.d/liscere.conf` and restart chrony. Frame timestamps come from the capture, so
the order of events inside a run does not depend on the clock; only correlation with other
devices does.

## Disk

`events.jsonl` is rotated by logrotate (it can be rebuilt from `capture/` by replay). The raw
capture under `capture/` and `verdicts.jsonl` are never truncated. At the bench's rate the capture
grows by roughly 100 MB per day of continuous run, so prune old run directories once they are
archived: `sudo rm -r /var/lib/liscere/runs/<run_id>`.

## Copying a run off the Pi

```
tar czf run.tgz -C /var/lib/liscere/runs <run_id>
```

## Archiving captures (one command)

Bench captures go to the private LiscereSecurity/Bench-Data repository as release assets, one
release per session, indexed by its `MANIFEST.json`:

```
sudo bench-data-upload 2026-09-15-bench ~/shared/*.pcapng --notes "15 cycles, Temperature excluded"
```

It hashes, uploads, and attaches a manifest fragment that the repository merges automatically.
Fields only a human knows (bench state, tags subscribed, cycles, security mode) are filled in
afterwards on the GitHub page. `--dry-run` shows what would be written without uploading.

Token, once: create a GitHub fine-grained personal access token restricted to `Bench-Data` with
Contents read and write, 90-day expiry, and store it on the Pi:

```
sudo install -m 600 /dev/null /etc/liscere/bench-data.token
sudo nano /etc/liscere/bench-data.token     # paste the token
```

Bench-Data's README has the step-by-step token instructions.
