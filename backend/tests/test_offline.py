"""Record and playback: the pipeline must run from recorded responses with
zero live calls. This is what offline mode relies on."""

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from fake_model import FakeClient
from fastapi.testclient import TestClient
from test_api import make_settings, wait

from agent_replay import demo
from agent_replay.api.app import create_app
from agent_replay.llm import (
    OfflineMiss,
    PlaybackClient,
    RecordingClient,
    make_client,
    request_key,
    response_from_dict,
    response_to_dict,
)
from agent_replay.tracing import TraceRecorder

RECORDED = Path(__file__).parent / "fixtures" / "recorded"


def test_response_round_trip() -> None:
    data = {
        "content": None,
        "tool_calls": [{"id": "c1", "name": "search_sections", "arguments": '{"course_code": "QCS 240"}'}],
        "prompt_tokens": 12,
        "completion_tokens": 3,
    }
    assert response_to_dict(response_from_dict(data)) == data
    final = {"content": "done", "tool_calls": [], "prompt_tokens": 1, "completion_tokens": 1}
    assert response_from_dict(final).choices[0].message.tool_calls is None


def test_request_key_depends_on_what_the_model_sees() -> None:
    base = {"model": "m", "messages": [{"role": "user", "content": "a"}], "tools": []}
    assert request_key(base) == request_key({**base, "tools": [{"x": 1}]})
    assert request_key(base) != request_key({**base, "model": "other"})
    assert request_key(base) != request_key({**base, "messages": [{"role": "user", "content": "b"}]})


def test_playback_serves_recordings_in_order_and_misses_cleanly(tmp_path: Path) -> None:
    class Varying(FakeClient):
        def create(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            return response_from_dict(
                {
                    "content": f"answer {len(self.calls)}",
                    "tool_calls": [],
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                }
            )

    request = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    recording = RecordingClient(Varying(), tmp_path)
    recording.chat.completions.create(**request)
    recording.chat.completions.create(**request)
    playback = PlaybackClient(tmp_path)
    answers = [playback.chat.completions.create(**request).choices[0].message.content for _ in range(3)]
    # The same request was answered twice. Both answers come back, then it wraps.
    assert answers == ["answer 1", "answer 2", "answer 1"]
    with pytest.raises(OfflineMiss):
        playback.chat.completions.create(model="m", messages=[{"role": "user", "content": "new"}])


def test_whole_demo_flow_replays_with_no_live_calls(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, recordings_dir=tmp_path / "rec")
    live = FakeClient()
    first = demo.walk_demo_flow(
        RecordingClient(live, settings.recordings_dir),
        TraceRecorder(tmp_path / "a.db"),
        settings,
        "scenario_01",
    )
    assert first["original"] == "failed" and first["fix_at_true_step"] == "passed" and live.calls
    calls_made = len(live.calls)
    replayed = demo.walk_demo_flow(
        PlaybackClient(settings.recordings_dir), TraceRecorder(tmp_path / "b.db"), settings, "scenario_01"
    )
    assert replayed == first and len(live.calls) == calls_made


def test_offline_api_plays_the_recorded_flow_and_explains_a_miss(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, recordings_dir=tmp_path / "rec")
    demo.walk_demo_flow(
        RecordingClient(FakeClient(), settings.recordings_dir),
        TraceRecorder(tmp_path / "rec.db"),
        settings,
        "scenario_01",
    )
    offline = dataclasses.replace(settings, offline=True)
    api = TestClient(create_app(offline))
    assert api.get("/api/health").json()["mode"] == "offline"
    run = wait(api, api.post("/api/runs", json={"scenario_id": "scenario_01"}).json()["run_id"])
    assert run["status"] == "failed"
    judged = api.post(f"/api/runs/{run['id']}/localize", json={"method": "llm"})
    assert judged.status_code == 200 and judged.json()["predicted_step"] == 3
    fix = {"student_id": "stu_01", "course_code": "QCS 340"}
    body = {"step": 3, "mode": "forced_action", "tool_name": "check_prerequisite", "arguments": fix}
    fixed = wait(api, api.post(f"/api/runs/{run['id']}/resume", json=body).json()["run_id"])
    assert fixed["status"] == "passed"
    # A scenario that was never recorded cannot run offline, and says so.
    other = wait(api, api.post("/api/runs", json={"scenario_id": "scenario_08"}).json()["run_id"])
    assert (other["status"], other["stop_reason"]) == ("error", "offline_unavailable")


def test_make_client_needs_a_key_unless_offline(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        make_client(make_settings(tmp_path))
    assert isinstance(make_client(make_settings(tmp_path, offline=True)), PlaybackClient)
    real = make_client(make_settings(tmp_path, openai_api_key="sk-test-not-real"), record_to=tmp_path / "r")
    assert isinstance(real, RecordingClient)


@pytest.mark.skipif(not RECORDED.is_dir(), reason="no real OpenAI responses have been recorded yet")
def test_pipeline_from_real_recorded_responses(tmp_path: Path) -> None:
    """Runs scenario_01 end to end from real recorded responses. Record them
    once with: RECORDINGS_DIR=backend/tests/fixtures/recorded python -m agent_replay.demo record scenario_01"""
    meta = json.loads((RECORDED / "meta.json").read_text())
    settings = make_settings(
        tmp_path, recordings_dir=RECORDED, openai_model=meta["openai_model"], judge_model=meta["judge_model"]
    )
    outcome = demo.walk_demo_flow(
        PlaybackClient(RECORDED), TraceRecorder(tmp_path / "r.db"), settings, "scenario_01"
    )
    assert outcome["original"] in {"failed", "passed"}


def test_preflight_reports_each_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    for name in ("MAX_TOKENS_PER_RUN", "MAX_STEPS_PER_RUN", "DAILY_TOKEN_LIMIT", "PRODUCTION_URL"):
        monkeypatch.delenv(name, raising=False)
    settings = make_settings(tmp_path, recordings_dir=tmp_path / "rec", demo_scenarios=("scenario_01",))
    assert demo.preflight(settings) == 1
    out = capsys.readouterr().out
    assert out.count("FAIL") == 5 and "PASS  Demo scenarios load: scenario_01" in out
    assert "1 of 6 checks passed" in out

    demo.walk_demo_flow(
        RecordingClient(FakeClient(), settings.recordings_dir),
        TraceRecorder(tmp_path / "rec.db"),
        settings,
        "scenario_01",
    )
    frozen = settings.results_dir / "frozen"
    frozen.mkdir(parents=True)
    (frozen / "results.json").write_text(
        json.dumps({"run_count": 3, "frozen": {"version": "v1", "frozen_on": "2026-11-28"}})
    )
    for name in ("MAX_TOKENS_PER_RUN", "MAX_STEPS_PER_RUN", "DAILY_TOKEN_LIMIT"):
        monkeypatch.setenv(name, "1000")
    assert demo.preflight(settings) == 1
    out = capsys.readouterr().out
    assert (
        "PASS  Offline mode works" in out
        and "PASS  Frozen results are present: v1 frozen on 2026-11-28" in out
    )
    assert "PASS  Token limits are set" in out and "4 of 6 checks passed" in out
    assert "FAIL  API key works: RuntimeError: OPENAI_API_KEY is not set" in out
