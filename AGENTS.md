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

## Memory evolution implementation plan — 2026-11-07

Status: implementation added in the memory-worker change described below; live database/provider validation and production rollout remain pending. The numbered packages below preserve the implementation intent and acceptance criteria. Preserve the earlier reliability backlog; memory work does not resolve input lifecycle/failure-outbox defects. Baseline reviewed: `b162cb2`. Only documentation changes belong to this planning task.

### Evidence and design interpretation

Read [Anthropic's components of agent memory](https://www.anthropic.com/research/components-of-agent-memory) and its [dreaming cookbook](https://platform.claude.com/cookbook/managed-agents-cma-dreaming-with-a-dynamic-workflow). Their useful design ideas are searchable originals, revisable notes, optional linked representations, a small injected index, and background correction against evidence. Their experiments are not proof of improvements for Brodar or documentation of the complete claude.ai implementation. Apply the ideas through our existing provider-neutral Brain and Neon tools; no Managed Agents or Supermemory dependency is proposed.

User intent: automatic learning from ordinary conversations and feedback, including communication style and useful knowledge, without per-note owner approval. Main persona and host authority stay fixed. Learned writing advice remains contextual and editable. Preserve natural slang, opinions and roasting; do not use this project to rewrite the personality. Distinguish a person's feedback in one situation from a universal group preference. Store tentative interpretations as tentative rather than treating repetition as verification.

### Code findings to address

- `runtime.py:Conversation._reflect` uses the foreground Brain, broker, turn lock and normal session record. `maintain` obtains the default-topic Conversation for an audience. Thus maintenance context enters a conversational session, and maintenance is coupled to its lifecycle. `pause_reflection` already revokes the capability and interrupts: preserve this behavior when moving workers.
- `maintain` performs indexing and reflection in one sequential loop. Embedding errors sleep for 60 seconds inside that loop, delaying other audiences and reflection. It checks foreground readiness before acquiring the semaphore, but foreground work may arrive afterward. Separate sessions alone will not remove resource contention.
- `LearningStore.reflection_batch` selects at most 50 changed messages after a timestamp/UUID cursor; threshold is 20 messages or one hour. `_reflect` caps most batches at 30,000 content characters, but accepts an oversized first row. It also lists only 40 recent notes, with previews, rather than loading complete evidence. Tools permit deeper retrieval, but no scheduled sweep guarantees older notes get revisited.
- `reflection_finish` advances on a completed terminal outcome. There is no durable run manifest describing examined messages, note revisions, outcomes or preemption. Zero note edits can be legitimate; do not force writes to mark progress.
- `turn_context` and `context_notes` duplicate selection logic: up to 12 notes, 3,000 characters each, 14,000 total, ranked by speaker association/group paths/recency. There is no explicit small index or revision-aware refresh of injected memory in persistent sessions.
- `LearningStore.index_candidates` fills its batch with oldest messages before considering notes. A chat backlog can starve new memory embeddings. `RuntimeStore.messages_for_index` is a second candidate selector with different eligibility; inventory callers before consolidating.
- `ToolBroker.search` awaits query embedding without the foreground preparation recall timeout. Search tools need their own bounded semantic wait and lexical fallback.
- `memory_write` validates source UUIDs within the audience, uses revision checks and clears stale vectors. Sources are optional; associations accumulate as a conservative union. Revisions store confidence/subjects, but not an exact per-revision source snapshot. Do not claim precise provenance recovery from current history.
- `memory_write` clears `needs_review` on any successful rewrite. For evidence-backed repairs, record which changed sources were actually checked; a rewrite alone is not evidence of correction.

### Implementation order and acceptance criteria

#### 1. Establish measurements and bound retrieval work

Files: `runtime.py`, `broker.py`, `learning.py`, `runtime_store.py`, `settings.py`, existing runtime/storage tests.

Record queue wait, preparation, provider first event, first successful delivery, recall duration, maintenance duration/preemption and index backlog by kind. Record counts/bytes and correlation IDs, never message bodies or credentials. Ensure metrics are observable at deployed log levels. Separate embedding backoff from the whole maintenance loop. Give messages and notes fair indexing quotas with bounded work per audience; reuse one candidate implementation. Bound tool-query embedding waits and fall back to lexical search on timeout. Retain cancellation propagation.

Acceptance: a blocked embedding service does not block lexical tool search or another audience's maintenance; a persistent message backlog does not starve notes; timings distinguish model time from preparation. Establish a baseline before claiming a speedup.

#### 2. Isolate background learning execution

Files: extract a small provider-construction helper from `Conversation.start`; introduce `brodar/reflection.py`; update `runtime.py`, `broker.py`, `mcp.py`, `settings.py` as needed.

Use one lazy maintenance worker at a time globally on the phone, keyed to an audience for each run. It owns a separate Brain session, cwd, broker, MCP endpoint and capability. Share the existing provider auth home, never copied tokens. Reuse both provider adapters. Initially use fresh maintenance threads per run and close the worker afterward: no sessions-table migration or fake topic IDs required. This trades startup overhead for simple restart/cross-audience isolation; measure before adding pooling. Durable progress lives in Neon.

Move reflection orchestration out of Conversation. Foreground arrivals signal preemption; revoke write capability before interrupting. Bound interrupt/close waits and own every task through shutdown. Avoid awaiting lengthy teardown in polling. Retain total provider concurrency limits, including maintenance, and foreground priority. The maintenance endpoint exposes history/memory tools only; no Telegram delivery, moderation, reminders or artifact side effects. Existing host checks remain defense in depth.

Acceptance: both providers receive isolated maintenance instructions/context; late tools after cancellation fail; foreground events are not lost or delayed by unbounded cleanup; restart does not resume a maintenance session as chat; no background Telegram actions succeed. Use fake Brain/provider tests before any live run.

#### 3. Make maintenance progress durable and inspectable

Files: `learning.py`, `reflection.py`, `brodar/migrations/007_*.sql` (use next free number), recovery/backup code where explicit table inventories require updates.

Add an audience-scoped maintenance run record: kind (incremental/consolidation), status, owner/fencing identity, input boundary, started/completed times, examined source IDs or bounded manifest, changed document revisions, bounded outcome/error, and retry state. Apply RLS/grants consistently. Reuse consumer fencing and permit only one maintenance run per audience; do not invent a second unfenced ownership mechanism.

Advance incremental cursor only after successful processing of the exact supplied window and durable run completion, in one transaction. Cancellation/failure keeps unfinished inputs eligible. Note writes may already have committed: retries read current revisions and avoid duplicate notes. An explicit successful no-change result is valid. Represent an oversized transcript with bounded chunks or continuation offsets; never advance past omitted text. Account for edits that arrive during a run. Add retention for run metadata.

Acceptance: crash before/after note writes, edits during processing and lease loss neither silently skip input nor overwrite newer progress. Exercise real transactions on a disposable migrated DB, not only mocked SQL. Backup/restore includes any new required state.

#### 4. Consolidate older knowledge and improve provenance

Files: `reflection.py`, `learning.py`, `store.py`, `mcp.py`, `broker.py`, migrations as required.

Keep incremental learning for new events. Add a separately scheduled, bounded consolidation pass selecting notes needing review first, then stale/contradictory or repeatedly used notes. Maintain a fair cursor so old notes are eventually visited. Start with a configurable quiet-period cadence and per-run time/tool/byte budget; initial values are tuning hypotheses. Do not scan all transcripts on every pass.

Expose source references with note reads and provide bounded retrieval of cited original messages. Add per-revision source snapshots for future revisions while preserving conservative forgetting associations. Unknown old provenance remains unknown. When merging notes, propagate their source dependencies so forgetting cannot leave derived summaries intact. If introducing explicit note-to-note links, track dependency invalidation and cycle-safe deletion traversal; otherwise flatten source UUIDs into derived notes. Inspect `forgetting.py` and edit invalidation paths before rollout.

The model may create, update, merge and retire arbitrary useful context notes. Give guidance to check claims, distinguish quotations/opinions/guesses, preserve dates and correct outdated information. Avoid rigid allowed-topic lists or a compulsory approval queue. Repairs record checked evidence; unexplained rewrites must not automatically mark invalidated claims verified.

Acceptance: later corrections supersede old claims; jokes do not become certain biographies in evaluation cases; unrelated groups/DMs remain inaccessible; source deletion invalidates all derived layers; concurrent foreground memory writes produce revision conflicts followed by rereads, never silent overwrites.

#### 5. Add a compact index and selective context loading

Files: `learning.py`, `prompts.py`, `store.py`, `reflection.py`, foreground context construction.

Use an ordinary virtual document such as `context/overview.md` as the learned index. It links to useful people/topic/method/style notes in the same audience and contains concise descriptions. Keep ordinary note creation unrestricted. Apply a configurable small injection budget to the index and relevant excerpts; retain full documents and transcript search for tools. Unify the two context-selection paths.

Record injected revisions per provider session. Send changes/corrections when needed without repeating every note on every turn. Fresh/resumed threads and compaction must recover the necessary index; verify provider events before relying on compaction notifications. If reliable tracking is unavailable, use bounded reinjection rather than silently dropping context. Deletion cannot erase an already supplied provider context: invalidate affected sessions when required, following existing forgetting semantics.

Acceptance: relevant older notes remain discoverable despite recency; broken/retired links are repaired; token/byte budgets hold; a fresh session can answer using old evidence; edits and forgetting do not leave stale injected advice authoritative. Do not lower compaction thresholds as a substitute for this work.

#### 6. Evaluate and roll out incrementally

Add synthetic, repository-safe cases for recall, cross-message synthesis, correction, preference scope, multilingual tone, callbacks, forgetting and malicious instructions in stored text. Compare current behavior, search-only retrieval and the proposed index/consolidation path. Score evidence correctness, unsupported assertions, tone feedback fit, retrieval/first-delivery latency and maintenance cost separately. Do not assume gains observed in Anthropic's models transfer to gpt-6-luna.

Run deterministic lifecycle/capability tests and disposable-database migration/restart tests first. Live provider evaluation consumes resources and must use an explicitly selected dataset; private chats stay out of Git. Deploy scheduling isolation and bounded search before enabling consolidation/index changes. Use feature switches for new maintenance modes, not an automatic provider fallback. Rollback disables the new modes and leaves additive schema/history intact. Keep ordinary foreground learning available.

Definition of done: code and migration review complete; targeted tests pass with skips disclosed; both adapters supported with live parity limitations stated; restart/forgetting paths covered; measured comparison recorded; AGENTS.md updated with actual deployed settings and results. No claim of smarter learning or lower latency from a successful process startup alone.

### Agent execution notes

Work in the numbered order, in reviewable commits on main as requested by the owner. Do not implement all phases as a wholesale rewrite. Start each phase by checking current code and migrations because this plan records a specific baseline. Update this section's status per phase; CLAUDE.md already imports it, so create no additional handoff/plan documents. Existing urgent reliability work may be interleaved before memory rollout. No runtime implementation, production migration, restart or live inference was performed for this planning task.


### Memory-worker implementation record — 2026-11-07

Implemented in source (production has NOT been migrated/restarted for this change):

- `Conversation.start(maintenance=True)` reuses both provider adapters with a separate cwd, fresh thread and history/memory-only MCP inventory. It never loads/saves the foreground session or starts the chat loop. Provider auth remains shared in the dedicated home. `reflection.Maintenance` owns one lazy worker, global concurrency slot, cancellation and bounded cleanup. Incoming human messages revoke the capability synchronously and cancel the owned run. Polling does not await teardown. Learned notes already committed survive preemption; unfinished batches replay.
- Incremental learning keeps the 20-message/hour threshold, 50-row batch and 30,000-character presentation budget. Migration 007 adds a partial-message cursor so oversized originals are processed across runs. Completion verifies presented evidence versions, cursor state, active consumer lease and run owner before atomically checkpointing. Run manifests retain source IDs/timestamps, status and changed write/restore revisions for 30 days, with no transcript content. Delete/forget operations remain in the tool audit rather than the changed-revision list. No-change outcomes can complete normally.
- Optional consolidation selects eight older notes per pass, rotates by last-maintained time and asks the model to correct, merge, retire and maintain `context/overview.md`. This is supplementary learned context, never system instructions. Full note reads/history tools remain available beyond previews. Cadence and resource limits are configurable. There is no owner approval queue.
- Source IDs are returned with note reads, inherited conservatively into new notes derived during the same tool turn, and stored in future revision metadata. These snapshots are conservative dependency sets, NOT exact claim-level provenance. Restore carries recorded source IDs forward. A flagged existing note stays flagged unless all linked source messages were read in the writing turn. Late reads cannot populate a subsequent turn's evidence set. Existing source forgetting invalidates derived notes through these associations; model output that omits all evidence dependencies is still a limitation, not solved semantic provenance.
- Foreground selection prioritizes the overview. A shared budget function caps overview injection at 2,000 characters, other excerpts at 3,000 and all notes, including semantic recall additions, at 14,000. Bounded reinjection is used deliberately; revision/compaction event tracking was not added without reliable cross-provider signals. This may still duplicate context in long threads.
- Tool-query embeddings time out with lexical fallback. Indexing reserves capacity for notes, shares one candidate selector, uses per-embedding timeouts and applies backoff without sleeping the whole maintenance loop. Foreground semaphore wait, preparation, provider duration, Codex first-event and broker first-delivery metrics are warning-level and omit content. First-delivery time starts at capability creation (after claim/work setup), not Telegram arrival. Metrics do not prove a speedup.

New operator settings: `BRODAR_LEARNING_ENABLED=true` (incremental default), `BRODAR_CONSOLIDATION_ENABLED=false` (enable after SQL/provider evaluation), `BRODAR_CONSOLIDATION_INTERVAL=21600` seconds and `BRODAR_MAINTENANCE_TIMEOUT=120` seconds. Existing Brodar-only compaction and recall settings remain in force. New inference budgeting is time/concurrency based; there is no exact provider cost/token cap or automatic choice of a different learning model.

Migration/rollout: apply `brodar/migrations/007_maintenance.sql` through the normal operator migration workflow on a disposable DB first. Regrant existing restricted runtime role table privileges using the updated provisioning workflow and its existing credential; never rotate it by accident. `maintenance_runs` is included in provisioning and encrypted backup table inventories. Apply the same additive migration/grants to production before restarting new code. Production is still recorded at schema 006 until that actually happens. Disabling consolidation retains incremental learning; disabling learning stops background reflection but foreground memory tools remain available. Rollback source deployment must account for the extra backup table and additive columns; never delete learned history as rollback.

Validation recorded: local `tests/brodar` after the changes: 81 passed, 10 skipped, one dependency warning. Earlier broader run during implementation: 773 passed, 20 skipped; later metrics/capability changes were covered by the subsequent Brodar run. Added deterministic tests cover maintenance-only tools, embedding timeout fallback, preemption/revocation, valid no-change completion, stale source reads and context budgets. Two added live SQL tests cover partial-message restart/edit invalidation and review-flag evidence, but were skipped because no `BRODAR_TEST_DATABASE_URL` was configured. SQL behavior is therefore not claimed validated against a real DB. Existing live tests were also skipped. No production migration/restart, real Telegram message or live inference was performed.

Evaluation: synthetic cases live in `tests/brodar/memory_cases.json`. Explicit opt-in runner: `.venv/bin/python scripts/evaluate-brodar-memory.py --mode search` and `--mode index`, with a disposable migrated `BRODAR_TEST_DATABASE_URL` and existing dedicated provider login. It creates isolated synthetic audiences/fresh provider sessions, prints answers/timings/manual rubrics, and cleans database test records. It never connects Telegram. Provider subprocess rollouts may remain under the dedicated runtime. This is a basic retrieval/index probe; it does not yet automate multi-session learning/consolidation comparisons or semantic grading. No improvement score has been measured. Add owner-approved realistic feedback examples and evaluate automatic learning quality before enabling consolidation broadly.

Remaining implementation/validation: disposable DB execution including lease loss/crash timing, live Claude/Codex maintenance parity, better telemetry for backlog/recall/Claude first-event, a full learned-notes ablation evaluation, and measured production latency. Fair indexing and bounded context reduce specific sources of overhead; startup still occurs per maintenance run and may be significant on the phone. The earlier failure-outbox and poll/startup reliability backlog remains separate. Do not mark every numbered acceptance criterion complete based on these unit results.
