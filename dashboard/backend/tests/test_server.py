def test_is_up_false_when_nothing_listening():
    from capevolve_dashboard import server
    # Port 1 is never an http health endpoint; should be quick-false.
    assert server.is_up(1) is False


def test_resolve_static_dir_returns_path_or_none():
    from capevolve_dashboard import server
    out = server.resolve_static_dir()
    assert out is None or out.name == "dist"


def test_url_for():
    from capevolve_dashboard import server
    assert server.url_for(7878) == "http://127.0.0.1:7878"
