"""Tests for Tautulli library media play aggregation."""

from scoparr.tautulli_library_client import (
    ingest_library_media_rows,
    library_plays_for_radarr_movie,
)


def test_ingest_sums_plays_by_tmdb_and_title_year() -> None:
    by_tmdb: dict[int, int] = {}
    by_ty: dict[tuple[str, int], int] = {}
    sec_tmdb: set[int] = set()
    sec_ty: set[tuple[str, int]] = set()
    ingest_library_media_rows(
        [
            {"guid": "plex://movie/guid1", "title": "Alpha", "year": 2020, "play_count": 3},
            {"guid": "plex://movie/guid1", "title": "Alpha", "year": 2020, "play_count": 1},
        ],
        into_by_tmdb=by_tmdb,
        into_by_title_year=by_ty,
        section_tmdb_ids=sec_tmdb,
        section_title_years=sec_ty,
    )
    # Without themoviedb guid, rows fall through to title+year
    assert by_tmdb == {}
    assert by_ty.get(("alpha", 2020)) == 4
    assert ("alpha", 2020) in sec_ty

    ingest_library_media_rows(
        [
            {
                "guid": "com.plexapp.agents.themoviedb://movie/999?lang=en",
                "title": "Beta",
                "year": 2019,
                "play_count": 2,
            },
        ],
        into_by_tmdb=by_tmdb,
        into_by_title_year=by_ty,
        section_tmdb_ids=sec_tmdb,
        section_title_years=sec_ty,
    )
    assert by_tmdb.get(999) == 2
    assert 999 in sec_tmdb


def test_library_plays_for_radarr_prefers_tmdb_in_section() -> None:
    m = {"title": "Beta", "year": 2019, "tmdbId": 999}
    got = library_plays_for_radarr_movie(
        m,
        plays_by_tmdb={999: 0},
        plays_by_title_year={("beta", 2019): 5},
        section_tmdb_ids={999},
        section_title_years={("beta", 2019)},
    )
    assert got == (0, "tmdb")


def test_library_plays_none_when_not_in_section() -> None:
    m = {"title": "Gamma", "year": 2021, "tmdbId": 1}
    assert (
        library_plays_for_radarr_movie(
            m,
            plays_by_tmdb={},
            plays_by_title_year={},
            section_tmdb_ids=set(),
            section_title_years=set(),
        )
        is None
    )
