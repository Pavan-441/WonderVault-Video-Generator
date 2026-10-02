# -*- coding: utf-8 -*-
import unittest
from app.services import voice


class TestVoiceYears(unittest.TestCase):
    def test_convert_year_to_telugu(self):
        cases = {
            1000: "వెయ్యి",
            1869: "పద్దెనిమిది వందల అరవై తొమ్మిది",
            1900: "పందొమ్మిది వందలు",
            1905: "పందొమ్మిది వందల ఐదు",
            1914: "పందొమ్మిది వందల పద్నాలుగు",
            1947: "పందొమ్మిది వందల నలభై ఏడు",
            1970: "పందొమ్మిది వందల డెబ్బై",
            1999: "పందొమ్మిది వందల తొంభై తొమ్మిది",
            2000: "రెండు వేలు",
            2001: "రెండు వేల ఒకటి",
            2008: "రెండు వేల ఎనిమిది",
            2014: "రెండు వేల పద్నాలుగు",
            2020: "రెండు వేల ఇరవై",
            2022: "రెండు వేల ఇరవై రెండు",
            2024: "రెండు వేల ఇరవై నాలుగు",
            2026: "రెండు వేల ఇరవై ఆరు",
        }
        for year, expected in cases.items():
            with self.subTest(year=year):
                self.assertEqual(voice.convert_year_to_telugu(year), expected)

    def test_convert_year_to_english(self):
        cases = {
            1000: "one thousand",
            1869: "eighteen hundred sixty-nine",
            1900: "nineteen hundred",
            1905: "nineteen hundred five",
            1914: "nineteen hundred fourteen",
            1947: "nineteen hundred forty-seven",
            1970: "nineteen hundred seventy",
            1999: "nineteen hundred ninety-nine",
            2000: "two thousand",
            2001: "two thousand one",
            2008: "two thousand eight",
            2014: "two thousand fourteen",
            2020: "two thousand twenty",
            2022: "two thousand twenty-two",
            2024: "two thousand twenty-four",
            2026: "two thousand twenty-six",
        }
        for year, expected in cases.items():
            with self.subTest(year=year):
                self.assertEqual(voice.convert_year_to_english(year), expected)

    def test_convert_decade_to_english(self):
        self.assertEqual(voice.convert_year_to_english(1970, is_decade=True), "nineteen hundred seventies")
        self.assertEqual(voice.convert_year_to_english(1980, is_decade=True), "nineteen hundred eighties")
        self.assertEqual(voice.convert_year_to_english(2020, is_decade=True), "two thousand twenties")

    def test_normalize_years_telugu(self):
        text = "1970లో ఒక సంఘటన జరిగింది. 1869 నుండి 1948 వరకు. 2014 మరియు 2022 నాటికి."
        expected = "పందొమ్మిది వందల డెబ్బైలో ఒక సంఘటన జరిగింది. పద్దెనిమిది వందల అరవై తొమ్మిది నుండి పందొమ్మిది వందల నలభై ఎనిమిది వరకు. రెండు వేల పద్నాలుగు మరియు రెండు వేల ఇరవై రెండు నాటికి."
        self.assertEqual(voice.normalize_years_for_tts(text, "te-IN-ShrutiNeural"), expected)

    def test_normalize_years_english(self):
        text = "In 1970, Apollo 13 was launched. Born in 1869. Between 2014 and 2022. Popular in the 1970s and 1980's."
        expected = "In nineteen hundred seventy, Apollo 13 was launched. Born in eighteen hundred sixty-nine. Between two thousand fourteen and two thousand twenty-two. Popular in the nineteen hundred seventies and nineteen hundred eighties."
        self.assertEqual(voice.normalize_years_for_tts(text, "en-US-AvaMultilingualNeural"), expected)

    def test_ignore_false_positives(self):
        text = "Version 1.1970 was released with price $1970.50 and phone 1970123456."
        self.assertEqual(voice.normalize_years_for_tts(text, "en-US-AvaMultilingualNeural"), text)

    def test_ignore_cjk(self):
        text = "1970年阿波罗13号发射，价格1970元。"
        self.assertEqual(voice.normalize_years_for_tts(text, "zh-CN-XiaoxiaoNeural"), text)

    def test_match_script_line_with_spoken_years(self):
        # English test
        script_lines = ["In 1970, Apollo 13 was launched.", "In 2014, a new mission began."]
        current_text = "In nineteen hundred seventy Apollo 13 was launched"
        matched = voice._match_script_line(script_lines, current_text, 0)
        self.assertEqual(matched, "In 1970, Apollo 13 was launched.")

        # Telugu test
        script_lines_te = ["1970లో ఒక సంఘటన జరిగింది.", "2014 నాటికి కొత్త చరిత్ర మొదలైంది."]
        current_text_te = "పందొమ్మిది వందల డెబ్బైలో ఒక సంఘటన జరిగింది"
        matched_te = voice._match_script_line(script_lines_te, current_text_te, 0)
        self.assertEqual(matched_te, "1970లో ఒక సంఘటన జరిగింది.")


if __name__ == "__main__":
    unittest.main()
