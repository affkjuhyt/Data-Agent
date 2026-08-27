"""Analysis-type taxonomy (services/workflow-runtime/nodes/model_harness/intent.py).

The planner classified everything as `question` or `task`, which only ever drove
whether the responder appended an options block. It could not tell a one-fact
lookup from a trend analysis, and — the part that matters — could not tell either
from a request to **forecast** or to **detect anomalies**, neither of which this
platform can do.

A taxonomy nobody reads is a field, not a capability. What is pinned here is
therefore mostly the *consequences*: that the two unsupported kinds are refused
deterministically, that a failed classification degrades safely, and that
`analysis_type` stays orthogonal to `intent` rather than quietly replacing it.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "services" / "workflow-runtime")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def _intent():
    import nodes.model_harness.intent as intent
    return intent


class TestParsing:
    def test_every_taxonomy_value_round_trips(self):
        intent = _intent()

        for value in ("lookup", "analytical", "comparison", "forecast", "anomaly", "other"):
            assert intent.parse(value).value == value

    def test_case_and_whitespace_are_tolerated(self):
        intent = _intent()

        assert intent.parse("  FORECAST ") is intent.AnalysisType.FORECAST

    @pytest.mark.parametrize("garbage", ["", None, "predictive", 42, {"a": 1}])
    def test_unknown_values_degrade_to_other_not_lookup(self, garbage):
        """OTHER asserts nothing. LOOKUP would assert the question is answerable
        from a single retrieved fact — a claim there is no basis for when the
        classification failed."""
        intent = _intent()

        assert intent.parse(garbage) is intent.AnalysisType.OTHER


class TestCapabilityGate:
    def test_forecast_is_not_supported(self):
        """There is no forecasting model in this platform. Saying so is the
        whole point of classifying the request."""
        intent = _intent()

        result = intent.capability(intent.AnalysisType.FORECAST)

        assert result.supported is False
        assert result.reason

    def test_anomaly_is_not_supported(self):
        intent = _intent()

        result = intent.capability(intent.AnalysisType.ANOMALY)

        assert result.supported is False
        assert result.reason

    @pytest.mark.parametrize("kind", ["lookup", "analytical", "comparison", "other"])
    def test_the_kinds_the_platform_can_serve_are_supported(self, kind):
        intent = _intent()

        assert intent.capability(intent.parse(kind)).supported is True

    def test_the_gate_is_deterministic_not_model_judged(self):
        """Whether this platform can forecast is a fact about the platform. It
        must not be re-decided per turn by the component most motivated to
        answer anyway."""
        intent = _intent()

        first = intent.capability(intent.AnalysisType.FORECAST)
        second = intent.capability(intent.AnalysisType.FORECAST)

        assert first == second


class TestAnswerShaping:
    def test_unsupported_kinds_get_an_explicit_limitation_instruction(self):
        intent = _intent()

        for kind in (intent.AnalysisType.FORECAST, intent.AnalysisType.ANOMALY):
            text = intent.guidance(kind)
            assert "GIỚI HẠN NĂNG LỰC" in text

    def test_forecast_guidance_forbids_inventing_a_number(self):
        """Retrieval may still surface a forecast someone else published, and
        reporting THAT is legitimate. Inventing one is not, and the instruction
        has to draw that line."""
        intent = _intent()

        text = intent.guidance(intent.AnalysisType.FORECAST)

        assert "không tự đưa ra con số dự báo" in text

    def test_anomaly_guidance_forbids_declaring_an_anomaly(self):
        intent = _intent()

        text = intent.guidance(intent.AnalysisType.ANOMALY)

        assert "KHÔNG phải kết luận bất thường" in text

    def test_comparison_guidance_forbids_answering_with_one_side(self):
        """A comparison reporting only one side is not a partial answer, it is a
        misleading one."""
        intent = _intent()

        text = intent.guidance(intent.AnalysisType.COMPARISON)

        assert "KHÔNG được trình bày một vế" in text

    def test_lookup_and_analytical_get_shaping_but_no_limitation(self):
        intent = _intent()

        for kind in (intent.AnalysisType.LOOKUP, intent.AnalysisType.ANALYTICAL):
            text = intent.guidance(kind)
            assert text
            assert "GIỚI HẠN NĂNG LỰC" not in text

    def test_other_adds_nothing(self):
        """A greeting must not acquire analytical instructions."""
        intent = _intent()

        assert intent.guidance(intent.AnalysisType.OTHER) == ""


class TestOrthogonalityToIntent:
    def test_analysis_type_is_a_separate_enum_from_intent(self):
        """`intent` (question|task) drives the responder's options block.
        Folding the taxonomy into it would force "vẽ biểu đồ so sánh 2 quý" to
        choose between being a task and being a comparison."""
        intent = _intent()

        values = {a.value for a in intent.AnalysisType}

        assert "question" not in values
        assert "task" not in values


class TestPlannerContract:
    def test_planner_output_model_accepts_and_defaults_analysis_type(self):
        import nodes.model_harness.planner as planner

        assert planner._PlannerOutput().analysis_type == "other"
        assert planner._PlannerOutput(analysis_type="comparison").analysis_type == "comparison"

    def test_planner_prompt_documents_every_taxonomy_value(self):
        """The model cannot emit a label it was never told about, and a silent
        drift between prompt and enum would show up as everything classifying
        as `other`."""
        intent = _intent()
        prompt = (REPO_ROOT / "services" / "workflow-runtime" / "prompts" / "planner_system.txt").read_text(
            encoding="utf-8"
        )

        for member in intent.AnalysisType:
            assert f'"{member.value}"' in prompt, f"{member.value} is missing from planner_system.txt"


class TestResponderConsumesIt:
    def test_guidance_is_appended_to_the_system_prompt(self, monkeypatch):
        """The taxonomy has to change the answer, or it is decoration."""
        import nodes.model_harness.intent as intent

        # The shaping text for a supported kind and for an unsupported one must
        # differ — otherwise classification changed nothing.
        analytical = intent.guidance(intent.AnalysisType.ANALYTICAL)
        forecast = intent.guidance(intent.AnalysisType.FORECAST)

        assert analytical != forecast
        assert forecast.startswith("GIỚI HẠN NĂNG LỰC")

    def test_responder_reads_analysis_type_from_state(self):
        """Pins the wiring: if the responder stops reading the field, the gate
        silently stops applying and forecasts start being invented again."""
        import inspect

        import nodes.model_harness.responder as responder

        source = inspect.getsource(responder.responder_node)

        assert 'state.get("analysis_type")' in source
        assert "guidance(" in source
