import pytest
import asyncio
import json
import os
import tempfile
from pathlib import Path
from tools.swarm import SWARM, AgentSwarmManager
from live_cli import TextLoop, TOOLS, GEMINI_TOOLS
from priya import Screen, PriyaApp, COMMANDS


def test_tool_registration_counts():
    """Verify all 46 tools are registered in Mistral and Gemini schemas."""
    assert len(TOOLS) == 46, f"Expected 46 tools in TOOLS, got {len(TOOLS)}"
    assert len(GEMINI_TOOLS) == 46, f"Expected 46 tools in GEMINI_TOOLS, got {len(GEMINI_TOOLS)}"

    tool_names = {t["function"]["name"] for t in TOOLS}
    swarm_tools = {"spawn_agent", "send_input", "wait_agent", "close_agent", "resume_agent", "spawn_agents_on_csv"}
    for st in swarm_tools:
        assert st in tool_names, f"Swarm tool {st} missing from live_cli.TOOLS"

    gemini_names = {g["name"] for g in GEMINI_TOOLS}
    for st in swarm_tools:
        assert st in gemini_names, f"Swarm tool {st} missing from live_cli.GEMINI_TOOLS"


def test_swarm_agent_lifecycle():
    """Test full lifecycle of background swarm agent: spawn -> send_input -> wait -> close -> resume."""
    async def _run():
        loop = TextLoop()

        # 1. Spawn agent
        spawn_res = await loop.execute_tool("spawn_agent", {
            "role": "code_reviewer",
            "prompt": "Verify security best practices for API endpoints."
        })
        _, s_out = spawn_res
        assert "agent_id" in s_out
        assert s_out["status"] == "running"
        aid = s_out["agent_id"]

        # 2. Send input
        _, i_out = await loop.execute_tool("send_input", {
            "agent_id": aid,
            "message": "Focus particularly on authentication headers."
        })
        assert i_out.get("delivered") is True

        # 3. Wait agent
        _, w_out = await loop.execute_tool("wait_agent", {
            "agent_id": aid,
            "timeout_s": 5
        })
        assert "status" in w_out
        assert "logs" in w_out

        # 4. Close agent
        _, c_out = await loop.execute_tool("close_agent", {"agent_id": aid})
        assert c_out["status"] == "closed"

        # 5. Resume agent
        _, r_out = await loop.execute_tool("resume_agent", {
            "agent_id": aid,
            "additional_prompt": "Continue audit on auth middleware."
        })
        assert r_out["status"] == "running"
        assert r_out.get("resumed") is True

    asyncio.run(_run())


def test_spawn_agents_on_csv():
    """Test batch spawning swarm agents on CSV dataset."""
    async def _run():
        loop = TextLoop()
        with tempfile.NamedTemporaryFile("w+", suffix=".csv", delete=False) as f:
            f.write("endpoint,method\n/login,POST\n/users,GET\n/admin,DELETE\n")
            f.flush()
            csv_file = f.name

        try:
            _, res = await loop.execute_tool("spawn_agents_on_csv", {
                "csv_path": csv_file,
                "prompt_template": "Test {method} on {endpoint}",
                "role": "endpoint_tester",
                "concurrency": 2
            })
            assert res["total_rows"] == 3
            assert res["agents_spawned"] == 3
            assert len(res["agent_ids"]) == 3
            assert res["status"] == "spawned"
        finally:
            os.remove(csv_file)

    asyncio.run(_run())


def test_model_switching_command_handling():
    """Verify live_cli correctly configures active_model and thinking budget."""
    loop = TextLoop()
    assert loop._active_model == "mistral-medium-latest"
    assert loop._gemini_thinking_budget == 4096

    # Simulate <<SET_MODEL>> payload for Gemini low effort
    payload_gemini_low = json.dumps({"model": "gemini-3.8-flash", "effort": "low", "budget": 1024})
    data = json.loads(payload_gemini_low)
    loop._active_model = data["model"]
    loop._gemini_thinking_budget = data["budget"]
    assert loop._active_model == "gemini-3.8-flash"
    assert loop._gemini_thinking_budget == 1024

    # Simulate <<SET_MODEL>> payload for Gemini high effort
    payload_gemini_high = json.dumps({"model": "gemini-3.8-flash", "effort": "high", "budget": 16384})
    data = json.loads(payload_gemini_high)
    loop._active_model = data["model"]
    loop._gemini_thinking_budget = data["budget"]
    assert loop._active_model == "gemini-3.8-flash"
    assert loop._gemini_thinking_budget == 16384

    # Switch back to mistral
    payload_mistral = json.dumps({"model": "mistral-medium-latest"})
    data = json.loads(payload_mistral)
    loop._active_model = data["model"]
    assert loop._active_model == "mistral-medium-latest"


def test_priya_models_interactive_flow():
    """Verify /models interactive flow in PriyaApp: unified model + effort selection and badge updates."""
    app = PriyaApp()
    assert app.active_model == "mistral-medium-latest"
    assert app.model_badge == "mistral-medium"

    # User invokes /models
    app._handle_cmd_models()
    assert app._question_state is not None
    assert app._question_state["is_model_picker"] is True
    assert app._question_state["step"] == 1
    options = app._question_state["questions"][0]["options"]
    assert len(options) == 5
    assert "mistral-medium-latest" in options[0]["label"]
    assert "Gemini 3.8 Flash (medium" in options[1]["label"]
    assert "Gemini 3.8 Flash (low" in options[2]["label"]
    assert "Gemini 3.8 Flash (high" in options[3]["label"]
    assert "Skip" in options[4]["label"]

    # User picks Gemini 3.8 Flash (medium - Recommended)
    app._submit_question_answer("Gemini 3.8 Flash (medium - Recommended)")
    assert app._question_state is None
    assert app.active_model == "gemini-3.8-flash"
    assert app.model_effort == "medium"
    assert app.model_badge == "gemini-3.8-flash (medium)"
    assert app.screen.model_badge == "gemini-3.8-flash (medium)"

    # User invokes /models again and picks mistral -> switches immediately
    app._handle_cmd_models()
    assert app._question_state["step"] == 1
    app._submit_question_answer("mistral-medium-latest")
    assert app._question_state is None
    assert app.active_model == "mistral-medium-latest"
    assert app.model_badge == "mistral-medium"
    assert app.screen.model_badge == "mistral-medium"


def test_priya_model_commands_in_help():
    """Verify /models and /model are registered in COMMANDS."""
    cmd_dict = dict(COMMANDS)
    assert "/model" in cmd_dict
    assert "/models" in cmd_dict
