"""Request-scoped worker routing; selection never proves provider execution."""

from copy import deepcopy

ROLES = ("assess", "work", "review", "triage", "watch")
MODES = ("auto", "codex-only", "claude-only")
PROVIDERS = ("codex", "claude")

# Local support snapshot, 2026-10-02: Codex models_cache.json (client 0.159.2)
# and Claude Code 2.1.287 embedded runtime.effort_levels. See the bundled
# skills/todo/worker-routing.md for provenance and account-availability limits.
# Unknown combinations stop instead of dropping effort.
CODEX_EFFORTS = {
    "gpt-6.1-sol": ("low", "medium", "high", "xhigh", "max"),
    "gpt-6-astra": ("low", "medium", "high", "xhigh", "max"),
    "gpt-6-sol": ("low", "medium", "high", "xhigh", "max"),
    "gpt-6-luna": ("low", "medium", "high", "xhigh", "max"),
    "gpt-reserve": ("low", "medium", "high", "xhigh", "max"),
    "gpt-5.6-sol": ("low", "medium", "high", "xhigh", "max"),
    "gpt-5.6-terra": ("low", "medium", "high", "xhigh", "max"),
    "gpt-5.6-luna": ("low", "medium", "high", "xhigh", "max"),
    "gpt-5.5": ("low", "medium", "high", "xhigh"),
    "codex-auto-review": ("low", "medium", "high", "xhigh", "max"),
}
CLAUDE_EFFORTS = {
    "claude-sonnet-4-6": ("low", "medium", "high", "max"),
    "claude-opus-4-6": ("low", "medium", "high", "max"),
    **{
        model: ("low", "medium", "high", "xhigh", "max")
        for model in (
            "claude-sonnet-5",
            "claude-sonnet-5-5",
            "claude-opus-4-7",
            "claude-opus-4-8",
            "claude-opus-5",
            "claude-opus-5-5",
            "claude-fable-5",
            "claude-fable-5-1",
        )
    },
}


def text(value, label):
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def roles(value):
    if not isinstance(value, dict) or set(value) - set(ROLES):
        raise ValueError("Worker roles must be an object keyed by assess/work/review/triage/watch")
    for role, selection in value.items():
        if not isinstance(selection, dict) or set(selection) != {
            "provider",
            "model",
            "effort",
            "basis",
        }:
            raise ValueError(f"{role} requires provider, model, effort and basis")
        if selection["provider"] not in PROVIDERS:
            raise ValueError(f"{role}: provider must be codex or claude")
        text(selection["basis"], role + ".basis")
        for key in ("model", "effort"):
            if selection[key] is not None:
                text(selection[key], role + "." + key)
    return deepcopy(value)


def plan(value):
    if value is None:
        return {}
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "roles"}
        or type(value["version"]) is not int
        or value["version"] != 1
    ):
        raise ValueError("Unsupported workerPlan; expected version=1 and roles")
    return roles(value["roles"])


def request(previous, config, document, mode=None, selections=None):
    """Freeze defaults and the authored plan once; reselect only explicit inputs."""
    if mode is not None and mode not in MODES:
        raise ValueError("Worker mode must be auto, codex-only or claude-only")
    if previous is not None and (
        type(previous.get("version")) is not int or previous["version"] != 1
    ):
        raise ValueError("Unsupported worker routing request version")
    if previous is None:
        previous = {
            "version": 1,
            "mode": "auto",
            "roles": {},
            "plan": plan(document.get("workerPlan")),
            "defaults": {
                "worker": deepcopy(config.get("worker", {})),
                "worker_profiles": deepcopy(config.get("worker_profiles", {})),
            },
        }
    return {
        **deepcopy(previous),
        "mode": mode if mode is not None else previous["mode"],
        "roles": roles(selections) if selections is not None else previous["roles"],
    }


def validate_adapter(adapter):
    provider = adapter.get("type")
    if provider not in (*PROVIDERS, "command"):
        raise ValueError("Unknown worker adapter")
    for field in ("model", "effort"):
        if adapter.get(field) is not None:
            text(adapter[field], "worker." + field)
    effort = adapter.get("effort")
    if effort is None:
        return
    model = adapter.get("model")
    supported = (CODEX_EFFORTS if provider == "codex" else CLAUDE_EFFORTS).get(model, ())
    if provider == "command" or effort not in supported:
        raise ValueError(
            f"Unsupported or unverified worker combination: {provider}/{model}/{effort}. "
            "Choose a documented model/effort, or explicitly use effort=null for the "
            "provider default. Codex ultra requires delegation disabled by this worker."
        )


def resolve(routing, role):
    if (
        type(routing.get("version")) is not int
        or routing["version"] != 1
        or routing.get("mode") not in MODES
        or role not in ROLES
    ):
        raise ValueError("Unsupported worker routing snapshot")
    mode = routing["mode"]
    allowed = PROVIDERS if mode == "auto" else (mode.removesuffix("-only"),)
    defaults = routing["defaults"]
    primary = defaults.get("worker", {})
    candidates = []
    for source, choices in (("request", routing["roles"]), ("document", routing["plan"])):
        if role in choices:
            choice = choices[role]
            candidates.append(
                (
                    source,
                    choice["basis"],
                    {
                        "type": choice["provider"],
                        "model": choice["model"],
                        "effort": choice["effort"],
                    },
                )
            )
    if primary:
        candidates.append(("project", "Project worker default", deepcopy(primary)))
    profiles = defaults.get("worker_profiles", {})
    if not isinstance(profiles, dict) or set(profiles) - set(PROVIDERS):
        raise ValueError("worker_profiles must be keyed by codex/claude")
    for provider in allowed:
        if provider in profiles:
            profile = profiles[provider]
            if not isinstance(profile, dict) or set(profile) - {"model", "effort"}:
                raise ValueError(f"Invalid {provider} worker profile; use model and effort")
            candidates.append(
                ("project-profile", f"Project {provider} profile", {"type": provider, **profile})
            )
    skipped = []
    for source, basis, adapter in candidates:
        provider = adapter.get("type")
        if provider not in allowed and not (mode == "auto" and provider == "command"):
            skipped.append({"source": source, "provider": provider, "reason": mode})
            continue
        validate_adapter(adapter)
        return {
            "version": 1,
            "mode": mode,
            "role": role,
            "requestId": routing["requestId"],
            "source": source,
            "basis": basis,
            "skipped": skipped,
            "adapter": adapter,
            "selected": {
                "provider": provider,
                "model": adapter.get("model"),
                "effort": adapter.get("effort"),
            },
        }
    raise ValueError(
        f"No allowed worker for {role} in {mode}. Supply --worker-roles with a "
        "selection for the allowed provider, or configure its worker_profiles entry. "
        "Custom command adapters cannot establish an exclusive provider boundary."
    )


def apply(config, selection):
    if selection is None:
        return config
    if type(selection.get("version")) is not int or selection["version"] != 1:
        raise ValueError("Unsupported worker routing attempt version")
    if selection.get("error"):
        raise ValueError(selection["error"])
    adapter = deepcopy(selection["adapter"])
    mode = selection["mode"]
    if mode not in MODES or (mode != "auto" and adapter["type"] != mode.removesuffix("-only")):
        raise ValueError("Attempt worker violates its persisted provider boundary")
    validate_adapter(adapter)
    return {**config, "worker": adapter}
