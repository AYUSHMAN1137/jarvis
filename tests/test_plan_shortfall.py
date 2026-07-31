"""A plan that stops short must be rejected, not executed.

This guards the worst failure observed in production: the planner decomposed
"turn off night light" into a single `open_application(Settings)` step. That one
step verified PASS, so the run reported "Done.", and the mapping was promoted
into the command cache -- permanently teaching the assistant that turning night
light off means opening Settings.
"""

from app.services.agent.planner.planner import plan_shortfall


class _Step:
    def __init__(self, tool: str, description: str = "") -> None:
        self.tool = tool
        self.description = description


class TestRejected:
    def test_change_goal_that_only_opens(self):
        reason = plan_shortfall("turn off night light", [_Step("open_application")])
        assert reason
        assert "only opens" in reason

    def test_change_output_device(self):
        assert plan_shortfall("open sound setting and change the output device",
                              [_Step("open_settings_page")])

    def test_multiple_open_steps_still_short(self):
        assert plan_shortfall("turn on bluetooth",
                              [_Step("open_settings_page"), _Step("focus_window")])

    def test_manual_handoff_in_description(self):
        steps = [_Step("open_application",
                       "Open Windows Settings, where you can manually turn off Night light")]
        reason = plan_shortfall("turn off night light", steps)
        assert reason
        assert "themselves" in reason

    def test_hindi_change_verb(self):
        assert plan_shortfall("bluetooth band kar do", [_Step("open_settings_page")])

    def test_empty_plan(self):
        assert plan_shortfall("turn off night light", [])


class TestAccepted:
    def test_plan_that_actually_acts(self):
        steps = [_Step("open_settings_page"), _Step("ui_do")]
        assert plan_shortfall("turn off night light", steps) == ""

    def test_pure_open_request_is_complete(self):
        # "open notepad" asks only for an open, so one open step IS the goal.
        assert plan_shortfall("open notepad", [_Step("open_application")]) == ""

    def test_direct_tool_is_complete(self):
        assert plan_shortfall("turn off bluetooth", [_Step("bluetooth_control")]) == ""

    def test_informational_request(self):
        assert plan_shortfall("open display settings", [_Step("open_settings_page")]) == ""
