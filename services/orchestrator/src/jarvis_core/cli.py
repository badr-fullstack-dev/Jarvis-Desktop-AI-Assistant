from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .api import LocalSupervisorAPI
from .models import ActionProposal


def main() -> None:
    parser = argparse.ArgumentParser(description="Jarvis guarded assistant scaffold CLI")
    parser.add_argument("objective", help="Objective to submit to the supervisor")
    parser.add_argument("--source", default="text")
    parser.add_argument("--capability", default="browser.read_page")
    parser.add_argument("--path", dest="target_path", default="https://example.com")
    parser.add_argument("--approved", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[4]
    api = LocalSupervisorAPI(root)
    task = asyncio.run(api.submit_voice_or_text_task(args.objective, source=args.source))

    proposal = ActionProposal(
        task_id=task.task_id,
        capability=args.capability,
        intent=args.objective,
        parameters={"url": args.target_path, "path": args.target_path, "name": args.target_path},
        requested_by="cli",
        evidence=["cli demo"],
        confidence=0.9,
    )
    result = api.submit_action(proposal, approved=args.approved)

    print(json.dumps({"task": task.to_dict(), "result": result.to_dict()}, indent=2))


if __name__ == "__main__":
    main()

