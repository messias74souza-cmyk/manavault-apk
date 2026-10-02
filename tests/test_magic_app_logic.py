import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "Magic versão 4.py"

spec = importlib.util.spec_from_file_location("magic_app_module", MODULE_PATH)
magic_app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(magic_app)


def test_parse_decklist_text_handles_main_and_sideboard():
    decklist = """
    4 Lightning Bolt
    2 Mountain
    Sideboard
    2 Engineered Explosives
    1 Dispel
    """

    parsed = magic_app.parse_decklist_text(decklist)

    assert parsed["main"]["Lightning Bolt"]["qty"] == 4
    assert parsed["main"]["Mountain"]["qty"] == 2
    assert parsed["side"]["Engineered Explosives"]["qty"] == 2
    assert parsed["side"]["Dispel"]["qty"] == 1


def test_keyword_reference_contains_twenty_six_searchable_rules():
    assert len(magic_app.KEYWORD_RULES) == 26

    matches = magic_app.filter_keyword_rules("toque mortifero")

    assert [rule["name"] for rule in matches] == ["Toque Mortífero"]


def test_keyword_reference_is_alphabetical_in_portuguese():
    rules = magic_app.filter_keyword_rules("")
    names = [rule["name"] for rule in rules]

    assert names == sorted(names, key=magic_app.normalize_keyword_search)


def test_keyword_reference_explains_multiple_blockers():
    for rule in magic_app.KEYWORD_RULES:
        sections = magic_app.get_keyword_rule_sections(rule)
        assert any(heading == "Contra vários bloqueadores" for heading, _ in sections)

    sections = magic_app.get_keyword_rule_sections(
        next(rule for rule in magic_app.KEYWORD_RULES if rule["name"] == "Voar")
    )
    multi_blocker_note = next(text for heading, text in sections if heading == "Contra vários bloqueadores")

    assert "bloqueadores" in multi_blocker_note


def test_keyword_reference_searches_rule_details():
    matches = magic_app.filter_keyword_rules("jogador defensor")

    assert any(rule["name"] == "Atropelar" for rule in matches)


def test_keyword_reference_searches_new_effects_without_accents():
    shroud_matches = magic_app.filter_keyword_rules("manto")
    persist_matches = magic_app.filter_keyword_rules("persist")

    assert any(rule["english"] == "Shroud" for rule in shroud_matches)
    assert any(rule["name"] == "Persist" for rule in persist_matches)


def test_filter_matches_by_multiple_criteria():
    matches = [
        {
            "deck": "Mono Red",
            "opponent_deck": "Izzet",
            "event_type": "Casual",
            "play_draw": "Você (Play)",
            "match_date": "2026-09-10",
            "tags": ["meta local"],
            "result": "Win",
        },
        {
            "deck": "Mono Red",
            "opponent_deck": "Azorius",
            "event_type": "Torneio Oficial (FNM/Regional)",
            "play_draw": "O Adversário (Draw)",
            "match_date": "2026-09-20",
            "tags": ["sideboard errado"],
            "result": "Loss",
        },
    ]

    filtered = magic_app.filter_matches(
        matches,
        deck="Mono Red",
        opponent="Izzet",
        event="Casual",
        play_draw="Você (Play)",
        start_date="2026-09-01",
        end_date="2026-09-30",
        tags=["meta local"],
    )

    assert len(filtered) == 1
    assert filtered[0]["opponent_deck"] == "Izzet"
