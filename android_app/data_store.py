"""Local JSON storage compatible with ManaVault's Windows data format."""

import json
import re
import shutil
import unicodedata
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


def normalize_keyword_search(value):
    """Normaliza texto para busca sem diferenciar maiúsculas ou acentos."""
    decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def normalize_tags(raw_tags):
    """Converte tags em lista normalizada."""
    if raw_tags is None:
        return []
    if isinstance(raw_tags, str):
        return [tag.strip() for tag in raw_tags.split(",") if tag.strip()]
    if isinstance(raw_tags, (list, tuple, set)):
        items = []
        for item in raw_tags:
            items.extend(normalize_tags(item))
        return items
    return []


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
            normalized["decks"][deck_name] = {"main": {}, "side": {}, "tags": "", "game_plan": ""}
            continue

        deck_info.setdefault("tags", "")
        deck_info.setdefault("game_plan", "")

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
                        migrated_card.setdefault("cmc", 0)
                        migrated_card.setdefault("color", "C")
                        migrated_card.setdefault("description", "")
                    else:
                        migrated_card = {
                            "qty": int(card),
                            "cmc": 0,
                            "color": "C",
                            "type": "Outros",
                            "description": "",
                        }
                    main[card_name] = migrated_card
                normalized["decks"][deck_name] = {
                    "main": main,
                    "side": {},
                    "tags": deck_info.get("tags", ""),
                    "game_plan": deck_info.get("game_plan", ""),
                }
                continue

        if "main" not in deck_info:
            normalized["decks"][deck_name] = {
                "main": {},
                "side": {},
                "tags": deck_info.get("tags", ""),
                "game_plan": deck_info.get("game_plan", ""),
            }
            continue

        for section in ("main", "side"):
            cards = deck_info.setdefault(section, {})
            if not isinstance(cards, dict):
                raise ValueError(f"A seção {section} do deck {deck_name!r} não é válida.")
            for card in cards.values():
                if isinstance(card, dict):
                    card.setdefault("type", "Outros")
                    card.setdefault("cmc", 0)
                    card.setdefault("color", "C")
                    card.setdefault("description", "")
                    card.setdefault("image_uri", "")

    today = datetime.now().strftime("%Y-%m-%d")
    for match in normalized["matches"]:
        if not isinstance(match, dict):
            raise ValueError("Uma partida do histórico não é válida.")
        match.setdefault("deck", "")
        match.setdefault("opponent_name", "")
        match.setdefault("opponent_deck", "")
        match.setdefault("result", "Win")
        match.setdefault("play_draw", "Você (Play)")
        match.setdefault("games_score", "2 x 0")
        match.setdefault("event_type", "Casual")
        match.setdefault("match_date", today)
        match.setdefault("tags", [])
        match.setdefault("notes", "")

    return normalized


def parse_decklist_text(decklist_text):
    """Interpreta decklists em formato simples, incluindo sideboard e linhas com quantidade."""
    parsed = {"main": {}, "side": {}}
    section = "main"
    for raw_line in str(decklist_text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        upper = line.upper()
        if upper.startswith("SIDEBOARD") or upper.startswith("SB"):
            section = "side"
            continue
        if upper.startswith("MAINBOARD") or upper.startswith("MB"):
            section = "main"
            continue
        if ":" in line and " " not in line:
            continue

        match = re.match(r"^(\d+)\s*(?:x|X)?\s*(.+)$", line)
        if match:
            qty, card_name = match.groups()
            card_name = card_name.strip()
            if not card_name:
                continue
            parsed.setdefault(section, {})
            existing = parsed[section].get(card_name, {"qty": 0, "cmc": 0, "color": "C", "type": "Outros", "description": ""})
            existing["qty"] = int(existing.get("qty", 0)) + int(qty)
            parsed[section][card_name] = existing
            continue

        match = re.match(r"^(?:SB\s*:|SIDE\s*:\s*)\s*(\d+)\s*(?:x|X)?\s*(.+)$", line, flags=re.IGNORECASE)
        if match:
            qty, card_name = match.groups()
            parsed["side"].setdefault(card_name.strip(), {"qty": 0, "cmc": 0, "color": "C", "type": "Outros", "description": ""})
            parsed["side"][card_name.strip()]["qty"] = int(qty)
            continue

        match = re.match(r"^(\d+)\s*\(\s*(?:x|X)?\s*\)\s*(.+)$", line)
        if match:
            qty, card_name = match.groups()
            parsed[section].setdefault(card_name.strip(), {"qty": 0, "cmc": 0, "color": "C", "type": "Outros", "description": ""})
            parsed[section][card_name.strip()]["qty"] = int(qty)

    for section_name, cards in parsed.items():
        for card_name, value in list(cards.items()):
            if not isinstance(value, dict):
                parsed[section_name][card_name] = {"qty": int(value), "cmc": 0, "color": "C", "type": "Outros", "description": ""}
            else:
                if not value.get("qty"):
                    value["qty"] = 1
                value.setdefault("cmc", 0)
                value.setdefault("color", "C")
                value.setdefault("type", "Outros")
                value.setdefault("description", "")

    return parsed


def format_decklist(deck_name, deck):
    """Exporta Mainboard e Sideboard no formato comum de decklist."""
    lines = [deck_name, "", "Mainboard"]
    for card, info in sorted(deck.get("main", {}).items()):
        qty = info.get("qty", 1) if isinstance(info, dict) else info
        lines.append(f"{qty} {card}")
    lines.extend(["", "Sideboard"])
    for card, info in sorted(deck.get("side", {}).items()):
        qty = info.get("qty", 1) if isinstance(info, dict) else info
        lines.append(f"{qty} {card}")
    return "\n".join(lines) + "\n"


def find_card_defaults(decks, card_name):
    """Busca em todos os decks cadastrados se a carta já existe para preencher tipo, CMC e Cor."""
    typed_name = normalize_keyword_search(card_name).strip()
    if not typed_name or not isinstance(decks, dict):
        return None

    for d_name, d_content in decks.items():
        for section in ("main", "side"):
            for c_name, c_info in d_content.get(section, {}).items():
                if normalize_keyword_search(c_name) == typed_name:
                    if isinstance(c_info, dict):
                        return {
                            "cmc": int(c_info.get("cmc", 0) or 0),
                            "color": str(c_info.get("color", "C")),
                            "type": str(c_info.get("type", "Outros")),
                            "description": str(c_info.get("description", "")),
                            "image_uri": str(c_info.get("image_uri", "")),
                        }
    return None


def map_scryfall_type(type_line):
    tl = (type_line or "").lower()
    if "creature" in tl:
        return "Criatura"
    if "instant" in tl:
        return "Mágica Instantânea"
    if "sorcery" in tl:
        return "Feitiço"
    if "enchantment" in tl:
        return "Encantamento"
    if "artifact" in tl:
        return "Artefato"
    if "planeswalker" in tl:
        return "Planeswalker"
    if "land" in tl:
        return "Terreno"
    return "Outros"


def fetch_scryfall_card_info(query):
    """Consulta os dados e imagem oficial da carta na Scryfall API."""
    import urllib.parse
    import urllib.request

    query = str(query or "").strip()
    if not query:
        return None

    headers = {"User-Agent": "ManaVaultApp/1.7.0", "Accept": "application/json"}
    url = "https://api.scryfall.com/cards/named?fuzzy=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=7) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            colors = data.get("colors", [])
            color_code = "".join(colors) if colors else "C"
            img = data.get("image_uris", {}).get("normal", "")
            if not img and "card_faces" in data:
                img = data["card_faces"][0].get("image_uris", {}).get("normal", "")
            return {
                "name": data.get("name"),
                "cmc": int(data.get("cmc", 0)),
                "color": color_code,
                "type": map_scryfall_type(data.get("type_line", "")),
                "image_url": img,
            }
    except Exception:
        try:
            s_url = "https://api.scryfall.com/cards/search?q=" + urllib.parse.quote(query)
            s_req = urllib.request.Request(s_url, headers=headers)
            with urllib.request.urlopen(s_req, timeout=7) as s_resp:
                s_data = json.loads(s_resp.read().decode("utf-8"))
                if s_data.get("data"):
                    card = s_data["data"][0]
                    colors = card.get("colors", [])
                    color_code = "".join(colors) if colors else "C"
                    img = card.get("image_uris", {}).get("normal", "")
                    if not img and "card_faces" in card:
                        img = card["card_faces"][0].get("image_uris", {}).get("normal", "")
                    return {
                        "name": card.get("name"),
                        "cmc": int(card.get("cmc", 0)),
                        "color": color_code,
                        "type": map_scryfall_type(card.get("type_line", "")),
                        "image_url": img,
                    }
        except Exception:
            return None


def check_deck_legality(deck):
    """Verifica regras oficiais para Mainboard (mín. 60) e Sideboard (máx. 15), além de limite de 4 cópias."""
    main_cards = deck.get("main", {})
    side_cards = deck.get("side", {})

    def quantity(card):
        value = card.get("qty", 0) if isinstance(card, dict) else card
        return int(value or 0)

    total_main = sum(quantity(card) for card in main_cards.values())
    total_side = sum(quantity(card) for card in side_cards.values())
    issues = []

    if total_main < 60:
        issues.append(f"O Mainboard possui {total_main} cartas. O mínimo permitido é 60 (faltam {60 - total_main}).")
    if total_side > 15:
        issues.append(f"O Sideboard possui {total_side} cartas. O máximo permitido é 15 (excedente de {total_side - 15}).")

    copies = {}
    for section in (main_cards, side_cards):
        for card_name, card in section.items():
            copies[card_name] = copies.get(card_name, 0) + quantity(card)

    basic_lands = {
        "FOREST", "ISLAND", "SWAMP", "MOUNTAIN", "PLAINS", "WASTES",
        "FLORESTA", "ILHA", "PANTANO", "PÂNTANO", "MONTANHA", "PLANICIE", "PLANÍCIE",
        "SNOW-COVERED FOREST", "SNOW-COVERED ISLAND", "SNOW-COVERED SWAMP",
        "SNOW-COVERED MOUNTAIN", "SNOW-COVERED PLAINS",
    }
    too_many = [
        f"{name} ({qty} cópias)"
        for name, qty in copies.items()
        if qty > 4 and name.upper() not in basic_lands
    ]
    if too_many:
        issues.append("As seguintes cartas ultrapassam o limite de 4 cópias:\n  • " + "\n  • ".join(too_many))

    valid = len(issues) == 0
    return {
        "valid": valid,
        "total_main": total_main,
        "total_side": total_side,
        "issues": issues,
    }


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
    basic_lands = {
        "FOREST", "ISLAND", "SWAMP", "MOUNTAIN", "PLAINS", "WASTES",
        "FLORESTA", "ILHA", "PANTANO", "PÂNTANO", "MONTANHA", "PLANICIE", "PLANÍCIE",
        "SNOW-COVERED FOREST", "SNOW-COVERED ISLAND", "SNOW-COVERED SWAMP",
        "SNOW-COVERED MOUNTAIN", "SNOW-COVERED PLAINS",
    }
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
        "valid": len(warnings) == 0,
    }


def filter_matches(matches, deck=None, opponent=None, event=None, play_draw=None, start_date=None, end_date=None, tags=None):
    """Filtra partidas por deck, oponente, evento, data e tags."""
    filtered = []
    requested_tags = {tag.strip().lower() for tag in normalize_tags(tags)}

    for match in matches or []:
        if deck and deck != "Todos os decks" and str(match.get("deck", "")).strip().lower() != str(deck).strip().lower():
            continue
        if opponent and opponent != "Todos os oponentes" and str(match.get("opponent_deck", "")).strip().lower() != str(opponent).strip().lower():
            continue
        if event and event != "Todos os eventos" and str(match.get("event_type", "")).strip().lower() != str(event).strip().lower():
            continue
        if play_draw and play_draw != "Todos" and str(match.get("play_draw", "")).strip().lower() != str(play_draw).strip().lower():
            continue

        match_date = str(match.get("match_date") or match.get("date") or "").strip()
        if start_date and match_date and match_date < str(start_date):
            continue
        if end_date and match_date and match_date > str(end_date):
            continue

        match_tags = {tag.strip().lower() for tag in normalize_tags(match.get("tags", []))}
        if requested_tags and not requested_tags.issubset(match_tags):
            continue

        filtered.append(match)

    return filtered


def calculate_full_stats(matches, changes=None):
    """Calcula estatísticas completas equivalentes à versão Desktop."""
    matches = list(matches or [])
    total_matches = len(matches)

    if total_matches == 0:
        recent_changes = (changes or [])[-12:]
        change_text = "\n".join(f"{item.get('date', '')} • {item.get('message', '')}" for item in reversed(recent_changes)) or "Nenhuma alteração registrada."
        return {
            "total_matches": 0,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "win_rate": 0.0,
            "play_wr": 0.0,
            "draw_wr": 0.0,
            "play_wins": 0,
            "play_total": 0,
            "draw_wins": 0,
            "draw_total": 0,
            "recent_windows": [],
            "deck_stats": [],
            "matchup_stats": [],
            "recent_changes": recent_changes,
            "summary_text": "Nenhuma partida registrada até o momento.\n\nÚLTIMAS ALTERAÇÕES\n" + change_text,
        }

    wins = sum(1 for m in matches if m.get("result") == "Win")
    draws = sum(1 for m in matches if m.get("result") == "Draw")
    losses = sum(1 for m in matches if m.get("result") == "Loss")
    if wins + losses + draws < total_matches:
        losses = total_matches - wins - draws

    win_rate = (wins / total_matches * 100) if total_matches else 0.0

    def window_rate(window_size):
        recent = matches[-window_size:]
        if not recent:
            return 0.0, 0, 0
        recent_wins = sum(1 for m in recent if m.get("result") == "Win")
        recent_total = len(recent)
        return (recent_wins / recent_total) * 100, recent_wins, recent_total

    recent_windows = []
    for size in (10, 20, 30):
        rate, r_wins, r_total = window_rate(size)
        if r_total:
            recent_windows.append(f"Últimos {size}: {rate:.1f}% ({r_wins}/{r_total})")

    play_matches = [m for m in matches if "Play" in m.get("play_draw", "")]
    play_wins = sum(1 for m in play_matches if m.get("result") == "Win")
    play_wr = (play_wins / len(play_matches) * 100) if play_matches else 0.0

    draw_matches = [m for m in matches if "Draw" in m.get("play_draw", "")]
    draw_wins = sum(1 for m in draw_matches if m.get("result") == "Win")
    draw_wr = (draw_wins / len(draw_matches) * 100) if draw_matches else 0.0

    deck_stats = []
    deck_names = sorted({m.get("deck", "Deck desconhecido") for m in matches}, key=str.casefold)
    for deck_name in deck_names:
        deck_m = [m for m in matches if m.get("deck", "Deck desconhecido") == deck_name]
        deck_total = len(deck_m)
        deck_w = sum(1 for m in deck_m if m.get("result") == "Win")
        deck_l = sum(1 for m in deck_m if m.get("result") == "Loss")
        deck_d = sum(1 for m in deck_m if m.get("result") == "Draw")
        deck_wr = (deck_w / deck_total * 100) if deck_total else 0.0

        d_play = [m for m in deck_m if "Play" in m.get("play_draw", "")]
        d_play_w = sum(1 for m in d_play if m.get("result") == "Win")
        d_play_wr = (d_play_w / len(d_play) * 100) if d_play else 0.0

        d_draw = [m for m in deck_m if "Draw" in m.get("play_draw", "")]
        d_draw_w = sum(1 for m in d_draw if m.get("result") == "Win")
        d_draw_wr = (d_draw_w / len(d_draw) * 100) if d_draw else 0.0

        deck_stats.append({
            "name": deck_name,
            "total": deck_total,
            "wins": deck_w,
            "losses": deck_l,
            "draws": deck_d,
            "win_rate": deck_wr,
            "play_wr": d_play_wr,
            "play_wins": d_play_w,
            "play_total": len(d_play),
            "draw_wr": d_draw_wr,
            "draw_wins": d_draw_w,
            "draw_total": len(d_draw),
            "text": (
                f"{deck_name}\n"
                f"  Total: {deck_total} | Vitórias: {deck_w} | Derrotas: {deck_l} | Win Rate: {deck_wr:.1f}%\n"
                f"  Indo primeiro: {d_play_wr:.1f}% ({d_play_w}/{len(d_play)} partidas)\n"
                f"  Adversário indo primeiro: {d_draw_wr:.1f}% ({d_draw_w}/{len(d_draw)} partidas)"
            ),
        })

    matchup_stats = []
    matchup_keys = sorted(
        {(m.get("deck", "Deck desconhecido"), m.get("opponent_deck", "Desconhecido")) for m in matches},
        key=lambda item: (item[0].casefold(), item[1].casefold()),
    )
    for your_deck, opponent_deck in matchup_keys:
        matchup_m = [m for m in matches if m.get("deck", "Deck desconhecido") == your_deck and m.get("opponent_deck", "Desconhecido") == opponent_deck]
        m_wins = sum(1 for m in matchup_m if m.get("result") == "Win")
        m_total = len(matchup_m)
        m_wr = (m_wins / m_total * 100) if m_total else 0.0
        matchup_stats.append({
            "deck": your_deck,
            "opponent": opponent_deck,
            "wins": m_wins,
            "total": m_total,
            "win_rate": m_wr,
            "text": f"{your_deck} vs {opponent_deck}: {m_wins}/{m_total} vitórias ({m_wr:.1f}%)",
        })

    recent_changes = (changes or [])[-12:]
    change_text = "\n".join(f"{item.get('date', '')} • {item.get('message', '')}" for item in reversed(recent_changes)) or "Nenhuma alteração registrada."

    deck_stats_text = "\n\n".join(d["text"] for d in deck_stats) or "Nenhum deck registrado."
    matchup_text = "\n".join(m["text"] for m in matchup_stats) or "Nenhum confronto registrado."

    summary_text = (
        f"Total de Partidas: {total_matches}  |  Vitórias: {wins}  |  Derrotas: {losses}\n"
        f"Win Rate Geral: {win_rate:.1f}%\n"
        f"--------------------------------------------------------------------\n"
        f"Win Rate [Indo Primeiro (Play)]: {play_wr:.1f}% ({play_wins}/{len(play_matches)})\n"
        f"Win Rate [Comprando 1º (Draw)]:  {draw_wr:.1f}% ({draw_wins}/{len(draw_matches)})\n"
        + ("\n".join(f"{label}" for label in recent_windows) + "\n\n" if recent_windows else "\n")
        + f"DESEMPENHO POR DECK\n"
        + "--------------------------------------------------------------------\n"
        + deck_stats_text
        + "\n\nCONFRONTOS (MATCHUPS)\n"
        + "--------------------------------------------------------------------\n"
        + matchup_text
        + "\n\nÚLTIMAS ALTERAÇÕES\n"
        + "--------------------------------------------------------------------\n"
        + change_text
    )

    return {
        "total_matches": total_matches,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "win_rate": win_rate,
        "play_wr": play_wr,
        "draw_wr": draw_wr,
        "play_wins": play_wins,
        "play_total": len(play_matches),
        "draw_wins": draw_wins,
        "draw_total": len(draw_matches),
        "recent_windows": recent_windows,
        "deck_stats": deck_stats,
        "matchup_stats": matchup_stats,
        "recent_changes": recent_changes,
        "summary_text": summary_text,
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
