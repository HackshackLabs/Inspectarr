"""Unit tests for Library Unwatched Sonarr row state annotation."""

from tautulli_inspector.sonarr_client import annotate_library_unwatched_row_state


def test_sonarr_not_configured_payload() -> None:
    p = annotate_library_unwatched_row_state(
        "show",
        {"sonarr_configured": False, "message": "off"},
    )
    assert p["media_state"] == "ok"
    assert p["actions_disabled"] is False


def test_missing_series_show() -> None:
    p = annotate_library_unwatched_row_state(
        "show",
        {"series_found": False, "message": "Series not found.", "file_count": 0},
    )
    assert p["media_state"] == "missing"
    assert p["actions_disabled"] is True
    assert p["media_state_detail"] == "Series not found."


def test_show_no_files_on_disk() -> None:
    p = annotate_library_unwatched_row_state(
        "show",
        {
            "series_found": True,
            "file_count": 0,
            "message": None,
            "monitored": True,
        },
    )
    assert p["media_state"] == "no_file"
    assert p["actions_disabled"] is False


def test_season_no_episodes_in_sonarr() -> None:
    p = annotate_library_unwatched_row_state(
        "season",
        {
            "series_found": True,
            "file_count": 0,
            "message": "No episodes for this season in Sonarr.",
        },
    )
    assert p["media_state"] == "missing"
    assert p["actions_disabled"] is True


def test_season_zero_files_on_disk() -> None:
    p = annotate_library_unwatched_row_state(
        "season",
        {
            "series_found": True,
            "file_count": 0,
            "message": None,
        },
    )
    assert p["media_state"] == "no_file"
    assert p["actions_disabled"] is False


def test_episode_not_found() -> None:
    p = annotate_library_unwatched_row_state(
        "episode",
        {
            "series_found": True,
            "file_count": 0,
            "message": "Episode S1E2 not found in Sonarr.",
        },
    )
    assert p["media_state"] == "missing"
    assert p["actions_disabled"] is True


def test_episode_no_file_on_disk() -> None:
    p = annotate_library_unwatched_row_state(
        "episode",
        {
            "series_found": True,
            "file_count": 0,
            "message": None,
            "monitored": True,
        },
    )
    assert p["media_state"] == "no_file"
    assert p["actions_disabled"] is False
