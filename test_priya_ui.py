import unittest

from priya import colorize_tool_text, extract_urls, tool_log_label


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
        self.assertEqual(tool_log_label("stdout", "done").plain, "stdout │ done")


if __name__ == "__main__":
    unittest.main()
