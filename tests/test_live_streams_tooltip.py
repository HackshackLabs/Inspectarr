"""Live dashboard session grouping for server tooltips."""

import unittest

from scoparr.live_streams import group_live_streams_by_server


class LiveStreamsByServerTests(unittest.TestCase):
    def test_groups_by_server_and_formats_title(self) -> None:
        sessions = [
            {
                "server_id": "a",
                "friendly_name": "Alice",
                "grandparent_title": "Show",
                "parent_media_index": 2,
                "media_index": 5,
            },
            {
                "server_id": "b",
                "user": "bob",
                "title": "Movie X",
            },
        ]
        grouped = group_live_streams_by_server(sessions)
        self.assertEqual(["Alice"], [x["user"] for x in grouped["a"]])
        self.assertEqual("Show S2E5", grouped["a"][0]["title"])
        self.assertEqual("bob", grouped["b"][0]["user"])
        self.assertEqual("Movie X", grouped["b"][0]["title"])

    def test_skips_non_dict_rows(self) -> None:
        self.assertEqual({}, group_live_streams_by_server([None, "x", {}]))  # type: ignore[list-item]


if __name__ == "__main__":
    unittest.main()
