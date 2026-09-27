"""The serving benchmark (model/bench).

What is worth pinning here is not that it produces numbers — that needs a GPU —
but that the numbers mean what the report says they mean:

  * the profiles are built from the REAL prompt files and the REAL call-site
    limits, so a profile that silently fell back to a placeholder is a failure
    rather than a slightly-off measurement;
  * decode speed excludes the first token, because the first token is prefill
    and counting it would blame the decoder for a long prompt;
  * the prefix-cache hit rate is a delta across the scenario, not the server's
    lifetime ratio, which is the difference between measuring this run and
    measuring whatever ran before it.
"""
from __future__ import annotations

import json

import pytest

from model.bench import client as bench_client
from model.bench import e2e as bench_e2e
from model.bench import gate as bench_gate
from model.bench import profiles as bench_profiles
from model.bench import run as bench_run
from model.bench import server_metrics

pytestmark = pytest.mark.unit


# ── profiles ───────────────────────────────────────────────────────────────

def test_every_profile_loads_a_real_prompt_and_real_limits():
    loaded = bench_profiles.load()
    assert set(loaded) == {
        "guardrail", "planner", "judge", "text_to_sql", "responder", "orchestrator-planner",
    }
    for name, profile in loaded.items():
        assert profile.system.strip(), f"{name} has an empty system prompt"
        assert profile.user_turns, f"{name} has no user turns"
        assert profile.max_tokens > 0
        assert profile.tier in ("main", "fast")


@pytest.mark.parametrize(
    ("name", "max_tokens", "temperature"),
    [
        # The numbers each call site passes to litellm. If one of these changes
        # in the service, the benchmark is measuring a workload the product no
        # longer sends — so this test is the alarm, not documentation.
        ("guardrail", 64, 0.0),
        ("planner", 256, 0.0),
        ("judge", 128, 0.0),
        ("text_to_sql", 512, 0.0),
        ("responder", 1000, 0.2),
    ],
)
def test_profile_limits_match_the_call_sites(name, max_tokens, temperature):
    profile = bench_profiles.load()[name]
    assert (profile.max_tokens, profile.temperature) == (max_tokens, temperature)


def test_user_turns_rotate_but_the_system_prefix_does_not():
    """The prefix-cache hit rate the report prints is only honest if the
    benchmark varies what production varies and holds fixed what it holds."""
    profile = bench_profiles.load()["planner"]
    first, second = profile.messages(0), profile.messages(1)
    assert first[0]["content"] == second[0]["content"]
    assert first[1]["content"] != second[1]["content"]
    # And it wraps rather than running off the end.
    assert profile.messages(len(profile.user_turns))[1] == first[1]


def test_derived_schema_covers_the_columns_the_sql_corpus_asserts_on():
    schema = bench_profiles._schema_text()
    for fragment in ("CREATE TABLE orders", "amount numeric", "status text"):
        assert fragment in schema


def test_prompts_are_built_at_the_caps_the_code_enforces():
    """Below the cap the measurement says nothing about the request that
    finally reaches it — and both caps are constants in the service, so a
    profile drifting under them is the bug this catches."""
    assert len(bench_profiles._schema_text()) <= bench_profiles.MAX_SCHEMA_CHARS
    # Within 10% of the ceiling: the point is to measure a full prompt.
    assert len(bench_profiles._schema_text()) > bench_profiles.MAX_SCHEMA_CHARS * 0.9

    turn = bench_profiles.load()["responder"].messages(0)[1]["content"]
    assert turn.count("] source=") == bench_profiles.CONTEXT_MAX_CHUNKS
    assert len(turn) > bench_profiles.CONTEXT_MAX_CHARS * 0.9


# ── timing ─────────────────────────────────────────────────────────────────

def test_decode_speed_excludes_the_first_token():
    # 101 tokens, first at 1000ms, done at 2000ms: 100 tokens in 1.0s.
    timing = bench_client.Timing(ttft_ms=1000.0, total_ms=2000.0,
                                 prompt_tokens=10, completion_tokens=101)
    assert timing.decode_tps == pytest.approx(100.0)


def test_decode_speed_is_unknowable_from_a_single_token():
    timing = bench_client.Timing(ttft_ms=500.0, total_ms=600.0,
                                 prompt_tokens=10, completion_tokens=1)
    assert timing.decode_tps is None


def test_a_failed_request_reports_no_timings():
    timing = bench_client.Timing(None, 12.0, 0, 0, error="HTTP 503")
    assert not timing.ok
    assert timing.decode_tps is None


# ── percentiles ────────────────────────────────────────────────────────────

def test_percentile_is_nearest_rank_so_every_value_was_observed():
    values = [float(v) for v in range(1, 11)]
    assert bench_run._percentile(values, 0.50) == 5.0
    assert bench_run._percentile(values, 0.95) == 10.0
    assert bench_run._percentile([], 0.5) is None


# ── server metrics ─────────────────────────────────────────────────────────

_SAMPLE = """\
# HELP vllm:prefix_cache_queries_total Prefix cache queries
vllm:prefix_cache_queries_total{engine="0",model_name="agent-main"} 1000.0
vllm:prefix_cache_hits_total{engine="0",model_name="agent-main"} 400.0
vllm:num_preemptions_total{engine="0",model_name="agent-main"} 0.0
vllm:kv_cache_usage_perc{engine="0",model_name="agent-main"} 0.25
vllm:prompt_tokens_total{engine="0",model_name="agent-main"} 5000.0
vllm:generation_tokens_total{engine="0",model_name="agent-main"} 2000.0
"""


def test_metrics_url_is_derived_from_the_openai_base():
    assert server_metrics.metrics_url("http://gpu:8001/v1") == "http://gpu:8001/metrics"
    assert server_metrics.metrics_url("http://gpu:8001/") == "http://gpu:8001/metrics"


def test_hit_rate_is_the_scenario_delta_not_the_lifetime_ratio():
    before = server_metrics.parse(_SAMPLE)
    after = server_metrics.parse(
        _SAMPLE.replace("queries_total{engine=\"0\",model_name=\"agent-main\"} 1000.0",
                        "queries_total{engine=\"0\",model_name=\"agent-main\"} 1200.0")
               .replace("hits_total{engine=\"0\",model_name=\"agent-main\"} 400.0",
                        "hits_total{engine=\"0\",model_name=\"agent-main\"} 600.0")
    )
    moved = server_metrics.delta(before, after)
    # Lifetime would be 600/1200 = 0.50; this scenario hit 200 of 200 queried.
    assert moved["prefix_cache_hit_rate"] == 1.0
    assert moved["prefix_cache_queried_tokens"] == 200


def test_preemptions_are_reported_so_a_saturated_run_is_not_read_as_latency():
    before = server_metrics.parse(_SAMPLE)
    after = server_metrics.parse(_SAMPLE.replace("num_preemptions_total{engine=\"0\","
                                                 "model_name=\"agent-main\"} 0.0",
                                                 "num_preemptions_total{engine=\"0\","
                                                 "model_name=\"agent-main\"} 7.0"))
    assert server_metrics.delta(before, after)["preemptions"] == 7


def test_in_flight_traffic_is_visible_in_the_gauges_a_scenario_reads():
    """`_scenario` flags contention from these two gauges. If the parser stopped
    returning them the flag would silently never fire, and a report measured
    against the live stack would read as a clean one."""
    parsed = server_metrics.parse(_SAMPLE + 'vllm:num_requests_running{engine="0"} 3.0\n')
    assert parsed["vllm:num_requests_running"] == 3.0
    assert "vllm:num_requests_waiting" not in parsed  # absent, not defaulted to zero


def test_a_server_without_metrics_yields_no_server_section():
    """An unreachable /metrics must not become a zero: "we could not measure the
    cache" and "the cache missed everything" are different states."""
    assert server_metrics.delta(None, server_metrics.parse(_SAMPLE)) is None
    assert server_metrics.delta(server_metrics.parse(_SAMPLE), None) is None


# ── the serving gate ───────────────────────────────────────────────────────
#
# Same principle as tests/unit/workflow_runtime/test_eval_gate.py: what is worth pinning is not
# that the gate passes a good report, it is that it REFUSES a bad one. A gate
# that only ever passes is indistinguishable from no gate, and on a serving
# benchmark the way that happens is not a wrong bound — it is a number that
# silently was not measured.

_THRESHOLDS = {
    "profiles": {
        "planner": {
            "prompt_tokens_max": 2000,
            "decode_tps_min": 190,
            "prefix_cache_hit_rate_min": 0.95,
            "_why": "ignored — underscore keys are commentary",
        },
    },
    "every_scenario": {"errors_max": 0, "preemptions_max": 0},
}


def _report(**overrides):
    scenario = {
        "profile": "planner", "concurrency": 1, "requests": 8, "errors": 0,
        "prompt_tokens_p50": 1895.0, "decode_tps": {"p50": 213.6},
        "server": {"prefix_cache_hit_rate": 0.99, "preemptions": 0},
    }
    scenario.update(overrides.pop("scenario", {}))
    report = {"target": "direct", "force_output": True, "scenarios": [scenario]}
    report.update(overrides)
    return report


def _states(report, thresholds=None, at=1):
    verdicts = bench_gate.check(report, thresholds or _THRESHOLDS, at)
    return {v.label: v.state for v in verdicts}


def test_a_baseline_shaped_report_passes():
    assert set(_states(_report()).values()) == {"ok"}


def test_commentary_keys_are_not_mistaken_for_budgets():
    """thresholds.json carries its reasoning inline. An underscore key read as a
    bound would be a budget nobody wrote."""
    assert "planner · _why" not in _states(_report())


@pytest.mark.parametrize(
    ("label", "scenario"),
    [
        ("planner · prompt_tokens", {"prompt_tokens_p50": 2400.0}),
        ("planner · decode_tps", {"decode_tps": {"p50": 90.0}}),
        ("planner · prefix_cache", {"server": {"prefix_cache_hit_rate": 0.40, "preemptions": 0}}),
    ],
)
def test_each_budget_fails_when_it_is_missed(label, scenario):
    assert _states(_report(scenario=scenario))[label] == "FAIL"


def test_a_failed_request_fails_the_run_it_was_measured_in():
    """Six of eight requests measured a different workload than eight of eight."""
    states = _states(_report(scenario={"errors": 2}))
    assert states["errors · planner @ c1"] == "FAIL"


def test_preemption_fails_because_the_whole_scenario_measured_a_saturated_server():
    states = _states(_report(scenario={
        "server": {"prefix_cache_hit_rate": 0.99, "preemptions": 3},
    }))
    assert states["preemption · planner @ c1"] == "FAIL"


def test_preemption_at_any_concurrency_fails_not_just_the_judged_one():
    report = _report()
    report["scenarios"].append({
        "profile": "planner", "concurrency": 8, "requests": 8, "errors": 0,
        "prompt_tokens_p50": 1895.0, "decode_tps": {"p50": 180.0},
        "server": {"prefix_cache_hit_rate": 0.99, "preemptions": 5},
    })
    assert _states(report)["preemption · planner @ c8"] == "FAIL"


def test_somebody_elses_traffic_is_inconclusive_not_a_pass():
    """This GPU also serves the deployed stack. Certifying a scenario measured
    around another request would certify their latency as ours."""
    states = _states(_report(scenario={"contended": 2}))
    assert states["planner"] == "INCONCLUSIVE"


def test_a_natural_run_cannot_judge_decode_speed():
    states = _states(_report(force_output=False))
    assert states["planner · decode_tps"] == "INCONCLUSIVE"
    # …but the deterministic budget is still judged.
    assert states["planner · prompt_tokens"] == "ok"


def test_a_report_without_server_metrics_cannot_judge_the_cache():
    states = _states(_report(scenario={"server": None}))
    assert states["planner · prefix_cache"] == "INCONCLUSIVE"


def test_a_budgeted_profile_that_was_not_measured_fails():
    """'We did not measure it' and 'it was fine' are different states, and only
    one of them is a reason to ship."""
    assert _states(_report(), at=4)["planner"] == "FAIL"


def test_the_shipped_thresholds_pass_the_baseline_they_were_derived_from():
    """If this goes red, either a bound was tightened past its own evidence or
    the recorded baseline was replaced without re-deriving the bounds."""
    baseline = json.loads(
        (bench_profiles.REPO_ROOT / "model" / "bench" / "baselines"
         / "2026-09-22-a100-40gb-decode.json").read_text(encoding="utf-8")
    )
    verdicts = bench_gate.check(baseline, bench_gate.load_thresholds(), 1)
    assert [v.label for v in verdicts if v.state != "ok"] == []


# ── end to end ─────────────────────────────────────────────────────────────

def test_e2e_refuses_to_run_without_a_target_rather_than_inventing_one():
    """There is no offline mode that would mean anything: the whole point is
    the deployed pipeline. Failing loudly beats reporting a shape."""
    with pytest.raises(bench_e2e.E2EError) as caught:
        bench_e2e._settings()
    assert "BENCH_AGENT_URL" in str(caught.value)


def test_e2e_names_the_credential_it_needs_when_the_endpoint_is_configured(monkeypatch):
    monkeypatch.setenv("BENCH_AGENT_URL", "http://agent:8240")
    monkeypatch.setenv("BENCH_AGENT_ID", "a")
    monkeypatch.setenv("BENCH_TENANT_ID", "t")
    monkeypatch.delenv("GATEWAY_KEY", raising=False)
    monkeypatch.delenv("KB_GATEWAY_KEY", raising=False)
    with pytest.raises(bench_e2e.E2EError, match="GATEWAY_KEY"):
        bench_e2e._settings()


def test_e2e_does_not_send_an_invented_conversation_id():
    """The runtime discards a conversation id the caller does not already own
    and starts a fresh one (conversation_runner.py). Sending one made the first
    real run read traces for a conversation that never existed and report every
    millisecond as unaccounted — so the id is found afterwards, by user_ref."""
    source = (bench_profiles.REPO_ROOT / "model" / "bench" / "e2e.py").read_text(encoding="utf-8")
    ask_body = source[source.index("async def ask("):source.index("async def find_conversation(")]
    assert '"conversation_id"' not in ask_body


def _turn(total_ms, stages, error=None):
    return {"total_ms": total_ms, "ttft_ms": 100.0, "error": error,
            "trace_fetch": "ok",
            "stages": [{"stage": n, "latency_ms": ms} for n, ms in stages]}


def test_unaccounted_time_is_the_wall_clock_no_stage_claims():
    """The number that decides whether to work on the stages or on what is
    around them. Getting it from subtraction rather than from a stage means it
    cannot be hidden by a stage that forgot to record itself."""
    summary = bench_e2e._summarise([
        _turn(1000.0, [("planner", 200), ("kb_search", 300)]),
        _turn(1000.0, [("planner", 200), ("kb_search", 300)]),
    ])
    assert summary["unaccounted_ms_p50"] == 500.0
    assert summary["total_ms_p50"] == 1000.0


def test_a_stage_called_twice_a_turn_is_reported_as_twice():
    """Sequential repeats of one stage are the cascade question: a turn making
    three planner calls is a different latency budget from one making one."""
    summary = bench_e2e._summarise([
        _turn(900.0, [("text_to_sql", 100), ("text_to_sql", 150), ("planner", 50)]),
    ])
    assert summary["stages"]["text_to_sql"]["calls_per_turn"] == 2.0
    assert summary["stages"]["planner"]["calls_per_turn"] == 1.0


def test_a_failed_turn_is_counted_but_kept_out_of_the_unaccounted_median():
    """A turn that errored halfway has a wall clock that means nothing, and
    averaging it in would move the one number this report is read for."""
    summary = bench_e2e._summarise([
        _turn(1000.0, [("planner", 400)]),
        _turn(30.0, [], error="HTTP 500"),
    ])
    assert summary["errors"] == 1
    assert summary["unaccounted_ms_p50"] == 600.0


def test_a_stage_with_no_recorded_latency_does_not_become_zero():
    summary = bench_e2e._summarise([
        {"total_ms": 500.0, "ttft_ms": None, "error": None, "trace_fetch": "ok",
         "stages": [{"stage": "rerank", "latency_ms": None}]},
    ])
    assert "rerank" not in summary["stages"]
    assert summary["unaccounted_ms_p50"] == 500.0
