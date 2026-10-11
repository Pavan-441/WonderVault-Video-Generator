import os
import tempfile
import unittest
from unittest import mock

from app.services import bgm as bgm_service
from app.services import video as vd


class RandomBgmPoolTests(unittest.TestCase):
    """随机 BGM 只能使用用户上传的音乐，不能选中内置（YouTube 来源）歌曲。"""

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        root = self._temp_dir.name
        self.builtin_dir = os.path.join(root, "songs")
        self.uploaded_dir = os.path.join(root, "uploaded")
        os.makedirs(self.builtin_dir)
        os.makedirs(self.uploaded_dir)
        for name in ("output000.mp3", "output001.mp3"):
            open(os.path.join(self.builtin_dir, name), "wb").close()

        patches = [
            mock.patch.object(bgm_service.utils, "song_dir", return_value=self.builtin_dir),
            mock.patch.object(bgm_service, "uploaded_bgm_dir", return_value=self.uploaded_dir),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._temp_dir.cleanup)

    def test_random_never_picks_builtin_songs(self):
        uploaded = os.path.join(self.uploaded_dir, "my-track.mp3")
        open(uploaded, "wb").close()

        picks = {vd.get_bgm_file("random") for _ in range(50)}

        self.assertEqual(picks, {uploaded})

    def test_random_returns_nothing_when_only_builtin_songs_exist(self):
        self.assertEqual(vd.get_bgm_file("random"), "")

    def test_builtin_songs_remain_listed_for_manual_preset_selection(self):
        builtin = bgm_service.list_builtin_bgm_files()

        self.assertEqual(
            sorted(os.path.basename(path) for path in builtin),
            ["output000.mp3", "output001.mp3"],
        )


if __name__ == "__main__":
    unittest.main()
