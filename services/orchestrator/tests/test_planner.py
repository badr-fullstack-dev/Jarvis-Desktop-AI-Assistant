"""Unit tests for the deterministic planner.

The planner is a pure function: given text, return a PlanResult. These
tests cover each supported intent, URL normalization, the app allowlist,
and the ambiguity / unsupported paths.
"""

from __future__ import annotations

import unittest

from src.jarvis_core.planner import (
    CLARIFICATION_NEEDED,
    MAPPED,
    UNSUPPORTED,
    DeterministicPlanner,
)


class PlannerMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = DeterministicPlanner()

    # --- browser.navigate -----------------------------------------------------

    def test_open_url_with_scheme(self) -> None:
        r = self.planner.plan("open https://example.com")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "browser.navigate")
        self.assertEqual(r.parameters, {"url": "https://example.com"})
        self.assertEqual(r.matched_rule, "open.url")

    def test_go_to_bare_domain_prepends_scheme(self) -> None:
        r = self.planner.plan("go to example.com")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "browser.navigate")
        self.assertEqual(r.parameters, {"url": "https://example.com"})

    def test_visit_subdomain_path(self) -> None:
        r = self.planner.plan("visit docs.example.com/install")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "browser.navigate")
        self.assertEqual(r.parameters["url"], "https://docs.example.com/install")

    # --- browser.read_page ----------------------------------------------------

    def test_read_url(self) -> None:
        r = self.planner.plan("read https://example.com")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "browser.read_page")
        self.assertEqual(r.parameters["url"], "https://example.com")

    def test_read_the_page_at_url(self) -> None:
        r = self.planner.plan("read the page at https://example.com/faq")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "browser.read_page")
        self.assertEqual(r.parameters["url"], "https://example.com/faq")

    # --- filesystem.read ------------------------------------------------------

    def test_read_file_with_extension(self) -> None:
        r = self.planner.plan("read configs/policy.default.json")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "filesystem.read")
        self.assertEqual(r.parameters, {"path": "configs/policy.default.json"})

    def test_read_file_word_file_prefix(self) -> None:
        r = self.planner.plan("read file runtime/sandbox/hello.txt")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "filesystem.read")
        self.assertEqual(r.parameters["path"], "runtime/sandbox/hello.txt")

    # --- filesystem.list ------------------------------------------------------

    def test_list_files_in_dir(self) -> None:
        r = self.planner.plan("list files in configs")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "filesystem.list")
        self.assertEqual(r.parameters, {"path": "configs"})

    def test_ls_short_form(self) -> None:
        r = self.planner.plan("ls runtime/sandbox")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "filesystem.list")
        self.assertEqual(r.parameters["path"], "runtime/sandbox")

    def test_list_rejects_url_target(self) -> None:
        r = self.planner.plan("list files in https://example.com")
        self.assertEqual(r.status, CLARIFICATION_NEEDED)
        self.assertIn("Cannot list a URL", r.ambiguity or "")

    # --- filesystem.write -----------------------------------------------------

    def test_write_to_sandbox(self) -> None:
        r = self.planner.plan("write hello to runtime/sandbox/hello.txt")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "filesystem.write")
        self.assertEqual(r.parameters, {
            "path": "runtime/sandbox/hello.txt",
            "content": "hello",
        })

    def test_write_with_quoted_content(self) -> None:
        r = self.planner.plan('save "hi there" to runtime/sandbox/msg.txt')
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.parameters["content"], "hi there")

    def test_write_outside_sandbox_refused(self) -> None:
        r = self.planner.plan("write secret to C:/Windows/System32/notes.txt")
        self.assertEqual(r.status, CLARIFICATION_NEEDED)
        self.assertEqual(r.matched_rule, "write.outside_sandbox")

    # --- app.launch -----------------------------------------------------------

    def test_open_notepad(self) -> None:
        r = self.planner.plan("open notepad")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "app.launch")
        self.assertEqual(r.parameters, {"name": "notepad"})

    def test_open_calculator_alias(self) -> None:
        r = self.planner.plan("launch calculator")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.parameters["name"], "calculator")

    def test_open_paint_alias_resolves_mspaint(self) -> None:
        r = self.planner.plan("open paint")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.parameters["name"], "mspaint")

    def test_open_app_wins_over_url_when_both_would_match(self) -> None:
        # "notepad" is an app; a bare word like this must NOT become a URL.
        r = self.planner.plan("open notepad")
        self.assertEqual(r.capability, "app.launch")


class PlannerAmbiguityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = DeterministicPlanner()

    def test_read_this_page_is_clarification_when_no_context(self) -> None:
        r = self.planner.plan("read this page")
        self.assertEqual(r.status, CLARIFICATION_NEEDED)
        self.assertEqual(r.matched_rule, "read.page_no_context")

    def test_read_this_page_maps_when_context_available(self) -> None:
        r = self.planner.plan("read this page", has_browser_context=True)
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "browser.current_page")

    def test_open_it_is_clarification(self) -> None:
        r = self.planner.plan("open it")
        self.assertEqual(r.status, CLARIFICATION_NEEDED)

    def test_open_unknown_target_is_clarification(self) -> None:
        r = self.planner.plan("open foobar")
        self.assertEqual(r.status, CLARIFICATION_NEEDED)
        self.assertEqual(r.matched_rule, "open.unknown_target")

    def test_read_bare_word_is_clarification(self) -> None:
        r = self.planner.plan("read notepad")
        self.assertEqual(r.status, CLARIFICATION_NEEDED)
        self.assertEqual(r.matched_rule, "read.ambiguous")

    def test_navigate_without_url_is_clarification(self) -> None:
        r = self.planner.plan("go to nowhere")
        self.assertEqual(r.status, CLARIFICATION_NEEDED)
        self.assertEqual(r.matched_rule, "nav.bad_target")

    def test_empty_text_is_unsupported(self) -> None:
        r = self.planner.plan("")
        self.assertEqual(r.status, UNSUPPORTED)

    def test_unrelated_text_is_unsupported(self) -> None:
        r = self.planner.plan("please summarize my week")
        self.assertEqual(r.status, UNSUPPORTED)
        self.assertEqual(r.matched_rule, "fallthrough")


class PlannerDesktopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = DeterministicPlanner()

    def test_clipboard_query_maps_to_read(self) -> None:
        for phrase in [
            "what is in my clipboard?",
            "what's in the clipboard",
            "read my clipboard",
            "show clipboard",
            "paste my clipboard",
        ]:
            r = self.planner.plan(phrase)
            self.assertEqual(r.status, MAPPED, phrase)
            self.assertEqual(r.capability, "desktop.clipboard_read")

    def test_copy_to_clipboard_maps_to_write(self) -> None:
        r = self.planner.plan("copy hello world to clipboard")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "desktop.clipboard_write")
        self.assertEqual(r.parameters, {"text": "hello world"})

    def test_copy_to_clipboard_strips_quotes(self) -> None:
        r = self.planner.plan("copy 'secret token' to clipboard")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.parameters["text"], "secret token")

    def test_notify_maps(self) -> None:
        r = self.planner.plan("send me a notification saying hello")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "desktop.notify")
        self.assertEqual(r.parameters["message"], "hello")

    def test_notify_shorthand(self) -> None:
        r = self.planner.plan("notify me hello jarvis")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "desktop.notify")
        self.assertIn("hello", r.parameters["message"])

    def test_foreground_window_query_maps(self) -> None:
        for phrase in [
            "show my current window",
            "what is the foreground window",
            "what window is open?",
            "current window",
        ]:
            r = self.planner.plan(phrase)
            self.assertEqual(r.status, MAPPED, phrase)
            self.assertEqual(r.capability, "desktop.foreground_window")

    def test_focus_allowlisted_app_maps(self) -> None:
        r = self.planner.plan("focus notepad")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "app.focus")
        self.assertEqual(r.parameters, {"name": "notepad"})

    def test_switch_to_calculator_maps(self) -> None:
        r = self.planner.plan("switch to calculator")
        self.assertEqual(r.status, MAPPED)
        self.assertEqual(r.capability, "app.focus")
        self.assertEqual(r.parameters["name"], "calculator")

    def test_focus_unknown_app_is_clarification(self) -> None:
        r = self.planner.plan("focus slack")
        self.assertEqual(r.status, CLARIFICATION_NEEDED)
        self.assertEqual(r.matched_rule, "desktop.focus.unknown_target")

    def test_focus_deictic_is_clarification(self) -> None:
        r = self.planner.plan("focus it")
        self.assertEqual(r.status, CLARIFICATION_NEEDED)

    def test_screenshot_foreground_variants(self) -> None:
        for phrase in [
            "take a screenshot",
            "screenshot",
            "screenshot my window",
            "take a screenshot of my current window",
            "capture a screenshot of the active window",
            "what is on my screen",
            "what's on my screen?",
            "show me my current screen",
        ]:
            r = self.planner.plan(phrase)
            self.assertEqual(r.status, MAPPED, phrase)
            self.assertEqual(r.capability, "desktop.screenshot_foreground", phrase)

    def test_screenshot_full_variants(self) -> None:
        for phrase in [
            "take a full screen screenshot",
            "capture the entire desktop",
            "screenshot the whole screen",
            "full screen screenshot",
            "take a screenshot of the entire screen",
            "capture my desktop",
        ]:
            r = self.planner.plan(phrase)
            self.assertEqual(r.status, MAPPED, phrase)
            self.assertEqual(r.capability, "desktop.screenshot_full", phrase)


class PlanResultSerializationTests(unittest.TestCase):
    def test_to_dict_shape(self) -> None:
        r = DeterministicPlanner().plan("open https://example.com")
        d = r.to_dict()
        self.assertEqual(
            set(d.keys()),
            {"status", "originalText", "capability", "parameters",
             "confidence", "rationale", "ambiguity", "matchedRule",
             "memoryHints"},
        )
        self.assertEqual(d["status"], "mapped")
        self.assertEqual(d["capability"], "browser.navigate")


if __name__ == "__main__":
    unittest.main()
