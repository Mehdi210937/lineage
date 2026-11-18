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
