# Brodar

A Telegram companion with selectable **Codex CLI or Claude Code brains**, searchable conversation memory in Neon, background research, native Telegram media and owner tools. It can follow approved group conversations, answer names/mentions/replies, react, make editable presentations and learn useful context without rewriting its main persona.

Forked from [Rustam-Z/hamroh](https://github.com/Rustam-Z/hamroh); MIT attribution and the original Claude worker are preserved. `brodar` runs the shared coordinator; the historical `hamroh` command is separate.

## Install and onboard

You need Python **3.11+**, Git, a Telegram BotFather bot, a Neon account (or existing Postgres connection), and **one** supported brain CLI with its own account login. Browser authorization/BotFather actions still require you. A Codex subscription is not required when using Claude with the connection-file Neon setup path.

From a reviewed source checkout:

```sh
git clone https://github.com/doniyor117/brodar.git
cd brodar
sh install.sh
```

The installer uses a private virtual environment and starts `brodar onboard`. It does not install development extras or replace your system Python. No PyPI distribution is advertised.

**Termux:** use native Termux Python, never a glibc Linux installer/binary. Install the native CLI through its supported Termux route. If building Python dependencies requires libraries:

```sh
pkg install python git clang rust libjpeg-turbo libpng libffi openssl pkg-config
sh install.sh --plain
```

This repository is developed on native Termux/Python 3.14. Linux is covered by CI. macOS/WSL use the same Python source-install route but have not been verified on real devices; native Windows service management is unsupported. Android background/autostart/battery settings can still stop a phone-hosted bot.

The resumable wizard configures:

1. Codex or Claude, an explicit model, and dedicated runtime login.
2. Your BotFather token (hidden input; checked using `getMe`). Active webhooks are reported, never deleted automatically.
3. **One primary owner** by numeric Telegram ID, entered in the trusted local setup terminal.
4. Neon through your authenticated **operator** MCP or an existing private admin-URI file. The deterministic setup applies repository migrations and creates a restricted runtime role; no model composes SQL or receives admin credentials.
5. Private Markdown prompts, aliases, timezone and file budgets.

For operator MCP setup, the wizard can register and log in to Neon. SSH users can use:

```sh
codex mcp add neon --url https://mcp.neon.tech/mcp
codex mcp login neon --no-browser
brodar setup-neon
```

Your selected project/branch/database and resource summary are shown before writes. Setup never upgrades your plan or silently picks the first project. If MCP permissions exclude provisioning, grant the needed permissions or choose the private connection-file path. OAuth, project creation and actual model quality depend on the user's accounts; skipped live checks are labelled unverified.

```sh
brodar onboard --resume
brodar configure brain
brodar configure telegram
brodar configure owner
brodar configure persona
brodar configure budgets
brodar doctor --json
brodar run                      # foreground worker, explicit and noninteractive
brodar service start            # packaged supervisor
brodar service status
brodar service logs
brodar service stop
brodar tui                      # attach; closing it leaves the worker running
```

The TUI provides Overview, private terminal Chat, Connections, Groups, Memory, Jobs, Skills, Persona/Settings and sanitized Logs. Tab switches views; F5 refreshes; `/configure [section]` edits through onboarding; Ctrl-Q detaches. It uses a private local socket, never a second Telegram poller. Settings changes take effect after a controlled worker restart. Bare `brodar` opens onboarding or the TUI only in an interactive terminal; service scripts must use `brodar run`.

Optional platform launchers: `brodar service install` writes a Termux:Boot or Linux user-service launcher. Choose one process manager. Do not start both systemd and the built-in supervisor.

## Telegram groups and capabilities

Only the configured owner may DM the bot. Add it to a group, then send `/approve` **from the owner account** in that group. Everyone in approved groups can use ordinary capabilities. Group administrator status does not confer bot ownership.

For ordinary group-message reception, configure BotFather privacy mode or Telegram administrator access appropriately. Messages received from Telegram are archived; ambient batches avoid inference on every fragment. Direct names, mentions, replies and short recent same-person follow-ups have priority. Bot exchanges have a host-enforced ten-reply limit and can end silently earlier.

Native tools support photos, videos, stickers, animation, voice/audio, files, albums, polls and permitted administrator actions. Sticker grids return short selection handles; inspect a full candidate image and send its handle so copied IDs cannot silently select another sticker. Telegram does not expose a complete arbitrary member list to bots; observed members and explicit lookups are the available sources.

The owner can run Python/shell/Git/package tasks and install public GitHub skills through tracked background host jobs. Ordinary members keep a limited gateway. Exact sensitive/risky commands require an owner-DM approval; credential access, main-prompt changes and known catastrophic commands are denied. There is no second approval/classification model.

**Isolation limit:** owner interpreters run under the host OS identity. Path/command checks and output redaction are defense in depth, not a hard sandbox against arbitrary code under that same identity. Use a separate execution identity/host when stronger isolation is required. Never put runtime secrets in source or skill packages.

## Files, memory and costs

- Chats, learned notes, provenance, task records and search indexes are durable in **Neon Postgres**. Extra learned context can evolve; the main system prompts and permissions cannot self-edit.
- Attachments, fetched content, generated files and ordinary deliverables are **local temporary files**. Sending a file does not permanently save it. Seven-day inactivity expiry is swept approximately weekly; active work is protected. Lost local bytes need re-import.
- Only an explicit keep/save request promotes permitted file bytes into private **Neon Object Storage**. Permanent storage is optional; local file/presentation tools still work without it.
- Application files are at most **50,000,000 bytes**; narrower Telegram method limits still apply. Initial configured budgets: **500 MB temporary**, **1 GB retained**, **2 GB encrypted backups**. These are application budgets, not a vendor billing cap. Owner command scratch is monitored/cleaned separately; OS-level quotas are not provided.
- Optional encrypted backups run **every fourteen days** when the supervisor has operator/storage/key paths. Two managed full generations are retained, plus pinned and legacy snapshots. Existing old objects/backups are never automatically reclassified/deleted. Keep recovery keys off-phone; backups on the same provider are not an independent-provider recovery copy.
- Neon compute, storage, network and embedding/provider usage may be billable. Consult your plan and usage dashboard; do not assume a free allowance or a per-file limit guarantees a free bill. The running lease/poller can keep compute awake.

Private configuration defaults to `~/.config/brodar/profile.json`, with an external secret file and private prompt directory. Runtime/workspaces default to `~/.local/share/brodar`. Use `--profile PATH` or `BRODAR_PROFILE` for another instance. No implicit import of a developer's `~/secrets/.env` occurs.

```sh
brodar profile export --output /private/path/profile-export
brodar profile import --archive /private/path/profile-export/profile.json --profile /private/path/new/profile.json
```

Exports exclude secrets/auth. Keep private customization repos separate from the engine; pin the engine revision and skill source commits. Encrypt credential recovery backups separately and keep decryption keys elsewhere. Never copy rotating provider OAuth sessions into competing profiles.

## Development

Read [AGENTS.md](AGENTS.md); [CLAUDE.md](CLAUDE.md) imports it. These are the development handoff. All runtime prompts live in `prompts/*.md` (or the operator's private override directory).

```sh
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]' build
.venv/bin/python -m pytest tests/unit tests/brodar --reruns 0
.venv/bin/python -m build --wheel
```

Live SQL tests require `BRODAR_TEST_DATABASE_URL` for a **disposable migrated database**. Do not point them at production. Provider/Telegram end-to-end tests are opt-in. See [SECURITY.md](SECURITY.md) for reporting and release hygiene.
