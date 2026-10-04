import json
import tempfile
import unittest
from pathlib import Path

from data_store import (
    DataStore,
    analyze_deck,
    check_deck_legality,
    calculate_full_stats,
    filter_matches,
    find_card_defaults,
    format_decklist,
    normalize_data,
    parse_decklist_text,
)


class DataStoreTests(unittest.TestCase):
    def test_deck_analysis_matches_desktop_summary_rules(self):
        summary = analyze_deck({
            "main": {
                "Lightning Bolt": {"qty": 4, "cmc": 1, "color": "R", "type": "Instantânea"},
                "Mountain": {"qty": 60, "cmc": 0, "color": "R", "type": "Terreno"},
            },
            "side": {"Lightning Bolt": {"qty": 1, "cmc": 1, "color": "R", "type": "Instantânea"}},
        })

        self.assertEqual(summary["main_count"], 64)
        self.assertEqual(summary["side_count"], 1)
        self.assertEqual(summary["mana_curve"], {0: 60, 1: 4})
        self.assertEqual(summary["colors"], {"R": 65})
        self.assertIn("Mais de 4 cópias: Lightning Bolt", summary["warnings"])

    def test_loads_desktop_data_and_migrates_match_fields(self):
        legacy = {
            "decks": {
                "Mono Red": {
                    "main": {"Lightning Bolt": {"qty": 4, "cmc": 1, "color": "R"}},
                    "side": {},
                }
            },
            "opponent_decks": ["Izzet"],
            "matches": [{"deck": "Mono Red", "result": "Win"}],
        }

        migrated = normalize_data(legacy)

        self.assertEqual(migrated["decks"]["Mono Red"]["main"]["Lightning Bolt"]["qty"], 4)
        self.assertEqual(migrated["decks"]["Mono Red"]["main"]["Lightning Bolt"]["type"], "Outros")
        self.assertEqual(migrated["matches"][0]["play_draw"], "Você (Play)")
        self.assertEqual(migrated["matches"][0]["tags"], [])

    def test_save_reload_and_export_preserve_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "magic_data.json"
            store = DataStore(path)
            store.load()
            store.data["decks"]["Elfos"] = {
                "main": {"Llanowar Elves": {"qty": 4, "type": "Criatura"}},
                "side": {},
            }
            store.save()

            reloaded = DataStore(path)
            reloaded.load()
            exported = json.loads(reloaded.export_json())

            self.assertEqual(exported["decks"]["Elfos"]["main"]["Llanowar Elves"]["qty"], 4)
            self.assertEqual(exported["schema_version"], 1)

    def test_invalid_import_does_not_replace_current_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "magic_data.json"
            store = DataStore(path)
            store.load()
            store.data["decks"]["Keep me"] = {"main": {}, "side": {}}
            store.save()

            with self.assertRaises(ValueError):
                store.import_json("{not json}")

            self.assertIn("Keep me", store.data["decks"])
            self.assertIn("Keep me", json.loads(path.read_text(encoding="utf-8"))["decks"])

    def test_save_keeps_backup_of_previous_document(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "magic_data.json"
            store = DataStore(path)
            store.load()
            store.data["decks"]["Before"] = {"main": {}, "side": {}}
            store.save()
            store.data["decks"]["After"] = {"main": {}, "side": {}}
            store.save()

            backup = json.loads(path.with_suffix(".backup.json").read_text(encoding="utf-8"))
            self.assertIn("Before", backup["decks"])
            self.assertNotIn("After", backup["decks"])

    def test_parse_and_format_decklist(self):
        text = """
        4 Lightning Bolt
        20 Mountain
        Sideboard
        2 Smash to Smithereens
        """
        parsed = parse_decklist_text(text)
        self.assertEqual(parsed["main"]["Lightning Bolt"]["qty"], 4)
        self.assertEqual(parsed["main"]["Mountain"]["qty"], 20)
        self.assertEqual(parsed["side"]["Smash to Smithereens"]["qty"], 2)

        formatted = format_decklist("Burn", parsed)
        self.assertIn("4 Lightning Bolt", formatted)
        self.assertIn("Sideboard", formatted)
        self.assertIn("2 Smash to Smithereens", formatted)

    def test_find_card_defaults(self):
        decks = {
            "Burn": {
                "main": {
                    "Lightning Bolt": {"qty": 4, "cmc": 1, "color": "R", "type": "Mágica Instantânea"}
                },
                "side": {},
            }
        }
        found = find_card_defaults(decks, "lightning bolt")
        self.assertIsNotNone(found)
        self.assertEqual(found["cmc"], 1)
        self.assertEqual(found["color"], "R")
        self.assertEqual(found["type"], "Mágica Instantânea")

    def test_check_deck_legality(self):
        legal_deck = {
            "main": {f"Card_{i}": {"qty": 1} for i in range(60)},
            "side": {"Side_1": {"qty": 15}},
        }
        res = check_deck_legality(legal_deck)
        self.assertFalse(res["valid"])  # Side_1 has 15 copies (> 4)
        self.assertTrue(any("Side_1" in issue for issue in res["issues"]))

        ok_deck = {
            "main": {"Mountain": {"qty": 60}},
            "side": {f"Side_{i}": {"qty": 1} for i in range(15)},
        }
        res_ok = check_deck_legality(ok_deck)
        self.assertTrue(res_ok["valid"])
        self.assertEqual(len(res_ok["issues"]), 0)

    def test_stats_and_filters(self):
        matches = [
            {"deck": "Burn", "opponent_deck": "Murktide", "result": "Win", "play_draw": "Você (Play)", "event_type": "Casual", "match_date": "2026-10-01", "tags": ["test"]},
            {"deck": "Burn", "opponent_deck": "Tron", "result": "Loss", "play_draw": "O Adversário (Draw)", "event_type": "Casual", "match_date": "2026-10-02", "tags": ["fnm"]},
        ]
        filtered = filter_matches(matches, deck="Burn", tags="test")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["opponent_deck"], "Murktide")

        stats = calculate_full_stats(matches)
        self.assertEqual(stats["total_matches"], 2)
        self.assertEqual(stats["wins"], 1)
        self.assertEqual(stats["win_rate"], 50.0)
        self.assertEqual(stats["play_wr"], 100.0)
        self.assertEqual(stats["draw_wr"], 0.0)
        self.assertIn("DESEMPENHO POR DECK", stats["summary_text"])
        self.assertIn("CONFRONTOS (MATCHUPS)", stats["summary_text"])

    def test_image_uri_normalization_and_scryfall_mapping(self):
        from data_store import map_scryfall_type

        self.assertEqual(map_scryfall_type("Creature — Goblin Guide"), "Criatura")
        self.assertEqual(map_scryfall_type("Instant"), "Mágica Instantânea")
        self.assertEqual(map_scryfall_type("Sorcery"), "Feitiço")
        self.assertEqual(map_scryfall_type("Basic Land — Mountain"), "Terreno")
        self.assertEqual(map_scryfall_type("Legendary Planeswalker — Chandra"), "Planeswalker")
        self.assertEqual(map_scryfall_type("Enchantment — Saga"), "Encantamento")
        self.assertEqual(map_scryfall_type("Artifact"), "Artefato")
        self.assertEqual(map_scryfall_type("Unknown Type"), "Outros")

        legacy = {
            "decks": {
                "Burn": {
                    "main": {"Lightning Bolt": {"qty": 4, "cmc": 1, "color": "R", "type": "Mágica Instantânea"}},
                    "side": {},
                }
            }
        }
        normalized = normalize_data(legacy)
        bolt = normalized["decks"]["Burn"]["main"]["Lightning Bolt"]
        self.assertIn("image_uri", bolt)
        self.assertIn("gemini_api_key", normalized)
        from data_store import DEFAULT_GEMINI_API_KEY
        self.assertEqual(normalized["gemini_api_key"], DEFAULT_GEMINI_API_KEY)

    def test_ocr_line_cleaning_and_scryfall_parsing(self):
        from data_store import clean_ocr_line, _parse_scryfall_card

        cleaned = clean_ocr_line("  >> Sol Ring (Artifact) !!  ")
        self.assertEqual(cleaned, "Sol Ring Artifact")

        card_data = {
            "name": "Lightning Bolt",
            "printed_name": "Raio",
            "cmc": 1.0,
            "colors": ["R"],
            "type_line": "Instant",
            "image_uris": {"normal": "https://cards.scryfall.io/test.jpg"},
        }
        parsed = _parse_scryfall_card(card_data)
        self.assertEqual(parsed["name"], "Lightning Bolt")
        self.assertEqual(parsed["printed_name"], "Raio")
        self.assertEqual(parsed["cmc"], 1)
        self.assertEqual(parsed["color"], "R")
        self.assertEqual(parsed["type"], "Mágica Instantânea")
        self.assertEqual(parsed["image_url"], "https://cards.scryfall.io/test.jpg")

    def test_offline_card_catalog_and_search(self):
        from data_store import search_card_database, find_card_defaults

        # Test offline search for Portuguese cards
        anel = search_card_database("Anel", limit=2)
        self.assertTrue(len(anel) > 0)
        self.assertEqual(anel[0]["name"], "Anel Solar")

        # Test offline search for user's specific cards
        cascavel = search_card_database("cascavel", limit=2)
        self.assertTrue(len(cascavel) > 0)
        self.assertEqual(cascavel[0]["name"], "Cascavel-do-rio Listrada")

        # Test defaults for a card in catalog
        defaults = find_card_defaults({}, "Sol Ring")
        self.assertIsNotNone(defaults)
        self.assertEqual(defaults["cmc"], 1)
        self.assertEqual(defaults["color"], "C")
        self.assertEqual(defaults["type"], "Artefato")

    def test_fuzzy_matching_and_title_cleaning(self):
        from data_store import (
            clean_ocr_card_title,
            card_similarity,
            fuzzy_find_in_catalog_or_decks,
            find_card_in_catalog_or_decks,
        )

        # 1. Title cleaning
        self.assertEqual(clean_ocr_card_title("Lightning Bolt {1}{R}"), "Lightning Bolt")
        self.assertEqual(clean_ocr_card_title("Raio 1"), "Raio")
        self.assertEqual(clean_ocr_card_title("Sol Ring 1"), "Sol Ring")

        # 2. Similarity calculation
        self.assertTrue(card_similarity("ralo", "raio") >= 0.75)
        self.assertTrue(card_similarity("ane1 so1ar", "anel solar") >= 0.80)
        self.assertTrue(card_similarity("banana", "anel solar") < 0.65)

        # 3. Fuzzy search in catalog for cards with typical OCR errors
        m1, s1 = fuzzy_find_in_catalog_or_decks("Ralo")
        self.assertIsNotNone(m1)
        self.assertEqual(m1["name"], "Raio")

        m2, s2 = fuzzy_find_in_catalog_or_decks("Ane1 So1ar")
        self.assertIsNotNone(m2)
        self.assertEqual(m2["name"], "Anel Solar")

        m3, s3 = fuzzy_find_in_catalog_or_decks("Cascave1 do rio listrada")
        self.assertIsNotNone(m3)
        self.assertEqual(m3["name"], "Cascavel-do-rio Listrada")

        # 4. find_card_in_catalog_or_decks uses fuzzy fallback
        found = find_card_in_catalog_or_decks("Lightn1ng Bo1t")
        self.assertIsNotNone(found)
        self.assertIn(found["name"], ["Lightning Bolt", "Raio"])

    def test_simulate_sample_hand(self):
        from data_store import simulate_sample_hand
        deck_cards = {
            "Mountain": {"qty": 20, "type": "Terreno", "cmc": 0, "color": "R"},
            "Lightning Bolt": {"qty": 20, "type": "Instantânea", "cmc": 1, "color": "R"},
            "Monastery Swiftspear": {"qty": 20, "type": "Criatura", "cmc": 1, "color": "R"},
        }
        res = simulate_sample_hand(deck_cards, hand_size=7)
        self.assertEqual(len(res["hand"]), 7)
        self.assertEqual(len(res["library"]), 53)
        self.assertEqual(res["total_deck_cards"], 60)
        self.assertEqual(res["lands_count"] + res["spells_count"], 7)

    def test_calculate_mana_base_recommendation(self):
        from data_store import calculate_mana_base_recommendation
        deck_cards = {
            "Mountain": {"qty": 18, "type": "Terreno", "cmc": 0, "color": "R"},
            "Lightning Bolt": {"qty": 20, "type": "Instantânea", "cmc": 1, "color": "R"},
            "Counterspell": {"qty": 22, "type": "Instantânea", "cmc": 2, "color": "U"},
        }
        rec = calculate_mana_base_recommendation(deck_cards)
        self.assertEqual(rec["current_lands"], 18)
        self.assertTrue(rec["recommended_lands"] in (19, 20, 21))
        self.assertIn("R", rec["color_sources"])
        self.assertIn("U", rec["color_sources"])


if __name__ == "__main__":
    unittest.main()

