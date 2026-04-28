"""Tests for bounded multi-step workflow orchestration.

Covers:
 - WorkflowPlanner: matches the four v1 patterns; rejects unsupported input.
 - WorkflowRunner: happy path, approval pause + resume, denial halt,
   policy-blocked step, step failure short-circuit.
 - End-to-end through LocalSupervisorAPI: read+summarize and
   write+read-back both run each step through the guarded gateway.
 - Unsupported multi-step requests fall back to the single-step planner
   and do not improvise an agent loop.
"""

from __future__ import annotations

import asyncio
import shutil
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from uuid import uuid4

from src.jarvis_core.api import LocalSupervisorAPI
from src.jarvis_core.models import ActionProposal, ActionResult
from src.jarvis_core.workflow import (
    STEP_BLOCKED,
    STEP_COMPLETED,
    STEP_FAILED,
    STEP_WAITING,
    WF_COMPLETED,
    WF_FAILED,
    WF_WAITING,
    Workflow,
    WorkflowPlan,
    WorkflowPlanner,
    WorkflowRunner,
    WorkflowStep,
)


# ---------------------------------------------------------------------------
# Planner tests
# ---------------------------------------------------------------------------

class WorkflowPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.p = WorkflowPlanner()

    def test_open_and_read(self) -> None:
        plan = self.p.plan("open https://example.com and read it")
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.pattern_id, "wf.open_and_read")
        self.assertEqual([s.capability for s in plan.steps],
                         ["browser.navigate", "browser.read_page"])
        self.assertEqual(plan.steps[0].parameters["url"], "https://example.com")

    def test_open_and_summarize(self) -> None:
        plan = self.p.plan("open example.com and summarize it")
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.pattern_id, "wf.open_and_summarize")
        self.assertEqual([s.capability for s in plan.steps],
                         ["browser.navigate", "browser.summarize"])
        self.assertTrue(plan.steps[0].parameters["url"].startswith("https://"))

    def test_read_then_summarize(self) -> None:
        plan = self.p.plan("read https://example.com then summarize this page")
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.pattern_id, "wf.read_then_summarize")
        self.assertEqual([s.capability for s in plan.steps],
                         ["browser.read_page", "browser.summarize"])
        self.assertTrue(plan.steps[1].parameters.get("use_context"))

    def test_write_then_read(self) -> None:
        plan = self.p.plan("write hello to runtime/sandbox/a.txt then read it back")
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.pattern_id, "wf.write_then_read")
        self.assertEqual([s.capability for s in plan.steps],
                         ["filesystem.write", "filesystem.read"])
        self.assertEqual(plan.steps[0].parameters["content"], "hello")

    def test_write_outside_sandbox_rejected(self) -> None:
        self.assertIsNone(
            self.p.plan("write hi to C:/Windows/System32/x.txt then read it"),
        )

    def test_unsupported_multi_step_returns_none(self) -> None:
        self.assertIsNone(self.p.plan("please summarize my week"))
        self.assertIsNone(self.p.plan("open notepad and type hello"))
        self.assertIsNone(self.p.plan("click the submit button on example.com"))
        self.assertIsNone(self.p.plan(""))


# ---------------------------------------------------------------------------
# Runner unit tests with a fake propose_fn
# ---------------------------------------------------------------------------

def _fake_plan() -> WorkflowPlan:
    return WorkflowPlan(
        pattern_id="test.two_step",
        rationale="two fake steps",
        steps=[
            WorkflowStep(0, "filesystem.read", {"path": "x"}, "step 0"),
            WorkflowStep(1, "filesystem.read", {"path": "y"}, "step 1"),
        ],
    )


def _exec_outcome(summary: str = "ok") -> dict:
    return {
        "status": "executed",
        "result": {"summary": summary, "status": "executed"},
        "decision": {},
    }


class WorkflowRunnerUnitTests(unittest.TestCase):
    def test_happy_path_completes(self) -> None:
        calls = []

        def propose(p: ActionProposal) -> dict:
            calls.append(p.capability)
            return _exec_outcome(f"exec {len(calls)}")

        runner = WorkflowRunner(propose)
        wf = runner.create("task-1", "obj", _fake_plan())
        runner.start(wf)
        self.assertEqual(wf.status, WF_COMPLETED)
        self.assertEqual([s.status for s in wf.steps],
                         [STEP_COMPLETED, STEP_COMPLETED])
        self.assertEqual(calls, ["filesystem.read", "filesystem.read"])

    def test_awaiting_approval_pauses(self) -> None:
        step_index = {"n": 0}

        def propose(p: ActionProposal) -> dict:
            i = step_index["n"]
            step_index["n"] += 1
            if i == 0:
                return _exec_outcome()
            return {
                "status": "awaiting_approval",
                "approval": {"approval_id": "appr-test", "approvalId": "appr-test"},
                "decision": {"requires_approval": True, "risk_tier": 2},
            }

        runner = WorkflowRunner(propose)
        wf = runner.create("task-1", "obj", _fake_plan())
        runner.start(wf)
        self.assertEqual(wf.status, WF_WAITING)
        self.assertEqual(wf.steps[0].status, STEP_COMPLETED)
        self.assertEqual(wf.steps[1].status, STEP_WAITING)
        self.assertIs(runner.lookup_by_approval("appr-test"), wf)

    def test_resume_after_approval_completes(self) -> None:
        outcomes = iter([
            _exec_outcome("s0"),
            {
                "status": "awaiting_approval",
                "approval": {"approval_id": "appr-1"},
                "decision": {"requires_approval": True, "risk_tier": 2},
            },
        ])
        runner = WorkflowRunner(lambda p: next(outcomes))
        wf = runner.create("task-1", "obj", _fake_plan())
        runner.start(wf)
        self.assertEqual(wf.status, WF_WAITING)

        # Simulate: supervisor.approve_and_execute ran the paused step.
        proposal = ActionProposal(
            task_id="task-1", capability="filesystem.read",
            parameters={"path": "y"}, intent="resume", requested_by="test",
            evidence=[], confidence=0.9,
        )
        result = ActionResult(proposal=proposal, status="executed",
                              summary="read y", output={"bytes": 10})
        resumed = runner.resume_after_approval("appr-1")
        self.assertIs(resumed, wf)
        runner.mark_step_executed(wf, result)
        runner.continue_(wf)
        self.assertEqual(wf.status, WF_COMPLETED)
        self.assertEqual(wf.steps[1].status, STEP_COMPLETED)

    def test_denied_approval_halts_workflow(self) -> None:
        outcomes = iter([
            _exec_outcome(),
            {
                "status": "awaiting_approval",
                "approval": {"approval_id": "appr-2"},
                "decision": {"requires_approval": True, "risk_tier": 2},
            },
        ])
        runner = WorkflowRunner(lambda p: next(outcomes))
        wf = runner.create("task-1", "obj", _fake_plan())
        runner.start(wf)
        runner.halt_after_denial("appr-2", reason="not now")
        self.assertEqual(wf.status, WF_FAILED)
        self.assertEqual(wf.steps[1].status, STEP_FAILED)
        self.assertIn("denied", wf.steps[1].error.lower())

    def test_blocked_step_fails_workflow(self) -> None:
        outcomes = iter([
            _exec_outcome(),
            {
                "status": "blocked",
                "decision": {"reason": "blocked by policy"},
            },
        ])
        runner = WorkflowRunner(lambda p: next(outcomes))
        wf = runner.create("task-1", "obj", _fake_plan())
        runner.start(wf)
        self.assertEqual(wf.status, WF_FAILED)
        self.assertEqual(wf.steps[1].status, STEP_BLOCKED)
        self.assertIn("blocked", wf.error.lower())

    def test_step_failure_short_circuits(self) -> None:
        outcomes = iter([
            {"status": "failed", "result": {"summary": "disk on fire"}},
            _exec_outcome(),  # should never run
        ])
        runner = WorkflowRunner(lambda p: next(outcomes))
        wf = runner.create("task-1", "obj", _fake_plan())
        runner.start(wf)
        self.assertEqual(wf.status, WF_FAILED)
        self.assertEqual(wf.steps[0].status, STEP_FAILED)
        self.assertEqual(wf.steps[1].status, "pending")


# ---------------------------------------------------------------------------
# End-to-end via LocalSupervisorAPI (loopback HTTP, real gateway)
# ---------------------------------------------------------------------------

class _PageHandler(BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):  # silence
        pass

    def do_GET(self):
        body = (
            b"<html><head><title>Workflow Fixture</title></head><body>"
            b"<p>First sentence. Second sentence with more content. "
            b"Third sentence closes the demo.</p></body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class WorkflowEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = HTTPServer(("127.0.0.1", 0), _PageHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        workspace_root = Path(__file__).resolve().parents[3]
        self.root = workspace_root / "runtime" / f"workflow-{uuid4()}"
        (self.root / "configs").mkdir(parents=True, exist_ok=True)
        (self.root / "configs" / "policy.default.json").write_text(
            (workspace_root / "configs" / "policy.default.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.api = LocalSupervisorAPI(self.root)
        self.url = f"http://127.0.0.1:{self.port}/"

    def tearDown(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    def test_read_then_summarize_workflow(self) -> None:
        text = f"read {self.url} then summarize this page"
        task = asyncio.run(self.api.submit_voice_or_text_task(text))
        wf_dict = task.context.get("workflow")
        self.assertIsNotNone(wf_dict)
        self.assertEqual(wf_dict["status"], WF_COMPLETED)
        self.assertEqual(wf_dict["patternId"], "wf.read_then_summarize")
        self.assertEqual(len(wf_dict["steps"]), 2)
        self.assertEqual(wf_dict["steps"][0]["capability"], "browser.read_page")
        self.assertEqual(wf_dict["steps"][1]["capability"], "browser.summarize")
        for s in wf_dict["steps"]:
            self.assertEqual(s["status"], STEP_COMPLETED)

        # Context must have been populated by step 1, then reused by step 2.
        self.assertTrue(self.api.browser_context.has_context())

    def test_write_then_read_workflow(self) -> None:
        # Use an absolute path so the filesystem adapter resolves against
        # the test's per-case sandbox root, not the process CWD.
        abs_path = str(self.root / "runtime" / "sandbox" / "wf_demo.txt").replace("\\", "/")
        text = f"write workflow-demo to {abs_path} then read it back"
        task = asyncio.run(self.api.submit_voice_or_text_task(text))
        wf_dict = task.context.get("workflow")
        self.assertIsNotNone(wf_dict)
        self.assertEqual(wf_dict["status"], WF_COMPLETED, msg=str(wf_dict))
        self.assertEqual(wf_dict["steps"][0]["capability"], "filesystem.write")
        self.assertEqual(wf_dict["steps"][1]["capability"], "filesystem.read")
        for s in wf_dict["steps"]:
            self.assertEqual(s["status"], STEP_COMPLETED)

        # Verification: the written file actually exists and contains the value.
        sandbox_file = self.root / "runtime" / "sandbox" / "wf_demo.txt"
        self.assertTrue(sandbox_file.exists())
        self.assertEqual(sandbox_file.read_text(encoding="utf-8"), "workflow-demo")

    def test_open_and_read_workflow_runs_navigate_then_read(self) -> None:
        text = f"open {self.url} and read it"
        task = asyncio.run(self.api.submit_voice_or_text_task(text))
        wf_dict = task.context.get("workflow")
        self.assertIsNotNone(wf_dict)
        self.assertEqual(wf_dict["status"], WF_COMPLETED)
        self.assertEqual(wf_dict["steps"][0]["capability"], "browser.navigate")
        self.assertEqual(wf_dict["steps"][1]["capability"], "browser.read_page")
        self.assertTrue(self.api.browser_context.has_context())

    def test_unsupported_multi_step_falls_back_to_single_step(self) -> None:
        # "open notepad and type hello" — not a workflow pattern; must
        # not be improvised. Falls through to the single-step planner,
        # which will produce 'clarification_needed' / 'unsupported'.
        task = asyncio.run(self.api.submit_voice_or_text_task(
            "open notepad and type hello"))
        self.assertNotIn("workflow", task.context)
        self.assertIn("plan", task.context)
        self.assertIn(task.context["plan"]["status"],
                      ("clarification_needed", "unsupported", "mapped"))
        # We specifically guarantee it didn't invent a multi-step plan.
        self.assertFalse(any(e.get("event") == "workflow.created"
                             for e in task.trace))

    def test_single_step_request_still_works(self) -> None:
        # "read <url>" is a single-step request; must NOT get turned
        # into a workflow and must still auto-execute.
        # The fetch hits a localhost HTTPServer fixture; on shared CI
        # runners (Windows in particular) that connection can drop
        # intermittently. Retry a couple of times on transient
        # network-style failures so we still assert the core
        # behaviour ("executed", not turned into a workflow) without
        # depending on a single perfect localhost round-trip.
        task = None
        for _ in range(3):
            task = asyncio.run(
                self.api.submit_voice_or_text_task(f"read {self.url}"))
            if task.context.get("planAction", {}).get("status") == "executed":
                break
        assert task is not None
        self.assertNotIn("workflow", task.context)
        self.assertEqual(task.context["plan"]["capability"], "browser.read_page")
        self.assertEqual(task.context["planAction"]["status"], "executed")


if __name__ == "__main__":
    unittest.main()
