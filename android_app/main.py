"""Offline ManaVault Android interface built with Kivy."""

import json
import re
import unicodedata
from datetime import date
from functools import partial
from pathlib import Path

from kivy.app import App
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import NoTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.utils import get_color_from_hex

from data_store import DataStore, analyze_deck, empty_data

try:
    from androidstorage4kivy import Chooser, ShareSheet, SharedStorage
except ImportError:
    Chooser = ShareSheet = SharedStorage = None


BACKGROUND = get_color_from_hex("#101114")
SURFACE = get_color_from_hex("#1B1D22")
TEXT_COLOR = get_color_from_hex("#F2F3F5")
MUTED = get_color_from_hex("#A5A9B2")
ACCENT = get_color_from_hex("#4B83E6")
DANGER = get_color_from_hex("#B94343")
SCORE_OPTIONS = (
    tuple(f"{wins} x {losses}" for wins in range(1, 6) for losses in range(wins))
    + tuple(f"{losses} x {wins}" for wins in range(1, 6) for losses in range(wins))
)


def normalized_text(value):
    decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def result_from_score(score, fallback="Win"):
    match = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", str(score or ""))
    if not match:
        return fallback
    ours, opponent = (int(value) for value in match.groups())
    if ours == opponent:
        return "Draw"
    return "Win" if ours > opponent else "Loss"


def make_label(text, height=34, font_size=15, color=TEXT_COLOR, bold=False):
    label = Label(
        text=str(text),
        size_hint_y=None,
        height=dp(height),
        font_size=f"{font_size}sp",
        color=color,
        bold=bold,
        halign="left",
        valign="middle",
        text_size=(Window.width - dp(36), None),
    )
    return label


def make_button(text, callback, color=ACCENT, height=46):
    button = Button(
        text=text,
        size_hint_y=None,
        height=dp(height),
        background_normal="",
        background_color=color,
        color=TEXT_COLOR,
        font_size="15sp",
    )
    button.bind(on_release=callback)
    return button


def make_input(hint, value=""):
    return TextInput(
        hint_text=hint,
        text=str(value or ""),
        size_hint_y=None,
        height=dp(48),
        multiline=False,
        font_size="16sp",
        padding=[dp(12), dp(12)],
        background_normal="",
        background_active="",
        background_color=SURFACE,
        foreground_color=TEXT_COLOR,
        hint_text_color=MUTED,
        cursor_color=ACCENT,
    )


def make_spinner(hint, values):
    return Spinner(
        text=hint,
        values=values,
        size_hint_y=None,
        height=dp(48),
        background_normal="",
        background_color=SURFACE,
        color=TEXT_COLOR,
        font_size="15sp",
    )


class ManaVaultScreen(Screen):
    def __init__(self, app_ref, **kwargs):
        self.app_ref = app_ref
        super().__init__(**kwargs)
        self.content = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            spacing=dp(9),
            padding=[dp(14), dp(12)],
        )
        self.content.bind(minimum_height=self.content.setter("height"))
        scroll = ScrollView(do_scroll_x=False, bar_width=dp(3))
        scroll.add_widget(self.content)
        self.add_widget(scroll)

    def heading(self, text):
        self.content.add_widget(make_label(text, height=38, font_size=19, bold=True))

    def add_field_label(self, text):
        self.content.add_widget(make_label(text, height=25, font_size=13, color=MUTED))

    def refresh(self):
        pass


class DecksScreen(ManaVaultScreen):
    def __init__(self, app_ref, **kwargs):
        super().__init__(app_ref, **kwargs)
        self.heading("Seus decks")
        self.name_input = make_input("Nome do novo deck")
        self.content.add_widget(self.name_input)
        self.content.add_widget(make_button("Criar deck", self.create_deck))
        self.analysis_label = make_label("", height=70, font_size=14, color=MUTED)
        self.content.add_widget(self.analysis_label)
        self.deck_rows = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(6))
        self.deck_rows.bind(minimum_height=self.deck_rows.setter("height"))
        self.content.add_widget(self.deck_rows)
        self.content.add_widget(make_label("Análise do deck", height=34, font_size=18, bold=True))
        self.analysis_spinner = make_spinner("Selecione um deck", [])
        self.analysis_spinner.bind(text=lambda *_: self.refresh_analysis())
        self.content.add_widget(self.analysis_spinner)
        self.deck_analysis_label = make_label("", height=154, font_size=13, color=MUTED)
        self.content.add_widget(self.deck_analysis_label)

    def create_deck(self, *_):
        name = self.name_input.text.strip()
        decks = self.app_ref.store.data["decks"]
        if not name:
            self.app_ref.set_status("Digite um nome para o deck.")
            return
        if name in decks:
            self.app_ref.set_status("Esse deck já existe.")
            return
        decks[name] = {"main": {}, "side": {}}
        self.app_ref.record_change(f"Deck criado: {name}")
        if self.app_ref.save_data():
            self.name_input.text = ""
            self.app_ref.set_status(f"Deck {name} criado.")
            self.app_ref.refresh_all()

    def confirm_delete(self, deck_name, *_):
        box = BoxLayout(orientation="vertical", spacing=dp(12), padding=dp(14))
        box.add_widget(make_label(f"Excluir o deck {deck_name}?", height=54))
        actions = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        popup = Popup(title="Confirmar exclusão", content=box, size_hint=(0.88, None), height=dp(180))
        actions.add_widget(make_button("Cancelar", popup.dismiss, color=SURFACE))
        actions.add_widget(make_button("Excluir", partial(self.delete_deck, deck_name, popup), color=DANGER))
        box.add_widget(actions)
        popup.open()

    def delete_deck(self, deck_name, popup, *_):
        self.app_ref.store.data["decks"].pop(deck_name, None)
        self.app_ref.record_change(f"Deck removido: {deck_name}")
        popup.dismiss()
        if self.app_ref.save_data():
            self.app_ref.refresh_all()
            self.app_ref.set_status("Deck removido.")

    def refresh(self):
        self.deck_rows.clear_widgets()
        data = self.app_ref.store.data
        decks = data["decks"]
        total_cards = sum(
            int(card.get("qty", 0))
            for deck in decks.values()
            for section in ("main", "side")
            for card in deck.get(section, {}).values()
            if isinstance(card, dict)
        )
        self.analysis_label.text = f"{len(decks)} decks  ·  {total_cards} cartas cadastradas"
        previous = self.analysis_spinner.text
        self.analysis_spinner.values = sorted(decks, key=normalized_text)
        if previous in self.analysis_spinner.values:
            self.analysis_spinner.text = previous
        elif decks:
            self.analysis_spinner.text = sorted(decks, key=normalized_text)[0]
        else:
            self.analysis_spinner.text = "Selecione um deck"
            self.deck_analysis_label.text = "Crie um deck e adicione cartas para ver a análise."
        if not decks:
            self.deck_rows.add_widget(make_label("Ainda não há decks cadastrados.", height=44, color=MUTED))
            return
        for deck_name, deck in sorted(decks.items(), key=lambda item: normalized_text(item[0])):
            main_cards = sum(int(card.get("qty", 0)) for card in deck.get("main", {}).values() if isinstance(card, dict))
            side_cards = sum(int(card.get("qty", 0)) for card in deck.get("side", {}).values() if isinstance(card, dict))
            row = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
            row.add_widget(make_label(f"{deck_name}  ·  {main_cards} + {side_cards} side", height=48, font_size=14))
            row.add_widget(make_button("Excluir", partial(self.confirm_delete, deck_name), color=DANGER, height=42))
            self.deck_rows.add_widget(row)
        self.refresh_analysis()

    def refresh_analysis(self):
        deck = self.app_ref.store.data["decks"].get(self.analysis_spinner.text)
        if not deck:
            self.deck_analysis_label.text = "Crie um deck e adicione cartas para ver a análise."
            return
        summary = analyze_deck(deck)
        curve = " · ".join(f"{cmc}: {qty}" for cmc, qty in summary["mana_curve"].items()) or "vazia"
        types = " · ".join(f"{name}: {qty}" for name, qty in summary["types"].items()) or "nenhum"
        colors = " · ".join(f"{name}: {qty}" for name, qty in summary["colors"].items()) or "nenhuma"
        legality = "OK" if not summary["warnings"] else " · ".join(summary["warnings"])
        self.deck_analysis_label.text = (
            f"Principal: {summary['main_count']}  ·  Sideboard: {summary['side_count']}\n"
            f"Curva (CMC: cartas): {curve}\n"
            f"Tipos: {types}\nCores: {colors}\nLegalidade: {legality}"
        )


class CardsScreen(ManaVaultScreen):
    def __init__(self, app_ref, **kwargs):
        super().__init__(app_ref, **kwargs)
        self.heading("Cartas do deck")
        self.deck_spinner = make_spinner("Selecione um deck", [])
        self.content.add_widget(self.deck_spinner)
        self.deck_spinner.bind(text=lambda *_: self.refresh_cards())
        self.section_spinner = make_spinner("Principal", ["Principal", "Sideboard"])
        self.content.add_widget(self.section_spinner)
        self.name_input = make_input("Nome da carta")
        self.content.add_widget(self.name_input)
        self.qty_input = make_input("Quantidade", "1")
        self.content.add_widget(self.qty_input)
        self.cmc_input = make_input("Valor de mana", "0")
        self.content.add_widget(self.cmc_input)
        self.color_input = make_input("Cores (W, U, B, R, G ou C)", "C")
        self.content.add_widget(self.color_input)
        self.type_input = make_spinner(
            "Tipo de carta",
            ["Criatura", "Feitiço", "Instantânea", "Encantamento", "Artefato", "Terreno", "Planeswalker", "Outros"],
        )
        self.content.add_widget(self.type_input)
        self.content.add_widget(make_button("Salvar carta", self.save_card))
        self.card_rows = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(5))
        self.card_rows.bind(minimum_height=self.card_rows.setter("height"))
        self.content.add_widget(self.card_rows)

    def refresh(self):
        names = sorted(self.app_ref.store.data["decks"], key=normalized_text)
        current = self.deck_spinner.text
        self.deck_spinner.values = names
        if current in names:
            self.deck_spinner.text = current
        elif names:
            self.deck_spinner.text = names[0]
        else:
            self.deck_spinner.text = "Selecione um deck"
        self.refresh_cards()

    def selected_section(self):
        return "side" if self.section_spinner.text == "Sideboard" else "main"

    def save_card(self, *_):
        deck_name = self.deck_spinner.text
        if deck_name not in self.app_ref.store.data["decks"]:
            self.app_ref.set_status("Crie ou selecione um deck primeiro.")
            return
        card_name = self.name_input.text.strip()
        try:
            quantity = int(self.qty_input.text)
            cmc = int(self.cmc_input.text or "0")
        except ValueError:
            self.app_ref.set_status("Quantidade e valor de mana precisam ser números inteiros.")
            return
        if not card_name or quantity < 1 or cmc < 0:
            self.app_ref.set_status("Informe uma carta e valores válidos.")
            return

        section = self.selected_section()
        cards = self.app_ref.store.data["decks"][deck_name].setdefault(section, {})
        card = dict(cards.get(card_name, {}))
        card.update({
            "qty": quantity,
            "cmc": cmc,
            "color": self.color_input.text.strip().upper() or "C",
            "type": self.type_input.text if self.type_input.text != "Tipo de carta" else "Outros",
        })
        cards[card_name] = card
        section_title = "Sideboard" if section == "side" else "deck principal"
        self.app_ref.record_change(f"Carta salva: {card_name} ({quantity}x) no {section_title} de {deck_name}")
        if self.app_ref.save_data():
            self.name_input.text = ""
            self.app_ref.set_status("Carta salva.")
            self.app_ref.refresh_all()

    def remove_card(self, deck_name, section, card_name, *_):
        deck = self.app_ref.store.data["decks"].get(deck_name, {})
        deck.get(section, {}).pop(card_name, None)
        self.app_ref.record_change(f"Carta removida: {card_name} de {deck_name}")
        if self.app_ref.save_data():
            self.app_ref.refresh_all()
            self.app_ref.set_status("Carta removida.")

    def refresh_cards(self):
        self.card_rows.clear_widgets()
        deck_name = self.deck_spinner.text
        deck = self.app_ref.store.data["decks"].get(deck_name, {})
        found = False
        for section, title in (("main", "Principal"), ("side", "Sideboard")):
            cards = deck.get(section, {})
            if cards:
                found = True
                self.card_rows.add_widget(make_label(title, height=30, font_size=16, bold=True))
            for card_name, card in sorted(cards.items(), key=lambda item: normalized_text(item[0])):
                qty = card.get("qty", 0) if isinstance(card, dict) else card
                row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
                row.add_widget(make_label(f"{qty}x  {card_name}", height=44, font_size=14))
                row.add_widget(make_button(
                    "Remover",
                    partial(self.remove_card, deck_name, section, card_name),
                    color=DANGER,
                    height=40,
                ))
                self.card_rows.add_widget(row)
        if not found:
            self.card_rows.add_widget(make_label("Nenhuma carta neste deck.", height=42, color=MUTED))


class MatchesScreen(ManaVaultScreen):
    def __init__(self, app_ref, **kwargs):
        super().__init__(app_ref, **kwargs)
        self.edit_index = None
        self.heading("Registrar partida")
        self.deck_spinner = make_spinner("Selecione seu deck", [])
        self.content.add_widget(self.deck_spinner)
        self.opponent_input = make_input("Nome do oponente (opcional)")
        self.content.add_widget(self.opponent_input)
        self.opponent_deck_input = make_input("Deck adversário")
        self.content.add_widget(self.opponent_deck_input)
        self.play_draw_spinner = make_spinner("Você (Play)", ["Você (Play)", "O Adversário (Draw)"])
        self.content.add_widget(self.play_draw_spinner)
        self.result_spinner = make_spinner("Win", ["Win", "Loss", "Draw"])
        self.content.add_widget(self.result_spinner)
        self.score_input = make_spinner("2 x 0", SCORE_OPTIONS)
        self.content.add_widget(self.score_input)
        self.score_input.bind(text=self.sync_result)
        self.event_spinner = make_spinner(
            "Casual", ["Casual", "Torneio Oficial (FNM/Regional)", "Liga Online", "Treino"]
        )
        self.content.add_widget(self.event_spinner)
        self.date_input = make_input("Data (AAAA-MM-DD)", date.today().isoformat())
        self.content.add_widget(self.date_input)
        self.tags_input = make_input("Tags separadas por vírgula")
        self.content.add_widget(self.tags_input)
        self.notes_input = TextInput(
            hint_text="Anotações da partida",
            size_hint_y=None,
            height=dp(90),
            multiline=True,
            font_size="15sp",
            padding=dp(10),
            background_normal="",
            background_color=SURFACE,
            foreground_color=TEXT_COLOR,
            hint_text_color=MUTED,
        )
        self.content.add_widget(self.notes_input)
        self.save_button = make_button("Registrar partida", self.save_match)
        self.content.add_widget(self.save_button)

    def refresh(self):
        names = sorted(self.app_ref.store.data["decks"], key=normalized_text)
        previous = self.deck_spinner.text
        self.deck_spinner.values = names
        if previous in names:
            self.deck_spinner.text = previous
        elif names:
            self.deck_spinner.text = names[0]
        else:
            self.deck_spinner.text = "Selecione seu deck"

    def load_match(self, match, index):
        self.edit_index = index
        self.deck_spinner.text = match.get("deck", "")
        self.opponent_input.text = match.get("opponent_name", "")
        self.opponent_deck_input.text = match.get("opponent_deck", "")
        self.play_draw_spinner.text = match.get("play_draw", "Você (Play)")
        self.result_spinner.text = match.get("result", "Win")
        self.score_input.text = match.get("games_score", "2 x 0")
        self.event_spinner.text = match.get("event_type", "Casual")
        self.date_input.text = match.get("match_date", date.today().isoformat())
        tags = match.get("tags", [])
        self.tags_input.text = ", ".join(tags if isinstance(tags, list) else [str(tags)])
        self.notes_input.text = match.get("notes", "")
        self.save_button.text = "Salvar alterações"
        self.app_ref.show_screen("matches")

    def sync_result(self, *_):
        self.result_spinner.text = result_from_score(
            self.score_input.text, fallback=self.result_spinner.text
        )

    def save_match(self, *_):
        deck_name = self.deck_spinner.text
        if deck_name not in self.app_ref.store.data["decks"]:
            self.app_ref.set_status("Crie ou selecione seu deck antes de registrar a partida.")
            return
        try:
            datetime_value = date.fromisoformat(self.date_input.text.strip()).isoformat()
        except ValueError:
            self.app_ref.set_status("A data precisa estar no formato AAAA-MM-DD.")
            return

        opponent_deck = self.opponent_deck_input.text.strip()
        match = {
            "deck": deck_name,
            "opponent_name": self.opponent_input.text.strip(),
            "opponent_deck": opponent_deck,
            "result": result_from_score(self.score_input.text, self.result_spinner.text),
            "notes": self.notes_input.text.strip(),
            "play_draw": self.play_draw_spinner.text,
            "games_score": self.score_input.text.strip() or "2 x 0",
            "event_type": self.event_spinner.text,
            "match_date": datetime_value,
            "tags": [tag.strip() for tag in self.tags_input.text.split(",") if tag.strip()],
        }
        if opponent_deck and opponent_deck not in self.app_ref.store.data["opponent_decks"]:
            self.app_ref.store.data["opponent_decks"].append(opponent_deck)

        matches = self.app_ref.store.data["matches"]
        if self.edit_index is None:
            matches.append(match)
            change = "Partida registrada"
        else:
            matches[self.edit_index] = match
            change = "Partida atualizada"
        self.app_ref.record_change(f"{change}: {deck_name} vs {opponent_deck or 'oponente'}")
        if self.app_ref.save_data():
            self.edit_index = None
            self.save_button.text = "Registrar partida"
            self.notes_input.text = ""
            self.tags_input.text = ""
            self.opponent_input.text = ""
            self.opponent_deck_input.text = ""
            self.app_ref.set_status(f"{change} com sucesso.")
            self.app_ref.refresh_all()
            self.app_ref.show_screen("stats")


class StatsScreen(ManaVaultScreen):
    def __init__(self, app_ref, **kwargs):
        super().__init__(app_ref, **kwargs)
        self.heading("Resultados e histórico")
        filters = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        self.deck_filter = make_spinner("Todos os decks", [])
        self.result_filter = make_spinner("Todos", ["Todos", "Win", "Loss", "Draw"])
        filters.add_widget(self.deck_filter)
        filters.add_widget(self.result_filter)
        self.content.add_widget(filters)
        self.deck_filter.bind(text=lambda *_: self.refresh_matches())
        self.result_filter.bind(text=lambda *_: self.refresh_matches())
        self.summary_label = make_label("", height=56, color=MUTED)
        self.content.add_widget(self.summary_label)
        actions = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        actions.add_widget(make_button("Importar backup", self.app_ref.import_backup, color=SURFACE))
        actions.add_widget(make_button("Exportar backup", self.app_ref.export_backup))
        self.content.add_widget(actions)
        self.match_rows = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(7))
        self.match_rows.bind(minimum_height=self.match_rows.setter("height"))
        self.content.add_widget(self.match_rows)

    def refresh(self):
        names = sorted(self.app_ref.store.data["decks"], key=normalized_text)
        current = self.deck_filter.text
        self.deck_filter.values = ["Todos os decks"] + names
        self.deck_filter.text = current if current in self.deck_filter.values else "Todos os decks"
        self.refresh_matches()

    def refresh_matches(self):
        self.match_rows.clear_widgets()
        matches = self.app_ref.store.data["matches"]
        deck_filter = self.deck_filter.text
        result_filter = self.result_filter.text
        filtered = [
            (index, match)
            for index, match in enumerate(matches)
            if (deck_filter in ("Todos os decks", "") or match.get("deck") == deck_filter)
            and (result_filter in ("Todos", "") or match.get("result") == result_filter)
        ]
        wins = sum(1 for _, match in filtered if match.get("result") == "Win")
        losses = sum(1 for _, match in filtered if match.get("result") == "Loss")
        draws = sum(1 for _, match in filtered if match.get("result") == "Draw")
        decisive = wins + losses
        rate = (wins / decisive * 100) if decisive else 0
        self.summary_label.text = (
            f"{len(filtered)} partidas  ·  {wins}V / {losses}D / {draws}E  ·  "
            f"Win rate {rate:.1f}%"
        )
        if not filtered:
            self.match_rows.add_widget(make_label("Nenhuma partida para esse filtro.", height=48, color=MUTED))
            return
        for index, match in reversed(filtered[-100:]):
            opponent = match.get("opponent_deck") or "Oponente não informado"
            details = f"{match.get('match_date', '')}  ·  {match.get('result', '')}  ·  {match.get('games_score', '')}"
            row = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(112), spacing=dp(2))
            row.add_widget(make_label(f"{match.get('deck', '')} vs {opponent}", height=31, font_size=15, bold=True))
            row.add_widget(make_label(details, height=27, font_size=13, color=MUTED))
            row.add_widget(make_label(match.get("notes", ""), height=28, font_size=12, color=MUTED))
            actions = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(7))
            actions.add_widget(make_button("Editar", partial(self.edit_match, index, match), color=SURFACE, height=36))
            actions.add_widget(make_button("Excluir", partial(self.confirm_delete, index, match), color=DANGER, height=36))
            row.add_widget(actions)
            self.match_rows.add_widget(row)

    def edit_match(self, index, match, *_):
        self.app_ref.screens["matches"].load_match(match, index)

    def confirm_delete(self, index, match, *_):
        box = BoxLayout(orientation="vertical", spacing=dp(12), padding=dp(14))
        box.add_widget(make_label(f"Excluir partida de {match.get('deck', '')}?", height=50))
        actions = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        popup = Popup(title="Confirmar exclusão", content=box, size_hint=(0.88, None), height=dp(180))
        actions.add_widget(make_button("Cancelar", popup.dismiss, color=SURFACE))
        actions.add_widget(make_button("Excluir", partial(self.delete_match, index, popup), color=DANGER))
        box.add_widget(actions)
        popup.open()

    def delete_match(self, index, popup, *_):
        matches = self.app_ref.store.data["matches"]
        if 0 <= index < len(matches):
            match = matches.pop(index)
            self.app_ref.record_change(f"Partida removida: {match.get('deck', '')}")
            popup.dismiss()
            if self.app_ref.save_data():
                self.app_ref.refresh_all()
                self.app_ref.set_status("Partida removida.")


class RulesScreen(ManaVaultScreen):
    def __init__(self, app_ref, **kwargs):
        super().__init__(app_ref, **kwargs)
        self.heading("Consulta de efeitos")
        self.search_input = make_input("Buscar habilidade ou explicação")
        self.search_input.bind(text=lambda *_: self.refresh())
        self.content.add_widget(self.search_input)
        self.rule_rows = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(10))
        self.rule_rows.bind(minimum_height=self.rule_rows.setter("height"))
        self.content.add_widget(self.rule_rows)
        rules_path = Path(__file__).with_name("keyword_rules.json")
        try:
            self.rules = json.loads(rules_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.rules = []

    def refresh(self):
        self.rule_rows.clear_widgets()
        query = normalized_text(self.search_input.text).strip()
        found = 0
        for rule in self.rules:
            searchable = " ".join((rule.get("name", ""), rule.get("english", ""), rule.get("summary", "")))
            searchable += " " + " ".join(
                f"{heading} {description}"
                for heading, description in rule.get("sections", [])
            )
            if query and query not in normalized_text(searchable):
                continue
            found += 1
            text = f"[b]{rule.get('name', '')}[/b]"
            english = rule.get("english", "")
            if english:
                text += f"  ({english})"
            text += f"\n{rule.get('summary', '')}"
            for heading, description in rule.get("sections", []):
                text += f"\n\n[b]{heading}[/b]\n{description}"
            label = Label(
                text=text,
                markup=True,
                size_hint_y=None,
                font_size="14sp",
                color=TEXT_COLOR,
                halign="left",
                valign="top",
                text_size=(Window.width - dp(52), None),
            )
            label.texture_update()
            label.height = max(dp(72), label.texture_size[1] + dp(18))
            self.rule_rows.add_widget(label)
        if not found:
            self.rule_rows.add_widget(make_label("Nenhum efeito encontrado.", height=48, color=MUTED))


class ManaVaultApp(App):
    title = "ManaVault"

    def build(self):
        Window.clearcolor = BACKGROUND
        self.store = DataStore(Path(self.user_data_dir) / "magic_data.json")
        self.storage_blocked = False
        self.load_error = ""
        try:
            self.store.load()
        except ValueError as exc:
            self.storage_blocked = True
            self.load_error = str(exc)

        root = BoxLayout(orientation="vertical", spacing=dp(4), padding=[dp(8), dp(7)])
        title = make_label("ManaVault", height=38, font_size=21, bold=True)
        root.add_widget(title)
        self.manager = ScreenManager(transition=NoTransition())
        self.screens = {
            "decks": DecksScreen(self, name="decks"),
            "cards": CardsScreen(self, name="cards"),
            "matches": MatchesScreen(self, name="matches"),
            "stats": StatsScreen(self, name="stats"),
            "rules": RulesScreen(self, name="rules"),
        }
        for screen in self.screens.values():
            self.manager.add_widget(screen)
        root.add_widget(self.manager)

        self.status_label = make_label("Offline", height=28, font_size=12, color=MUTED)
        root.add_widget(self.status_label)
        navigation = BoxLayout(size_hint_y=None, height=dp(54), spacing=dp(4))
        for screen_name, caption in (
            ("decks", "Decks"), ("cards", "Cartas"), ("matches", "Partidas"),
            ("stats", "Resultados"), ("rules", "Efeitos"),
        ):
            button = Button(
                text=caption,
                background_normal="",
                background_color=SURFACE,
                color=TEXT_COLOR,
                font_size="12sp",
            )
            button.bind(on_release=partial(self.show_screen, screen_name))
            navigation.add_widget(button)
        root.add_widget(navigation)
        self.refresh_all()
        if self.load_error:
            self.set_status(f"{self.load_error} Importe um backup válido para continuar.")
        return root

    def set_status(self, message):
        if hasattr(self, "status_label"):
            self.status_label.text = message

    def record_change(self, message):
        changes = self.store.data.setdefault("changes", [])
        changes.append({"date": date.today().strftime("%d/%m/%Y"), "message": message})
        self.store.data["changes"] = changes[-500:]

    def save_data(self):
        if self.storage_blocked:
            self.set_status("Os dados atuais estão protegidos. Importe um backup JSON válido primeiro.")
            return False
        try:
            self.store.save()
        except OSError as exc:
            self.set_status(f"Não foi possível salvar os dados: {exc}")
            return False
        return True

    def refresh_all(self):
        for screen in getattr(self, "screens", {}).values():
            screen.refresh()

    def show_screen(self, screen_name, *_):
        self.manager.current = screen_name
        self.screens[screen_name].refresh()

    def import_backup(self, *_):
        if Chooser is None or SharedStorage is None:
            self.set_status("A seleção de arquivos está disponível no APK Android.")
            return
        self.chooser = Chooser(self._on_backup_selected)
        self.chooser.choose_content("application/json")

    def _on_backup_selected(self, shared_files):
        if not shared_files:
            return
        try:
            private_file = SharedStorage().copy_from_shared(shared_files[0])
            if not private_file:
                raise OSError("Não foi possível ler o arquivo escolhido.")
            self.store.import_json(Path(private_file).read_text(encoding="utf-8"))
            self.storage_blocked = False
            self.load_error = ""
            self.set_status("Backup importado. Os dados foram salvos neste celular.")
            self.refresh_all()
        except (OSError, ValueError) as exc:
            self.set_status(f"Importação cancelada: {exc}")

    def export_backup(self, *_):
        if self.storage_blocked:
            self.set_status("Importe um backup válido antes de exportar.")
            return
        if SharedStorage is None or ShareSheet is None:
            self.set_status("A exportação de arquivos está disponível no APK Android.")
            return
        try:
            export_path = self.store.path.with_name("magic_data_export.json")
            export_path.write_text(self.store.export_json(), encoding="utf-8")
            shared_file = SharedStorage().copy_to_shared(
                str(export_path), collection="Documents", filepath="ManaVault/magic_data.json"
            )
            if not shared_file:
                raise OSError("O Android não conseguiu criar o arquivo de backup.")
            self.share_sheet = ShareSheet()
            self.share_sheet.share_file(shared_file)
            self.set_status("Backup pronto para salvar ou compartilhar.")
        except OSError as exc:
            self.set_status(f"Não foi possível exportar o backup: {exc}")


if __name__ == "__main__":
    ManaVaultApp().run()
