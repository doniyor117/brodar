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
- Approved groups/topics share one serialized provider session for coordinated work; owner DMs have separate private sessions. Archives/notes remain audience scoped; explicit routing/search tools bridge approved groups. Do not mix private DM history into the shared group session.
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
- Private DM conversations own their provider process and scoped localhost MCP endpoint; approved groups share GroupSession's process/thread/endpoint with per-turn broker binding. Auth is shared within a dedicated provider home so independent copies do not fight rotating refresh tokens; sessions/work directories remain conversation-specific.
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

### Production deployment record — 2026-11-07

Owner explicitly requested deployment after the implementation. Migration 007 was applied first to the separate checkpoint Neon branch, then production. Existing restricted runtime-role grants were updated with its existing password. Production is now schema 007; earlier statements recording schema 006 are historical. Main provider/model remains Codex / `gpt-6-luna`; the main Telegram token and three-chat allowlist were retained.

Private runtime configuration now enables automatic learning and consolidation (`BRODAR_LEARNING_ENABLED=true`, `BRODAR_CONSOLIDATION_ENABLED=true`), with a 21,600-second consolidation interval and 120-second maintenance limit. The verified old supervisor was stopped gracefully, then the supervisor was restarted with the existing six-hour backup arguments. Boot entry continues to launch that same wrapper. No manual Telegram message or moderation action was used for validation.

Live checkpoint validation: all nine `test_postgres_live.py` checks passed, including the new partial-message restart/edit and review-flag checks. A separate consolidation selection/completion/cadence probe passed. A real isolated `gpt-6-luna` maintenance turn completed; a second explicit synthetic memory-tool probe created a note and read it back successfully. Synthetic records were removed afterward. These probes establish the runtime/tool path, not conversational learning quality. Live Claude parity and comparative recall/tone evaluation remain unmeasured.

The older input lifecycle regression initially failed on the checkpoint branch and exposed a real inbox SQL problem: the limited `IN (SELECT ... FOR UPDATE SKIP LOCKED)` candidate selection could be rescanned during the UPDATE, claiming more rows than requested and charging additional attempts. `claim_inputs` now uses a MATERIALIZED candidate CTE and updates only those fixed IDs. The live lease test was updated to renew its lease during repeated operations, as production does, and to report states on failure. It then passed (1 passed). Local Brodar checks after the fix: 81 passed, 10 intentionally skipped live tests, one dependency warning. The nine storage checks and the corrected lease check together cover the ten live tests; they were not rerun as a combined suite after the CTE change because only the claim query changed and its dedicated live regression passed.

Production doctor verified database connectivity as the restricted runtime role, without superuser or RLS bypass. One active consumer lease was observed after restart. Polling/maintenance startup observation is recorded in the deployment follow-up below. Process startup and synthetic tests do not establish multi-day Android survival or a measured response-speed improvement.

Deployment follow-up: service log confirmed polling as `@brodar_ai_bot` with Codex / `gpt-6-luna` at 17:51:02 phone-local time. Supervisor and worker were present, and a production consolidation run was recorded as running. Other configured audiences had not yet produced maintenance runs at that observation. Automatic group participation is enabled as before; no manual group test message was sent.


### Latency audit and corrective plan — 2026-11-07

Owner reports very slow replies and disappearing typing after memory rollout. Read-only code/log research identified the following; no runtime changes or restart were performed for this audit. Previous deployment success does not establish acceptable latency.

Observed service-log sample: foreground preparation 11,049 ms and 5,032 ms; provider turns 215.12 s and 17.72 s; one semaphore wait 26,670 ms. First-delivery metric for the shorter turn was 15,848 ms from capability creation, not Telegram receipt. The long turn cannot be attributed wholly to model generation or memory from these metrics: provider duration includes tool work/steering, and first_event is the first item/turn event, not first useful output. Empty maintenance checks repeatedly cost around three seconds.

Confirmed implementation problems:
- No sustained-idle gate. Maintenance checks instantaneous wake/inflight state, not elapsed quiet time. That state also misses conversation creation and the window between clearing wake and claiming inputs. Consolidation interval is time since previous consolidation, not inactivity.
- Indexing runs ahead of the maintenance busy check, during chat activity; preempt cancels only the model maintenance task, not indexing. Preemption itself occurs after durable ingest. Maintenance retains a foreground semaphore slot through status updates and cleanup (bounded individually at five/ten seconds). Separate DB handles do not eliminate database row-lock/network contention.
- Every nonempty archived message is eligible for semantic indexing, including tiny reactions. Candidates select at most three messages plus two notes per pass; vacant note capacity is not refilled. Each source/chunk is embedded sequentially through embedContent, then stored in its own transaction; inserts are per-vector. This is neither API batching nor conversational-window indexing. Oldest-first backlog may leave recent useful material unindexed.
- Foreground automatic recall embeds any combined incoming query >=20 characters if the audience has any index. Only embedding has the two-second deadline; the following multi-query SQL retrieval does not share that budget. Everyday chat therefore pays optional remote memory costs.
- All foreground rooms share one locked Postgres connection. Each transaction has BEGIN/settings, audience setup, query steps and commit round trips. Reply validation, delivery reservation/lease validation, completion and awaited MCP audit add serial remote work. Preserve fencing/receipt guarantees while combining operations; never remove durability just to reduce latency.
- Polling awaits ingest, conversation creation and signal. Signal may await claims, history, provider steer and bookkeeping. One slow room delays later updates. Empty conversation recovery runs every 30 seconds, occupies a foreground slot and queries Neon even with no new messages.
- Typing waits four seconds after each request completes; Telegram status lasts at most five seconds. BadRequest/Forbidden silently end the task, transient failures are only debug-level, RetryAfter lacks explicit scheduling. Presence starts before ingest, but an older turn can stop it before the new event sets wake; process start does not reacquire presence. Ambient-selected responses never start typing. Use work-generation ownership, not simply one task per room.
- Each normal turn reinjects up to 35 recent messages, reply ancestors and 14,000 characters of notes into an already persistent thread. Notes are bounded, historical message text has no total envelope budget. This duplicates context and increases token/compaction cost; its actual latency share is unmeasured.
- Semantic retrieval calculates grouped exact distances over audience vectors. No ANN index exists. This is a scaling risk, not a measured cause at current archive size; inspect EXPLAIN and counts before adopting HNSW and preserve audience filtering/recall.

Implementation order:
1. Add monotonic arrival-to-delivery stage timings, SQL queue/transaction timings, provider first useful output/tool timings and typing failures. No private text in logs. Capture baseline and p50/p95 with sample counts; distinguish direct, ambient and complex jobs.
2. Make memory truly idle-only: configurable initial quiet window (e.g. five minutes, tuning hypothesis), reset on arrival before ingest; include queued/creating/active work. Gate both indexing and learning; immediate preemption, no new background requests after foreground arrives, drain/release resources without holding foreground capacity through teardown. Keep durable resumable progress and source-version checks. A separate background process alone does not solve shared DB/phone resource contention.
3. Repair typing ownership and start-to-start refresh cadence (~three seconds), bound request waits, log/rate-limit errors and obey RetryAfter. Track addressed pending and active work across old-turn completion, retries and cancellation. Start ambient typing only when actually producing an answer, not for silent observation. Test arrival-during-ingest/old-turn-finish races.
4. Remove unconditional semantic prefetch from ordinary chat. Use compact cached/revision-aware context and on-demand search for older evidence; allow explicit recall intent to trigger a bounded full retrieval pipeline. Cache invalidation must cover edits, forgetting, revisions and restarted/compacted sessions. Cancellation of asyncio.to_thread does not stop the SQL thread: use server-side statement budgets and retain connection ownership correctly.
5. Batch independent embedding requests with batchEmbedContents; do not accidentally aggregate unrelated contents into one vector. Batch database persistence with bounded transactions and revalidate source versions. Keep raw message archive immediately durable/searchable. Add derived bounded conversation windows (time/topic/token limits) with exact source membership for edits/forgetting; do not embed entire unbounded sessions or delete originals. Batch API is an optional asynchronous historical backfill path, separate from online batching. Fair recent/backfill/note queues and capped resource budget.
6. Decouple ingestion from provider steering through owned room tasks; preserve durable acknowledgment/order and turn authority. Combine safe database operations; consider a bounded foreground connection pool after measuring contention, not unlimited connections. Replace empty room polling with deadline-aware recovery scheduling. Preserve lease fencing and uncertain-send semantics.
7. Reduce duplicate prompt injection with revision/delta tracking and bounded total historical context. Keep reliable resumption/compaction recovery. Measure before changing the model or compaction threshold. Add idle lifecycle cleanup for provider processes if memory/CPU measurements warrant it.

Acceptance: stalled embedding, SQL and maintenance cleanup cannot starve normal message dispatch; new human arrivals prevent new background starts; cancellation never advances unprocessed memory cursors; typing survives queued/retried work without old generations stopping new ones; batched embeddings map correctly and edits/forgetting invalidate all derived windows. Test deterministically, then compare representative measured response latency. Do not claim a fixed target from two log samples.

Primary references: https://www.anthropic.com/research/components-of-agent-memory (sleep-time organization between interactions); https://core.telegram.org/bots/api#sendchataction (five-second-or-less typing lifetime); https://ai.google.dev/gemini-api/docs/embeddings and https://ai.google.dev/api/embeddings (batchEmbedContents versus aggregated multimodal contents; asynchronous Batch API for non-latency-sensitive work). Google pages were retrieved directly as Markdown because the web reader rejected their content type.

### Accepted async/workspace implementation plan — 2026-11-07

Owner requests implementation, including async priority, idle memory, agentic Neon retrieval, 50 MB durable workspace/media, installed skills and owner-DM approval for risky actions. Implement in this order: (1) sustained idle/preemption for indexing and reflection; batched embeddings; remove automatic query embedding on chat path; (2) independent room DB handles and nonblocking owned steering/dispatch, typing generations; (3) virtual durable artifact workspace, additional media and 50 MB storage bounds with Telegram inbound 20 MB limitation; (4) progressive installed skill discovery/read through scoped tools; (5) durable exact-action owner-DM approvals with expiry and origin binding. Keep shell unavailable until an actually isolated execution backend is configured: same-UID Termux subprocesses can read service/auth credentials regardless of cwd or clean environment. Approval is authorization, not OS isolation. No claim that skills requiring unavailable external integrations/scripts are fully executable. Validate cancellation, batching, typing races, traversal/size controls and approval authentication. Preserve source-version checks, durable ingest, RLS and uncertain-delivery protection. Document actual completed scope and deployment separately.

### Async/workspace implementation record — 2026-11-07

Implemented: shared monotonic five-minute quiet window (BRODAR_MAINTENANCE_IDLE_SECONDS=300) gates both indexing and learning, resets before ingest, includes creation/dispatch/foreground activity, and cancels indexing and model maintenance on human arrival. Maintenance uses its own single slot so provider cleanup cannot hold foreground capacity; during preemption there may briefly be foreground work plus an interrupting background worker. Cancellation of a pg8000 thread does not terminate an already-issued SQL transaction. Separate background DB handles and bounded batches reduce contention but do not promise zero shared server/phone contention.

Foreground changes: BRODAR_AUTOMATIC_RECALL=false by default removes automatic embedding/search from ordinary turn preparation. Agentic history_search exposes hybrid, lexical and semantic modes, with lexical fallback if embedding fails; source UUID retrieval and paginated history remain. Room dispatch and steering are owned async tasks; polling no longer waits on provider steering or new-room startup after ingest. Each room owns a foreground connection and a separate audit connection. Audits use one bounded queue/consumer per MCP server (100 records, five-second shutdown drain); audit overflow backpressures, crashes or drain timeout may lose audit records. Authoritative message ingest, delivery receipts, mutation fences and lease checks remain synchronous/durable. Empty-room recovery polling and some serial per-turn transactions remain. Transaction setup/RLS scope is now one network request, with quoted literals; verified against checkpoint DB including an apostrophe in scope and read-only status.

Typing uses arrival generations so older work cannot stop newer feedback, refreshes on a three-second start-to-start cadence, bounds network waits and reports permission/transient errors with rate-limit handling. It remains addressed-work feedback; ambient silence does not emit typing. Recent historical injection now has a 12,000-character content budget with explicit truncation markers; fresh input is not truncated. Full revision/delta session context tracking is still future work.

Embeddings now use independent batchEmbedContents requests (32 inputs per HTTP request), and a batched SQL persistence path revalidates locked source content, deletes superseded vectors and inserts valid vectors via jsonb_to_recordset. Stale candidates are skipped. Source selection is still individual messages/notes with overlapping chunks, not derived conversational-window embeddings. Thus request/transaction batching is implemented; source-window membership, invalidation and an ANN index remain deferred rather than pretending whole-session embedding is complete.

Workspace: private S3 artifact/object cap is now 50,000,000 bytes. Telegram cloud incoming download limit remains 20 MB; this is enforced before materialization. Additional inbound video/audio/voice/animation/video-note/sticker files are archived when supported and within that limit; no transcription is implied. workspace_save accepts bounded binary payloads (base64 <=1.4M chars per call), while create_file/presentation and artifact_list/read/send remain available. No arbitrary host file paths or S3 credentials are model arguments. workspace_protect only adds sensitivity; protected files are hidden from ordinary listings and cannot be read/sent without the new approval path. Sensitive artifact reads consume a one-use approval tied to exact content hash, audience and topic. Approval to read explicitly authorizes its contents to be used in that origin chat; it is not a private vault search or arbitrary cross-DM access capability.

Migration 008 adds forced-RLS approvals and is included in grants/backups. request_file_approval notifies both configured owner accounts by DM (requires that they previously started the bot). Only direct, unforwarded, unedited private messages from trusted numeric owner metadata may /approve UUID or /deny UUID. Requests expire after 15 minutes. No model/user-supplied identity can approve. Approval does not automatically resume a finished foreground turn; ask Brodar in the originating chat to continue with the approval ID. No implicit ownership or main-prompt change is authorized. Raw tool strings and outgoing chat text are credential-scrubbed; matching known credential patterns is defense in depth, not proof that arbitrary credentials inside user images/binary files can be detected.

Installed skill packages are discoverable across .agents/skills, .codex/skills and plugin caches, and .claude/skills and plugin caches through skills_list/skill_read, identically for both providers. Resources are paginated and package-root confined, symlink escapes rejected, no host directory listings exposed. Bundled scripts can be read as data; shell execution and missing third-party integrations are NOT implemented by this. Native CLI host-tool discovery stays disabled. This is a concrete outstanding requirement: provision an actually isolated execution worker (no phone mounts/service/auth credentials), artifact transfer and exact-action DM approvals before exposing script/shell execution. proot on this phone is not treated as a security boundary. Do not claim every installed skill can run end-to-end.

Validation: broad intermediate suite 780 passed/20 skipped; subsequent final Brodar result recorded after final changes. Live checkpoint migration 008/grants and combined batch-index/owner-approval regression passed (one live test). It verifies stale source rejection, valid vectors, wrong-audience denial and one-use approval consumption; no real DM/group messages were sent by these tests. Remaining: real DM UX, live skill use with each provider, response latency distributions and multi-day survival. Source/context quality improvements are not benchmarked. Deployment status follows below.

Deployment follow-up for async/workspace: production migration 008 and existing-role grants applied successfully. Verified old supervisor stopped gracefully (worker exit 0). Final Brodar suite: 87 passed, 11 skipped (live checks opt-in), one dependency warning; the new live checkpoint regression separately passed. Intermediate broad suite: 780 passed, 20 skipped. Installed skill catalog scan discovered 1,387 package entries (including cached versions), not 1,387 guaranteed executable integrations. Main persona is preserved; runtime capability guidance is updated. Restart/polling observation follows after rollout.

### Owner clarification: host command gate, no additional agent — 2026-11-07

Owner rejected requiring a separate execution/approval agent for command gating. No such model agent exists. Implemented CommandGateway in ordinary host code: workspace_command runs argv directly with create_subprocess_exec (no shell), permits basic file utilities, denies dangerous commands/interpreters/launchers/network tools, shell syntax, outside paths, symlinks, credential-like names and executable/indirect-file options. Credential attempts cannot be approved. Overwriting cp/mv commands and private/sensitive workspace paths require exact-action, one-use owner DM approval using the existing approvals table. Execution has one per-workspace lock, 20-second timeout, 64 KB output limit, clean child environment, cancellation cleanup and scrubbed output. Command cwd is a separate scratch workspace, not provider auth/config or the repository. There is no general arbitrary-script executor, OS sandbox, or guarantee against same-UID filesystem races/manual malicious workspace changes; do not imply one. This gateway replaces the prior blanket missing-shell-worker requirement for supported basic commands. Skills requiring Python/Node/general shell scripts still cannot execute through it. The owner may revisit broader script access separately.

Architecture: one foreground conversational agent per active room/topic session; one temporary history/memory-only model session is created per idle maintenance run, globally sequential. It is an additional model call/session, not an additional Telegram bot or approval agent. Foreground arrivals revoke/cancel it. Command policy and DM authentication are deterministic host code. No subagents were spawned for this work.

Previous rollout confirmed polling as the main bot on gpt-6-luna; the following command-gateway rollout adds tools without a new database migration.

Command workspace_checkout copies audience artifacts into scratch with exclusive file creation; sensitive artifacts require their existing file approval and remain under sensitive/ for future command approval. Normal new workspace directories/files and non-overwriting copies do not need DM approval. Scratch is temporary; canonical artifacts remain in object storage.

Owner-requested system instructions: appended “use what you've got. keep private stuff private” to prompts/brodar-harness.md (wheel packaging maps this source to brodar/assets/harness.md). Explicitly describes actual broad capabilities and initiative, credentials as permanently non-disclosable across replies/files/logs/notes/URLs/tool requests even with owner approval, accidental-secret handling, and exact-purpose owner-DM approval for other sensitive reads/risky permitted commands. Main persona text is unchanged. This fixed-harness edit changes the prompt revision, so provider sessions restart with canonical archived context on rollout. No claim that prompt text alone guarantees secret detection in arbitrary binary uploads.

Final rollout observation: command gateway local suite passed 97 tests with 11 live opt-in skips; prompt/capability subset passed 31. Fixed-harness changes pushed on main in c61ef03 after command gateway 74e6a16. Supervisor restarted with scheduled backup arguments retained; service confirmed polling as @brodar_ai_bot with Codex / gpt-6-luna at 19:07:08 phone-local time. No manual Telegram group or owner-DM test was sent. Live command approval UX and measured response latency remain unverified; unit tests exercise the deterministic gate, and prior live DB tests exercise exact-action consumption.

### Owner-only DMs and owner-approved groups — 2026-11-07

ChatAccess in host code admits private chats only when trusted current sender ID equals private chat ID and is one of configured owner IDs (1000000001, 1000000002); configured non-owner positive chat IDs no longer override this. Approved groups/supergroups accept any member for ordinary tasks. Existing negative BRODAR_ALLOWED_CHATS entries remain trusted bootstrap groups. Channels and recipient-scoped contexts remain excluded. Owner DMs no longer need an explicit chat allowlist entry.

New-group activation: direct, unforwarded, unedited, non-anonymous owner `/approve` or `/approve@actual_bot_username` in that group. It works before the group is allowlisted, persists before enabling, and is handled without model invocation. Other users/admin roles/pasted IDs cannot activate a group; other-bot commands and quoted/forwarded/edited commands do not authorize. Group `/approve` is distinct from DM `/approve UUID` for sensitive actions. Normal messages before group approval are ignored rather than imported retrospectively. Approval ack is best effort and may repeat on an interrupted cursor write; permission persistence is idempotent.

Migration 009 stores group_access under RLS in access:<instance_id>. This is a control audience, registered with chat_id=0 solely for backup inventory; no Telegram messages, provider sessions or dispatch target use chat 0. Backups include the new scoped table. Startup loads durable groups; owner DMs are included in recovery/maintenance/reminders, and newly approved groups enter the same live audience set immediately. Sensitive-action approval resolution uses that live set, not the stale launch allowlist. No group revoke command or automatic group migration mapping is included. Model remains gpt-6-luna. Validation/deployment record follows.

Access validation: final local Brodar suite 110 passed, 12 live opt-in skips, one dependency warning. Separate checkpoint schema 009/grants and live approval/reload regression passed (1 test); it verifies persisted owner attribution, acceptance of another member after policy restart, and rejection of a non-owner DM. Unit coverage includes both owner IDs in synthetic form, sender/chat mismatch, positive allowlist bypass prevention, forwarded/edited/anonymous commands, other-bot suffixes and failed-persistence admission. No real Telegram messages were sent as test fixtures. Empty owner DMs do not create provider sessions on startup; pending owner DM work is restored and new DMs dispatch lazily.

### Editable prompt assets — 2026-11-07

Active Brodar model-facing prose is now in root prompts/*.md. brodar/prompt_files.py reads source files in a checkout and packaged assets in a wheel; assets are explicitly included by pyproject.toml. Loads are cached for the process lifetime: edit files as the operator, then restart. Templates use ${name} placeholders; preserve required placeholders and literal JSON/control names. Context XML construction, capability tokens, schemas/types, permission enforcement, error/status messages and logging remain code. Those are transport/host behavior, not editable model instructions.

Prompt map:
- brodar.md: base persona; brodar-harness.md: standing harness/security/group behavior.
- runtime.md: shared current capabilities/model/tool inventory; codex-developer.md: Codex developer message.
- memory-maintenance.md: learning/consolidation instructions; memory-turn.md and memory-consolidation.md: run-specific assembly.
- continue-task.md, repair-no-delivery.md, repair-missing-files.md: task continuation/repair messages; context-truncated.md: historical excerpt marker.
- tool-descriptions.md: one ## tool_name section per active MCP tool; tool-authorization.md: common tool nonce instruction. Edit prose freely, preserve section names used by tools. Tool signatures/authorization remain code.
- embedding-query.md and embedding-document.md: exact Gemini retrieval prefixes. Extraction preserves existing prefixes. Changing them later can require reindexing old vectors and evaluating retrieval; a prompt edit does not automatically reembed history.
- claude-runtime.md, claude-tools.md, claude-external-tools.md: appended inherited Claude worker instructions; control-action.md and control-reason.md: model-facing structured-result schema descriptions used by both adapters. Schema validation stays code. The inherited worker now imports the shared asset loader, but its subprocess/protocol behavior is unchanged.

compose hashes the fixed persona/harness, runtime/provider guidance and tool/control descriptions for session revision invalidation; changing those creates a new provider session with durable archive context on restart. Memory/repair prompts load on restart without needing to reuse old embedded Python strings. Existing upstream system.md/subagents.md are legacy Hamroh assets, not Brodar's persona; do not edit them expecting the active bot to change. Existing installed skill text remains in its installed skill packages rather than duplicated into project prompts.

Learned memory is canonical in Neon brodar_v2.memory_documents, with revisions in memory_revisions and source associations in memory_sources; embeddings and archived messages are also Neon-backed. context/*.md names are virtual document keys, not Markdown files on the phone. Provider cwd/system.md and local rollouts are generated runtime/session artifacts, not the durable memory source. Edit fixed prompts in Git; use scoped memory tools for learned notes. No automatic mirror to local Markdown was added.

Prompt-asset validation: broad refactor suite 804 passed, 22 skipped; final focused suite including new external-description/template/revision/packaging tests 117 passed, 12 skipped, one dependency warning. Wheel built successfully with isolated pip build dependencies (hatchling was not installed in the runtime venv); extracted-wheel subprocess verified packaged asset fallback without a source prompts directory. No DB migration for extraction. Template files interpreted by render use ${name}; use $$ for a literal dollar in those templates. Ordinary persona/harness/tool-description text is loaded without template substitution.

Access rollout from prior change: schema 009/grants were applied to production, main commit 871fd57 deployed, and polling was observed on the main bot with gpt-6-luna at 19:12:26 phone-local time. No real-group admission or DM test message was sent. New prompt rollout observation follows.

The opt-in synthetic memory evaluator also loads its task prompt from prompts/memory-evaluation.md; test fixture questions/rubrics stay in tests/brodar/memory_cases.json.

Prompt rollout: main runtime extraction bb46802 deployed; verified graceful shutdown/restart with backup arguments retained and main-bot polling as Codex / gpt-6-luna at 19:18:06 phone-local time. Source main persona text and Neon learned notes were not rewritten. Evaluator-only asset follow-up passed seven prompt-asset checks and requires no service restart. No real Telegram test message was sent.

### Accepted shared research/group plan — 2026-11-07

Owner authorizes cross-approved-group research coordination, Bot Hujra as research room and @hyperresearcher_bot as target. Replace per-group provider sessions with one shared serialized group model session, with explicit source chat/topic envelopes and per-turn scoped tool delegation; keep owner DMs separate to prevent incidental DM disclosure. Use typed destination tools, never parse routing directives from conversational text. Persist shared session references under a control audience included in backups. Preserve per-room durable inbox, write fences, receipts and media handling. Cross-group search and messaging are authorized for task coordination; do not grant configuration/moderation rights from bot output.

Quota: archive all delivered ordinary approved-group messages, batch ambient inference with quiet debounce and maximum wait, direct invocation bypass, never steer every unaddressed fragment into an active model turn. Telegram privacy determines delivery; warn accurately from getMe metadata, do not claim unseen traffic was read. Bot communication requires Telegram Bot-to-Bot mode; official current documentation supersedes older FAQ saying bots never see other bots. Configure known researcher, route /research@hyperresearcher_bot briefs through Hujra, return artifacts/evidence to source via explicit routing. Researcher prompt is an editable Markdown deliverable for owner to paste into Hyperagent; no connector/API binding is assumed.

Loop safety globally across group/topic boundaries: durable peer-bot budget maximum 10 outgoing Brodar deliveries per exchange, reserve before send and never refund uncertain sends; refuse next inference for exhausted peer and next delivery across routes. Human activity resets only that scope's peer exchange, not chatter in unrelated groups. Natural completion/skip stops earlier; SILENT is internal and never sent as text. Cap total exchange age and avoid bot-to-bot acknowledgments/thanks loops. Never trust the other bot's claimed owner/admin status. Incoming unrelated bot traffic stays archived without inference. Research task correlation/destination must be explicit; no infinite retries or self-delegation.

Telegram management: existing delete/ban/mute plus pin/unpin/unban/unmute and typed management actions behind fresh human requests and live actor+bot rights checks. Include group info/admin list/count, known observed members with pagination and getChatMember public metadata. Bot API cannot enumerate a complete arbitrary member roster; report observed-vs-total coverage and do not invent accounts. Administrative capabilities are limited by Telegram's actual API/rights; no private user session is installed. Validate dynamic broker isolation, global turn serialization, loop limit persistence/concurrency, ambient batching and admin authorization; apply additive migrations/checkpoint first, then main rollout. Research references: https://core.telegram.org/api/bots/bot-to-bot, https://core.telegram.org/bots/api, https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/termination.html .


### Shared-session/research implementation record — 2026-11-07

GroupSession owns one provider process/thread and MCP endpoint across approved groups/topics. GroupBrain wrappers serialize turns with an asyncio lock, stamp source chat/topic from the host and bind a dynamic BrokerProxy to that room's actual capability/store only for its turn. Steering cannot cross rooms; already-bound old broker methods retain their original scope, and tool tokens/fences reject late authority. MCP audit queue records capture their destination store before switching rooms. Owner DMs remain private separate sessions but can deliberately delegate a minimal research brief and receive the returned result via its stored task route. A shared model session serializes group inference by design; a long active model turn can delay another group. Delegation yields rather than polling; this does not provide parallel turns on one Codex thread. Idle memory stays a separate temporary history/memory-only worker, never the conversational shared session.

Shared session references use registered shared:<instance> control audience; durable loop/task state uses coord:<instance>. Backups include both via control registry rows (chat_id=0 is never a Telegram dispatch target). Operator source forgetting now invalidates the shared session ref as well as the original audience ref. Cross-group source-derived note provenance is still conservative: source IDs cannot be cited across audience boundaries, so shared-session inference is not a complete cross-group dependency graph or universal erasure guarantee.

Routing: group_routes lists actual approved destinations. group_send and group_history_search use explicit group IDs/topics; no hidden output routing text is parsed. Delegation targets Bot Hujra -1000000000102 and @hyperresearcher_bot (owner-supplied username), configurable via BRODAR_RESEARCH_CHAT_ID and BRODAR_RESEARCH_BOT_USERNAME. delegate_research persists a correlated task/return route and sends /research@hyperresearcher_bot; research_return reads the actual saved route and sends a chunked final report to its origin group/topic or verified owner DM. Routed sends retain target delivery receipts plus an origin acknowledgment so the source work can finish without repair loops. Briefs are <=3000 characters, final returned reports <=12000; longer evidence should be an actual artifact. Research tasks have a 20-minute deadline. No third-party Hyperagent API credential/connector is assumed; owner must save the supplied prompt/configure the researcher connection on that platform.

Loop controls: migration 010 adds bot_exchanges, bot_reservations and research_tasks with forced RLS. Global per-peer state survives group changes and restarts, with an atomic ten-reservation limit, 20-minute age bound, exact-body dedup and three-second inbound admission rate. Reservations are conservative and are not refunded on failure/uncertainty. Existing operation IDs are idempotent. Incoming peer turns are checked again before inference. All visible actions of a bot-originated turn share that peer budget (thus some human-facing return messages count conservatively too). Research username has a stable canonical peer key before/after numeric ID discovery. A fresh addressed human message resets exchanges originating in that room; a new human-created research task can reset that research-room exchange once, never on an idempotent retry or bot request. Bot-generated recursive delegation is denied. Natural skip closes the exchange; research_return closes it after delivery. Current code archives unrelated bot messages but invokes inference only for delivered bot messages directly addressing/replying to Brodar and admitted by the guard. Silent markers don't invoke inference and the send tool refuses to publish [SILENT]. No claim that another provider's own host enforces its prompt-only limits.

Message visibility/quota: getMe confirmed can_read_all_group_messages=True on the main bot. It can archive ordinary delivered group traffic; Telegram still controls which bot-to-bot updates arrive. Bot-to-Bot Communication Mode must be enabled in BotFather for the participating bots (at least one for addressed group bot exchanges; both recommended). It cannot be enabled by this prompt or ordinary Bot API settings here. Ambient default interval is now 60 seconds, quiet debounce 8 seconds and maximum quiet wait 60 seconds (BRODAR_AMBIENT_INTERVAL, BRODAR_AMBIENT_QUIET_SECONDS, BRODAR_AMBIENT_MAX_WAIT). Direct human invocations bypass ambient waiting. Unaddressed fragments never individually steer active turns. Batching still costs model inference periodically while groups are active; no claim of free continuous model observation. Canonical messages now record trusted sender_is_bot/sender_username metadata for context.

Group management: telegram_group_info provides group metadata/count, admin roster (including bots where API supports it), known-member public Telegram metadata and paginated observed speakers. complete_roster=false is explicit; Bot API has no full-member enumeration endpoint. Existing exact-command delete/ban/mute remain. telegram_admin_action supports pin/unpin/unpin_all, unban/unmute, title/description/photo changes, permissions/admin promotion/title, join approvals, invite creation/revocation and forum topic creation/close/reopen/delete. These are typed allowlisted methods, not every Telegram admin API. Fresh human /action parameters must match exactly; requester and bot rights are checked live in the current group. Advanced actions use matching JSON command parameters. No bot can authorize moderation from its report; no additional user-account/MTProto session was installed.

External prompt: prompts/hyperresearcher-system.md is the ready-to-paste Hyperagent research persona. It includes evidence-rich agent handoffs, social/video source inspection honesty, provenance/citations/timestamps, capability/access limits, credentials/privacy, task correlation, natural termination and a prompt-level ten-reply limit. It deliberately does not assume Hyperagent supports a [SILENT] control token. Active Brodar routing/session guidance and delegated brief templates are editable Markdown and included in the wheel. No Hyperagent account was changed through tools.

Follow-up hardening: coordination, routing and approval DB connections are separate from polling so research/loop SQL cannot queue behind the ingestion connection lock. bot_aliases (migration 011) pins the numeric bot ID to its canonical peer budget once observed, preventing a username rename from resetting the exchange. A small in-process room set avoids a remote loop-reset transaction on ordinary human chat when no bot exchange exists; it is rebuilt at startup and remains an optimization over durable state. group_send_document copies non-sensitive artifacts into the selected group's storage, with source/target receipts and cancellation checks; a sensitive read grant is not treated as cross-group disclosure permission. Routed-file receipts satisfy the originating artifact delivery requirement. Shared-session source forgetting also clears its global session reference. These control/state tables are included in grants and backups.

Validation so far: local Brodar suite 125 passed, 13 opt-in skips; broad intermediate run 819 passed, 23 skipped; one dependency warning. Live checkpoint schema 010 tests passed for batched embedding/source validation and exact owner file approvals, plus a ten-send peer limit across restart/group changes (2 tests). After the alias/cache hardening, the dedicated live limit test is rerun separately. A real isolated gpt-6-luna probe using the shared GroupSession retained a synthetic marker across two group turns in the same provider thread and delivered through a fake Telegram bot only to the second group's scoped store. Synthetic DB records were removed afterward; local provider test rollouts can remain. No real group/DM delivery, moderation or Hyperagent inference was used for verification. Live Claude parity, actual research round-trip, permission-backed admin calls and measured production latency remain unverified. Wheel build passed before the final alias/file-routing changes; packaging includes all active routing prompt assets.

Final validation/rollout preparation: 126 local Brodar tests passed, 13 opt-in live skips; the latest shared-queue check independently proves an exhausted peer is skipped again after waiting for the global lock, before a provider inference call. Broad intermediate suite 819 passed, 23 skipped. Live checkpoint alias-aware global-budget regression passed after schema 011, including rename identity persistence, idempotent reservations, eleventh-send denial, cross-group/restart persistence and human reset scoping. The earlier two live SQL checks also passed. Shared model session IDs are persisted only when changed, removing a redundant remote write after each turn; shared queue wait is logged explicitly. Bot username mentions/command tags in routed/current-group text also reserve recipient budgets, including initial human-originated delegation. Numeric aliases pin all observed bot identities against renaming. Quotas remain conservative when both sender and recipient peers need reservations.

Production migrations 010/011 and existing runtime-role grants applied successfully before restart. Read-only Telegram checks verified the main bot is an administrator in both Original gangsters and Bot Hujra. Both grant pin rights; Hujra currently lacks delete/restrict rights, while Original gangsters grants both. No moderation was executed. getMe group privacy flag is disabled. Bot-to-Bot mode still requires BotFather/platform configuration and is not verified/enabled by the runtime. Researcher system prompt is ready in prompts/hyperresearcher-system.md; owner must save it in Hyperagent. No end-to-end researcher response is claimed. Deployment/polling observation follows.


Shared research rollout: main e37b634 pushed and deployed after production schema 010/011/grants. The first worker encountered a transient Telegram initialize/getMe timeout; the existing supervisor restarted it automatically. Service then confirmed polling as @brodar_ai_bot with Codex / gpt-6-luna at 20:11:36 phone-local time. Scheduled backup arguments remain retained. Runtime configuration resolves ambient interval 60 seconds, quiet debounce 8 seconds, Hujra -1000000000102 and researcher hyperresearcher_bot. No real group research message or moderation test was sent. The external researcher connection, prompt save and BotFather bot-to-bot setting are still owner-side setup, not claimed completed. Backup encrypted-artifact read bound was corrected to 70 MB so a 50 MB stored file plus Fernet/base64 overhead can be restored; snapshot bound is separate. This backup-only follow-up needs no bot restart.

Read-only post-deployment check confirmed exactly one Codex session row under shared:brodar and exactly one app-server process in the shared group's provider cwd. This establishes the deployed shared-session topology; it does not prove external researcher integration or real moderation execution.

### Typing completion fix — 2026-11-07

Owner observed typing after delivered replies. Root causes: feedback waited for work-state/session/inbox bookkeeping; any ambient wake prevented final cleanup; steered addressed updates incremented presence generation without transferring that generation to the active turn. This could leave feedback active until a later empty recovery pass.

TypingPresence now maps the last 256 addressed update IDs per room to arrival generations. Claimed and successfully steered inputs own only their matching generations; accepting steering adopts the new generation before remote attempt/work bookkeeping. Empty claims own generation 0 and cannot stop a newer request still being ingested. Ambient wakes no longer suppress cleanup; generation equality preserves genuinely newer addressed work. Retried addressed work restarts feedback when claimed.

telegram_send_message and telegram_send_document accept final=true by default. The source turn's final-delivery callback cancels typing renewal before sending to Telegram (and before slow delivery_finish SQL); progress calls use final=false. Terminal stop/skip also stops feedback immediately before further bookkeeping. Receipt durability/uncertain-send handling is unchanged, and stopping typing does not falsely mark the job complete. Telegram has no explicit 'clear typing' API; final content clears it client-side and any already accepted in-flight action is bounded by Telegram's short lifetime.

Validation: local Brodar suite 131 passed, 13 live opt-in skipped, one dependency warning. Five regressions cover final feedback cancellation before send and a blocked receipt save, interim progress preserving typing, ambient wake cleanup, preserving a newer unclaimed request and adopting a steered generation before bookkeeping. No migration or real Telegram test message required. Deployment record follows.
