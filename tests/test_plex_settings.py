"""Plex settings mapping helpers."""

import unittest

from inspectarr.settings import (
    PlexServer,
    Settings,
    plex_mapped_tautulli_server_ids,
    resolve_plex_for_tautulli,
)


class PlexSettingsTests(unittest.TestCase):
    def test_resolve_uses_token_profile_and_requires_client_id(self) -> None:
        s = Settings(
            tautulli_servers=[],
            plex_servers=[
                PlexServer(
                    id="p1",
                    base_url="https://plex1.example.com",
                    tautulli_server_id="t1",
                    token_profile="primary",
                )
            ],
            plex_token_primary="tok-a",
            plex_token_secondary="tok-b",
            plex_client_identifier="cid-1",
        )
        r = resolve_plex_for_tautulli(s, "t1")
        assert r is not None
        ps, tok, cid = r
        self.assertEqual(ps.base_url, "https://plex1.example.com")
        self.assertEqual(tok, "tok-a")
        self.assertEqual(cid, "cid-1")

        s2 = s.model_copy(update={"plex_token_primary": ""})
        self.assertIsNone(resolve_plex_for_tautulli(s2, "t1"))

        s3 = s.model_copy(update={"plex_client_identifier": ""})
        self.assertIsNone(resolve_plex_for_tautulli(s3, "t1"))

    def test_secondary_profile(self) -> None:
        s = Settings(
            tautulli_servers=[],
            plex_servers=[
                PlexServer(
                    id="p4",
                    base_url="https://plex4.example.com",
                    tautulli_server_id="t4",
                    token_profile="secondary",
                )
            ],
            plex_token_primary="aaa",
            plex_token_secondary="bbb",
            plex_client_identifier="cid",
        )
        r = resolve_plex_for_tautulli(s, "t4")
        assert r is not None
        self.assertEqual(r[1], "bbb")

    def test_mapped_server_ids_unique(self) -> None:
        s = Settings(
            tautulli_servers=[],
            plex_servers=[
                PlexServer(
                    id="a",
                    base_url="https://a.example.com",
                    tautulli_server_id="s1",
                    token_profile="primary",
                ),
                PlexServer(
                    id="b",
                    base_url="https://b.example.com",
                    tautulli_server_id="s1",
                    token_profile="primary",
                ),
            ],
            plex_token_primary="x",
            plex_client_identifier="c",
        )
        self.assertEqual(plex_mapped_tautulli_server_ids(s), ["s1"])


if __name__ == "__main__":
    unittest.main()
