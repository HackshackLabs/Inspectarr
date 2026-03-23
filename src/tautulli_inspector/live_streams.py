"""Helpers for live activity presentation."""


def group_live_streams_by_server(sessions: list[dict]) -> dict[str, list[dict[str, str]]]:
    """Group active sessions for live-dashboard tooltips keyed by server_id."""
    out: dict[str, list[dict[str, str]]] = {}
    for session in sessions:
        if not isinstance(session, dict):
            continue
        sid = str(session.get("server_id") or "")
        if not sid:
            continue
        user = str(session.get("friendly_name") or session.get("user") or "").strip() or "Unknown user"
        title_base = str(session.get("grandparent_title") or session.get("title") or "").strip() or "Unknown title"
        pmi = session.get("parent_media_index")
        mi = session.get("media_index")
        extra = ""
        if pmi is not None and str(pmi) != "" and mi is not None and str(mi) != "":
            extra = f" S{pmi}E{mi}"
        title = f"{title_base}{extra}".strip()
        out.setdefault(sid, []).append({"user": user, "title": title})
    return out
