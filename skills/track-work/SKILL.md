---
name: track-work
description: Perform one bounded TODO Flow work request from durable state and return a proposal, evidence, or a concrete decision wait.
---

# track-work

Read installed `project.json` for STATE and primary language, then STATE's `config/1.json`. Use the project language for user-facing reports and new documents unless explicitly overridden; default to English. Preserve protocol keys, identifiers, commands and source quotations.

Read task purpose, goal revision, current files, exact change, verification, decisions and recent results. Choose the useful work now; no global phase order. The built-in runtime invokes this role as a fresh tools-disabled worker and applies bounded file proposals itself.

Return the worker JSON contract: summary, optional changes[{path,content}], verify, publish, next[{kind,purpose}], question, watches, findings[{observation,evidence}]. Changes are complete UTF-8 content within configured writable patterns. Never change tests just to conceal a failure. Next kinds: assess, work, verify, review, land, triage, complete, watch. A question has no mutations or next requests. Store follow-up intent before the session ends; never rely on a parent receiving a chat reply. Current scope failures are work, not watch.

Persist concrete findings for post-landing triage. Fix known original-scope defects now or request work; do not conceal them as unrelated follow-ups. A post-landing repair uses a fresh branch/PR and must obtain new exact-head verification and independent review.
