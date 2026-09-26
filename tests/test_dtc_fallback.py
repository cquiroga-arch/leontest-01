"""Every scanned code should show *something* useful, even if it isn't in
the curated database - and the newly-added common codes should be present.
"""
from vagscan.dtc_db import DtcDatabase, describe_code


def test_p0036_now_in_database_with_fixes():
    db = DtcDatabase.load_default()
    info = db.lookup("P0036")
    assert info
    assert info[0].common_causes
    assert info[0].suggested_checks


def test_describe_falls_back_for_unknown_code():
    db = DtcDatabase.load_default()
    info = db.describe("P2199")  # not curated
    assert info.title  # never blank
    assert "SAE" in info.source or "estructura" in info.source


def test_has_distinguishes_curated_from_fallback():
    db = DtcDatabase.load_default()
    assert db.has("P0300") is True
    assert db.has("P2199") is False


def test_fallback_reports_system_area_by_letter():
    assert "Chasis" in describe_code("C1234").title or "Chasis" in describe_code("C1234").description
    assert "Carrocer" in describe_code("B1234").description
    assert "Red" in describe_code("U1234").description


def test_fallback_flags_manufacturer_specific_codes():
    # second char '1' = manufacturer-specific
    generic = describe_code("P0420")
    mfr = describe_code("P1136")
    assert "estándar" in generic.description.lower()
    assert "fabricante" in mfr.description.lower()


def test_fallback_never_raises_on_odd_input():
    for code in ("", "P", "X9999", "P03"):
        info = describe_code(code)
        assert info.title is not None
