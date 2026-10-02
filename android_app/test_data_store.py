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
        self.assertEqual(bolt["image_uri"], "")
        self.assertIn("gemini_api_key", normalized)
        self.assertEqual(normalized["gemini_api_key"], "")

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


if __name__ == "__main__":
    unittest.main()

