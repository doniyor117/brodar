# Brodar — guide for development agents

This file is for agents developing the repository, **not the Telegram bot's system prompt**. Read it before making changes. This file contains the complete development handoff, including detailed project context below. CLAUDE.md imports this same guide.

## What we are building

Brodar Simon (also samy / simon bro) is a casual Telegram group companion that can participate without a mention, remember conversations and feedback, research, make files, react, and perform explicitly authorized group actions. the owner owns the project. The product should feel like a member of the group, not a formal helpdesk. Preserve the user's persona; do not “improve” it into corporate language.

This is a fork of Rustam-Z/hamroh with upstream history and MIT attribution preserved. The active application is `brodar/`; `hamroh/` remains upstream code and supplies the Claude stream-json worker. Both Claude and Codex use the new shared Brodar coordinator. Upstream capabilities are **not** automatically capabilities of the Brodar entry point.

## Start here

1. Inspect `git status`, recent commits and the files relevant to the task. Never assume the recorded deployment is still running.
2. Read the architecture, current-state and remaining-work sections below.
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

See the recorded production setup below; optional operator reference: docs/brodar/DEPLOYMENT.md. No PIDs or tokens in docs. Check current processes and lease ownership before stopping/restarting anything; restarting a worker is different from stopping its supervisor. Do not run upstream auto-update/publish scripts against runtime material.

## Handoff discipline

For meaningful changes, update this AGENTS.md with: behavior, decisions, migration requirements, validation, what was actually deployed, and remaining work. Keep dated evidence separate from current claims. Do not mark the project complete based on mocked tests, and do not advertise planned tools as implemented. Preserve this guide's useful specificity without copying private conversations or credentials into it.

# Detailed architecture and project context

Updated 2026-11-07, following implementation commit `2eaac48`. This section is the narrative handoff. Read actual code/configuration for current truth; recorded startup is not proof that Android has kept a process alive. No credentials or private chat contents belong here.

## Purpose and user intent

the owner wants a Telegram companion in his friend group: casual, witty, multilingual (English/Uzbek/Russian), capable of disagreeing, friendly roasting and useful agentic tasks. Its name is Brodar Simon; samy and simon bro are aliases. Ordinary users can ask for research, presentations and other supported work. It should participate naturally, sometimes react, sometimes remain silent, and learn useful preferences and group context over time.

The original Hyperagent persona was too formal/restrictive. A previous custom Brodar implementation also existed. The chosen direction was to retain Hamroh's upstream history and Claude integration, add Codex as a selectable brain, and build durable context around Neon rather than depend on files/SQLite surviving Termux. The main prompt should stay fixed to the agent, while supplementary notes evolve freely. Do not revive the earlier review-only memory policy: it was explicitly superseded.

## Repository and architecture decisions

- Upstream baseline: `0970d3a3dc8ff50970998515afe4ef79510f397e` from Rustam-Z/hamroh. MIT attribution and ancestry retained.
- Active package `brodar` is a new coordinator/tool/storage implementation, not just a patched upstream engine. `hamroh` remains importable and retains its CLI; ClaudeBrain wraps its CcWorker. Reusing the worker avoids pretending Codex speaks Claude's wire protocol.
- Both providers conform to `Brain`: start/resume, turn, steer, interrupt, close and session ID. Outcomes distinguish completed/failed/interrupted and stop/skip/heartbeat.
- A conversation owns a provider process and scoped localhost MCP endpoint. Auth is shared within a dedicated provider home so independent copies do not fight rotating refresh tokens; sessions/work directories remain conversation-specific.
- Scope audience is `telegram:{instance_id}:{chat_id}`; conversation adds topic ID. Bot ID is separately used for polling and sender checks. Topics share knowledge intentionally; different groups and DMs do not. Provider is a session dimension.
- Broad host capability restrictions protect main prompt, private services and system permissions. Learning freedom is preserved through memory tools, not arbitrary host execution. This is a capability design, not a promise that XML prevents all prompt injection.

## Message lifecycle

1. `run()` initializes Telegram, checks no webhook is active, acquires local instance lock and a database consumer lease, and restores pending topics. Separate database handles serve ingestion/control, foreground work, learning and reminders.
2. Poll only messages/edited messages. Filter allowlisted chats and omit recipient-only/ephemeral/business/guest contexts from ordinary public history. Archive service messages as service context, without treating them as requests. Bot messages are retained without triggering bot-to-bot loops.
3. `ScopedStore.ingest` persists canonical messages, inbox payloads and cursor progress. Quotes restore context but are not fresh authorizations. Edits invalidate dependent notes/embeddings; immutable archive UUIDs differ from Telegram numeric message IDs.
4. `addressed()` recognizes aliases, @username, replies to the bot and private chats. Addressed work immediately starts periodic typing. Ordinary group messages still enter ambient processing, normally spaced by 30 seconds. Debounce defaults to 1.5 seconds; direct events bypass ambient waiting.
5. Each Conversation claims inputs, starts/continues a durable work record, materializes supported media, and composes new messages, recent history, reply parents, notes, recall and prior receipts. Automatic embedding recall now has a two-second optional wait budget. Database work may still take longer.
6. Host creates a turn capability and supplies its nonce to tool calls. New text can be steered into an active turn. Media waits for foreground materialization. Inflight inputs are excluded from reclaim, claims renew every minute, and rejected steering does not consume processing attempts.
7. Model speaks through Telegram MCP tools. Plain assistant text is internal. Structured stop requires successful delivery; skip means silence; heartbeat permits bounded continuation (four turns, each capped at 600 seconds). Standalone `[SILENT]` is accepted internally. Each generated artifact must be delivered before normal completion.
8. Completion writes inbox/work status and receipts. Failed eligible inputs can retry; retry exhaustion is per input. Cancellation revokes authority and interrupts the provider. Some terminal addressed failures attempt a brief sanitized notice; comprehensive crash-safe failure notification is still unfinished.

One turn per room is enforced with a lock; global concurrency defaults to two rooms. Foreground traffic interrupts reflection. Long-lived per-topic processes are not currently evicted when idle.

## Provider behavior and compaction

Codex uses native CLI app-server over stdio JSON-RPC, not one `codex exec` per message and not custom calls using copied ChatGPT tokens. `RpcProcess` correlates requests and drains stdout/stderr. CodexBrain initializes, starts/resumes threads, binds steering to expectedTurnId, handles completion races/errors, and interrupts on cancellation. Missing local rollouts can create a new thread backed by canonical restored context.

Process feature flags, clean child environment, explicit instructions and empty environments limit native tools. Child environment retains necessary Termux linker/certificate variables plus scoped MCP authentication, not Telegram/Neon/Google/S3 credentials. Do not infer complete sandbox isolation from flags or unit tests alone; previous controlled canary results are historical evidence.

ClaudeBrain retains upstream argv/parser/readers, isolates environment/cwd, and treats upstream undrained injection fallback as rejected input. Its session/auth handling differs from Codex. Live Claude parity is not established by adapter tests; verify the actual login and supported capabilities when asked to deploy it.

Codex automatically compacts history. `BRODAR_CODEX_AUTO_COMPACT_TOKEN_LIMIT` passes `model_auto_compact_token_limit` only to Brodar app-server subprocesses. Recorded setting: 80000 tokens. Unset uses the model default. Global developer Codex config is untouched. Lower thresholds trade retained context for more frequent compaction cost; 80000 is a tuning hypothesis, not a benchmark-derived optimum. Compaction is not the same as durable group/user learning in Neon. Do not add a competing summarizer or falsify model_context_window to force compaction.

Official references: [Codex app-server](https://developers.openai.com/codex/app-server), [configuration](https://developers.openai.com/codex/config-reference). Check installed schemas/version before adopting newly documented protocol options.

## Durable memory and retrieval

Neon Postgres is canonical. `Postgres` uses pg8000 off the asyncio loop and a lock per connection, sets transaction-local audience for RLS, enforces statement timeouts, and does not blindly replay failed writes. Restricted runtime credentials are separate from operator credentials.

Messages remain searchable independently of learned notes. Search blends lexical and semantic results with rank fusion. Gemini Embedding 2 produces normalized 768-dimensional vectors; long sources are chunked. Semantic hit hydration now uses at most two bulk fetches instead of one query per hit. History browsing supports pagination and sender/topic filters; the model can retrieve beyond its current context window.

Notes are virtual `context/...` documents in the database, not arbitrary host files. They can contain facts, interests, social context, writing advice and methods. Tools support list/read/write/delete/history/restore/forget. Writes use optimistic revision checks and source references. Ordinary members' feedback can inform learning without approval, but cannot alter host permissions or the base prompt.

Foreground context prioritizes relevant speakers and group notes. Reflection periodically processes archived changes, can revise notes, and cannot publish Telegram messages. Edits mark dependent notes for review and clear stale vectors. Learned text is placed in contextual input, not elevated to a system instruction.

Migration 006 adds revision metadata for confidence and subject IDs. New restores recover those values. Older revisions lack recorded metadata and preserve current values instead of inventing history. Source associations remain a conservative union for forgetting propagation; this is not exact per-revision provenance restoration.

`memory_delete` is reversible retirement; `memory_forget` erases note content/history. Operator `forget-history` removes selected originals and source-linked derivatives, invalidates sessions and adds tombstones to prevent replay/reimport. It requires the consumer stopped. Quoted copies, artifacts, old public source tables, exports/backups and provider retention are separate: never promise universal erasure.

## Schema map

All application tables use `brodar_v2`; consult migrations for exact constraints/indexes/RLS.

| Tables | Responsibility |
| --- | --- |
| messages, inbox | Canonical archive and pending processing |
| poll_state, audience_registry | Consumer cursor/lease and deployment ownership |
| sessions | Provider session references keyed by scope/provider/prompt revision |
| deliveries | Stable operation receipts, including uncertain outcomes |
| jobs | Reminders and checkpointed foreground work |
| memory_documents, memory_revisions, memory_sources | Current notes, history/metadata and provenance |
| embeddings, reflection_state | Derived semantic index and learning cursor |
| forgotten_sources | Replay/import deletion tombstones |
| artifacts | Scoped object metadata and checksums |
| tool_calls | Bounded/redacted audit records |
| schema_migrations | Applied version/checksum tracking |

`brodar_ops.backup_keys` is operator-only. Six additive migrations are recorded as applied to production; never assume a fresh test branch has them.

## Tools and practical limits

`mcp.py` defines the actual inventory. Tool calls require the current turn nonce and authenticated endpoint; broker methods bind their audience outside model arguments. Write fences recheck revoked authority before database operations/commit.

- Messaging: send replies, real emoji reactions, anonymous polls, edit Brodar's own messages. No general cross-chat sending interface.
- Moderation: delete/mute/ban based on fresh explicit request archive IDs. Check current requester and bot rights. No automatic strike/ban system. No real moderation validation has been recorded.
- Research: provider-supported public web search/fetch, not guaranteed identical inventories across providers.
- Files: durable private S3 storage, text/code generation and actual editable PPTX creation; inbound photos/documents; image transport, paged PDF/PPTX and text reading. Not a full arbitrary-code document/rendering platform. Voice/video descriptions explicitly lack transcription/interpretation.
- Reminders: one-shot durable jobs with explicit timezone; creator/owner-bound cancellation; scheduler sends without model inference. No recurring-reminder feature.
- Work: durable checkpoint/status tools and bounded continuation, not unlimited autonomous execution.
- Knowledge: history and memory tools described above. Supermemory is not configured or integrated.

PPTX uses a fixed layout and density checks; do not claim presentation quality from file validity. Files live in object storage with DB metadata/checksums. Sending uses artifact IDs rather than arbitrary local paths.

## Delivery/recovery mechanics

Receipts progress through pending/sending/sent/failed/uncertain. Stable operation IDs detect duplicate calls and conflicting requests. Network ambiguity can mean Telegram accepted a send even if the client saw failure; uncertain sends are deliberately not automatically repeated. This is not exactly-once messaging.

Consumer leases fence overlapping hosts; local locks fence duplicate local instances. Supervisor restarts with backoff, obtains a Termux wake lock and rotates logs. Android can still kill the service. Scheduled encrypted backups use a separate operator subprocess, normally every six hours. Restore requires a matching, empty target database and verifies data/artifact checksums. Recovery keys must survive losing the phone. Neon DB and object storage sharing a provider does not protect against losing that whole provider.

## Recorded deployment (recheck before acting)

- Phone workspace: `~/projects/brodar`; native Termux on Redmi Note 9 Pro, Android 12/MIUI 14, Python 3.14. These are recorded machine details, not universal requirements.
- GitHub `doniyor117/brodar`; previous `brodar-old` retained. Latest application change recorded here: `2eaac48`, pushed to origin/main.
- Main token was switched to `@brodar_ai_bot`; Original gangsters was added to the private allowlist. Test bot `@brodar_2_bot` remains separate. Do not repeat old claims that production is still test-token-only.
- Recorded model: `gpt-6-luna`, provider Codex, timezone Asia/Samarkand. Brodar's dedicated ChatGPT device login was completed; don't copy personal auth.
- On 2026-11-07 the supervisor was found absent, migration 006 was applied, local compaction/recall settings were updated and a new supervisor was launched. No subsequent end-to-end latency measurement or Telegram test message was performed in that repair session.
- Secrets locations: `~/.config/brodar/runtime.env`, `~/secrets/.env`. Operator URI `~/.config/brodar/production-admin-uri`; private storage and recovery JSON alongside it. Paths are operational pointers, not permission to expose their contents.
- External runtime defaults to `~/.local/share/brodar`; provider auth in `codex/` and `claude/`; provider conversation directories under `providers/`. Boot entry: `~/.termux/boot/10-brodar.sh`. Never preserve PIDs in a handoff.
- Config precedence: runtime.env, then selected secrets/env-file, then process environment; explicit --brain wins provider selection. Read Settings.load before changing assumptions about precedence.

See [DEPLOYMENT.md](docs/brodar/DEPLOYMENT.md) for migration, backup, restore and supervisor commands. A webhook/second consumer conflict must be resolved deliberately, not by silently deleting another deployment's webhook.

## Validation and remaining work

At `2eaac48`, local `tests/brodar` returned 76 passed and 8 skipped. New tests cover bounded embedding wait, compaction argv locality, failure notices, search hydration/rank and revision metadata. Live SQL lifecycle test was collected but skipped without a disposable DB. Earlier STATUS.md records broader tests/live probes from older commits; those results were not rerun comprehensively after the latest changes. CI result for the latest code is not established by this guide.

Recent logs showed preparation delays up to 103.6 seconds before inference. Fixes removed the 60-query hydration loop and bounded optional embedding waiting. No measured before/after speedup is available. Other latency sources remain: synchronous steering/startup on poll-dispatch path, queue contention, repeated context, provider inference/research, and background activity.

Next work, in priority order:

1. Exercise the input lifecycle SQL regression in a disposable migrated database; add fault injection for accepted steering/accounting races, renewal failure and shutdown.
2. Make failure notices a durable independent outbox and cover preparation exceptions, timeouts and mixed exhausted/fresh batches. Current notice delivery is best effort and not all failure paths are covered.
3. Record numeric queue/preparation/provider/tool/first-delivery timing with bounded logs; compare ordinary usage before claiming a performance gain. Provider timing is currently INFO; default service threshold is WARNING.
4. Decouple slow steering/startup from polling using owned tasks and deterministic cancellation/shutdown, not untracked fire-and-forget tasks.
5. Reduce duplicate recent-context injection into persistent threads with compaction/session-aware bookkeeping. Preserve access to older history and correction semantics.
6. Consolidate competing embedding/indexing paths and split RuntimeStore by transactional responsibility only where useful. Avoid another wholesale framework rewrite.
7. Measure idle provider-process footprint before implementing eviction. Validate live Claude separately, real spare-group reminders/moderation only when authorized, and phone reboot/multi-day survival.
8. Evaluate tone and recall with owner-approved examples. Do not solve infrastructure delays by removing personality, learning or ordinary group participation.

## Optional historical references and precedence

- AGENTS.md: complete development orientation and continuation context; CLAUDE.md imports it.
- LATENCY-AND-COMPACTION.md: latest repair evidence, research, configuration and remaining optimization work.
- REVIEW-2026-11-07.md: findings against the pre-fix commit; not a claim all findings still exist unchanged.
- IMPLEMENTATION-PLAN-2026-11-07.md: repair packages, some now implemented; consult latest update for remaining portions.
- DEPLOYMENT.md: practical operator procedures; recorded rollout statements may be older.
- PLAN.md / MEMORY-ARCHITECTURE.md / SELF-EVOLUTION.md: design rationale; automatic learning supersedes review-only drafts.
- STATUS.md: dated historical validation inventory, not a live health dashboard.
- CODEX-BRAIN-RESEARCH.md / HAMROH-ARCHITECTURE-REVIEW.md / CLAUDE-AI-LEARNING-RESEARCH.md: original research and upstream constraints; recheck version-dependent claims.
- PERSONA.original.md / PERSONA.md: archives/design copies. Actual prompt inputs are under repository `prompts/`.

These historical documents are supplementary; the development handoff is contained here. For conflicts, current explicit user decisions and actual code/runtime evidence take precedence over historical plans. Record uncertainties instead of resolving them through invented claims.
