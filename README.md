# Brodar

A Telegram companion with selectable **Codex CLI or Claude Code brains**, searchable conversation memory in Neon, background research, native Telegram media and owner tools. It can follow approved group conversations, answer names/mentions/replies, react, make editable presentations and learn useful context without rewriting its main persona.

Forked from [Rustam-Z/hamroh](https://github.com/Rustam-Z/hamroh); MIT attribution and the original Claude worker are preserved. `brodar` runs the shared coordinator; the historical `hamroh` command is separate.

**Start here:** [Quickstart](#quickstart) · [Commands](#run-stop-and-open-the-tui) · [Update](#update) · [Uninstall](#uninstall) · [Troubleshooting](#troubleshooting)

## How it works

You run one Brodar worker on your computer, phone or server. It receives Telegram updates, archives conversations in Neon Postgres, and asks your selected Codex CLI or Claude Code brain to act. A terminal dashboard (TUI) attaches to that worker for private chat, configuration and status. Closing the dashboard does not stop Telegram. The background supervisor owns the worker and its brain subprocess; you never need to find and kill a Codex session manually.

Brodar is Python software installed **from this Git repository**. There is currently no supported `npx brodar`, npm package or published PyPI install. The commands below download the source and create its environment for you; no pre-existing Brodar environment is needed.

## Quickstart

### 1. Prepare your computer

You need Python **3.11+**, Git, an internet connection, a Telegram account and a [Neon account](https://console.neon.tech/) or existing Postgres database. Install the tools for your platform:

**Ubuntu/Debian or Ubuntu in WSL2:**

```sh
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

Check `python3 --version` is 3.11 or newer; older distributions need a newer Python first.

**Android / Termux:**

```sh
pkg update
pkg install python git clang rust libjpeg-turbo libpng libffi openssl pkg-config codex
```

Use native Termux packages, not the desktop Linux installer or a glibc binary. Brodar is developed on Termux/Python 3.14. Turn off battery optimization and allow background/autostart for Termux if hosting on a phone.

**macOS:** install Python 3.11+ and Git through your usual package manager (for Homebrew: `brew install python git`). macOS and WSL use the source route below but have not been exercised on real devices. Native Windows service management is unsupported; use WSL2.

### 2. Pick a brain and create a Telegram bot

Install **one** brain CLI before onboarding:

- **Codex:** Termux users installed it above. On desktop Linux/macOS/WSL with Node.js/npm installed, run `npm install -g @openai/codex`; see the [official Codex installation guide](https://developers.openai.com/codex/cli/). Check `codex --version`.
- **Claude Code:** follow [Anthropic's installation guide](https://code.claude.com/docs/en/setup) for your platform, then check `claude --version`. Native Android compatibility depends on its available CLI build.

Use an account/plan that grants access to the selected CLI and model. Brodar onboarding opens **its own dedicated runtime login**; logging into your normal CLI alone does not authorize Brodar. You only need a Codex account if you choose the Codex brain or the optional Codex-based Neon MCP provisioning route. Claude users can use the existing-database connection-file route instead.

In Telegram, open [@BotFather](https://t.me/BotFather), send `/newbot`, follow the prompts, and keep the token private. The wizard asks for it using hidden input. You also need your own **numeric Telegram user ID**, which you enter in the trusted local setup terminal. Never put your token in a command, Git commit or group message.

### 3. Download and install Brodar

```sh
git clone https://github.com/doniyor117/brodar.git
cd brodar
sh install.sh
```

The installer creates `~/.local/share/brodar/venv`, installs dependencies, writes `~/.local/bin/brodar`, and opens the setup wizard automatically. It does not replace your system Python. For simple prompts over SSH, use `sh install.sh --plain` instead.

After installation, make the command available in your current shell:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

Add that export line once to your shell startup file (`~/.bashrc` or `~/.zshrc`) for future terminals. You can always run `~/.local/bin/brodar` directly.

### 4. Finish the resumable wizard

The wizard walks through:

1. Brain, explicit model, reasoning effort and dedicated provider sign-in.
2. Telegram token validation and your primary owner's numeric ID.
3. Neon project/branch/database selection, migrations and restricted runtime role.
4. Persona Markdown, aliases, timezone and local/permanent file budgets.
5. Connection checks and a choice to start in the foreground, start in the background, or start later.

It saves progress. If interrupted, run `brodar onboard --resume`. Change individual sections later with `brodar configure brain`, `telegram`, `owner`, `neon`, `persona` or `budgets`.

For Neon you can choose either:

- **Authenticated operator MCP:** the wizard can register and log in to Neon through Codex. For manual setup or SSH, use the commands below, complete the browser authorization, then run `brodar setup-neon`.
- **Existing database:** put its admin connection URI in a private file (`chmod 600`), then select the connection-file route or run `brodar setup-neon --admin-url-file /private/path/admin-uri`. This works with either brain. Do not paste the URI into shell arguments.

```sh
codex mcp add neon --url https://mcp.neon.tech/mcp
codex mcp login neon --no-browser
brodar setup-neon
```

Setup shows the concrete resource summary before writes; it does not upgrade a paid plan. Browser authorization and BotFather steps require you. Permanent object storage, research delegation and encrypted backups are optional; ordinary local files work without object storage.

### 5. Start and talk to it

If you chose “start later” in onboarding:

```sh
brodar service start
brodar service status
brodar doctor --json
brodar tui
```

DM your bot from the configured owner account. For a group, add the bot and send `/approve` from that same owner account. Other group members can then use it. Configure BotFather `/setprivacy` to **Disable** for ambient group reception, or give the bot suitable Telegram administrator access. Re-add it if Telegram requires that change to take effect. Administrator tools also need the corresponding Telegram permissions.

## Run, stop and open the TUI

| Goal | Command |
| --- | --- |
| Start in the background | `brodar service start` |
| Stop background worker and brain | `brodar service stop` |
| Apply changes / restart | `brodar service restart` |
| Check managed process | `brodar service status` |
| Check actual connections/readiness | `brodar doctor --json` |
| Read recent sanitized logs | `brodar service logs` |
| Open terminal dashboard/chat | `brodar tui` |
| Run in the foreground | `brodar run` (Ctrl-C stops it) |
| Reopen setup | `brodar onboard --resume` |
| Change model/brain | `brodar configure brain`, then restart |
| Sign in to the dedicated brain | `brodar login` |
| All commands and flags | `brodar --help` or `brodar -h` |
| Service command help | `brodar service --help` |
| Installer help before any environment exists | `sh install.sh --help` |

The TUI has Overview, private terminal Chat, Connections, Groups, Memory, Jobs, Skills, Settings/Persona and Logs. Tab switches views, F5 refreshes, Ctrl-Q detaches. `/configure [section]`, `/doctor` and `/restart` are available inside it. **Detaching leaves the bot running.** Bare `brodar` opens setup or the TUI in an interactive terminal. `brodar tui` attaches to an existing worker; start the worker first.

`service start` uses the packaged supervisor and does not require a desktop terminal to stay open. A “started” PID alone is not readiness: use `doctor`. Only run one worker per bot. Foreground workers must be stopped with Ctrl-C in their own terminal; externally managed workers must use their process manager.

### Start after a reboot (optional)

`brodar service install` writes a platform launcher; installing it does not immediately start the bot.

- **Termux:** install/enable the Termux:Boot app from the same trusted source as Termux, open it once, and allow background/autostart. The generated boot script runs the Brodar supervisor. Android can still stop/throttle it.
- **Linux systemd user service:** first `brodar service stop`, then `brodar service install`, `systemctl --user daemon-reload`, and `systemctl --user enable --now brodar`. Use `systemctl --user status brodar`, `restart brodar` and `stop brodar` for this mode. To survive logout, your system may require the operator to enable user lingering.

Choose **one** process manager. Do not start the built-in supervisor alongside the systemd worker. `brodar service stop` refuses to kill an unrelated/externally managed worker.

## Update

From your source checkout:

```sh
git pull --ff-only
sh install.sh --no-onboard
brodar service restart
brodar doctor --json
```

For a systemd deployment use `systemctl --user restart brodar` instead of `brodar service restart`. External configuration, secrets, learned memories and private prompts survive reinstalling the package. Check release/migration changes before an update; never point tests at the production database. Edit your persona with `brodar configure persona` rather than modifying the shared engine prompt.

## Uninstall

From your checkout, for a source installation made with `install.sh`:

```sh
sh uninstall.sh
```

This stops the managed worker, removes its matching startup launcher, removes `~/.local/bin/brodar` and its marked virtual environment. **It keeps** configuration, runtime files, recovery keys, this source checkout, brain CLIs and all Neon/cloud data. No Codex session hunting is needed. A custom install must use the same `BRODAR_INSTALL_VENV` value; select another instance with `sh uninstall.sh --profile /path/profile.json`.

If using systemd, first run `systemctl --user disable --now brodar`; after uninstall run `systemctl --user daemon-reload`. For an older unmarked source installation, run `sh install.sh --no-onboard` first so ownership is recorded. For a manually installed venv, stop its worker and run that environment's `python -m pip uninstall brodar`; its environment is yours to remove.

To also erase local data, **first export anything you want to keep and stop every instance**. Then delete the intended configuration directory (default `~/.config/brodar`), runtime directory (default `~/.local/share/brodar`) and cloned checkout yourself. This also destroys local prompts, provider sessions, temporary files and potentially your only recovery key. Separate profiles may use different paths. Cloud chats/files/backups and billing resources are separate: remove only the intended project/database/bucket through Neon after reviewing what else shares it. Uninstall does not delete a Telegram bot; revoke/remove it through BotFather if desired.

## Troubleshooting

- **`brodar: command not found`:** run `export PATH="$HOME/.local/bin:$PATH"` or use `~/.local/bin/brodar`.
- **Missing brain executable:** install the chosen Codex/Claude CLI for your platform and check its `--version`, then `brodar login`.
- **Stopped / no replies:** `brodar service status`, `brodar doctor --json`, then `brodar service logs`. In systemd mode use `systemctl --user status brodar` and `journalctl --user -u brodar`.
- **No group messages:** approve the group as owner, check BotFather privacy/admin permissions, and make sure another host is not polling the same bot.
- **Authorization incomplete over SSH:** finish the displayed browser/device flow. Dedicated Brodar login and operator Neon MCP login are separate.
- **Lost setup terminal:** `brodar onboard --resume --plain`.
- **Wrong profile:** every command accepts `--profile /path/profile.json`; consistently use the same profile for start/stop/TUI/configuration.
- **Package build failed on Termux:** install the native compiler/libraries above; do not replace Android Python with desktop Linux wheels.
- **Need options:** `brodar --help`, `sh install.sh --help`, `sh uninstall.sh --help`.

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

Exports exclude secrets/auth. A private deployment repository may contain the complete engine plus its own portable configuration and prompts; keep it private, preserve upstream provenance and pin skill source commits. Encrypt credential recovery backups separately and keep decryption keys elsewhere. Never copy rotating provider OAuth sessions into competing profiles.

## Development

Read [AGENTS.md](AGENTS.md); [CLAUDE.md](CLAUDE.md) imports it. These are the development handoff. All runtime prompts live in `prompts/*.md` (or the operator's private override directory).

```sh
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]' build
.venv/bin/python -m pytest tests/unit tests/brodar --reruns 0
.venv/bin/python -m build --wheel
```

Live SQL tests require `BRODAR_TEST_DATABASE_URL` for a **disposable migrated database**. Do not point them at production. Provider/Telegram end-to-end tests are opt-in. See [SECURITY.md](SECURITY.md) for reporting and release hygiene.
