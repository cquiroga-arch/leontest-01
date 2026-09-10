from vagscan.dtc_db import DtcDatabase


def test_load_default_has_entries():
    db = DtcDatabase.load_default()
    assert len(db) > 20


def test_lookup_known_generic_code():
    db = DtcDatabase.load_default()
    matches = db.lookup("P0300")
    assert matches
    assert "misfire" in matches[0].title.lower()


def test_lookup_unknown_code_returns_empty():
    db = DtcDatabase.load_default()
    assert db.lookup("P9999") == []


def test_meta_entry_is_not_loaded_as_a_dtc():
    db = DtcDatabase.load_default()
    assert not any("VCDS" in entry.title for entry in db.search(""))


def test_by_module_returns_airbag_guidance():
    db = DtcDatabase.load_default()
    airbag_entries = db.by_module("15")
    assert airbag_entries
    assert all(e.module_address == "15" for e in airbag_entries)


def test_search_matches_on_module_name():
    db = DtcDatabase.load_default()
    results = db.search("airbag")
    assert results
