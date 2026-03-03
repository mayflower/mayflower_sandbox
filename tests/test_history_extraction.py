"""Unit tests for history_extraction module."""

from mayflower_sandbox.history_extraction import (
    _extract_text_from_item,
    _iter_message_texts,
    _normalize_list_content,
    _normalize_message_content,
    _parse_fence_info,
    _select_block,
    extract_fenced_blocks,
    extract_fenced_code_from_messages,
)


class TestExtractTextFromItem:
    """Tests for _extract_text_from_item helper."""

    def test_string_input(self):
        assert _extract_text_from_item("hello") == "hello"

    def test_dict_with_text(self):
        assert _extract_text_from_item({"text": "hello"}) == "hello"

    def test_dict_with_content(self):
        assert _extract_text_from_item({"content": "hello"}) == "hello"

    def test_dict_text_priority_over_content(self):
        # text takes priority when both exist
        assert (
            _extract_text_from_item({"text": "from_text", "content": "from_content"}) == "from_text"
        )

    def test_dict_empty_text_falls_back_to_content(self):
        assert _extract_text_from_item({"text": "", "content": "fallback"}) == "fallback"

    def test_dict_none_text_falls_back_to_content(self):
        assert _extract_text_from_item({"text": None, "content": "fallback"}) == "fallback"

    def test_dict_empty_returns_none(self):
        assert _extract_text_from_item({}) is None

    def test_dict_with_empty_values(self):
        assert _extract_text_from_item({"text": "", "content": ""}) is None

    def test_none_input(self):
        assert _extract_text_from_item(None) is None

    def test_int_input(self):
        assert _extract_text_from_item(123) is None

    def test_list_input(self):
        assert _extract_text_from_item(["a", "b"]) is None


class TestNormalizeListContent:
    """Tests for _normalize_list_content helper."""

    def test_list_of_strings(self):
        assert _normalize_list_content(["hello", "world"]) == "hello\nworld"

    def test_list_of_dicts(self):
        items = [{"text": "first"}, {"text": "second"}]
        assert _normalize_list_content(items) == "first\nsecond"

    def test_mixed_list(self):
        items = ["string", {"text": "dict"}, None, {"content": "content"}]
        assert _normalize_list_content(items) == "string\ndict\ncontent"

    def test_empty_list(self):
        assert _normalize_list_content([]) == ""

    def test_list_with_none_values(self):
        assert _normalize_list_content([None, None]) == ""


class TestNormalizeMessageContent:
    """Tests for _normalize_message_content helper."""

    def test_string_content(self):
        assert _normalize_message_content("hello") == "hello"

    def test_list_content(self):
        assert _normalize_message_content(["a", "b"]) == "a\nb"

    def test_none_content(self):
        assert _normalize_message_content(None) == ""

    def test_other_type_converts_to_string(self):
        assert _normalize_message_content(123) == "123"


class TestParseFenceInfo:
    """Tests for _parse_fence_info helper."""

    def test_empty_info(self):
        assert _parse_fence_info("") == (None, None)

    def test_language_only(self):
        assert _parse_fence_info("python") == ("python", None)

    def test_language_with_file_path(self):
        assert _parse_fence_info("python file=main.py") == ("python", "main.py")

    def test_language_with_path_attribute(self):
        assert _parse_fence_info("javascript path=app.js") == ("javascript", "app.js")

    def test_quoted_file_path(self):
        # Note: current implementation splits on whitespace first, so spaces in filenames
        # are not fully supported. Quotes around simple filenames are stripped.
        assert _parse_fence_info('python file="main.py"') == ("python", "main.py")

    def test_single_quoted_file_path(self):
        assert _parse_fence_info("python file='main.py'") == ("python", "main.py")

    def test_multiple_attributes_uses_first_file(self):
        assert _parse_fence_info("python file=first.py path=second.py") == ("python", "first.py")

    def test_language_with_extra_whitespace(self):
        assert _parse_fence_info("  python  ") == ("python", None)


class TestExtractFencedBlocks:
    """Tests for extract_fenced_blocks function."""

    def test_empty_text(self):
        assert extract_fenced_blocks("") == []

    def test_none_text(self):
        assert extract_fenced_blocks(None) == []

    def test_no_code_blocks(self):
        assert extract_fenced_blocks("Just plain text") == []

    def test_single_block(self):
        text = "```python\nprint('hello')\n```"
        blocks = extract_fenced_blocks(text)
        assert len(blocks) == 1
        assert blocks[0]["language"] == "python"
        assert blocks[0]["code"] == "print('hello')"
        assert blocks[0]["file_path"] is None

    def test_block_with_file_path(self):
        text = "```python file=main.py\nprint('hello')\n```"
        blocks = extract_fenced_blocks(text)
        assert len(blocks) == 1
        assert blocks[0]["language"] == "python"
        assert blocks[0]["file_path"] == "main.py"

    def test_multiple_blocks(self):
        text = """
```python
code1
```
Some text
```javascript
code2
```
"""
        blocks = extract_fenced_blocks(text)
        assert len(blocks) == 2
        assert blocks[0]["language"] == "python"
        assert blocks[0]["code"] == "code1"
        assert blocks[1]["language"] == "javascript"
        assert blocks[1]["code"] == "code2"

    def test_block_without_language(self):
        text = "```\nplain code\n```"
        blocks = extract_fenced_blocks(text)
        assert len(blocks) == 1
        assert blocks[0]["language"] is None
        assert blocks[0]["code"] == "plain code"

    def test_block_without_trailing_newline(self):
        text = "```python\ncode```"
        blocks = extract_fenced_blocks(text)
        assert len(blocks) == 1
        assert blocks[0]["code"] == "code"


class TestSelectBlock:
    """Tests for _select_block helper."""

    def test_empty_blocks(self):
        assert _select_block([]) == ""

    def test_single_block_no_filter(self):
        blocks = [{"language": "python", "file_path": None, "code": "code1"}]
        assert _select_block(blocks) == "code1"

    def test_returns_last_block_when_no_filter(self):
        blocks = [
            {"language": "python", "file_path": None, "code": "first"},
            {"language": "python", "file_path": None, "code": "last"},
        ]
        assert _select_block(blocks) == "last"

    def test_filter_by_file_path(self):
        blocks = [
            {"language": "python", "file_path": "a.py", "code": "code_a"},
            {"language": "python", "file_path": "b.py", "code": "code_b"},
        ]
        assert _select_block(blocks, file_path="a.py") == "code_a"

    def test_filter_by_basename(self):
        blocks = [
            {"language": "python", "file_path": "a.py", "code": "code_a"},
            {"language": "python", "file_path": "b.py", "code": "code_b"},
        ]
        assert _select_block(blocks, file_path="/some/path/a.py") == "code_a"

    def test_filter_by_relative_basename(self):
        blocks = [
            {"language": "python", "file_path": "./main.py", "code": "code_main"},
        ]
        assert _select_block(blocks, file_path="/project/main.py") == "code_main"

    def test_filter_by_language(self):
        blocks = [
            {"language": "python", "file_path": None, "code": "py_code"},
            {"language": "javascript", "file_path": None, "code": "js_code"},
        ]
        assert _select_block(blocks, language="javascript") == "js_code"

    def test_filter_by_language_case_insensitive(self):
        blocks = [
            {"language": "Python", "file_path": None, "code": "py_code"},
        ]
        assert _select_block(blocks, language="python") == "py_code"

    def test_combined_filters(self):
        blocks = [
            {"language": "python", "file_path": "a.py", "code": "py_a"},
            {"language": "javascript", "file_path": "a.js", "code": "js_a"},
            {"language": "python", "file_path": "b.py", "code": "py_b"},
        ]
        assert _select_block(blocks, file_path="a.py", language="python") == "py_a"

    def test_block_with_none_code(self):
        blocks = [{"language": "python", "file_path": None, "code": None}]
        assert _select_block(blocks) == ""


class TestIterMessageTexts:
    """Tests for _iter_message_texts helper."""

    def test_empty_messages(self):
        assert _iter_message_texts([]) == []
        assert _iter_message_texts(None) == []

    def test_dict_messages(self):
        messages = [{"content": "first"}, {"content": "second"}]
        texts = _iter_message_texts(messages)
        # reversed order
        assert texts == ["second", "first"]

    def test_object_messages(self):
        class Message:
            def __init__(self, content):
                self.content = content

        messages = [Message("first"), Message("second")]
        texts = _iter_message_texts(messages)
        assert texts == ["second", "first"]

    def test_tuple_messages(self):
        messages = [("user", "first"), ("assistant", "second")]
        texts = _iter_message_texts(messages)
        assert texts == ["second", "first"]

    def test_filters_empty_content(self):
        messages = [{"content": "text"}, {"content": ""}, {"content": None}]
        texts = _iter_message_texts(messages)
        assert texts == ["text"]


class TestExtractFencedCodeFromMessages:
    """Tests for extract_fenced_code_from_messages function."""

    def test_empty_messages(self):
        assert extract_fenced_code_from_messages([]) == ""

    def test_no_code_blocks(self):
        messages = [{"content": "Just plain text"}]
        assert extract_fenced_code_from_messages(messages) == ""

    def test_extracts_code_from_message(self):
        messages = [{"content": "```python\nprint('hello')\n```"}]
        assert extract_fenced_code_from_messages(messages) == "print('hello')"

    def test_returns_most_recent_match(self):
        messages = [
            {"content": "```python\nold_code\n```"},
            {"content": "```python\nnew_code\n```"},
        ]
        # Most recent is last in list, but _iter_message_texts reverses
        assert extract_fenced_code_from_messages(messages) == "new_code"

    def test_filter_by_file_path(self):
        messages = [
            {"content": "```python file=a.py\ncode_a\n```\n```python file=b.py\ncode_b\n```"}
        ]
        assert extract_fenced_code_from_messages(messages, file_path="a.py") == "code_a"

    def test_filter_by_language(self):
        messages = [{"content": "```python\npy_code\n```\n```javascript\njs_code\n```"}]
        assert extract_fenced_code_from_messages(messages, language="javascript") == "js_code"

    def test_with_langchain_message_objects(self):
        class AIMessage:
            def __init__(self, content):
                self.content = content

        messages = [AIMessage("```python\ncode\n```")]
        assert extract_fenced_code_from_messages(messages) == "code"

    def test_with_multipart_content(self):
        messages = [{"content": [{"text": "Here's the code:"}, {"text": "```python\ncode\n```"}]}]
        assert extract_fenced_code_from_messages(messages) == "code"
