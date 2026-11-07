# brodar

A Telegram companion built on [Hamroh](https://github.com/Rustam-Z/hamroh), with selectable Codex and Claude brains, durable Neon history, and automatic conversational learning.

**The spare-group pilot is running.** Both adapters, Neon history, automatic learning, hybrid recall, durable files, real PPTX delivery, group-action tools, reminders, task checkpoints and encrypted backup/restore are implemented. Live Claude, group behavior and sustained phone reliability still need validation. See [the tracker](docs/brodar/STATUS.md) for evidence and remaining work.

## Run

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
brodar doctor
brodar --brain codex
# or
brodar --brain claude
```

On Termux, existing native Python libraries can be reused with `python -m venv --system-site-packages .venv`. Chromium is optional: install `.[browser]` only on a supported host. A CLI login is needed for the chosen brain.

Copy the setting names from [.env.brodar.example](.env.brodar.example) into an external private file, then use `brodar --env-file /path/to/private.env`. The default reads `~/.config/brodar/runtime.env` and `~/secrets/.env`. Never commit tokens, provider authentication, runtime state, or chat archives.

The application uses a restricted database role. Apply versioned migrations with an operator connection on a test branch first; the runtime does not migrate or use the owner role. [Deployment instructions](docs/brodar/DEPLOYMENT.md) cover provisioning, history import, backups and the Termux supervisor.

## Conversation and learning

The main persona stays fixed. Brodar can create, revise, search, retire and forget supplementary context notes without owner approval. Notes have source links and revisions; private chats and groups have separate audiences. Telegram topics share group knowledge while keeping separate provider sessions.

History search combines PostgreSQL lexical retrieval with Gemini Embedding 2 vectors. Original messages survive model context compaction. Background reflection consolidates useful feedback and experience; it does not rewrite the main prompt or grant permissions.

Public replies use conversation-bound tools and durable delivery receipts. Internal model text is never broadcast as a fallback. A network timeout can leave a send uncertain; the runtime avoids blindly repeating it.

## Development

```sh
pytest tests/unit tests/brodar
```

Browser tests require the optional browser package. Live Claude tests require `BRODAR_LIVE_CLAUDE_TESTS=1`; disposable Neon integration tests require `BRODAR_TEST_DATABASE_URL`. Mocked tests do not establish live provider or Telegram behavior.

[Plan](docs/brodar/PLAN.md) · [Memory design](docs/brodar/SELF-EVOLUTION.md) · [Code review](docs/brodar/CODE-REVIEW.md) · [Upstream Hamroh documentation](docs/UPSTREAM-HAMROH.md)

Brodar retains Hamroh's Git history and MIT attribution. The historical `hamroh` command and Claude worker remain available; Brodar's shared runtime lives in `brodar/`.

## Continuing development

Development agents: read [AGENTS.md](AGENTS.md), which contains the complete project context. [CLAUDE.md](CLAUDE.md) points to the same guide.
