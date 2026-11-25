from lineage.extract.schema import ExtractionResult, InfluenceClaim, validate_against_source

BIO = "Duo formé en 2008, très influencé par Old Drone Master et la scène chopped and screwed."


def test_keeps_quotes_present_in_source():
    r = ExtractionResult(claims=[InfluenceClaim(
        other_artist="Old Drone Master", relation="influenced_by",
        evidence_quote="très influencé par Old Drone Master", confidence=0.9)])
    assert len(validate_against_source(r, BIO).claims) == 1


def test_drops_hallucinated_quotes():
    r = ExtractionResult(claims=[InfluenceClaim(
        other_artist="Famous Band", relation="influenced_by",
        evidence_quote="cite Famous Band comme influence majeure", confidence=0.95)])
    assert validate_against_source(r, BIO).claims == []


def test_demo_claims_quote_the_demo_bio():
    """Les affirmations fictives de la base de démo respectent la même règle que le vrai LLM."""
    from fake_api import DEMO_LLM_CLAIMS, TEST_COVEN_BIO

    claims = [
        InfluenceClaim(other_artist=other, relation=rel, evidence_quote=quote, confidence=conf)
        for _, other, rel, quote, conf in DEMO_LLM_CLAIMS
        if other is not None
    ]
    kept = validate_against_source(ExtractionResult(claims=claims), TEST_COVEN_BIO).claims
    assert len(kept) == len(claims) == 6
