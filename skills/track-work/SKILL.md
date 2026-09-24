---
name: track-work
description: Perform one bounded TODO Flow work request from durable state and return a proposal, evidence, or a concrete decision wait.
---

# track-work

Read installed `project.json` for STATE and primary language, then STATE's `config/1.json`. Use the project language for user-facing reports and new documents unless explicitly overridden; default to English. Preserve protocol keys, identifiers, commands and source quotations.

Read task purpose and the supplied `workspace` and `paths`. Open the goal/condition document and relevant evidence, decisions and recent results by path. Search the actual workspace with file reads and rg/Glob/Grep; `context_patterns` are starting hints, not preloaded files or read-access controls. Choose the useful work now; no global phase order. Built-in workers have read-only exploration tools and return proposals; the runtime applies changes and runs verification. Never assume that a path in the handoff means its contents have already been read.

Return the worker JSON contract: summary, optional changes[{path,content}], verify, publish, next[{kind,purpose}], question, watches, findings[{observation,evidence}]. Changes are complete UTF-8 content within configured writable patterns. Never change tests just to conceal a failure. Next kinds: assess, work, verify, review, land, triage, complete, watch. A question has no mutations or next requests. Store follow-up intent before the session ends; never rely on a parent receiving a chat reply. Current scope failures are work, not watch.

Persist concrete findings for post-landing triage. Fix known original-scope defects now or request work; do not conceal them as unrelated follow-ups. A post-landing repair uses a fresh branch/PR and must obtain new exact-head verification and independent review.
