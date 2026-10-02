"""Local JSON storage compatible with ManaVault's Windows data format."""

import json
import shutil
from datetime import datetime
from pathlib import Path

DATA_SCHEMA_VERSION = 1


def empty_data():
    return {
        "schema_version": DATA_SCHEMA_VERSION,
        "decks": {},
        "opponent_decks": [],
        "matches": [],
        "changes": [],
        "last_tab": 0,
        "last_deck": "",
    }


def normalize_data(data):
    """Validate and migrate data without discarding supported user fields."""
    if not isinstance(data, dict):
        raise ValueError("O arquivo precisa conter um objeto JSON.")

    normalized = dict(data)
    normalized["schema_version"] = DATA_SCHEMA_VERSION
    normalized.setdefault("decks", {})
    normalized.setdefault("opponent_decks", [])
    normalized.setdefault("matches", [])
    normalized.setdefault("changes", [])
    normalized.setdefault("last_tab", 0)
    normalized.setdefault("last_deck", "")

    if not isinstance(normalized["decks"], dict):
        raise ValueError("A lista de decks no arquivo não é válida.")
    if not isinstance(normalized["matches"], list):
        raise ValueError("O histórico de partidas no arquivo não é válido.")
    if not isinstance(normalized["opponent_decks"], list):
        raise ValueError("A lista de decks adversários no arquivo não é válida.")
    if not isinstance(normalized["changes"], list):
        raise ValueError("O histórico de alterações no arquivo não é válido.")

    for deck_name, deck_info in list(normalized["decks"].items()):
        if not isinstance(deck_info, dict):
            normalized["decks"][deck_name] = {"main": {}, "side": {}}
            continue

        if "cards" in deck_info and isinstance(deck_info["cards"], dict):
            old_cards = deck_info["cards"]
            if (
                any(not isinstance(card, dict) or "qty" not in card for card in old_cards.values())
                or not any(section in deck_info for section in ("main", "side"))
            ):
                main = {}
                for card_name, card in old_cards.items():
                    if isinstance(card, dict):
                        migrated_card = dict(card)
                        migrated_card.setdefault("type", "Outros")
                    else:
                        migrated_card = {
                            "qty": int(card), "cmc": 0, "color": "C", "type": "Outros"
                        }
                    main[card_name] = migrated_card
                normalized["decks"][deck_name] = {"main": main, "side": {}}
                continue

        if "main" not in deck_info:
            normalized["decks"][deck_name] = {"main": {}, "side": {}}
            continue

        for section in ("main", "side"):
            cards = deck_info.setdefault(section, {})
            if not isinstance(cards, dict):
                raise ValueError(f"A seção {section} do deck {deck_name!r} não é válida.")
            for card in cards.values():
                if isinstance(card, dict):
                    card.setdefault("type", "Outros")

    today = datetime.now().strftime("%Y-%m-%d")
    for match in normalized["matches"]:
        if not isinstance(match, dict):
            raise ValueError("Uma partida do histórico não é válida.")
        match.setdefault("play_draw", "Você (Play)")
        match.setdefault("games_score", "2 x 0")
        match.setdefault("event_type", "Casual")
        match.setdefault("match_date", today)
        match.setdefault("tags", [])

    return normalized


def analyze_deck(deck):
    """Return ManaVault's deck counts, curve, types, colors, and basic warnings."""
    main = deck.get("main", {})
    side = deck.get("side", {})

    def quantity(card):
        value = card.get("qty", 0) if isinstance(card, dict) else card
        return int(value or 0)

    main_count = sum(quantity(card) for card in main.values())
    side_count = sum(quantity(card) for card in side.values())
    curve = {}
    types = {}
    colors = {color: 0 for color in "WUBRGC"}

    for card in main.values():
        qty = quantity(card)
        cmc = int(card.get("cmc", 0) or 0) if isinstance(card, dict) else 0
        card_type = card.get("type", "Outros") if isinstance(card, dict) else "Outros"
        curve[cmc] = curve.get(cmc, 0) + qty
        types[card_type] = types.get(card_type, 0) + qty

    for card in list(main.values()) + list(side.values()):
        qty = quantity(card)
        color_value = card.get("color", "C") if isinstance(card, dict) else "C"
        for color in colors:
            if color in str(color_value).upper():
                colors[color] += qty

    copies = {}
    for section in (main, side):
        for card_name, card in section.items():
            copies[card_name] = copies.get(card_name, 0) + quantity(card)

    warnings = []
    if main_count < 60:
        warnings.append(f"Mainboard abaixo de 60 ({main_count})")
    if side_count > 15:
        warnings.append(f"Sideboard acima de 15 ({side_count})")
    basic_lands = {"FOREST", "ISLAND", "SWAMP", "MOUNTAIN", "PLAINS", "WASTES"}
    too_many = [
        name for name, qty in copies.items()
        if qty > 4 and name.upper() not in basic_lands
    ]
    if too_many:
        warnings.append("Mais de 4 cópias: " + ", ".join(too_many))

    return {
        "main_count": main_count,
        "side_count": side_count,
        "mana_curve": dict(sorted(curve.items())),
        "types": dict(sorted(types.items())),
        "colors": {color: qty for color, qty in colors.items() if qty},
        "warnings": warnings,
    }


class DataStore:
    """Stores a ManaVault JSON document in the app's private data directory."""

    def __init__(self, path):
        self.path = Path(path)
        self.data = empty_data()

    def load(self):
        if not self.path.exists():
            self.save()
            return self.data
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
            self.data = normalize_data(parsed)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("O arquivo de dados está corrompido; os dados não foram alterados.") from exc
        return self.data

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            backup_path = self.path.with_suffix(".backup.json")
            try:
                shutil.copyfile(self.path, backup_path)
            except OSError:
                pass

        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary_path.write_text(
                json.dumps(self.data, indent=4, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(self.path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def import_json(self, text):
        """Validate an imported desktop backup before replacing app data."""
        try:
            imported = normalize_data(json.loads(text))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("O arquivo selecionado não é um JSON válido.") from exc

        previous = self.data
        self.data = imported
        try:
            self.save()
        except OSError:
            self.data = previous
            raise
        return self.data

    def export_json(self):
        return json.dumps(self.data, indent=4, ensure_ascii=False) + "\n"
