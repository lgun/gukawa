import unittest

from yt_dlp import YoutubeDL

from gukawa.source import DOWNLOAD_FORMAT


class DownloadFormatTests(unittest.TestCase):
    def choose(self, formats):
        with YoutubeDL({"quiet": True}) as downloader:
            selector = downloader.build_format_selector(DOWNLOAD_FORMAT)
            return list(selector({"formats": formats, "has_merged_format": True, "incomplete_formats": False}))

    def test_separate_1080_video_is_preferred_over_combined_720(self):
        formats = [
            {"format_id": "audio", "ext": "m4a", "url": "https://example.invalid/a", "vcodec": "none", "acodec": "mp4a.40.2"},
            {"format_id": "combined720", "ext": "mp4", "url": "https://example.invalid/720", "height": 1280, "width": 720, "vcodec": "avc1", "acodec": "mp4a.40.2"},
            {"format_id": "video1080", "ext": "mp4", "url": "https://example.invalid/1080", "height": 1920, "width": 1080, "vcodec": "vp9", "acodec": "none"},
        ]
        result = self.choose(formats)
        self.assertEqual([item["format_id"] for item in result[0]["requested_formats"]], ["video1080", "audio"])

    def test_combined_video_still_works_when_separate_streams_are_absent(self):
        formats = [{"format_id": "combined720", "ext": "mp4", "url": "https://example.invalid/720", "vcodec": "avc1", "acodec": "mp4a.40.2"}]
        self.assertEqual(self.choose(formats)[0]["format_id"], "combined720")


if __name__ == "__main__":
    unittest.main()
