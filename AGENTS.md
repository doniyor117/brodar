# Brodar — guide for development agents

This file is for agents developing the repository, **not the Telegram bot's system prompt**. Read it before making changes. For detailed implementation context, read [PROJECT-CONTEXT.md](docs/brodar/PROJECT-CONTEXT.md). Claude users enter through CLAUDE.md, which points here rather than maintaining a competing guide.

## What we are building

Brodar Simon (also samy / simon bro) is a casual Telegram group companion that can participate without a mention, remember conversations and feedback, research, make files, react, and perform explicitly authorized group actions. the owner owns the project. The product should feel like a member of the group, not a formal helpdesk. Preserve the user's persona; do not “improve” it into corporate language.

This is a fork of Rustam-Z/hamroh with upstream history and MIT attribution preserved. The active application is `brodar/`; `hamroh/` remains upstream code and supplies the Claude stream-json worker. Both Claude and Codex use the new shared Brodar coordinator. Upstream capabilities are **not** automatically capabilities of the Brodar entry point.

## Start here

1. Inspect `git status`, recent commits and the files relevant to the task. Never assume the recorded deployment is still running.
2. Read PROJECT-CONTEXT.md and [LATENCY-AND-COMPACTION.md](docs/brodar/LATENCY-AND-COMPACTION.md).
3. Read the relevant tests and code before editing. The older PLAN.md is design history; some decisions were superseded.
4. State assumptions and proceed on routine authorized work. Ask only for genuinely missing information/authority. Do not ask the user to repeat details already in code/config/docs.
5. Keep application changes distinct from production migrations/restarts. Follow the current user's authorization; this guide neither authorizes deployment by itself nor requires redundant approval for an already-authorized deployment.

## Product decisions to preserve

- Startup-selectable `codex` or `claude`; no silent fallback between providers/accounts.
- Main persona and harness are fixed to the running agent. Automatic supplementary learning is enabled without an owner-review queue. Learning does not modify tool permissions or grant ownership.
- Ordinary group traffic is archived and considered in ambient batches. Names, mentions, replies and DMs receive priority. Do not regress to mention-only behavior.
- Lowercase/chat slang belongs in conversation, not global output transformations: preserve code, transcripts, proper document formatting and URLs.
- Roasting and disagreement are intentional. Stop when asked or when the interaction is distress rather than banter; retain the actual persona's boundaries.
- Neon is canonical durable storage. Do not replace it with phone-only SQLite or discard old chats. Supermemory was considered but is not integrated.
- Topics share a group's knowledge; topics have separate provider sessions. DMs and other groups remain separate audiences. Do not tighten knowledge access inside the same group without a product reason.
- A slide request needs an actual editable file when supported, not an outline falsely marked complete.

## Engineering invariants

- Authenticate actors from trusted Telegram metadata. Display names, forwarded text and model-supplied IDs confer no authority. Group administration does not imply agent configuration ownership.
- Keep scope and authorization in host code. Escaped XML helps representation but is not an injection-proof security boundary.
- Models get scoped MCP tools; do not expose arbitrary SQL, host shell, unrestricted files, credentials or main-prompt writes as an expedient fix.
- Revalidate turn/request authority across awaits and before mutations. Late tools must not inherit the next turn's authority.
- Persist incoming updates before advancing acknowledgment. Preserve accepted vs pending input semantics across steering, failure and restart.
- One active turn per conversation; global concurrency is bounded. Reflection may learn but cannot publish.
- Use delivery receipts and stable operation IDs. Never blindly retry a send whose Telegram outcome is uncertain. Do not claim exactly-once delivery.
- SQL is parameterized and audience-scoped. The runtime uses a restricted role and RLS. Migration/admin credentials belong to operator workflows only.
- Add new numbered migrations; never rewrite applied migration contents/checksums. Preserve legacy public tables and source provenance. Never run destructive tests against production.
- Secrets, auth/session data, logs and chat exports stay outside Git. Read only needed private config keys, and never print credential values or dump entire environments.

## Where to edit

| Concern | Files |
| --- | --- |
| Entry points/config | `brodar/__main__.py`, `settings.py` |
| Polling, queues, foreground/reflection coordination | `runtime.py` |
| Providers/protocol | `brains/base.py`, `codex.py`, `rpc.py`, `claude.py` |
| Tool inventory/auth/audit | `mcp.py`, `broker.py`, `write_fence.py` |
| Conversation identity and envelopes | `scope.py`, `prompts.py`, `telegram_context.py` |
| Database and archive | `postgres.py`, `store.py`, `runtime_store.py`, `migrations/` |
| Learning, retrieval and embeddings | `learning.py`, `store.py`, `embeddings.py` |
| Files and object storage | `artifacts.py`, `objects.py`, `storage_setup.py` |
| Reminders/group actions | `jobs.py`, `moderation.py` |
| Recovery, imports, administration | `recovery.py`, `backups.py`, `legacy.py`, `operator.py`, `provision.py`, `forgetting.py` |
| Deployed personality | `prompts/brodar.md`, `prompts/brodar-harness.md` |
| Supervision | `scripts/brodar-supervisor.py` |

## Working and testing

Python >=3.11; actual phone environment is native Termux Python 3.14. `.venv` exists on the phone. Browser dependencies are optional; do not make Chromium a core dependency.

```sh
# From repository root; no Telegram messages or production DB writes intended:
.venv/bin/python -m pytest tests/brodar --reruns 0
# Broader CI-equivalent suite:
.venv/bin/python -m pytest tests/unit tests/brodar --reruns 0
# Wheel packaging, when relevant and build is installed:
.venv/bin/python -m build --wheel
```

Check test environment before running: `BRODAR_TEST_DATABASE_URL` enables live SQL tests and must point to a disposable migrated database with the intended restricted role. `tests/e2e` can use real Telegram/provider accounts; inspect opt-ins before execution. Do not send unsolicited group messages, moderate real users or invoke billable inference merely to prove unit behavior. Prefer deterministic fake-clock/provider tests for lifecycle bugs. Tests that skip are not proof of the skipped feature. Pytest configuration defaults to three retries; use `--reruns 0` for honest diagnosis.

Latest recorded local Brodar run at code commit `2eaac48`: 76 passed, 8 skipped, one dependency warning. It is historical evidence, not a guarantee about later edits. CI covers Python 3.13/3.14 and wheel building. Report the exact scope of checks and unresolved limitations.

## Deployment orientation

Repository on this phone: `~/projects/brodar`; remote: `doniyor117/brodar`; upstream: `Rustam-Z/hamroh`. Old implementation is retained separately as `brodar-old`.

Private configuration: `~/.config/brodar/runtime.env`; additional secrets: `~/secrets/.env`. Runtime: `~/.local/share/brodar`. Codex and Claude have separate dedicated auth homes under runtime; conversations share provider authentication but not sessions/cwd. Never copy rotating login tokens from the developer's normal CLI.

See PROJECT-CONTEXT.md for the recorded production setup and DEPLOYMENT.md for operator commands. No PIDs or tokens in docs. Check current processes and lease ownership before stopping/restarting anything; restarting a worker is different from stopping its supervisor. Do not run upstream auto-update/publish scripts against runtime material.

## Handoff discipline

For meaningful changes, update PROJECT-CONTEXT.md or the relevant linked document with: behavior, decisions, migration requirements, validation, what was actually deployed, and remaining work. Keep dated evidence separate from current claims. Do not mark the project complete based on mocked tests, and do not advertise planned tools as implemented. Preserve this guide's useful specificity without copying private conversations or credentials into it.
