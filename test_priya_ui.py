import asyncio
import unittest
from unittest.mock import Mock

from priya import PriyaInput, colorize_tool_text, extract_urls, tool_log_label
from textual.app import App, ComposeResult
from textual.events import Paste


class LinkRenderingTests(unittest.TestCase):
    def test_extracts_distinct_urls_from_text_and_markdown(self):
        self.assertEqual(
            extract_urls(
                "Open [the demo](http://127.0.0.1:8765/artifact/demo) or "
                "https://example.com/docs. Repeating https://example.com/docs is ignored."
            ),
            ["http://127.0.0.1:8765/artifact/demo", "https://example.com/docs"],
        )

    def test_ignores_non_urls(self):
        self.assertEqual(extract_urls("No browser address here."), [])
        self.assertEqual(extract_urls(None), [])

    def test_tool_logs_preserve_text_and_attach_color_spans(self):
        rendered = colorize_tool_text('error: failed at ./src/app.py; retry', "stderr")
        self.assertEqual(rendered.plain, 'error: failed at ./src/app.py; retry')
        self.assertTrue(rendered.spans)
        self.assertEqual(tool_log_label("stdout", "done").plain, "done")

    def test_multiline_paste_is_inserted_as_one_input_line(self):
        class PasteInput(PriyaInput):
            @property
            def selection(self):
                return Mock(is_empty=True)

        input_widget = PasteInput()
        input_widget.insert_text_at_cursor = Mock()
        event = Mock(text="first line\nsecond line")
        input_widget._on_paste(event)
        input_widget.insert_text_at_cursor.assert_called_once_with("first line second line")
        event.stop.assert_called_once()

    def test_paste_reaches_a_real_focused_input_once(self):
        class PasteApp(App):
            def compose(self) -> ComposeResult:
                yield PriyaInput(id="input")

        async def verify():
            app = PasteApp()
            async with app.run_test() as pilot:
                input_widget = app.query_one("#input", PriyaInput)
                input_widget.focus()
                input_widget.post_message(Paste("first\nsecond"))
                await pilot.pause()
                self.assertEqual(input_widget.value, "first second")

        asyncio.run(verify())


if __name__ == "__main__":
    unittest.main()
