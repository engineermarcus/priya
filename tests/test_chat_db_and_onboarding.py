"""
test_chat_db_and_onboarding.py - Unit and integration tests for SQLite chat storage,
/delete command, and onboarding API key management.
"""

import os
import sys
import tempfile
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from chat_db import ChatDB
from env_manager import (
    load_project_env,
    save_api_key,
    get_api_keys_status,
    get_onboarding_instructions,
)
import priya


def test_chat_db_crud_and_isolation(tmp_path):
    db_file = tmp_path / "test_chats.db"
    db = ChatDB(db_path=db_file)

    proj_a = str(tmp_path / "project_a")
    proj_b = str(tmp_path / "project_b")

    # Create chats in two different projects
    c1 = db.create_chat(proj_a, title="Project A Chat 1", model="mistral-medium-latest")
    c2 = db.create_chat(proj_a, title="Project A Chat 2", model="gemini-3.8-flash")
    c3 = db.create_chat(proj_b, title="Project B Chat 1", model="gemini-3.7-flash")

    # Add messages
    db.add_message(c1, "user", "Hello world from A1")
    db.add_message(c1, "assistant", "Hello! How can I assist you?")
    db.add_message(c2, "user", "What is Python?")

    # Verify project isolation
    chats_a = db.get_chats(proj_a)
    assert len(chats_a) == 2
    ids_a = [c["id"] for c in chats_a]
    assert c1 in ids_a and c2 in ids_a
    assert c3 not in ids_a

    chats_b = db.get_chats(proj_b)
    assert len(chats_b) == 1
    assert chats_b[0]["id"] == c3

    # Check message counts
    msg_dict = {c["id"]: c["message_count"] for c in chats_a}
    assert msg_dict[c1] == 2
    assert msg_dict[c2] == 1

    # Check message retrieval
    msgs_1 = db.get_messages(c1)
    assert len(msgs_1) == 2
    assert msgs_1[0]["sender"] == "user"
    assert msgs_1[0]["content"] == "Hello world from A1"
    assert msgs_1[1]["sender"] == "assistant"

    # Auto title update test
    c4 = db.create_chat(proj_a, title="New Chat")
    db.add_message(c4, "user", "Write a fibonacci function in rust\nwith comments")
    chat_info = db.get_chat(c4)
    assert chat_info["title"].startswith("Write a fibonacci function")

    # Update model test
    db.update_chat_model(c4, "gemini-3.7-flash")
    chat_info_updated = db.get_chat(c4)
    assert chat_info_updated["model"] == "gemini-3.7-flash"

    # Delete chat test
    deleted = db.delete_chat(c1)
    assert deleted is True
    assert db.get_chat(c1) is None
    assert len(db.get_messages(c1)) == 0  # Cascade deleted

    chats_a_after = db.get_chats(proj_a)
    assert len(chats_a_after) == 2  # c2 and c4 remain
    assert c1 not in [c["id"] for c in chats_a_after]


def test_env_manager_save_and_load(tmp_path, monkeypatch):
    proj_dir = tmp_path / "env_test_proj"
    proj_dir.mkdir()

    # Clear env vars for test
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    status_empty = get_api_keys_status(str(proj_dir))
    assert status_empty["mistral"] is False
    assert status_empty["gemini"] is False
    assert status_empty["has_any_key"] is False

    # Save Mistral key
    save_api_key(str(proj_dir), "MISTRAL_API_KEY", "mistral_test_secret_12345")
    assert os.environ.get("MISTRAL_API_KEY") == "mistral_test_secret_12345"

    status_1 = get_api_keys_status(str(proj_dir))
    assert status_1["mistral"] is True
    assert status_1["has_any_key"] is True
    assert "mist" in status_1["mistral_masked"]

    # Save Gemini key
    save_api_key(str(proj_dir), "GEMINI_API_KEY", "gemini_test_secret_67890")
    assert os.environ.get("GEMINI_API_KEY") == "gemini_test_secret_67890"

    status_2 = get_api_keys_status(str(proj_dir))
    assert status_2["gemini"] is True
    assert status_2["has_any_key"] is True

    # Test updating existing key in .env
    save_api_key(str(proj_dir), "MISTRAL_API_KEY", "mistral_updated_99999")
    assert os.environ.get("MISTRAL_API_KEY") == "mistral_updated_99999"

    env_content = (proj_dir / ".env").read_text()
    assert "mistral_updated_99999" in env_content
    assert "gemini_test_secret_67890" in env_content
    assert env_content.count("MISTRAL_API_KEY") == 1

    # Test load_project_env in a clean sub-env
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    loaded = load_project_env(str(proj_dir))
    assert loaded.get("MISTRAL_API_KEY") == "mistral_updated_99999"
    assert loaded.get("GEMINI_API_KEY") == "gemini_test_secret_67890"
    assert os.environ.get("MISTRAL_API_KEY") == "mistral_updated_99999"


def test_onboarding_instructions_markdown():
    status = {
        "has_env_file": True,
        "env_path": "/fake/path/.env",
        "mistral": True,
        "mistral_masked": "mist…1234",
        "gemini": False,
        "gemini_masked": "Not set",
        "has_any_key": True,
    }
    md = get_onboarding_instructions(status)
    assert "console.mistral.ai" in md
    assert "aistudio.google.com" in md
    assert "MISTRAL_API_KEY" in md
    assert "GEMINI_API_KEY" in md
    assert "✓ Configured" in md
    assert "✗ Missing" in md


def test_priya_slash_commands_registration():
    cmd_names = [c[0] for c in priya.COMMANDS]
    assert "/delete" in cmd_names
    assert "/onboarding" in cmd_names
    assert "/models" in cmd_names
    assert "/help" in cmd_names


def test_priya_app_delete_chat_flow(tmp_path):
    db_file = tmp_path / "test_app_chats.db"
    proj_dir = str(tmp_path / "mock_project")
    os.makedirs(proj_dir, exist_ok=True)

    with patch.object(priya, "ChatDB", lambda *args, **kwargs: ChatDB(db_path=db_file)):
        app = priya.PriyaApp()
        app.project_dir = proj_dir
        app.chat_db = ChatDB(db_path=db_file)
        app.current_chat_id = app.chat_db.create_chat(proj_dir, title="Chat to Keep")
        c_del = app.chat_db.create_chat(proj_dir, title="Chat to Delete")

        # Call /delete handler
        app._handle_cmd_delete()

        assert app._question_state is not None
        assert app._question_state.get("is_delete_picker") is True
        opts = app._question_state["questions"][0]["options"]
        assert len(opts) == 3  # 2 chats + Cancel

        # Select option for c_del
        del_idx = next(i for i, o in enumerate(opts) if o.get("chat_id") == c_del)
        app._question_state["selected_option"] = del_idx
        del_opt = opts[del_idx]
        assert del_opt["chat_id"] == c_del

        # Simulate user choosing to delete
        app._submit_question_choice()

        # Verify chat was deleted from database
        assert app.chat_db.get_chat(c_del) is None
        assert app.chat_db.get_chat(app.current_chat_id) is not None
        assert app._question_state is None


def test_priya_app_onboarding_flow(tmp_path):
    db_file = tmp_path / "test_app_chats_2.db"
    proj_dir = str(tmp_path / "mock_project_2")
    os.makedirs(proj_dir, exist_ok=True)

    with patch.object(priya, "ChatDB", lambda *args, **kwargs: ChatDB(db_path=db_file)):
        app = priya.PriyaApp()
        app.project_dir = proj_dir

        # Call /onboarding handler
        app._handle_cmd_onboarding()

        assert app._question_state is not None
        assert app._question_state.get("is_onboarding_picker") is True
        opts = app._question_state["questions"][0]["options"]
        assert any(o.get("key_target") == "MISTRAL_API_KEY" for o in opts)
        assert any(o.get("key_target") == "GEMINI_API_KEY" for o in opts)

        # Select Gemini key config
        gemini_idx = next(i for i, o in enumerate(opts) if o.get("key_target") == "GEMINI_API_KEY")
        app._question_state["selected_option"] = gemini_idx

        # Submit choice
        app._submit_question_choice()

        # Should prompt user to enter key
        assert app._pending_key_entry == "GEMINI_API_KEY"
        assert app._question_state is None

        # Simulate user typing new API key and pressing Enter
        app.screen.input_text = "AIzaSy_fake_test_gemini_key_123"
        app._send_line = MagicMock()
        app._do_submit()

        # Verify saved to .env and os.environ
        assert os.environ.get("GEMINI_API_KEY") == "AIzaSy_fake_test_gemini_key_123"
        env_content = (Path(proj_dir) / ".env").read_text()
        assert "AIzaSy_fake_test_gemini_key_123" in env_content
        # Verify <<SET_KEYS>> was sent to worker
        app._send_line.assert_called_with('<<SET_KEYS>>{"GEMINI_API_KEY": "AIzaSy_fake_test_gemini_key_123"}')
        assert app._pending_key_entry is None


def test_command_list_scrolling_when_only_slash_entered():
    app = priya.PriyaApp()
    app.screen.input_text = "/"
    app.screen.input_cursor = 1

    total_cmds = len(priya.COMMANDS)
    assert total_cmds >= 10

    # User presses DOWN arrow repeatedly across the entire list
    for step in range(total_cmds + 2):
        app._handle_key("DOWN")
        assert app.suggestion_index == (step + 1) % total_cmds

    # User presses UP arrow repeatedly
    for _ in range(3):
        cur = app.suggestion_index
        app._handle_key("UP")
        assert app.suggestion_index == (cur - 1) % total_cmds

    # User uses WHEEL_DOWN and WHEEL_UP
    cur = app.suggestion_index
    app._handle_key("WHEEL_DOWN")
    assert app.suggestion_index == (cur + 1) % total_cmds
    app._handle_key("WHEEL_UP")
    assert app.suggestion_index == cur

    # User presses RIGHT arrow to autocomplete
    app.suggestion_index = 0
    expected_first = priya.COMMANDS[0][0]
    app._handle_key("RIGHT")
    assert app.screen.input_text == expected_first


def test_onboarding_instructions_has_urls_and_shell_exports():
    status = {
        "has_env_file": False,
        "env_path": "/fake/path/.env",
        "mistral": False,
        "mistral_masked": "Not set",
        "gemini": False,
        "gemini_masked": "Not set",
        "has_any_key": False,
    }
    instr = get_onboarding_instructions(status)
    # Check explicit URLs
    assert "https://console.mistral.ai/" in instr
    assert "https://aistudio.google.com/" in instr

    # Check export instructions
    assert 'export MISTRAL_API_KEY="your-mistral-api-key"' in instr
    assert 'export GEMINI_API_KEY="your-gemini-api-key"' in instr

    # Check permanence in .bashrc and .zshrc
    assert "~/.bashrc" in instr
    assert "~/.zshrc" in instr
    assert "source ~/.bashrc" in instr
    assert "source ~/.zshrc" in instr

