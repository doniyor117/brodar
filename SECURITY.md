# Security

Do not include bot tokens, database URLs/passwords, OAuth callbacks, private chat data or provider sessions in issues or pull requests. Report a vulnerability privately through the repository's security reporting channel; if unavailable, contact a maintainer privately without posting the exploit or credentials publicly.

Runtime ownership comes from platform metadata and host capabilities, not names, prompts, learned notes or group roles. Owner host execution runs under the process OS identity; deny rules are not an OS sandbox. Contributors must preserve audience separation, fresh-request validation, cancellation fences, delivery receipts and restricted SQL roles.

Before publication, scan the intended Git refs and package contents with the pinned secret scanner. Rotate confirmed exposed credentials before rewriting history. Keep private preserved history and recovery keys separate. CI uses no production accounts or deployment secrets, especially on untrusted pull requests.
