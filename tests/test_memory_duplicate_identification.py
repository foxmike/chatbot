from chatbot.consolidation.duplicates import identify_duplicates


def test_groups_only_trivial_text_variants_and_retains_source_ids():
    decision = identify_duplicates(
        [
            {"id": "fact-b", "user_id": "u1", "fact_text": "  I prefer tea! "},
            {"id": "fact-a", "user_id": "u1", "fact_text": "i prefer TEA"},
        ]
    )
    assert len(decision.groups) == 1
    group = decision.groups[0]
    assert group.canonical_id == "fact-a"
    assert group.source_ids == ("fact-a", "fact-b")
    assert len(group.fingerprint) == 64


def test_keeps_distinct_assertions_values_times_owners_and_scopes_separate():
    entries = [
        {"id": "a", "user_id": "u1", "fact_text": "I live in Oslo"},
        {"id": "b", "user_id": "u1", "fact_text": "I live in Bergen"},
        {"id": "c", "user_id": "u1", "fact_text": "I lived in Oslo in 2019"},
        {"id": "d", "user_id": "u2", "fact_text": "I live in Oslo"},
        {"id": "e", "user_id": "u1", "companion_id": "c1", "fact_text": "I live in Oslo"},
        {"id": "f", "user_id": "u1", "companion_id": "c2", "fact_text": "I live in Oslo"},
        {"id": "g", "user_id": "u1", "room_id": "r1", "fact_text": "I live in Oslo"},
        {"id": "h", "user_id": "u1", "room_id": "r2", "fact_text": "I live in Oslo"},
    ]
    assert identify_duplicates(entries).groups == ()


def test_missing_owner_is_not_consolidated_and_order_is_input_independent():
    entries = [
        {"id": "x", "fact_text": "same assertion"},
        {"id": "z", "user_id": "u1", "fact_text": "Same assertion."},
        {"id": "a", "user_id": "u1", "fact_text": "same assertion"},
    ]
    forward = identify_duplicates(entries)
    reverse = identify_duplicates(list(reversed(entries)))
    assert forward == reverse
    assert forward.groups[0].source_ids == ("a", "z")
