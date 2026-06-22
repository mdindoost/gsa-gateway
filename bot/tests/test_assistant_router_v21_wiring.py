def test_no_router_when_flag_off(monkeypatch):
    monkeypatch.setattr("bot.config.ROUTER_V21", False, raising=False)
    from bot.core.assistant import maybe_build_unified_router
    assert maybe_build_unified_router(db_path=":memory:", embedder=object(),
                                      intent_detector=object()) is None


def test_router_built_when_flag_on(monkeypatch):
    monkeypatch.setattr("bot.config.ROUTER_V21", True, raising=False)
    import bot.core.assistant as a
    monkeypatch.setattr(a, "build_classifier", lambda conn, emb, *a, **k: "CLF")
    monkeypatch.setattr(a, "verify_stamp", lambda emb, stamp: None)
    monkeypatch.setattr(a, "encoder_stamp", lambda emb: {"model": "m"})
    r = a.maybe_build_unified_router(db_path=":memory:", embedder=object(), intent_detector="ID")
    assert r is not None and r.classifier == "CLF" and r.intent_detector == "ID"
