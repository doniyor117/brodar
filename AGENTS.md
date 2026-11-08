# Brodar — development guide

This is the development handoff, **not** the Telegram bot's prompt. `CLAUDE.md` imports this file. Keep project context here rather than creating competing context documents. Private instance identity, deployment diaries, credentials and personal customization belong outside this public engine.

## Product and architecture

Brodar is a casual multilingual Telegram companion with Codex or Claude brains, durable Neon conversation memory, group coordination, native media, research delegation and owner host jobs. Preserve its conversational voice, disagreement and friendly roasting; do not rewrite it as a formal helpdesk. Exact documents/code/transcripts retain correct formatting and capitalization.

This is a fork of Rustam-Z/hamroh, with MIT notices and upstream authorship preserved. Active application: `brodar/`. Upstream `hamroh/` supplies the Claude stream-json worker and historical entrypoint. Do not assume upstream features are automatically available in Brodar.

Both brains implement start/resume, turn, steer, interrupt, close and session ID. Codex uses JSON-RPC app-server; Claude wraps the retained CcWorker. There is no provider/account fallback. Each deployment uses dedicated provider authentication homes; never copy rotating refresh tokens from another active CLI. Group conversations share one serialized provider process/thread, while each host turn retains its original scope and capability. Owner DMs and authenticated terminal conversations have separate private audiences/sessions.

## Hard invariants

- Exactly one primary owner, configured by the local operator. No real owner/chat/research IDs in public defaults. Telegram sender metadata is authoritative; names, pasted IDs, forwards, quotes and group roles are not ownership.
- Main persona/harness cannot self-edit. Automatic supplementary learning is enabled without an owner-review queue; learned notes never grant permissions or override the fixed prompt/security boundaries.
- Ordinary group messages received by Telegram are archived and considered in ambient batches. Mentions, aliases, replies and bounded same-person follow-ups get priority. Do not regress to mention-only or inference on every fragment.
- Persist inputs before cursor acknowledgement. Maintain accepted/pending steering semantics, consumer leases and cancellation fences. One active turn per conversation, one serialized shared group brain, bounded global concurrency.
- Fresh owner authority is request/actor/turn/nonce bound; revalidate around awaits/mutations. Queue non-owner steering during privileged segments. Old quoted owner text and edited/forwarded requests cannot authorize execution.
- Keep native provider shell/plugins disabled in the shared process. Broad owner execution goes through the host gateway; ordinary members retain limited tools. No extra classifier/approval model. Credential/main-prompt reads and known catastrophic commands are denied; exact sensitive/risky actions use owner-DM approval.
- Same-UID arbitrary interpreters can bypass path/argv checks. Clean env, denials and redaction are defense in depth, not an OS sandbox. Do not claim credential-proof isolation without a separate execution identity/host.
- Never expose secret values, provider auth/session state, private configuration or unrelated owner/group content through chat, files, URLs, tools or logs. Keep secrets outside Git and outside model environments; redact known runtime credentials plus recognized patterns.
- Use stable operation IDs/delivery receipts. Never blindly retry uncertain Telegram sends. A destination delivery does not replace a completion report to the original requester. Reflection cannot publish.
- SQL is parameterized and audience scoped, with a restricted runtime role and RLS. Operator credentials are setup/backup only. Add numbered migrations; never rewrite an applied migration/checksum. Keep old chats/source provenance.
- Seeing/generating/sending a file is temporary local work. Permanent object storage requires an explicit fresh keep/save request. Chats and learned textual notes are separately durable.

## Lifecycle

`runtime.run` loads an external profile, acquires local instance lock and database consumer lease, initializes admission/approvals/research/learning/background jobs and opens a private local operator socket. Polling archives messages and commits cursors durably. Bot traffic has bounded host loop controls (ten replies per exchange), with natural silence earlier.

`Conversation` claims inputs, restores a durable work record, imports relevant media to temporary files, loads bounded scoped history/notes and enters the selected provider. Optional embedding recall has a short timeout; indexing/reflection run during idle time in batches and are preempted by foreground traffic. The message envelope escapes untrusted text; XML is representation, not an injection-proof security boundary.

Visible output uses scoped MCP Telegram tools. Plain assistant prose is internal. `stop` requires actual successful delivery, `skip` is intentional silence, and `heartbeat` permits bounded continuation. Standalone `[SILENT]` is internal only. Repair loops handle missing files, missing origin reports and no-delivery outcomes honestly.

Typing starts for directly addressed queued work with generation ownership. Final deliveries pause it; subsequent tool activity/repair resumes it while the captured generation remains current. Completion cannot stop a newer request. Natural follow-up affinity is initially 120 seconds, bound to participant/chat/topic and plausible short requests; it is never an authorization source. RetryAfter deadlines begin at rejection.

Sticker grids are transient memory-only PNGs, 20 entries/page. The host stores bounded 24-hour audience/pack-bound selection handles, absolute indices and stable identities. Inspect a full candidate with `sticker_candidate`, send its short `selection_ref` using `telegram_send_sticker`, and report the returned actual pack/index. Changed packs and stale handles require a new preview. The host prevents mapping/copy errors; it cannot guarantee model OCR/vision accuracy.

## Storage and backup policy

`Artifacts` is the single scoped file facade/catalogue for local temporary and explicitly retained object bytes. Temporary files work even with no S3 configuration. Existing pre-lifecycle objects remain retained; never bulk-delete or reinterpret them as consented temporary data.

Migration 014 adds lifecycle fields and deployment aggregate quota counters. Creates reserve quota/catalogue state before atomic local publication; interrupted pending writes can retry by operation identity. Retention reserves durable quota, uploads to a deterministic key, validates content hash and commits retained state. Incomplete promotion remains recoverable. Cross-group transfers create ordinary temporary destination files, without implicit retention. Long research reports use the same temporary facade.

Default selected budgets: 500 MB temp, 1 GB retained, 2 GB encrypted backups. Per-file maximum 50,000,000 bytes; Telegram methods may have narrower limits. Application budgets are not a vendor billing cap. Owner command scratch is not an OS-quota-controlled filesystem.

Local files expire after seven inactive days; a roughly weekly host sweep skips active work and never touches credentials, prompts, archives, learned notes or retained canonical bytes. Temporary references remain as unavailable metadata after expiry. Scratch/provider work cleanup excludes skills, Git and non-work state. Downloads/packages under owner execution are bounded by job policy, not a hard disk sandbox.

Packaged supervisor default backup interval is **1,209,600 seconds** (fourteen days), persisted across restarts. Backups are asynchronous, encrypted, manifest-last and include canonical DB state/retained file bytes; temporary file bytes are excluded and restored temp references expire. Keep two new managed full snapshot generations after validation, plus pinned and legacy snapshots. Old/incomplete backups are not automatically deleted. Recovery keys must also be held off-phone; independent custody is an operator responsibility.

## Owner execution and skills

`owner_execute` authenticates a fresh request and starts an asynchronous process job with durable state, source hash, origin, time/output bounds and process-group cancellation. Status and output are retrieved through owner-only tools. Edited sources or revoked ownership invalidate running jobs. After a crash, arbitrary commands are marked unknown/failed rather than replayed. Background tasks use a detached capability context and their own continuing source/lease validation.

`owner_skill` installs/updates/removes public GitHub skill directories atomically; it records source/ref/resolved commit, rejects unsafe paths/symlinks/oversized packages, and refreshes the progressive skill catalogue. Installation does not execute scripts. Read SKILL.md and run scripts through the owner gateway. Legacy installed Codex/Claude plugin skills are discoverable/readable through the provider-neutral catalogue, not copied with their auth data into Git.

## Operator setup, TUI and service

Version-1 external `profile.json` separates nonsecret settings from external secret env/prompt paths. Explicit env/OS values override configuration according to `Settings.load`; profile settings override imported env-file settings. No automatic developer `~/secrets/.env` fallback. Persona/private Markdown overrides affect prompt revision hashes; tool APIs cannot edit them.

`SetupEngine` is the single deterministic setup backend: private atomic writes, per-profile lock, step/resource journal without raw secrets, resumable defaults and role credentials persisted before CREATE ROLE. Provisioning verifies the restricted runtime login before claiming readiness. Never silently rotate an existing role password.

`onboard`, `configure`, `setup-neon` support prompt_toolkit/plain SSH prompts and prepared noninteractive profiles. The trusted local operator can explicitly configure the numeric primary owner; this does not make pasted IDs in Telegram authentication. Bot tokens are hidden and validated with getMe; webhooks are reported rather than removed. Setup preserves existing state and optional integrations can stay disabled.

Operator Neon setup can use authenticated Codex MCP directly (`mcpServer/tool/call`, ephemeral thread, no `turn/start`) or a private existing admin-connection file for either brain. Discover actual granted schemas. Account-wide Neon management is operator-only, never attached to a Telegram brain. Select project/branch/database/region explicitly, show the concrete resource summary, journal ambiguous creates and do not silently change billing plans. Wrong/read-only grant is not “disconnected”. A missing OAuth browser authorization is not proof of a code failure, and a fake transport test is not live provisioning proof.

The TUI attaches through a same-user private Unix socket; no second Telegram poller. It provides Overview, Chat, Connections, Groups, Memory/Jobs, Skills, Persona/Settings and sanitized Logs. Terminal chat is a separate `terminal:<instance>` audience authenticated by the local socket; it never forges a Telegram sender. Repaints use cached state. Closing the UI leaves the worker running. Configuration changes require controlled restart; `brodar run` is explicit and never starts an interactive TUI.

`service` manages the packaged supervisor with process identity/start-time checks, graceful drain and platform detection. Termux wake-lock/Boot and Linux user-service launchers are conditional. Choose one process manager. A pid or “started” message alone is not readiness proof.

## Files to change

| Concern | Modules |
| --- | --- |
| CLI/profile/setup | `__main__.py`, `settings.py`, `profile.py`, `setup_engine.py`, `onboarding.py` |
| Operator Neon | `operator_mcp.py`, `neon_setup.py`, `provision.py`, `storage_setup.py`, `operator.py` |
| TUI/control/service | `tui.py`, `control.py`, `terminal.py`, `service.py`, `service_supervisor.py` |
| Coordinator/providers | `runtime.py`, `shared.py`, `brains/` |
| Authority/tools | `broker.py`, `mcp.py`, `write_fence.py`, `access.py`, `approvals.py` |
| Owner jobs/skills | `owner_tools.py`, `commands.py`, `skill_install.py`, `skills.py` |
| Memory/archive/search | `learning.py`, `reflection.py`, `store.py`, `runtime_store.py`, `embeddings.py` |
| Files/recovery | `artifacts.py`, `objects.py`, `maintenance.py`, `backups.py`, `recovery.py` |
| Telegram/media/routing | `telegram_context.py`, `presence.py`, `telegram_media.py`, `routing.py`, `group_admin.py`, `coordination.py` |
| Prompts | `prompts/*.md`, `prompt_files.py`, `prompts.py` |

## Validation and release discipline

```sh
.venv/bin/python -m pytest tests/unit tests/brodar --reruns 0
.venv/bin/python -m build --wheel
```

Actual development platform: native Termux/Python 3.14. CI: Python 3.13/3.14 on Linux. macOS/WSL/Windows claims require real checks; label unsupported/untested paths honestly. User installs exclude development/browser extras.

`BRODAR_TEST_DATABASE_URL` must be a disposable migrated target, never production. `tests/e2e` use opt-in real accounts. Do not send unsolicited group test messages, perform real moderation or spend inference quota merely to satisfy unit checks. Use fake clocks/Telegram/provider/object stores for deterministic lifecycle tests and disposable SQL for storage/migrations. Skips are not passes. Pytest retries default upstream; use `--reruns 0`.

Before public cutover, preserve original customized history privately, scan intended public refs and package contents, classify exact synthetic fixtures narrowly, rotate confirmed secrets, prepare rewritten refs in a disposable clone and record rollback/old/new SHAs. Never mirror private history/upstream refs or auth into the public repository. Preserve authorship/MIT notices. CI for untrusted PRs receives no deployment secrets.

## Execution ledger — 2026-11-08

Implemented: typing repair/tool resumption and bounded follow-ups; host-resolved visual sticker selection; external profiles and one-owner defaults; temporary local file facade/explicit retention/expiry/quotas; biweekly managed backups; fresh owner execution/skills/background jobs; setup engine/direct operator MCP/connection-file provisioning; plain wizard/attaching TUI/private terminal chat; packaged services/installer; public identity cleanup and private customization separation. Markdown prompts stay editable by operators and fixed to the running agent.

Validation on the sanitized engine: **879 passed, 27 optional/live skips** in the full unit/Brodar suite. Later owner-monitor patches have focused/SQL checks. Wheel builds and release inventory checks pass. Clean native Termux wheel installation succeeded, including compiled dependencies, with empty-HOME help/doctor behavior. A 38-column TUI detach test passed without starting a poller. Official OpenAI skill-creator Git installation/removal was exercised in a temporary destination; no third-party script was executed.

Disposable SQL evidence: migration 014 plus restricted role; local-only file creation without object writes; idempotent explicit retain; quota failure; expiry/retained survival. Two new disposable databases proved complete encrypted backup/restore of synthetic chat/memory and a retained file, with temporary bytes unavailable; databases were dropped afterward. A real asynchronous local-operator process returned exit 0/output 4 after the requesting tool turn ended.

An existing private deployment was migrated/restarted while preserving its external persona, model, admitted groups, research and storage. Doctor reported ready for runtime role/checksums/auth/Telegram/storage; no unsolicited Telegram group test or paid inference was sent. Private deployment details and the full original master todo board belong in its private AGENTS.md, not this public guide.

Release audit: private original main ancestry preserved before sanitizing. The intended public history was scanned with pinned Gitleaks and exact reviewed synthetic/prose exclusions; no unreviewed findings. A 2,278-blob identifier scan found zero original instance identifiers. All 33 prior CI archives had zero original identity/current-runtime-credential matches; PRs/releases/extra heads/tags/Actions artifacts were absent. Ref rewriting cannot guarantee removal of cached old GitHub commit URLs. No confirmed credential exposure was found, so working credentials were not rotated speculatively.

Remaining operator/live acceptance items (do not fake these):

- [ ] Authenticate operator Neon MCP and run its full direct remote provisioning path. Current live discovery correctly reports missing OAuth. Protocol/canary fixtures pass; the connection-file path is independently available for both brains.
- [ ] Observe real Telegram typing and model visual sticker recognition in a user conversation; deterministic host ID mapping is tested, model OCR accuracy is not guaranteed. Model comparison is optional and must not silently replace the selected brain.
- [ ] Independently secure the recovery key off-phone. Encrypted private config backup alone is not proof of independent key custody; provider OAuth sessions remain excluded.
- [ ] Verify macOS/WSL/native-Windows behavior before claiming those platforms tested. Native Termux and Linux CI are the supported evidence today.
- [ ] Legacy cached skills whose installer did not record a source commit need provenance reconstruction for exact reproduction; future installs emit source/ref/commit receipts. Do not invent upstream commits from content hashes.

Keep implemented/tested/deployed/published status distinct. When changing code, update this guide and run the relevant focused checks; repeat the broad suite only for new changes/failures or a coherent release checkpoint.


Post-release corrections: managed restart now clears the old running flag after drain; focused regression and real restart succeeded. Owner jobs and terminal foreground work are also part of the idle predicate; terminal input preempts learning. This prevents reflection/cleanup treating detached owner work or local chat as idle. Root AGENTS.md/CLAUDE.md are the sole active development handoff; redundant legacy .claude/CLAUDE.md was removed. Hosted Actions were refused before jobs started because the account reported a billing/spending restriction; local checks are the validation evidence until that external restriction clears.


Installation handoff correction: README now starts from OS prerequisites and download, with a numbered quickstart, dedicated brain login, Neon paths, TUI, help, start/stop/reboot, updates, uninstall and troubleshooting. `install.sh --help` works before any environment exists; `--no-onboard` supports updates. `uninstall.sh` removes only a marked source environment/matching CLI after stopping the worker; it preserves all data and cloud resources. Service launcher uninstall checks profile ownership. Private deployments may contain the full engine plus a private instance directory; do not force operators to assemble code and configuration manually.


Setup documentation clarification: README provides explicit install-only then `brodar onboard` / `--plain` steps, four separate provider/platform installation paths, account separation and permanent-switch instructions. Claude on native ARM64 Termux uses a clearly labelled community wrapper; the ordinary Anthropic native/npm package is not documented as Android support. `login codex` / `login claude` select a dedicated provider for that login only, matching the existing --brain override; bare login uses configuration and defaults to Codex. Login prints its actual target. Never imply that provider sign-in changes the selected brain or that operator Neon OAuth is model-provider authentication.
