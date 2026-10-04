import unittest
from app.models.schema import VideoAspect, VideoConcatMode, VideoParams, MaterialInfo
from app.services import video, material, task


class TestVideoConcurrencyAndColor(unittest.TestCase):
    def test_schema_concurrency_defaults(self):
        params = VideoParams(video_subject="Test")
        self.assertEqual(params.stock_material_concurrency, 1)
        self.assertEqual(params.clip_rendering_concurrency, 1)

        custom = VideoParams(
            video_subject="Test",
            stock_material_concurrency=4,
            clip_rendering_concurrency=3,
        )
        self.assertEqual(custom.stock_material_concurrency, 4)
        self.assertEqual(custom.clip_rendering_concurrency, 3)

    def test_bt709_ffmpeg_params_merging(self):
        # Empty dict
        merged = video._merge_bt709_ffmpeg_params({})
        params_list = merged.get("ffmpeg_params", [])
        self.assertIn("-color_primaries", params_list)
        self.assertIn("bt709", params_list)
        self.assertIn("-color_trc", params_list)
        self.assertIn("-colorspace", params_list)
        self.assertIn("-color_range", params_list)
        self.assertIn("tv", params_list)
        self.assertTrue(any("h264_metadata" in p for p in params_list))

        # Existing custom params preserved
        existing = {"ffmpeg_params": ["-preset", "fast", "-crf", "18"]}
        merged_custom = video._merge_bt709_ffmpeg_params(existing)
        custom_list = merged_custom.get("ffmpeg_params", [])
        self.assertIn("-preset", custom_list)
        self.assertIn("-crf", custom_list)
        self.assertIn("-colorspace", custom_list)

    def test_clean_pexels_search_term(self):
        self.assertEqual(
            material._clean_pexels_search_term("“Saturn planet”"), "Saturn planet"
        )
        self.assertEqual(
            material._clean_pexels_search_term("  'telescope' [night sky]  "),
            "telescope night sky",
        )

    def test_score_pexels_video_relevance(self):
        video_relevant = {
            "url": "https://www.pexels.com/video/saturn-floating-on-ocean-20464846/"
        }
        video_irrelevant = {
            "url": "https://www.pexels.com/video/two-women-standing-back-to-back-6967495/"
        }
        score_rel = material._score_pexels_video_relevance(
            video_relevant, "Saturn planet"
        )
        score_irrel = material._score_pexels_video_relevance(
            video_irrelevant, "Saturn planet"
        )
        self.assertGreater(score_rel, score_irrel)

    def test_pure_accuracy_anchor_enforcement_and_trap_filtering(self):
        # 1. Contradictory lifestyle traps must be strictly disqualified (0.0)
        cliff_jump = {"url": "https://www.pexels.com/video/daring-cliff-jump-at-sunset-38451004/"}
        trampoline = {"url": "https://www.pexels.com/video/child-jumping-on-trampoline-in-backyard-setting-34631452/"}
        family_walk = {"url": "https://www.pexels.com/video/family-walk-on-scenic-pathway-at-sunset-39322844/"}
        dam_walk = {"url": "https://www.pexels.com/video/daring-walk-along-massive-dam-structure-38922058/"}

        self.assertEqual(material._score_pexels_video_relevance(cliff_jump, "moon gravity jump"), 0.0)
        self.assertEqual(material._score_pexels_video_relevance(trampoline, "human jumping on moon"), 0.0)
        self.assertEqual(material._score_pexels_video_relevance(family_walk, "low gravity space walk"), 0.0)
        self.assertEqual(material._score_pexels_video_relevance(dam_walk, "low gravity space walk"), 0.0)

        # 2. Genuine anchor and synonym matches must be approved (> 0.0)
        moon_crater = {"url": "https://www.pexels.com/video/moon-surface-with-crater-under-starry-sky-37003414/"}
        lunar_rotation = {"url": "https://www.pexels.com/video/lunar-surface-rotation-in-starry-space-36964618/"}
        helmet_moon = {"url": "https://www.pexels.com/video/reflection-of-the-moon-on-a-man-s-helmet-7649284/"}
        astronaut_galaxy = {"url": "https://www.pexels.com/video/galaxy-reflected-on-astronaut-helmet-7649294/"}

        self.assertGreater(material._score_pexels_video_relevance(moon_crater, "moon gravity jump"), 0.0)
        self.assertGreater(material._score_pexels_video_relevance(lunar_rotation, "human jumping on moon"), 0.0)
        self.assertGreater(material._score_pexels_video_relevance(helmet_moon, "astronaut jumping on moon"), 0.0)
        self.assertGreater(material._score_pexels_video_relevance(astronaut_galaxy, "astronaut space walk"), 0.0)

        # 3. Cross-domain accuracy: Deep sea and Ancient history
        deep_sea_clip = {"url": "https://www.pexels.com/video/underwater-deep-sea-bioluminescence-12345/"}
        gym_clip = {"url": "https://www.pexels.com/video/guy-bench-pressing-weights-in-gym-67890/"}
        self.assertGreater(material._score_pexels_video_relevance(deep_sea_clip, "deep sea pressure survive"), 0.0)
        self.assertEqual(material._score_pexels_video_relevance(gym_clip, "deep sea pressure survive"), 0.0)

        rome_clip = {"url": "https://www.pexels.com/video/colosseum-rome-ancient-ruins-11223/"}
        boxing_clip = {"url": "https://www.pexels.com/video/boxing-match-ring-punch-44556/"}
        self.assertGreater(material._score_pexels_video_relevance(rome_clip, "ancient roman gladiators fighting"), 0.0)
        self.assertEqual(material._score_pexels_video_relevance(boxing_clip, "ancient roman gladiators fighting"), 0.0)

    def test_download_videos_balanced_round_robin(self):
        # Verify that round robin interleaved candidates across terms
        terms = ["Saturn", "Telescope", "Stars"]
        term_candidates = [
            [MaterialInfo(provider="pexels", url=f"http://test/{term}_{i}.mp4", duration=10) for i in range(5)]
            for term in terms
        ]
        audio_duration = 12.0
        max_clip_duration = 3
        # In download_videos logic:
        selected_items = []
        accumulated_duration = 0.0
        idx = 0
        while term_candidates and accumulated_duration < audio_duration:
            any_added = False
            for cand_list in term_candidates:
                if idx < len(cand_list):
                    item = cand_list[idx]
                    selected_items.append(item)
                    accumulated_duration += min(max_clip_duration, item.duration)
                    any_added = True
                    if accumulated_duration >= audio_duration:
                        break
            if not any_added:
                break
            idx += 1

        # Check that we got 1 from Saturn, 1 from Telescope, 1 from Stars, then 1 from Saturn
        selected_urls = [it.url for it in selected_items]
        self.assertIn("Saturn_0", selected_urls[0])
        self.assertIn("Telescope_0", selected_urls[1])
        self.assertIn("Stars_0", selected_urls[2])
        self.assertIn("Saturn_1", selected_urls[3])


if __name__ == "__main__":
    unittest.main()
