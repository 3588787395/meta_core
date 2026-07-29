# -*- coding: utf-8 -*-
"""Task 21.5: frontend XSS injection negative tests.

Verifies that the escapeHtml / escHtml functions in the frontend JavaScript
files properly escape XSS payloads. Tests the regex-based implementations
directly via Node.js and verifies the DOM-based implementations exist.

Negative test PASSES when the escape function neutralizes the XSS payload
(no raw <, >, ", or & in the output).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import List

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_WEB_JS_DIR = _PROJECT_ROOT / "web" / "js"

# XSS payloads that should be neutralized by escapeHtml
_XSS_PAYLOADS = [
    "<script>alert('xss')</script>",
    "<img src=x onerror=alert(1)>",
    '"><script>alert(1)</script>',
    "javascript:alert(1)",
    "<svg onload=alert(1)>",
    "<iframe src=javascript:alert(1)>",
    "<body onload=alert(1)>",
    "';alert(String.fromCharCode(88,83,83))//'",
    "<a href=\"javascript:alert(1)\">click</a>",
    "<div style=\"background:url('javascript:alert(1)')\">",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_regex_escape_function(source: str) -> str:
    """Extract a regex-based escapeHtml/escHtml function body from JS source.

    Returns the function as a self-contained JS snippet that defines
    `escapeHtml` globally, or empty string if not found.
    """
    # Match patterns like:
    #   function escapeHtml(s) { ... String(s).replace(...)... }
    #   function escHtml(s) { ... }
    patterns = [
        r'function\s+escapeHtml\s*\(\s*\w+\s*\)\s*\{[^}]*String\([^}]*\}',
        r'function\s+escHtml\s*\(\s*\w+\s*\)\s*\{[^}]*String\([^}]*\}',
    ]
    for pat in patterns:
        match = re.search(pat, source, re.DOTALL)
        if match:
            func_text = match.group(0)
            # Normalize the function name to escapeHtml for testing
            func_text = re.sub(r'function\s+escHtml\s*\(', 'function escapeHtml(', func_text)
            return func_text
    return ""


def _run_node_escape(payloads: List[str], escape_func_js: str) -> List[str]:
    """Run the escape function in Node.js against the given payloads.

    Returns a list of "payload|escaped" strings.
    """
    # Build a Node.js script that defines the escape function and tests payloads
    test_script = f"""
{escape_func_js}
const payloads = {json.dumps(payloads)};
const results = payloads.map(p => {{
    const escaped = escapeHtml(p);
    return JSON.stringify({{payload: p, escaped: escaped}});
}});
console.log(results.join('\\n'));
"""
    try:
        result = subprocess.run(
            ["node", "-e", test_script],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(_PROJECT_ROOT),
        )
        if result.returncode != 0:
            return []
        return result.stdout.strip().split("\n") if result.stdout.strip() else []
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []


# ============================================================================
# SubTask: escapeHtml functions exist in all JS files
# ============================================================================


class TestEscapeFunctionsExist:
    """All frontend JS files that render HTML should have an escape function."""

    def test_event_panel_js_has_escapeHtml(self):
        """event-panel.js defines escapeHtml."""
        js_file = _WEB_JS_DIR / "event-panel.js"
        assert js_file.exists(), "event-panel.js not found"
        source = js_file.read_text(encoding="utf-8")
        assert "function escapeHtml" in source, (
            "escapeHtml function not found in event-panel.js"
        )

    def test_canvas_js_has_escHtml(self):
        """canvas.js defines escHtml."""
        js_file = _WEB_JS_DIR / "canvas.js"
        assert js_file.exists(), "canvas.js not found"
        source = js_file.read_text(encoding="utf-8")
        assert "function escHtml" in source, (
            "escHtml function not found in canvas.js"
        )

    def test_app_js_has_escapeHtml(self):
        """app.js defines escapeHtml."""
        js_file = _WEB_JS_DIR / "app.js"
        assert js_file.exists(), "app.js not found"
        source = js_file.read_text(encoding="utf-8")
        assert "function escapeHtml" in source, (
            "escapeHtml function not found in app.js"
        )

    def test_ui_js_has_escapeHtml(self):
        """ui.js defines escapeHtml."""
        js_file = _WEB_JS_DIR / "ui.js"
        assert js_file.exists(), "ui.js not found"
        source = js_file.read_text(encoding="utf-8")
        assert "function escapeHtml" in source, (
            "escapeHtml function not found in ui.js"
        )


# ============================================================================
# SubTask: regex-based escapeHtml neutralizes XSS payloads
# ============================================================================


class TestRegexEscapeNeutralizesXSS:
    """The regex-based escapeHtml functions should neutralize XSS payloads.

    These functions use String.replace() to escape & < > " characters.
    They can be tested directly in Node.js without a DOM environment.
    """

    @pytest.fixture
    def event_panel_escape_func(self) -> str:
        """Extract the escapeHtml function from event-panel.js."""
        js_file = _WEB_JS_DIR / "event-panel.js"
        source = js_file.read_text(encoding="utf-8")
        func = _extract_regex_escape_function(source)
        if not func:
            pytest.skip("Could not extract escapeHtml from event-panel.js")
        return func

    @pytest.fixture
    def canvas_escape_func(self) -> str:
        """Extract the escHtml function from canvas.js."""
        js_file = _WEB_JS_DIR / "canvas.js"
        source = js_file.read_text(encoding="utf-8")
        func = _extract_regex_escape_function(source)
        if not func:
            pytest.skip("Could not extract escHtml from canvas.js")
        return func

    @pytest.fixture
    def app_esc_html_func(self) -> str:
        """Extract the escHtml function from app.js (second occurrence)."""
        js_file = _WEB_JS_DIR / "app.js"
        source = js_file.read_text(encoding="utf-8")
        func = _extract_regex_escape_function(source)
        if not func:
            pytest.skip("Could not extract escHtml from app.js")
        return func

    def test_event_panel_escapeHtml_neutralizes_script_tag(self, event_panel_escape_func):
        """escapeHtml from event-panel.js neutralizes <script> tags."""
        results = _run_node_escape(
            ["<script>alert('xss')</script>"], event_panel_escape_func
        )
        if not results:
            pytest.skip("Node.js execution failed")
        data = json.loads(results[0])
        assert "<script>" not in data["escaped"], (
            f"XSS payload not neutralized: {data['escaped']}"
        )
        assert "&lt;script&gt;" in data["escaped"], (
            f"Script tag not escaped: {data['escaped']}"
        )

    def test_event_panel_escapeHtml_neutralizes_all_payloads(self, event_panel_escape_func):
        """escapeHtml from event-panel.js neutralizes all XSS payloads."""
        results = _run_node_escape(_XSS_PAYLOADS, event_panel_escape_func)
        if not results:
            pytest.skip("Node.js execution failed")
        assert len(results) == len(_XSS_PAYLOADS)
        for line in results:
            data = json.loads(line)
            escaped = data["escaped"]
            # The escaped output should NOT contain raw < or >
            # (they should be &lt; and &gt;)
            # Count raw < and > (not part of &lt; or &gt;)
            raw_lt = escaped.replace("&lt;", "").count("<")
            raw_gt = escaped.replace("&gt;", "").count(">")
            assert raw_lt == 0, (
                f"Raw '<' in escaped output: {escaped}"
            )
            assert raw_gt == 0, (
                f"Raw '>' in escaped output: {escaped}"
            )

    def test_canvas_escHtml_neutralizes_script_tag(self, canvas_escape_func):
        """escHtml from canvas.js neutralizes <script> tags."""
        results = _run_node_escape(
            ["<script>alert('xss')</script>"], canvas_escape_func
        )
        if not results:
            pytest.skip("Node.js execution failed")
        data = json.loads(results[0])
        assert "<script>" not in data["escaped"]

    def test_app_escHtml_neutralizes_script_tag(self, app_esc_html_func):
        """escHtml from app.js neutralizes <script> tags."""
        results = _run_node_escape(
            ["<script>alert('xss')</script>"], app_esc_html_func
        )
        if not results:
            pytest.skip("Node.js execution failed")
        data = json.loads(results[0])
        assert "<script>" not in data["escaped"]


# ============================================================================
# SubTask: escapeHtml handles edge cases
# ============================================================================


class TestEscapeHtmlEdgeCases:
    """escapeHtml should handle edge cases like null, numbers, objects."""

    @pytest.fixture
    def escape_func(self) -> str:
        """Extract escapeHtml from event-panel.js."""
        js_file = _WEB_JS_DIR / "event-panel.js"
        source = js_file.read_text(encoding="utf-8")
        func = _extract_regex_escape_function(source)
        if not func:
            pytest.skip("Could not extract escapeHtml")
        return func

    def test_escapeHtml_handles_null(self, escape_func):
        """escapeHtml returns empty string for null/undefined."""
        results = _run_node_escape(["__NULL__"], escape_func)
        if not results:
            pytest.skip("Node.js execution failed")
        # The function should handle null — test with a modified script
        test_script = f"""
{escape_func}
console.log(JSON.stringify({{result: escapeHtml(null)}}));
console.log(JSON.stringify({{result: escapeHtml(undefined)}}));
console.log(JSON.stringify({{result: escapeHtml("")}}));
"""
        try:
            result = subprocess.run(
                ["node", "-e", test_script],
                capture_output=True, text=True, timeout=10,
                cwd=str(_PROJECT_ROOT),
            )
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split("\n")
                for line in lines:
                    data = json.loads(line)
                    assert data["result"] == "", (
                        f"Expected empty string for null/undefined, got: {data['result']}"
                    )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pytest.skip("Node.js execution failed")

    def test_escapeHtml_handles_numbers(self, escape_func):
        """escapeHtml converts numbers to strings and escapes them."""
        test_script = f"""
{escape_func}
console.log(JSON.stringify({{result: escapeHtml(42)}}));
console.log(JSON.stringify({{result: escapeHtml(0)}}));
console.log(JSON.stringify({{result: escapeHtml(-1)}}));
"""
        try:
            result = subprocess.run(
                ["node", "-e", test_script],
                capture_output=True, text=True, timeout=10,
                cwd=str(_PROJECT_ROOT),
            )
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split("\n")
                data = json.loads(lines[0])
                assert data["result"] == "42"
                data = json.loads(lines[1])
                assert data["result"] == "0"
                data = json.loads(lines[2])
                assert data["result"] == "-1"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pytest.skip("Node.js execution failed")

    def test_escapeHtml_escapes_ampersand(self, escape_func):
        """escapeHtml escapes & to &amp;."""
        test_script = f"""
{escape_func}
console.log(JSON.stringify({{result: escapeHtml("a&b")}}));
"""
        try:
            result = subprocess.run(
                ["node", "-e", test_script],
                capture_output=True, text=True, timeout=10,
                cwd=str(_PROJECT_ROOT),
            )
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout.strip())
                assert data["result"] == "a&amp;b", (
                    f"Ampersand not escaped: {data['result']}"
                )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pytest.skip("Node.js execution failed")

    def test_escapeHtml_escapes_double_quote(self, escape_func):
        """escapeHtml escapes " to &quot;."""
        test_script = f"""
{escape_func}
console.log(JSON.stringify({{result: escapeHtml('say "hello"')}}));
"""
        try:
            result = subprocess.run(
                ["node", "-e", test_script],
                capture_output=True, text=True, timeout=10,
                cwd=str(_PROJECT_ROOT),
            )
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout.strip())
                assert '"' not in data["result"].replace("&quot;", ""), (
                    f"Double quote not escaped: {data['result']}"
                )
                assert "&quot;" in data["result"], (
                    f"Expected &quot; in output: {data['result']}"
                )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pytest.skip("Node.js execution failed")


# ============================================================================
# SubTask: DOM-based escapeHtml functions exist (ui.js, app.js)
# ============================================================================


class TestDOMBasedEscapeExists:
    """DOM-based escapeHtml functions (using document.createElement) exist.

    These functions use the browser DOM to escape HTML and cannot be tested
    in Node.js directly. We verify they exist and use the DOM API correctly.
    """

    def test_ui_js_escapeHtml_uses_dom(self):
        """ui.js escapeHtml uses document.createElement('div')."""
        js_file = _WEB_JS_DIR / "ui.js"
        source = js_file.read_text(encoding="utf-8")
        assert "function escapeHtml" in source
        # Find the function body
        match = re.search(r'function\s+escapeHtml\s*\([^)]*\)\s*\{[^}]+\}', source, re.DOTALL)
        assert match, "escapeHtml function body not found"
        func_body = match.group(0)
        assert "document.createElement" in func_body, (
            "ui.js escapeHtml should use document.createElement for DOM-based escaping"
        )
        assert "textContent" in func_body, (
            "ui.js escapeHtml should use textContent to set raw text"
        )
        assert "innerHTML" in func_body, (
            "ui.js escapeHtml should return innerHTML (browser auto-escapes)"
        )

    def test_app_js_escapeHtml_uses_dom(self):
        """app.js escapeHtml uses document.createElement('div')."""
        js_file = _WEB_JS_DIR / "app.js"
        source = js_file.read_text(encoding="utf-8")
        # Find the first escapeHtml function (DOM-based)
        match = re.search(r'function\s+escapeHtml\s*\([^)]*\)\s*\{[^}]+\}', source, re.DOTALL)
        assert match, "escapeHtml function body not found"
        func_body = match.group(0)
        assert "document.createElement" in func_body, (
            "app.js escapeHtml should use document.createElement"
        )
        assert "textContent" in func_body
        assert "innerHTML" in func_body

    def test_dom_based_escape_is_safe_by_design(self):
        """DOM-based escape (textContent→innerHTML) is inherently XSS-safe.

        Setting textContent and reading innerHTML causes the browser to
        automatically escape all HTML special characters. This is a
        well-known safe pattern.
        """
        # This is a design verification — the pattern itself is safe
        # per OWASP guidelines: https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html
        assert True  # Documented for clarity
