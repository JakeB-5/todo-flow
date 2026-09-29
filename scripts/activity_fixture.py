"""Synthetic activity QA data. Use a new local state directory; never real project state."""

import argparse
import time
from pathlib import Path

from todo_flow.store import Store, encode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True)
    parser.add_argument(
        "--scenario", choices=["normal", "waiting", "expired", "legacy"], default="normal"
    )
    parser.add_argument("--language", choices=["en", "ko"], default="en")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    directory = Path(args.state)
    if directory.exists():
        parser.error("Use a new state directory")
    store = Store(directory)
    store.configure(
        {
            "github": None,
            "base": "main",
            "endpoint": "review",
            "display_name": "Synthetic activity QA",
            "demo": True,
            "language": args.language,
        }
    )
    now = time.time()
    output = "SYNTHETIC_FAILURE 검증 실패 😀\n" * 800
    with store.transaction() as c:
        for track, title in (
            ("alpha", "Synthetic recovery implementation"),
            ("beta", "Synthetic verification repair"),
        ):
            document = {
                "id": track,
                "title": title,
                "goal": "Inspect activity summaries and progressive details.",
                "scope": "Synthetic UI fixture only.",
                "evidence": "No real project or execution.",
                "conditions": [],
            }
            c.execute(
                "INSERT INTO tracks(id,revision,document,control,verification,updated)"
                " VALUES(?,1,?,'active',?,?)",
                (track, encode(document), encode({"ok": False, "output": output, "at": now}), now),
            )
        status = "waiting" if args.scenario == "waiting" else "running"
        lease = now - 1 if args.scenario == "expired" else now + 3600
        purpose = (
            "Legacy instructions without a structured summary. " + output
            if args.scenario == "legacy"
            else "Fix verification: " + output
        )
        c.executemany(
            "INSERT INTO tasks(id,track,kind,purpose,status,owner,lease,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            [
                ("one", "alpha", "work", purpose, status, "synthetic-worker", lease, 1, now),
                ("two", "alpha", "review", output, "queued", None, None, 2, now),
                ("three", "beta", "work", purpose, "running", "synthetic-worker", lease, 3, now),
            ],
        )
        if args.scenario == "waiting":
            c.execute(
                "INSERT INTO decisions(id,track,task,question,status,revision,created)"
                " VALUES('choice','alpha','one','Synthetic policy choice','open',1,?)",
                (now,),
            )
    from todo_flow.web import serve

    print(f"Synthetic fixture only: http://127.0.0.1:{args.port}/#activity", flush=True)
    serve(store, args.port)


if __name__ == "__main__":
    main()
