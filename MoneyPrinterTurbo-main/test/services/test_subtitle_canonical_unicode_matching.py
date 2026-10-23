import unicodedata
from datetime import timedelta
from types import SimpleNamespace

import pytest
from edge_tts.srt_composer import Subtitle

from app.services import voice
from app.services.subtitle import file_to_subtitles


@pytest.mark.parametrize("script,cue", [
    ("Café", unicodedata.normalize("NFD", "Café")),
    (unicodedata.normalize("NFD", "Café"), "Café"),
    (unicodedata.normalize("NFD", "한글"), "한글"),
    # 泰卢固语元音符号 ై (U+0C48) 的 NFD 形式是 U+0C46 U+0C56。
    ("వైజ్ఞానిక వాస్తవం", unicodedata.normalize("NFD", "వైజ్ఞానిక వాస్తవం")),
    (unicodedata.normalize("NFD", "వైజ్ఞానిక వాస్తవం"), "వైజ్ఞానిక వాస్తవం"),
])
def test_canonically_equivalent_cues_publish_requested_caption(tmp_path, script, cue):
    maker = SimpleNamespace(cues=[Subtitle(index=1, start=timedelta(seconds=0.1), end=timedelta(seconds=0.9), content=cue)])
    destination = tmp_path / "captions.srt"
    voice.create_subtitle(maker, script, str(destination))
    captions = file_to_subtitles(str(destination))
    assert len(captions) == 1
    assert captions[0][2] == script
    assert captions[0][1] == "00:00:00,100 --> 00:00:00,900"


def test_distinct_accented_text_is_not_treated_as_equivalent():
    assert voice._match_script_line(["Café"], "Cafe", 0) == ""
