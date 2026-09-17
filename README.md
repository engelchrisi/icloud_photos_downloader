# icloudpd — stripped, read-only, session-only fork

A minimal fork of [icloud_photos_downloader](https://github.com/icloud-photos-downloader/icloud_photos_downloader)
(MIT, forked at **v1.32.3**), reduced to a single job: **download photos and videos from iCloud
to a local directory.**

It is built to run in an isolated, network-restricted container that holds no Apple credentials.
See `proxmox/icloud-import/` in the `hw_docu` repo for the deployment plan and the strip logbook.

## What this fork cannot do

These are removed at the source level, not merely disabled by a flag:

| Removed | Why |
|---|---|
| Interactive login, SRP password auth, 2FA/2SA, SMS codes, keyring | The password must never reach this code. Sessions are created elsewhere. |
| Delete, auto-delete, delete-after-download | Nothing may write to iCloud. The only CloudKit write endpoint (`records/modify`) is gone. |
| Every non-Photos iCloud service (Drive/Documents, Contacts, Calendar, Find My, Mail, Notes, Reminders) | Only the photos library is in scope. |
| Web UI / daemon (Flask + waitress), watch mode | No long-running service, no listening socket. |
| Email/SMTP and script notifications | No outbound channels beyond Apple. |

Consequences: dependencies drop from 14 to 9 (no Flask, waitress, keyring, keyrings-alt, srp),
and the only hosts the code can contact are `setup.icloud.com`, `www.icloud.com` (plus `.cn`
regional variants) and the CloudKit host Apple returns during session validation. The login host
`idmsa.apple.com` is no longer referenced at all.

Every request to iCloud is a read: `records/query`, `internal/records/query/batch` and
`zones/list`, plus a streaming `GET` per asset. The CloudKit container is hardcoded to
`com.apple.photos.cloud`.

## Authentication

This build cannot log in. It reuses a session created by the **unmodified upstream** tool on a
trusted machine:

```sh
# on a trusted machine, with upstream icloudpd installed
icloudpd --username you@example.com --auth-only --cookie-directory ./session
```

Copy the two resulting files (`<username>` cookiejar and `<username>.session`) into this build's
`--cookie-directory`. If the session and trust token are both expired, this build exits with
status 1 and an explanatory message rather than prompting for anything.

## Usage

```sh
icloudpd \
  --username you@example.com \
  --cookie-directory /path/to/session \
  --directory /path/to/output \
  --skip-created-before 2026-09-01 \
  --folder-structure "{:%Y/%m}" \
  --log-level info \
  --no-progress-bar
```

`--only-print-filenames` lists what would be downloaded without downloading it.

## Tests

```sh
python -m venv .venv && .venv/bin/pip install -e . pytest mock freezegun vcrpy pytest-timeout
.venv/bin/python -m pytest tests/ -q
```

The suite runs against recorded HTTP fixtures and a session fixture, so it exercises the
session-reuse path with no network and no credentials. Tests asserting timestamps assume the host
is on UTC; on other timezones six of them fail, as they also do upstream.

## License and attribution

This is a **modified version** of iCloud Photos Downloader, not the original. Files have been
deleted and functions removed; see the strip logbook for the exact changes.

Licensed under the MIT License, unchanged from upstream. The original copyright notice and
permission notice are retained verbatim in [LICENSE.md](LICENSE.md):

> Copyright (c) 2016 Nathan Broadbent

The MIT license permits modification and redistribution provided that notice is kept intact,
which it is. This fork claims no copyright over the upstream code and adds no further
restrictions. Upstream project:
<https://github.com/icloud-photos-downloader/icloud_photos_downloader>.

The package version is marked `1.32.3+strip.1` so it cannot be mistaken for upstream's released
`1.32.3`. This fork is not published to PyPI, npm, Docker Hub or any other registry.
