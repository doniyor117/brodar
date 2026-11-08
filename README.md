# Brodar

A Telegram companion with selectable **Codex CLI or Claude Code brains**, searchable conversation memory in Neon, background research, native Telegram media and owner tools. It can follow approved group conversations, answer names/mentions/replies, react, make editable presentations and learn useful context without rewriting its main persona.

Forked from [Rustam-Z/hamroh](https://github.com/Rustam-Z/hamroh); MIT attribution and the original Claude worker are preserved. `brodar` runs the shared coordinator; the historical `hamroh` command is separate.

**Already installed? Open the setup wizard:**

```sh
brodar onboard
# Simple prompts for SSH/small terminals:
brodar onboard --plain
```

**Start here:** [Quickstart](#quickstart) · [Commands](#run-stop-and-open-the-tui) · [Hyperagent research](#optional-hyperagent-research) · [Update](#update) · [Uninstall](#uninstall) · [Troubleshooting](#troubleshooting)

## How it works

You run one Brodar worker on your computer, phone or server. It receives Telegram updates, archives conversations in Neon Postgres, and asks your selected Codex CLI or Claude Code brain to act. A terminal dashboard (TUI) attaches to that worker for private chat, configuration and status. Closing the dashboard does not stop Telegram. The background supervisor owns the worker and its brain subprocess; you never need to find and kill a Codex session manually.

Brodar is Python software installed **from this Git repository**. There is currently no supported `npx brodar`, npm package or published PyPI install. The commands below download the source and create its environment for you; no pre-existing Brodar environment is needed.

## Quickstart

### 1. Prepare your computer

You need Python **3.11+**, Git, an internet connection, a Telegram account and a [Neon account](https://console.neon.tech/) or existing Postgres database. Install the tools for your platform:

**Ubuntu/Debian or Ubuntu in WSL2:**

```sh
sudo apt update
sudo apt install -y git curl ca-certificates bash python3 python3-venv python3-pip
```

Check `python3 --version` is 3.11 or newer; older distributions need a newer Python first.

**Android / Termux:**

```sh
pkg update
pkg install python git clang rust libjpeg-turbo libpng libffi openssl pkg-config
```

Use native Termux packages, not the desktop Linux installer or a glibc binary. Brodar is developed on Termux/Python 3.14. Turn off battery optimization and allow background/autostart for Termux if hosting on a phone.

**macOS:** install Python 3.11+ and Git through your usual package manager (for Homebrew: `brew install python git`). macOS and WSL use the source route below but have not been exercised on real devices. Native Windows service management is unsupported; use WSL2.

### 2. Install your chosen brain CLI

Pick **Codex or Claude Code** below for your operating system. You may install both and switch later, but only one is selected per Brodar instance. These commands install the CLI executable; Brodar's dedicated sign-in happens in step 4.

#### Codex on Linux / WSL2

OpenAI's standalone installer does not require Node/npm:

```sh
curl -fsSL https://chatgpt.com/codex/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
codex --version
```

If you already manage Node.js/npm, the alternative is `npm install -g @openai/codex`. Choose one installation method. See [OpenAI's installation instructions](https://github.com/openai/codex#installing-and-running-codex-cli).

#### Codex on Android / Termux

```sh
pkg update
pkg install codex
codex --version
```

This uses Termux's native Android build. Do not run the desktop Linux installer in native Termux. Source: [Termux's Codex package recipe](https://github.com/termux/termux-packages/blob/master/packages/codex/build.sh). If your repository mirror does not offer it, check `termux-change-repo` and update the package lists rather than substituting a Linux binary.

#### Claude Code on Linux / WSL2

Anthropic's standalone installer does not require Node/npm:

```sh
curl -fsSL https://claude.ai/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"
claude --version
```

Alternatively, with Node.js 22+ and npm installed: `npm install -g @anthropic-ai/claude-code`. Choose one installation method. See [Anthropic's platform and installation documentation](https://code.claude.com/docs/en/setup).

#### Claude Code on Android / Termux

Android is not in Anthropic's supported native/npm platform list. Plain `npm install -g @anthropic-ai/claude-code` is **not** the current native-Termux solution: it can install a launcher without a runnable Android binary. See the [upstream Android issue](https://github.com/anthropics/claude-code/issues/50270).

For **aarch64 / ARM64 Termux**, a community compatibility route used by this project is:

```sh
pkg update
pkg install nodejs-lts git ripgrep
npm install -g @xurxuo/claude-code-termux
claude --version
```

If Node.js 18+ is already installed, keep it; do not install both `nodejs` and `nodejs-lts`.

The [wrapper's published instructions](https://www.npmjs.com/package/@xurxuo/claude-code-termux) describe running the official Linux ARM64 binary through `glibc-runner`. It is a third-party wrapper, not official Anthropic Android support; it can install compatibility dependencies and fetch/update the native binary on first launch, including `--version`. To disable its daily update check after initial setup, use `export CLAUDE_CODE_TERMUX_NO_AUTO_UPDATE=1`. Other Android architectures are not covered by these instructions. A working `claude --version` verifies launch, not a complete Brodar conversation.

#### Accounts and Telegram bot

Use an account/plan that grants access to your selected CLI and model. **The default brain is Codex**, but the wizard explicitly asks `Brain (codex/claude)` and saves your choice. Installing both CLIs does not make Brodar use both or choose automatically. Brodar has no automatic provider fallback.

Brodar uses **dedicated authentication directories**, separate from your normal terminal CLI login. The wizard can open the chosen provider's sign-in. See [Which provider does login use?](#which-provider-does-login-use) for explicit commands. A Claude deployment does not need a Codex account when using the existing-database Neon connection-file route.

In Telegram, open [@BotFather](https://t.me/BotFather), send `/newbot`, follow the prompts, and keep the token private. The wizard asks for it using hidden input. You also need your own **numeric Telegram user ID**, which you enter in the trusted local setup terminal. Never put your token in a command, Git commit or group message.

### 3. Download and install Brodar

```sh
git clone https://github.com/doniyor117/brodar.git
cd brodar
sh install.sh --no-onboard
export PATH="$HOME/.local/bin:$PATH"
```

The installer creates `~/.local/share/brodar/venv`, installs dependencies and writes `~/.local/bin/brodar`. It does not replace your system Python. `--no-onboard` deliberately leaves starting the wizard to the next step. If you prefer installation and setup in one command, plain `sh install.sh` opens the wizard automatically.

Add `export PATH="$HOME/.local/bin:$PATH"` once to your shell startup file (`~/.bashrc` or `~/.zshrc`) for future terminals. You can always run `~/.local/bin/brodar` directly.

### 4. Open the setup wizard

Run this **after installation**:

```sh
brodar onboard
```

For SSH or a small phone terminal, use simple line-by-line prompts instead:

```sh
brodar onboard --plain
```

Both commands open the same guided setup. Choose `codex` or `claude` at the brain question; this determines the model and login flow that follow. Type `back` to revisit the previous step or `quit` to save progress and leave. Reopen with saved answers using `brodar onboard --resume --plain`. `onboard` is the setup wizard; `tui` is the running bot's dashboard, described later.

The wizard walks through:

1. Brain, explicit model, reasoning effort and dedicated provider sign-in.
2. Telegram token validation and your primary owner's numeric ID.
3. Neon project/branch/database selection, migrations and restricted runtime role.
4. Persona Markdown, aliases, timezone and local/permanent file budgets.
5. Connection checks and a choice to start in the foreground, start in the background, or start later.

To change one section later, run a complete command:

```sh
brodar configure brain       # choose provider, model and login
brodar configure telegram    # change bot token
brodar configure owner       # change primary owner
brodar configure neon        # change database connection
brodar configure persona     # edit persona Markdown
brodar configure budgets     # storage budgets
```

Restart the worker after configuration changes. The wizard does not require editing Python source files.

For Neon you can choose either:

- **Authenticated operator MCP:** the wizard can register and log in to Neon through Codex. For manual setup or SSH, use the commands below, complete the browser authorization, then run `brodar setup-neon`.
- **Existing database:** put its admin connection URI in a private file (`chmod 600`), then select the connection-file route or run `brodar setup-neon --admin-url-file /private/path/admin-uri`. This works with either brain. Do not paste the URI into shell arguments.

```sh
codex mcp add neon --url https://mcp.neon.tech/mcp
codex mcp login neon --no-browser
brodar setup-neon
```

The `codex mcp login neon` command logs the **operator's Neon integration** in; it does not sign Brodar into either model provider. You only need that Codex-based route if you choose MCP provisioning. Choosing a Claude brain with a private existing-database URI file needs neither Codex installation nor Codex login.

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

## Which provider does login use?

| Command | Signs in to | Changes the saved brain? |
| --- | --- | --- |
| `brodar login codex` | Brodar's dedicated Codex / OpenAI profile | No |
| `brodar login claude` | Brodar's dedicated Claude Code / Anthropic profile | No |
| `brodar login` | The brain selected in this instance's configuration; Codex if none configured | No |
| `brodar configure brain` | Lets you select/save the brain and offers its login | Yes |
| `codex mcp login neon --no-browser` | Operator Neon MCP, for optional database provisioning | No |
| `brodar hyperagent-login` | Hyperagent MCP account access, for optional research | No |

Login prints **Codex** or **Claude Code** and the dedicated profile directory before opening authorization. `--brain codex` / `--brain claude` are equivalent one-command overrides, not permanent switches. An explicit `BRODAR_BRAIN` environment setting overrides the saved profile; unset that override if the wizard's saved choice is not being used.

For example, to **switch an existing bot to Claude**, install Claude Code using the matching platform instructions, then:

```sh
brodar configure brain    # answer claude, select its model, finish sign-in
# If you skipped sign-in in the wizard:
brodar login claude
brodar service restart
brodar doctor --offline
```

Use `codex` instead when switching back to Codex. `doctor --offline` shows the selected provider/model without calling the model. Use `brodar doctor --json` for network connection checks. Neither doctor command runs paid inference to verify model quality. To sign in over SSH, follow the selected CLI's displayed device/browser instructions; no generic `brodar login` callback flow is shared between the two providers.

For another instance, add the same profile path to every command: `brodar login claude --profile /path/profile.json` and `brodar service restart --profile /path/profile.json`.

## Optional Hyperagent research

[Hyperagent](https://hyperagent.com/) can be Brodar's remote research partner. Brodar sends it a focused assignment; the researcher uses its own configured tools/connections and returns evidence that Brodar can use in the original conversation. This is optional: ordinary Brodar chat, memory and local tools work without it. It works with either Brodar brain.

### What it adds

A researcher can combine browsing/search with connected services: **YouTube**, **Instagram**, **Twitter/X**, and **Google Workspace** apps such as Gmail, Drive, Docs, Sheets, Slides and Calendar. Connect the required accounts under Hyperagent **Settings → Integrations**, then attach those connections to the researcher agent. YouTube/account actions, video analysis, public search and access to private content are different capabilities; use what the selected tools and permissions actually provide. A connection is not unlimited access to every video/post/account. See Hyperagent's [integrations guide](https://www.hyperagent.com/docs/integrations/) and [action catalogue](https://www.hyperagent.com/docs/concepts/agents/autonomy/access-policy-json).

Examples: investigate sources across X and the web; analyze an accessible YouTube video; inspect available Instagram content; find authorized Drive documents; or prepare research for a presentation. Brodar receives the report, not the connected accounts' credentials. Only attach private accounts whose information you intend the researcher to use for the chats that can request research.

### ChatGPT sign-in and costs

Hyperagent offers **Sign in with ChatGPT**. For an existing Hyperagent account, open **Settings → AI providers → Continue with ChatGPT**, approve subscription sharing, and confirm the connection says **Active**. Account sign-in alone does not share the subscription. Eligible supported GPT usage can draw from that allowance; other models, search/browser/connected-service actions and image generation may still be billed by Hyperagent. Review its fallback-to-paid-plan setting and usage limits. This connection is separate from `brodar login codex`. Source: [Hyperagent's ChatGPT connection guide](https://www.hyperagent.com/docs/billing/plans/connect-chatgpt-subscription).

### 1. Create and configure the researcher

In Hyperagent, create an agent named **Hyper Researcher**. You can use [the included research prompt](prompts/hyperresearcher-system.md): it requests detailed, source-linked reports for a receiving agent, including uncertainty, access failures and actionable findings. Enable needed tools, connect/attach integrations, and choose its model/provider there. Customizing this cloud agent does not change Brodar's local persona.

For MCP execution, choose an execution mode that supports unattended runs. Hyperagent currently rejects legacy **Ask first** agents through MCP; configure **Auto** and an appropriate access policy for this research agent. MCP authorization does not grant Brodar permission to approve arbitrary remote actions. Read the [MCP invocation documentation](https://www.hyperagent.com/docs/concepts/agents/invocations/mcp-server).

No Telegram research group, researcher bot, email address or bot-to-bot reply setting is needed for this transport.

### 2. Add the MCP server and authorize Brodar

Install the **Codex CLI** using the platform instructions above, even if Brodar uses Claude. The current Hyperagent login helper uses Codex as its OAuth client; that does **not** require selecting Codex as Brodar's brain or signing into an OpenAI model account.

```sh
codex mcp add hyperagent --url https://hyperagent.com/api/mcp
brodar hyperagent-login
brodar hyperagent-status
```

`hyperagent-login` invokes the OAuth flow with `--no-browser`, prints an authorization link and binds Brodar to the saved connection. Open the link in your browser and approve it. Over SSH, follow the CLI's local callback instructions; if the browser cannot reach the remote localhost callback, paste the final callback URL into the CLI's callback prompt. That URL contains a temporary authorization code: enter it only into your trusted login terminal. No token/API key needs copying into a prompt or repository.

With exactly one agent named **Hyper Researcher**, Brodar selects it automatically. Otherwise `hyperagent-status` lists available IDs; select the intended one:

```sh
brodar hyperagent-status --agent-id YOUR_RESEARCHER_AGENT_ID
```

Use its Hyperagent **agent ID**, not its Telegram username. `hyperagent-status` checks access/discovery; it does not launch paid research or prove every connected service works.

For **standalone interactive Codex** usage, `codex mcp login hyperagent --no-browser` and `codex mcp list` are the normal client commands. For Brodar, use `brodar hyperagent-login` as above: a generic MCP login alone does not establish Brodar's saved binding. The helper references the canonical Codex OAuth record rather than copying rotating refresh credentials.

For **standalone interactive Claude Code**, Hyperagent documents this registration:

```sh
claude mcp add --transport http hyperagent https://hyperagent.com/api/mcp
```

Open Claude Code, run `/mcp`, select `hyperagent`, and authenticate. This connects your interactive Claude client; Brodar's background research still uses the host connection established by `brodar hyperagent-login`. MCP access is account-level, so there is no separate per-agent MCP invocation card to enable.

### 3. Enable the connection in Brodar

```sh
brodar configure research --plain
# Answer yes, then confirm/paste the researcher agent ID.
brodar service restart
brodar hyperagent-status
```

This saves the selected agent and enables `hyperagent_mcp` in the external instance profile. It does not start a research run. For another instance, add `--profile /path/profile.json` to **each** Brodar command. Use the actual process manager's restart command if running under systemd.

### 4. Ask for research

In the owner DM or an approved group, for example:

> brodar, ask the research agent to compare these sources and summarize this YouTube video: [link]. include timestamps where available, source links, disagreements and anything it couldn't access.

Brodar's `delegate_research` tool queues a durable task and returns immediately. Background code creates a Hyperagent thread, polls its status and brings the completed report back to the **originating chat/topic**, where Brodar continues. The conversation need not sit in an inference loop while the researcher works. `research_tasks` exposes scoped status; `research_follow_up` continues the same thread when substantive details are missing. Task deadlines and bounded follow-ups prevent endless agent conversations. Open the research thread in Hyperagent to inspect its full tool trail.

The host currently calls only `list_agents`, `create_thread`, `get_thread` and `send_message`. It does not expose all Hyperagent account/approval tools directly to the Telegram brain. Cloud tools run inside the configured researcher; they are not newly installed local Brodar tools.

### Troubleshooting or disabling

- **Research unavailable:** run `brodar hyperagent-login`, check `brodar hyperagent-status`, enable with `brodar configure research`, then restart.
- **Agent listed but invocation rejected:** check the researcher's execution mode and MCP/integration access policy in Hyperagent.
- **Service/video/post unavailable:** connect and attach the required app, inspect its granted actions and report actual access limits rather than assuming the connector can fetch it.
- **Work still running:** open the Hyperagent thread; completion is asynchronous. Do not repeatedly resubmit a possibly accepted task.
- **Disable research:** `brodar configure research`, answer no, then restart. Revoke the client under Hyperagent **Settings → MCP access** if you also want to revoke its grant.

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
| Open setup wizard | `brodar onboard` (or `brodar onboard --plain` over SSH) |
| Reopen saved setup | `brodar onboard --resume` |
| Change model/brain | `brodar configure brain`, then restart |
| Sign in to Codex | `brodar login codex` |
| Sign in to Claude Code | `brodar login claude` |
| Connect optional researcher | `brodar hyperagent-login`, then `brodar configure research` |
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
- **Missing brain executable:** install the chosen Codex/Claude CLI for your platform and check its `--version`, then `brodar login codex` or `brodar login claude`.
- **Stopped / no replies:** `brodar service status`, `brodar doctor --json`, then `brodar service logs`. In systemd mode use `systemctl --user status brodar` and `journalctl --user -u brodar`.
- **No group messages:** approve the group as owner, check BotFather privacy/admin permissions, and make sure another host is not polling the same bot.
- **Authorization incomplete over SSH:** finish the displayed browser/device flow. Dedicated Brodar login and operator Neon MCP login are separate.
- **Lost setup terminal:** `brodar onboard --resume --plain`.
- **Wrong profile:** every command accepts `--profile /path/profile.json`; consistently use the same profile for start/stop/TUI/configuration.
- **Package build failed on Termux:** install the native compiler/libraries above; do not replace Android Python with desktop Linux wheels.
- **Need options:** `brodar --help`, `sh install.sh --help`, `sh uninstall.sh --help`.

## Telegram groups and capabilities

Only the configured owner may DM the bot. Add it to a group, then send `/approve` **from the owner account** in that group. Everyone in approved groups can use ordinary capabilities. Group administrator status does not confer bot ownership.

### Session context and compaction

Send `/status` in the owner DM or any approved group. It shows the selected brain/model, latest provider-reported context estimate, remaining window and configured automatic compaction threshold. `/status@YourBot` also works. Group status describes the one shared group session; DM status describes the separate owner session. No model call is made. Usage can be unknown after startup/resume until the next provider usage event. Context usage is an estimate based on the last request, **not your subscription quota** or cumulative session token billing.

The owner can send `/compact` in either location. It queues compaction behind an active turn, reports completion back to the same chat and leaves the polling loop responsive. Other group members cannot compact the shared session. Manual compaction currently supports **Codex**; Claude reports unsupported honestly. Compaction itself uses provider quota and does not reset account limits. Codex also compacts automatically; `BRODAR_CODEX_AUTO_COMPACT_TOKEN_LIMIT` configures that threshold for Brodar's provider subprocess only, without editing your global Codex settings. Restart after changing this setting.

The same controls work from the CLI while the worker is running:

```sh
brodar status                 # shared group session; no inference
brodar status dm              # owner DM session
brodar status terminal --json # local terminal session, structured output
brodar compact               # queue shared group compaction
brodar compact dm            # queue owner DM compaction
brodar status                # check the queued compaction result
```

`brodar service status` checks the managed process; `brodar status` checks its model session. Session controls never create a new brain just to display status. Implementation uses Codex's [app-server token-usage events and thread compaction API](https://learn.chatgpt.com/docs/app-server), waiting for completion rather than treating the initial RPC acknowledgment as success.

For ordinary group-message reception, configure BotFather privacy mode or Telegram administrator access appropriately. Messages received from Telegram are archived; ambient batches avoid inference on every fragment. Direct names, mentions, replies and short recent same-person follow-ups have priority. Bot exchanges have a host-enforced ten-reply limit and can end silently earlier.

Native tools support photos, videos, stickers, animation, voice/audio, files, albums, polls and permitted administrator actions. Sticker grids return short selection handles; inspect a full candidate image and send its handle so copied IDs cannot silently select another sticker. Telegram does not expose a complete arbitrary member list to bots; observed members and explicit lookups are the available sources.

The owner can run Python/shell/Git/package tasks and install public GitHub skills through tracked background host jobs. Ordinary members keep a limited gateway. Exact sensitive/risky commands require an owner-DM approval; credential access, main-prompt changes and known catastrophic commands are denied. There is no second approval/classification model.

To add a skill, send its GitHub repository link and ask Brodar to install it. A single nested skill is discovered automatically; repositories with several skills may need a folder choice. The completion receipt names the installed folder and commit. Then ask Brodar to use that skill for the task. Installation copies instructions and scripts; runtime dependencies are installed separately when needed. Skills remain discoverable after restarting. The owner runner supports Python, bash/sh and Node `.js`/`.mjs`/`.cjs` scripts. The built-in presentation tool is a basic fixed template; custom slide design uses a skill or an owner-run generator.

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
