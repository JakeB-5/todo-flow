---
name: track-run
description: Compatibility alias for the trackrun skill when an existing session uses track-run.
---

Read [trackrun](../trackrun/SKILL.md) and use `trackrun <track-id...>`. The canonical user-facing flow is todo registration → dashboard or track-picks selection → trackrun execution.

Read installed `project.json` for STATE and primary language, then STATE's `config/1.json`. Use the project language for user-facing reports and new documents unless explicitly overridden; default to English. Preserve protocol keys, identifiers, commands and source quotations.
