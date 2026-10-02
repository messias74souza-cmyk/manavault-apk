import json
import tempfile
import unittest
from pathlib import Path

from data_store import DataStore, analyze_deck, normalize_data


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


if __name__ == "__main__":
    unittest.main()
