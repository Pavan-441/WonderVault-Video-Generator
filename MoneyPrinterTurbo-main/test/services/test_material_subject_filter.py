import unittest
from unittest import mock

from app.models.schema import MaterialInfo
from app.services import llm, material


def _clip(slug: str) -> MaterialInfo:
    item = MaterialInfo()
    item.provider = "pexels"
    item.url = f"https://videos.pexels.com/{slug}.mp4"
    item.duration = 10
    item.source_info = {"source_page": f"https://www.pexels.com/video/{slug}-123456/"}
    return item


OCTOPUS_TERMS = [
    "octopus ocean creature",
    "octopus three hearts",
    "octopus gills blood",
    "octopus swimming underwater",
    "deep sea octopus",
    "ocean mysteries wonder vault",
]


class PrimarySubjectTests(unittest.TestCase):
    def test_most_repeated_keyword_word_is_the_subject(self):
        subject = material.primary_subject_words(OCTOPUS_TERMS)

        self.assertEqual(subject, frozenset({material._stem_word("octopus")}))

    def test_no_subject_when_no_word_repeats(self):
        self.assertEqual(
            material.primary_subject_words(["metal blade", "gastric juices"]),
            frozenset(),
        )


class SubjectMatchTests(unittest.TestCase):
    def setUp(self):
        self.subject = material.primary_subject_words(OCTOPUS_TERMS)

    def _keeps(self, slug, term, subject=None):
        return material._matches_keyword_subject(
            _clip(slug), term, self.subject if subject is None else subject
        )

    def test_side_word_alone_does_not_pull_in_unrelated_clip(self):
        self.assertFalse(
            self._keeps("creepy-vampire-woman-drinking-blood-from-test-tube", "octopus gills blood")
        )
        self.assertFalse(self._keeps("flat-lay-video-of-hearts", "octopus three hearts"))

    def test_clip_missing_the_subject_is_dropped_even_with_other_matches(self):
        self.assertFalse(
            self._keeps("graceful-sea-turtle-swimming-underwater", "octopus swimming underwater")
        )

    def test_clip_showing_the_subject_is_kept(self):
        self.assertTrue(
            self._keeps("octopus-swimming-near-coral-reef", "octopus swimming underwater")
        )

    def test_terms_without_subject_need_two_matching_words(self):
        no_subject = frozenset()
        self.assertFalse(self._keeps("flat-lay-of-fruit-juices", "gastric juices", no_subject))
        self.assertFalse(
            self._keeps("adventure-walk-along-narrow-water-channel", "Wonder Vault channel", no_subject)
        )
        self.assertTrue(
            self._keeps("blood-vessels-animation", "blood vessels constriction", no_subject)
        )

    def test_generic_repeated_word_alone_does_not_match_long_keyword(self):
        night = frozenset({"night"})
        self.assertFalse(self._keeps("night-streets-in-city", "fireflies glowing night", night))
        self.assertTrue(self._keeps("fireflies-glowing-at-night-in-forest", "fireflies glowing night", night))

    def test_plural_and_singular_count_as_the_same_word(self):
        self.assertTrue(self._keeps("blood-vessel-close-up", "blood vessels", frozenset()))

    def test_single_word_keyword_needs_that_word(self):
        self.assertTrue(self._keeps("calm-ocean-waves", "ocean", frozenset()))
        self.assertFalse(self._keeps("city-traffic-at-night", "ocean", frozenset()))

    def test_clip_without_readable_title_is_kept(self):
        item = MaterialInfo()
        item.source_info = {}

        self.assertTrue(material._matches_keyword_subject(item, "octopus gills blood", self.subject))


class DownloadFallbackTests(unittest.TestCase):
    def test_retries_with_loose_match_when_strict_filter_finds_nothing(self):
        calls = []

        def fake_download(**kwargs):
            calls.append(kwargs["subject_filter"])
            return [] if kwargs["subject_filter"] else ["loose.mp4"]

        with mock.patch.object(material, "_download_videos", side_effect=fake_download):
            paths = material.download_videos(task_id="t", search_terms=["octopus"], source="pexels")

        self.assertEqual(paths, ["loose.mp4"])
        self.assertEqual(calls, [True, False])

    def test_no_retry_when_strict_filter_finds_clips(self):
        with mock.patch.object(
            material, "_download_videos", return_value=["strict.mp4"]
        ) as download:
            paths = material.download_videos(task_id="t", search_terms=["octopus"], source="pexels")

        self.assertEqual(paths, ["strict.mp4"])
        self.assertEqual(download.call_count, 1)

    def test_search_drops_off_topic_cached_clips(self):
        items = [
            _clip("octopus-gills-close-up"),
            _clip("creepy-vampire-woman-drinking-blood-from-test-tube"),
        ]
        captured = []

        def fake_by_order(**kwargs):
            captured.extend(
                kwargs["search_videos"](
                    search_term="octopus gills blood",
                    minimum_duration=5,
                    video_aspect=material.VideoAspect.portrait,
                )
            )
            return ["ok.mp4"]

        with (
            mock.patch.object(material, "_search_videos_with_cache", return_value=items),
            mock.patch.object(material, "_suggest_visual_alternatives", return_value={}),
            mock.patch.object(material, "_download_videos_by_script_order", side_effect=fake_by_order),
        ):
            material.download_videos(
                task_id="t",
                search_terms=OCTOPUS_TERMS,
                source="pexels",
                match_script_order=True,
            )

        self.assertEqual(
            [item.source_info["source_page"] for item in captured],
            ["https://www.pexels.com/video/octopus-gills-close-up-123456/"],
        )


def _search_by_term(results: dict):
    def fake_search(**kwargs):
        return list(results.get(kwargs["search_term"], []))

    return fake_search


def _collect_searches(terms, **download_kwargs):
    """Run the pexels download path and return what search_videos yields per keyword."""
    collected = {}

    def fake_by_order(**kwargs):
        for term in kwargs["search_terms"]:
            collected[term] = kwargs["search_videos"](
                search_term=term,
                minimum_duration=5,
                video_aspect=material.VideoAspect.portrait,
            )
        return ["ok.mp4"]

    with mock.patch.object(material, "_download_videos_by_script_order", side_effect=fake_by_order):
        material.download_videos(
            task_id="t", search_terms=terms, source="pexels", match_script_order=True, **download_kwargs
        )
    return collected


def _pages(items):
    return [item.source_info["source_page"].split("/video/")[1] for item in items]


class VisualRescueTests(unittest.TestCase):
    TERMS = ["megalodon open mouth", "megalodon giant tooth"]

    def test_unfilmable_keyword_is_filled_with_relevant_alternative_clips(self):
        results = {
            "megalodon open mouth": [_clip("close-up-shot-of-mouth-while-talking")],
            "great white shark mouth": [_clip("great-white-shark-mouth-open"), _clip("diver-with-camera")],
        }
        with (
            mock.patch.object(material, "_search_videos_with_cache", side_effect=_search_by_term(results)),
            mock.patch.object(
                material,
                "_suggest_visual_alternatives",
                return_value={"megalodon open mouth": ["great white shark mouth"]},
            ),
        ):
            collected = _collect_searches(self.TERMS)

        self.assertEqual(_pages(collected["megalodon open mouth"]), ["great-white-shark-mouth-open-123456/"])

    def test_alternatives_are_requested_once_per_video(self):
        with (
            mock.patch.object(material, "_search_videos_with_cache", return_value=[]),
            mock.patch.object(material, "_suggest_visual_alternatives", return_value={}) as suggest,
        ):
            _collect_searches(self.TERMS)

        self.assertEqual(suggest.call_count, 1)

    def test_no_llm_call_when_every_keyword_has_enough_clips(self):
        results = {
            term: [_clip(f"{term.replace(' ', '-')}-model"), _clip(f"{term.replace(' ', '-')}-replica")]
            for i, term in enumerate(self.TERMS)
        }
        with (
            mock.patch.object(material, "_search_videos_with_cache", side_effect=_search_by_term(results)),
            mock.patch.object(material, "_suggest_visual_alternatives") as suggest,
        ):
            collected = _collect_searches(self.TERMS)

        suggest.assert_not_called()
        self.assertEqual(len(collected["megalodon open mouth"]), 2)


class VisualAlternativeParsingTests(unittest.TestCase):
    def test_parses_fenced_json_and_keeps_two_new_alternatives(self):
        response = (
            '```json\n{"Megalodon Open Mouth": ["great white shark mouth", '
            '"megalodon open mouth", "shark teeth", "extra"]}\n```'
        )
        with mock.patch.object(llm, "_generate_response", return_value=response):
            result = llm.generate_visual_alternatives(["megalodon open mouth"])

        self.assertEqual(result, {"megalodon open mouth": ["great white shark mouth", "shark teeth"]})

    def test_llm_error_returns_no_alternatives(self):
        with mock.patch.object(llm, "_generate_response", return_value="Error: quota exceeded"):
            self.assertEqual(llm.generate_visual_alternatives(["megalodon"]), {})

    def test_non_json_reply_returns_no_alternatives(self):
        with mock.patch.object(llm, "_generate_response", return_value="Sure! Try sharks."):
            self.assertEqual(llm.generate_visual_alternatives(["megalodon"]), {})


if __name__ == "__main__":
    unittest.main()
