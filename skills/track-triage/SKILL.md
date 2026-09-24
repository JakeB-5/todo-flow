---
name: track-triage
description: Assess confirmed landed code, review residue, findings and relevant Watch records; atomically connect them to repair, existing or new TODOs, conditional observation, or evidence-backed closure before completion.
---

# track-triage — disposition after confirmed landing

Read installed `project.json` and STATE's `config/1.json` for the primary language. Use it for reports and new track documents unless explicitly overridden; default to English. Preserve protocol keys, IDs and source quotations.

This is a temporary, read-only role scheduled after confirmed landing during trackrun. Inspect the landed SHA, original goal and conditions, extra review observations, GitHub review discussions, findings and relevant watches. Verify whether an old observation is already fixed. Do not create a resident supervisor or re-audit every track.

Assess each source ID exactly once with current evidence, scope and a disposition reason. Return an explicit empty `triage` list when no findings remain. Additional observations may use stable `new:<key>` IDs. Do not invent work to fill a quota.

- **repair**: an unresolved original-scope defect. The host creates fresh work and a repair branch on the same track. New verification, independent review, landing and triage precede completion.
- **existing**: separate scope already owned by an unfinished track. Provide its target ID and evidence of matching scope. Active targets receive assessment work; unselected targets only receive a link.
- **new-track**: a distinct requirement not absorbed elsewhere. Follow todo's investigation, overlap search and document contract; propose a registration JSON string with goal, scope, evidence, conditions and useful design. The host registers an HTML review document and provenance. It awaits human review and selection, not automatic execution.
- **watch**: an uncertain conditional observation, with `confirmed:false`, a concrete deferral reason, trigger and next action. Confirmed original obligations do not become watches.
- **resolved / dismissed**: closure supported by evidence that the issue is fixed or does not apply.

Duplicate search uses filesystem `rg` matches and candidates' goals and scopes. If results are truncated or a new finding needs different search terms, return only `triage_search:[precise terms]`. The host searches again and resumes the role. Do not combine refinement with dispositions; explain overlap judgments rather than delegating them to string equality.

If scope or product authority is unclear, return only a concrete question. Do not mix a decision wait with changes, findings, watches or dispositions. Do not edit files, publish, select next work, push or create remote items yourself. The host rechecks the landing boundary and atomically records dispositions, follow-ups and the worker result.

Results live in `findings/`, `triages/`, linked TODOs, watches and events. Completion requires a cleared receipt matching current evidence. A session ending does not erase dispositions. Repetition alone does not dismiss an unresolved issue, and new TODOs do not recursively execute.
