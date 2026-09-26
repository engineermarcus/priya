import unittest
from unittest.mock import MagicMock
import json
import asyncio
import priya

class TestAskUserQuestion(unittest.TestCase):
    def setUp(self):
        self.app = priya.PriyaApp.__new__(priya.PriyaApp)
        self.app.history = []
        self.app.history_index = -1
        self.app.saved_input = ""
        self.app._question_state = None
        self.app._edit_state = None
        self.app._busy = True
        self.app._interrupted = False
        self.app._running = True
        self.app.suggestion_index = 0
        self.app.active_model = "mistral-medium-latest"
        self.app.model_effort = "medium"
        self.app.model_badge = "mistral-medium"
        self.app._stdin_lock = priya.threading.Lock()
        self.app.proc = MagicMock()
        self.app.proc.stdin = MagicMock()

        screen = priya.Screen.__new__(priya.Screen)
        screen.scroll_offset = 0
        screen._convo_h = 20
        screen._w = 80
        screen.input_text = ""
        screen.input_cursor = 0
        screen.status_text = ""
        screen.status_color = priya.C_STATUS
        screen._question_state = None
        screen._edit_state = None
        screen.suggestion_index = 0
        screen.busy = True
        screen._lock = priya.threading.RLock()
        screen.cur_turn = priya.Turn("test prompt")
        screen.set_question = MagicMock(side_effect=lambda q: setattr(screen, "_question_state", q))
        screen.set_status = MagicMock(side_effect=lambda t, c: setattr(screen, "status_text", t))
        screen.set_model_badge = MagicMock(side_effect=lambda b: setattr(screen, "model_badge", b))
        screen.new_turn = MagicMock(side_effect=lambda p, is_system=False: priya.Turn(p, is_system=is_system))
        screen.end_turn = MagicMock()
        screen.input_clear = MagicMock(side_effect=lambda: setattr(screen, "input_text", ""))
        screen.input_insert = MagicMock(side_effect=lambda ch: setattr(screen, "input_text", screen.input_text + ch))
        screen.append_tool_log = MagicMock()
        screen.redraw = MagicMock()

        self.app.screen = screen

    def test_single_question_pick_option(self):
        # Setup question state
        self.app._question_state = {
            "id": 1,
            "questions": [{
                "header": "Choice",
                "question": "Choose A or B",
                "options": [
                    {"label": "A", "description": "Option A"},
                    {"label": "B", "description": "Option B"},
                ]
            }],
            "answers": [],
            "index": 0,
            "selected_option": 0,
        }

        # User presses 1 to pick Option A
        self.app._handle_key("1")

        # Verify question state was cleared and answer sent
        self.assertIsNone(self.app._question_state)
        self.app.proc.stdin.write.assert_called_once()
        written = self.app.proc.stdin.write.call_args[0][0]
        self.assertTrue(written.startswith("<<ASK_USER_ANSWER>>"))
        payload = json.loads(written[len("<<ASK_USER_ANSWER>>"):])
        self.assertEqual(payload["id"], 1)
        self.assertEqual(payload["answers"], ["A"])

    def test_cancel_question_via_esc(self):
        self.app._question_state = {
            "id": 42,
            "questions": [{
                "question": "Do you want this?",
                "options": [{"label": "Yes"}, {"label": "No"}]
            }],
            "answers": [],
            "index": 0,
            "selected_option": 0,
        }

        self.app._handle_key("ESC")

        self.assertIsNone(self.app._question_state)
        written = self.app.proc.stdin.write.call_args[0][0]
        self.assertTrue(written.startswith("<<ASK_USER_ANSWER>>"))
        payload = json.loads(written[len("<<ASK_USER_ANSWER>>"):])
        self.assertEqual(payload["id"], 42)
        self.assertTrue(payload["cancelled"])

    def test_dropdown_iterate_and_enter_select(self):
        self.app._question_state = {
            "id": 10,
            "questions": [{
                "header": "Options",
                "question": "Choose an option",
                "options": [
                    {"label": "Opt 1", "description": "Desc 1"},
                    {"label": "Opt 2", "description": "Desc 2"},
                    {"label": "Opt 3", "description": "Desc 3"},
                ]
            }],
            "answers": [],
            "index": 0,
            "selected_option": 0,
        }

        # Press DOWN to highlight Opt 2
        self.app._handle_key("DOWN")
        self.assertEqual(self.app._question_state["selected_option"], 1)

        # Press ENTER to complete/select Opt 2
        self.app._handle_key("ENTER")
        self.assertIsNone(self.app._question_state)

        written = self.app.proc.stdin.write.call_args[0][0]
        self.assertTrue(written.startswith("<<ASK_USER_ANSWER>>"))
        payload = json.loads(written[len("<<ASK_USER_ANSWER>>"):])
        self.assertEqual(payload["id"], 10)
        self.assertEqual(payload["answers"], ["Opt 2"])

    def test_slash_command_dropdown_enter_completes_and_runs(self):
        # User types "/m"
        self.app.screen.input_text = ""
        self.app.screen.input_cursor = 0
        self.app._handle_key("/")
        self.app._handle_key("m")
        self.assertEqual(self.app.screen.input_text, "/m")

        # Press ENTER - should complete to /models and open model picker
        self.app._handle_key("ENTER")
        self.assertIsNotNone(self.app._question_state)
        self.assertEqual(self.app._question_state.get("id"), "model_select_1")

    def test_model_picker_full_flow_with_arrows_and_enter(self):
        # Open models picker
        self.app._handle_cmd_models()
        self.assertIsNotNone(self.app._question_state)
        self.assertEqual(self.app._question_state["step"], 1)

        # Arrow down to Gemini 3.8 Flash (high) (index 3)
        self.app._handle_key("DOWN")
        self.assertEqual(self.app._question_state["selected_option"], 1)
        self.app._handle_key("DOWN")
        self.assertEqual(self.app._question_state["selected_option"], 2)
        self.app._handle_key("DOWN")
        self.assertEqual(self.app._question_state["selected_option"], 3)

        # Enter to confirm in single unified step
        self.app._handle_key("ENTER")
        self.assertIsNone(self.app._question_state)
        self.assertEqual(self.app.active_model, "gemini-3.8-flash")
        self.assertEqual(self.app.model_effort, "high")
        self.assertEqual(self.app.model_badge, "gemini-3.8-flash (high)")

        written = self.app.proc.stdin.write.call_args[0][0]
        self.assertTrue(written.startswith("<<SET_MODEL>>"))
        payload = json.loads(written[len("<<SET_MODEL>>"):])
        self.assertEqual(payload["model"], "gemini-3.8-flash")
        self.assertEqual(payload["effort"], "high")
        self.assertEqual(payload["budget"], 16384)

    def test_skip_question_via_key_s(self):
        self.app._question_state = {
            "id": 101,
            "questions": [{
                "question": "What is your preference?",
                "options": [{"label": "Option 1"}, {"label": "Skip", "skip": True}]
            }],
            "answers": [],
            "index": 0,
            "selected_option": 0,
        }
        # Press 's' to skip
        self.app._handle_key("s")
        self.assertIsNone(self.app._question_state)
        written = self.app.proc.stdin.write.call_args[0][0]
        self.assertTrue(written.startswith("<<ASK_USER_ANSWER>>"))
        payload = json.loads(written[len("<<ASK_USER_ANSWER>>"):])
        self.assertEqual(payload["id"], 101)
        self.assertTrue(payload.get("skipped"))

    def test_skip_question_via_option_choice(self):
        self.app._question_state = {
            "id": 102,
            "questions": [{
                "question": "Choose an approach",
                "options": [{"label": "Fast"}, {"label": "Safe"}, {"label": "Skip", "skip": True}]
            }],
            "answers": [],
            "index": 0,
            "selected_option": 2,
        }
        # Press ENTER on Skip option
        self.app._handle_key("ENTER")
        self.assertIsNone(self.app._question_state)
        written = self.app.proc.stdin.write.call_args[0][0]
        self.assertTrue(written.startswith("<<ASK_USER_ANSWER>>"))
        payload = json.loads(written[len("<<ASK_USER_ANSWER>>"):])
        self.assertEqual(payload["id"], 102)
        self.assertTrue(payload.get("skipped"))

    def test_model_picker_skip(self):
        self.app.active_model = "mistral-medium-latest"
        self.app._handle_cmd_models()
        self.assertIsNotNone(self.app._question_state)
        # Skip via 's'
        self.app._handle_key("s")
        self.assertIsNone(self.app._question_state)
        self.assertEqual(self.app.active_model, "mistral-medium-latest")

    def test_dropdown_overlay_rendered_in_redraw(self):
        screen = priya.Screen.__new__(priya.Screen)
        screen._w = 80
        screen._h = 24
        screen._convo_h = 20
        screen.scroll_offset = 0
        screen.input_text = ""
        screen.input_cursor = 0
        screen.status_text = ""
        screen.status_color = priya.C_STATUS
        screen.model_badge = "mistral-medium"
        screen.plan_mode = False
        screen.busy = False
        screen.spinner_i = 0
        screen.history_badge = ""
        screen.active_tool_name = ""
        screen.active_tool_detail = ""
        screen.active_thinking = ""
        screen._git_branch = None
        screen._lock = priya.threading.RLock()
        screen.turns = []
        screen.cur_turn = None
        screen._lines_cache = []
        screen._cache_dirty = True
        screen.suggestion_index = 0
        screen._edit_state = None

        captured = []
        screen._write = MagicMock(side_effect=lambda buf: captured.append(buf))
        screen._update_size = MagicMock()
        screen._check_git = MagicMock()
        screen._get_lines = MagicMock(return_value=[])

        # Test slash command dropdown
        screen.input_text = "/m"
        screen._question_state = None
        screen._redraw_locked()
        out = "".join(captured)
        self.assertIn("┌─ Commands", out)
        self.assertIn("/models", out)
        self.assertIn("[↑/↓] iterate • [Enter] complete", out)

        # Test question dropdown
        captured.clear()
        screen.input_text = ""
        screen._question_state = {
            "id": 1,
            "header": "Select Active Model",
            "questions": [{
                "question": "Choose AI model:",
                "options": [{"label": "Mistral"}, {"label": "Gemini"}]
            }],
            "index": 0,
            "selected_option": 0,
        }
        screen._redraw_locked()
        out = "".join(captured)
        self.assertIn("┌─ Select Active Model", out)
        self.assertIn("Choose AI model:", out)
        self.assertIn("❯ [1] Mistral", out)
        self.assertIn("[↑/↓] iterate • [Enter] confirm", out)




class TestLiveCliAskUserQuestionConcurrency(unittest.IsolatedAsyncioTestCase):
    async def test_ask_user_question_resolves_when_answer_fed(self):
        import live_cli
        loop = live_cli.TextLoop()

        # Start ask_user_question in a task
        q_args = {
            "questions": [{
                "question": "Which backend to use?",
                "options": [{"label": "Mistral"}, {"label": "Gemini"}]
            }]
        }

        task = asyncio.create_task(loop.ask_user_question(q_args))
        # Yield to let task run and create the pending question
        await asyncio.sleep(0.05)

        self.assertIsNotNone(loop._pending_question)
        qid = loop._pending_question["id"]

        # Simulate stdin_loop receiving the answer JSON from Priya
        answer_payload = {"id": qid, "answers": ["Gemini"]}
        loop._resolve_pending_question(answer_payload)

        # Wait for the task to complete
        result = await asyncio.wait_for(task, timeout=1.0)
        self.assertEqual(result, {"answers": [{"question": "Which backend to use?", "answer": "Gemini"}]})
        self.assertIsNone(loop._pending_question)

    async def test_ask_user_question_handles_cancellation(self):
        import live_cli
        loop = live_cli.TextLoop()

        q_args = {
            "questions": [{
                "question": "Confirm deletion?",
                "options": [{"label": "Yes"}, {"label": "No"}]
            }]
        }

        task = asyncio.create_task(loop.ask_user_question(q_args))
        await asyncio.sleep(0.05)

        self.assertIsNotNone(loop._pending_question)
        qid = loop._pending_question["id"]

        # Simulate user cancelling the modal
        cancel_payload = {"id": qid, "cancelled": True}
        loop._resolve_pending_question(cancel_payload)

        result = await asyncio.wait_for(task, timeout=1.0)
        self.assertEqual(result, {"cancelled": True})
        self.assertIsNone(loop._pending_question)

    async def test_interrupt_resolves_pending_question(self):
        import live_cli
        loop = live_cli.TextLoop()

        q_args = {
            "questions": [{
                "question": "Proceed?",
                "options": [{"label": "Yes"}, {"label": "No"}]
            }]
        }

        task = asyncio.create_task(loop.ask_user_question(q_args))
        await asyncio.sleep(0.05)

        self.assertIsNotNone(loop._pending_question)
        # Simulate PRIYA_INTERRUPT
        loop._resolve_pending_question({"cancelled": True})

        result = await asyncio.wait_for(task, timeout=1.0)
        self.assertEqual(result, {"cancelled": True})
        self.assertIsNone(loop._pending_question)

    async def test_full_stdin_loop_resolves_ask_user_question(self):
        import live_cli
        loop = live_cli.TextLoop()

        lines = [
            "<<ASK_USER_ANSWER>>" + json.dumps({"id": 99, "answers": ["Option B"]}) + "\n",
            "q\n",
        ]
        line_iter = iter(lines)

        def mock_readline():
            try:
                return next(line_iter)
            except StopIteration:
                return ""

        future = asyncio.get_running_loop().create_future()
        loop._pending_question = {"id": 99, "future": future, "questions": [{"question": "Q"}]}

        with unittest.mock.patch("sys.stdin.readline", side_effect=mock_readline):
            await loop.stdin_loop()

        self.assertTrue(future.done())
        res = future.result()
        self.assertEqual(res, {"id": 99, "answers": ["Option B"]})


