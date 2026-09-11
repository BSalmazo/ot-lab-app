# Security

Liscere is a passive observer: it reads a mirror port, holds no address on the control segment and
injects nothing. It is not an interlock, not in the control path, not an enforcement layer, and not
a replacement for firewalls, segmentation or access control.

## Reporting a vulnerability

Please do not open a public issue for a vulnerability. Report it privately through GitHub's
"Report a vulnerability" on the repository's Security tab (private vulnerability reporting), or by
email to the maintainer listed in `CODEOWNERS`. Expect an acknowledgement within a week.

## What a report should contain

The version (`liscere-observe --version`), the input (a capture or a recorded run under
`runs/<id>/capture`, which can be replayed exactly), the observed behaviour and the expected one.

## Supply chain

Every release attaches a CycloneDX SBOM (`sbom.cdx.json`) alongside the wheel and `SHA256SUMS`;
the Pi's `liscere-update` verifies the checksum before installing. Dependencies are pinned in
`uv.lock` and updated by Dependabot.
