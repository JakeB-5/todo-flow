"""Explicitly labeled, local-only dashboard dataset; never imports real project content."""

import argparse
import json
import time
from pathlib import Path

from todo_flow.store import Store, encode

TITLES = [
    (
        "font-selection",
        "Make font selection predictable",
        "Apply font changes consistently to the selected text.",
        "Workspace",
    ),
    (
        "export-guide",
        "Explain export failures",
        "Explain why an export failed and what to do next.",
        "Workspace",
    ),
    (
        "draft-restore",
        "Restore drafts reliably",
        "Preserve editing state and selection after restoring a document.",
        "Storage",
    ),
    (
        "paint-order",
        "Preserve paint order",
        "Keep the original stacking order in exported results.",
        "Rendering",
    ),
    (
        "draft-policy",
        "Choose draft retention scope",
        "Choose between device-local storage and account sharing.",
        "Storage",
    ),
    (
        "selection-restore",
        "Restore selection context",
        "Restore selection context when reopening a document.",
        "Workspace",
    ),
    (
        "canvas-align",
        "Align objects consistently",
        "Use consistent alignment anchors for objects and artboards.",
        "Canvas",
    ),
    (
        "asset-search",
        "Clarify asset search results",
        "Show active search filters and the selected asset clearly.",
        "Assets",
    ),
]


def seed(store, active=48, completed=2500):
    now = time.time()
    with store.transaction() as c:
        for i in range(active + completed):
            base, title, goal, area = TITLES[i % len(TITLES)]
            done = i >= active
            id_ = f"{base}-{i:04}"
            status = "done" if done else "open"
            control = (
                "finished"
                if done
                else ["idle", "idle", "active", "active", "active", "paused", "idle", "idle"][i % 8]
            )
            title = title if i < 8 else title + f" · {i + 1:04}"
            doc = {
                "id": id_,
                "title": title,
                "goal": goal,
                "scope": "Synthetic list-scale fixture. Include only changes related to this goal.",
                "evidence": "Synthetic UI test data. Does not represent a real project or execution.",
                "priority": ["HIGH", "MEDIUM", "HIGH", "HIGH", "MEDIUM", "MEDIUM", "LOW", "MEDIUM"][
                    i % 8
                ],
                "area": area,
                "conditions": [
                    {
                        "id": "behavior",
                        "text": "The requested behavior is observable.",
                        "method": "Compare the scenario with the actual result",
                    },
                    {
                        "id": "regression",
                        "text": "Existing behavior is preserved.",
                        "method": "Regression tests",
                    },
                ],
            }
            updated = now - (i - active) * 3600 if done else now - i * 60
            review = {
                "verdict": "met",
                "conditions": [
                    {
                        "id": x["id"],
                        "verdict": "met",
                        "evidence": "Synthetic assessment for list-scale validation",
                    }
                    for x in doc["conditions"]
                ],
            }
            verification = {
                "ok": True,
                "output": "SYNTHETIC_LARGE_EVIDENCE\n" * 200,
                "head": f"{i:040x}",
            }
            c.execute(
                "INSERT INTO tracks(id,revision,document,status,control,head,verification,review,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    id_,
                    1,
                    encode(doc),
                    status,
                    control,
                    f"{i:040x}" if done else None,
                    encode(verification) if done else None,
                    encode(review) if done else None,
                    updated,
                ),
            )
            if not done and control == "active":
                waiting = i % 8 == 4
                kind = "review" if i % 8 == 3 else "work"
                work = "work-demo-" + str(i)
                c.execute(
                    "INSERT INTO tasks(id,track,kind,purpose,status,owner,lease,generation,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        work,
                        id_,
                        kind,
                        "Compare the candidate with acceptance conditions."
                        if kind == "review"
                        else "Inspect restoration behavior and the verification environment.",
                        "waiting" if waiting else "running",
                        None if waiting else "worker-demo-" + str(i),
                        None if waiting else now + 3600,
                        1,
                        updated,
                        now - 15,
                    ),
                )
                if waiting:
                    c.execute(
                        "INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?)",
                        (
                            "decision-demo-" + str(i),
                            id_,
                            work,
                            "Should drafts be device-local or shared by account? Only work needing this decision waits.",
                            "open",
                            1,
                            None,
                            updated,
                        ),
                    )
            if i < 40:
                store.event(
                    c,
                    "work.result" if done else "document.registered",
                    id_,
                    {"summary": "Synthetic activity record"},
                )
        c.execute(
            "INSERT INTO watches VALUES(?,?,?,?,?,?,?)",
            (
                "watch-demo",
                TITLES[0][0] + "-0000",
                encode(
                    {
                        "observation": "Check the font-change path for a template object",
                        "reason": "Reproduction conditions are not established",
                        "trigger": "The symptom is reproduced with this template",
                        "next_action": "Collect selection context and connect it to a focused investigation",
                    }
                ),
                "open",
                "template-reproduced",
                None,
                now,
            ),
        )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--state", required=True)
    p.add_argument("--language", choices=["en", "ko"], default="en")
    args = p.parse_args()
    state = Path(args.state)
    if state.exists():
        p.error("Use a new state directory")
    store = Store(state)
    store.configure(
        {
            "github": None,
            "base": "main",
            "endpoint": "review",
            "display_name": "TODO Flow · Demo workspace",
            "demo": True,
            "language": args.language,
        }
    )
    seed(store)
    print(
        json.dumps(
            {"state": str(state.resolve()), "active": 48, "completed": 2500, "synthetic": True}
        )
    )


if __name__ == "__main__":
    main()
