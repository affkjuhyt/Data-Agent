"""The answer shown while the responder streams it (conversation_runner._LiveAnswer).

What streams must match what the approved answer would show: the same tags
stripped, nothing leaked from <OPTIONS>, and a final text the stream can be
completed to — or a reset when it cannot.
"""
import pytest

from services.workflow_runtime.runtime_services.conversation_runner import (
    _LiveAnswer,
    _remaining,
    _strip_think_and_options,
)

pytestmark = pytest.mark.unit


def _stream(pieces: list[str]) -> str:
    live = _LiveAnswer()
    return "".join(live.feed(p) for p in pieces)


def _char_by_char(text: str) -> str:
    return _stream(list(text))


class TestLiveAnswer:
    def test_plain_text_streams_unchanged(self):
        assert _stream(["Doanh thu ", "tháng 8 ", "là 12 tỷ."]) == "Doanh thu tháng 8 là 12 tỷ."

    def test_options_block_is_never_shown_even_split_across_tokens(self):
        text = 'Bạn muốn xem kỳ nào?\n<OPTIONS>["Tháng 7", "Tháng 8"]</OPTIONS>'
        assert "<" not in _char_by_char(text)
        assert _char_by_char(text).strip() == "Bạn muốn xem kỳ nào?"

    def test_think_block_is_dropped_and_the_answer_after_it_shown(self):
        assert _stream(["<th", "ink>nháp", " nội bộ</thi", "nk>\nKết quả: 5"]) == "Kết quả: 5"

    def test_unclosed_think_shows_nothing(self):
        assert _char_by_char("<think>còn đang nghĩ") == ""

    def test_a_lone_angle_bracket_is_released_once_it_is_not_a_tag(self):
        assert _stream(["a <", " b"]) == "a < b"

    @pytest.mark.parametrize("text", [
        "Trả lời thẳng.",
        "  \nCó khoảng trắng đầu dòng",
        "<think>x</think>Sau think",
        'Hỏi lại?\n\n<OPTIONS>["A"]</OPTIONS>',
        "Bảng: a < b và c > d",
    ])
    def test_streamed_text_completes_to_the_final_answer(self, text):
        final, _ = _strip_think_and_options(text)
        streamed = _char_by_char(text)
        rest = _remaining(streamed, final)
        assert rest is not None
        assert (streamed + rest).strip() == final


class TestRemaining:
    def test_prefix_sends_only_the_tail(self):
        assert _remaining("Doanh thu", "Doanh thu 12 tỷ") == " 12 tỷ"

    def test_trailing_whitespace_before_options_needs_nothing(self):
        assert _remaining("Hỏi lại?\n\n", "Hỏi lại?") == ""

    def test_appended_chart_note_is_sent(self):
        assert _remaining("Kết quả", "Kết quả\n\n📊 Đã tạo biểu đồ") == "\n\n📊 Đã tạo biểu đồ"

    def test_diverging_text_asks_for_a_reset(self):
        assert _remaining("Bản cũ", "Bản mới") is None
