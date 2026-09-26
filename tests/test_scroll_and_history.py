import unittest
from unittest.mock import MagicMock
import priya

class TestScrollAndHistory(unittest.TestCase):
    def setUp(self):
        self.app = priya.PriyaApp.__new__(priya.PriyaApp)
        self.app.history = ["command 1", "command 2", "command 3"]
        self.app.history_index = -1
        self.app.saved_input = ""
        self.app._question_state = None
        self.app._edit_state = None
        self.app._busy = False
        self.app._interrupted = False
        self.app._running = True

        screen = priya.Screen.__new__(priya.Screen)
        screen.scroll_offset = 0
        screen._convo_h = 20
        screen._w = 80
        screen._lines_cache = ["line"] * 50
        screen._cache_dirty = False
        screen.input_text = ""
        screen.input_cursor = 0
        screen.history_badge = ""
        screen.busy = False
        screen._lock = priya.threading.RLock()
        screen.redraw = MagicMock()
        screen._get_lines = MagicMock(return_value=["line"] * 50)

        self.app.screen = screen

    def test_mouse_wheel_scrolling(self):
        # WHEEL_UP should increase scroll_offset
        self.app._handle_key("WHEEL_UP")
        self.assertEqual(self.app.screen.scroll_offset, 3)

        self.app._handle_key("WHEEL_UP")
        self.assertEqual(self.app.screen.scroll_offset, 6)

        # WHEEL_DOWN should decrease scroll_offset
        self.app._handle_key("WHEEL_DOWN")
        self.assertEqual(self.app.screen.scroll_offset, 3)

        # WHEEL_DOWN cannot go below 0
        self.app._handle_key("WHEEL_DOWN")
        self.app._handle_key("WHEEL_DOWN")
        self.assertEqual(self.app.screen.scroll_offset, 0)

    def test_scrolled_up_arrow_keys_scroll_conversation(self):
        # When user is scrolled up into conversation, UP and DOWN scroll
        self.app.screen.scroll_offset = 5
        self.app._handle_key("UP")
        self.assertEqual(self.app.screen.scroll_offset, 8)
        self.assertEqual(self.app.history_index, -1)  # History was NOT touched

        self.app._handle_key("DOWN")
        self.assertEqual(self.app.screen.scroll_offset, 5)
        self.assertEqual(self.app.history_index, -1)

    def test_prompt_level_arrow_keys_navigate_history(self):
        # When scroll_offset is 0, UP navigates history
        self.assertEqual(self.app.screen.scroll_offset, 0)
        self.app._handle_key("UP")
        self.assertEqual(self.app.history_index, 2)
        self.assertEqual(self.app.screen.input_text, "command 3")

        self.app._handle_key("UP")
        self.assertEqual(self.app.history_index, 1)
        self.assertEqual(self.app.screen.input_text, "command 2")

        self.app._handle_key("DOWN")
        self.assertEqual(self.app.history_index, 2)
        self.assertEqual(self.app.screen.input_text, "command 3")

        self.app._handle_key("DOWN")
        self.assertEqual(self.app.history_index, -1)
        self.assertEqual(self.app.screen.input_text, "")

    def test_esc_resets_scroll_when_scrolled(self):
        self.app.screen.scroll_offset = 10
        self.app._handle_key("ESC")
        self.assertEqual(self.app.screen.scroll_offset, 0)

    def test_mouse_escape_sequences(self):
        enable = priya.enable_mouse()
        self.assertIn("?1000h", enable)
        self.assertIn("?1006h", enable)

        disable = priya.disable_mouse()
        self.assertIn("?1000l", disable)
        self.assertIn("?1006l", disable)
