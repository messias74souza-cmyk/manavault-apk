"""Offline ManaVault Android interface built with Kivy with full desktop parity and refined mobile UX."""

import json
import re
import unicodedata
from datetime import date, datetime
from functools import partial
from pathlib import Path

from kivy.app import App
from kivy.core.clipboard import Clipboard
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

from data_store import (
    DataStore,
    analyze_deck,
    calculate_full_stats,
    check_deck_legality,
    empty_data,
    filter_matches,
    find_card_defaults,
    format_decklist,
    normalize_keyword_search,
    normalize_tags,
    parse_decklist_text,
)

try:
    from androidstorage4kivy import Chooser, ShareSheet, SharedStorage
except ImportError:
    Chooser = ShareSheet = SharedStorage = None


# Theme Colors
BACKGROUND = get_color_from_hex("#101114")
SURFACE = get_color_from_hex("#1B1D22")
SURFACE_LIGHT = get_color_from_hex("#262930")
TEXT_COLOR = get_color_from_hex("#F2F3F5")
MUTED = get_color_from_hex("#A5A9B2")
ACCENT = get_color_from_hex("#4B83E6")
SUCCESS = get_color_from_hex("#34A853")
WARNING = get_color_from_hex("#FBBC04")
DANGER = get_color_from_hex("#B94343")

CARD_TYPES = [
    "Criatura",
    "Mágica Instantânea",
    "Feitiço",
    "Encantamento",
    "Artefato",
    "Planeswalker",
    "Terreno",
    "Outros",
]

COLOR_OPTIONS_MAP = {
    "Incolor (C) - Artefatos / Terrenos": "C",
    "Branco (W)": "W",
    "Azul (U)": "U",
    "Preto (B)": "B",
    "Vermelho (R)": "R",
    "Verde (G)": "G",
    "Multicolorida (Duas ou mais cores)": "MULTI",
}

PLAY_DRAW_OPTIONS = ["Você (Play)", "O Adversário (Draw)"]
EVENT_TYPES = ["Casual", "Torneio Oficial (FNM/Regional)", "Liga Online", "Treino"]
RESULT_OPTIONS = ["Win", "Loss", "Draw"]

SCORE_OPTIONS = (
    tuple(f"{wins} x {losses}" for wins in range(1, 6) for losses in range(wins))
    + tuple(f"{losses} x {wins}" for wins in range(1, 6) for losses in range(wins))
)


def format_card_color(color_code):
    """Converte códigos curtos como 'R' ou 'C' em texto explicativo e legível."""
    raw = str(color_code or "C").strip().upper()
    if not raw or raw == "C":
        return "Incolor (C)"
    color_map = {
        "W": "Branco (W)",
        "U": "Azul (U)",
        "B": "Preto (B)",
        "R": "Vermelho (R)",
        "G": "Verde (G)",
    }
    if raw in color_map:
        return color_map[raw]
    # Multicolor
    names = [color_map[c].split()[0] for c in raw if c in color_map]
    if names:
        return f"Multicolorida ({' / '.join(names)} - {raw})"
    return f"Outra ({raw})"


def result_from_score(score, fallback="Win"):
    match = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", str(score or ""))
    if not match:
        return fallback
    ours, opponent = (int(v) for v in match.groups())
    if ours == opponent:
        return "Draw"
    return "Win" if ours > opponent else "Loss"


def make_label(text, height=30, font_size=13, color=TEXT_COLOR, bold=False, halign="left"):
    lbl = Label(
        text=str(text),
        size_hint_y=None,
        height=dp(height),
        font_size=f"{font_size}sp",
        color=color,
        bold=bold,
        halign=halign,
        valign="middle",
    )
    lbl.bind(width=lambda instance, val: setattr(instance, "text_size", (val, None)))
    return lbl


def make_button(text, callback, color=ACCENT, text_color=TEXT_COLOR, height=44, font_size=13):
    btn = Button(
        text=text,
        size_hint_y=None,
        height=dp(height),
        background_normal="",
        background_color=color,
        color=text_color,
        font_size=f"{font_size}sp",
        bold=True,
    )
    if callback:
        btn.bind(on_release=callback)
    return btn


def make_input(hint, value="", multiline=False, height=46):
    return TextInput(
        hint_text=hint,
        text=str(value or ""),
        size_hint_y=None,
        height=dp(height),
        multiline=multiline,
        font_size="15sp",
        padding=[dp(10), dp(10)],
        background_normal="",
        background_active="",
        background_color=SURFACE,
        foreground_color=TEXT_COLOR,
        hint_text_color=MUTED,
        cursor_color=ACCENT,
    )


def make_spinner(default_text, values, height=46):
    return Spinner(
        text=str(default_text),
        values=list(values),
        size_hint_y=None,
        height=dp(height),
        background_normal="",
        background_color=SURFACE,
        color=TEXT_COLOR,
        font_size="14sp",
    )


def show_info_dialog(title, message):
    box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
    scroll = ScrollView(do_scroll_x=False)
    lbl = Label(
        text=message,
        size_hint_y=None,
        font_size="14sp",
        color=TEXT_COLOR,
        halign="left",
        valign="top",
    )
    lbl.bind(width=lambda inst, val: setattr(inst, "text_size", (val, None)))
    lbl.bind(texture_size=lambda inst, val: setattr(inst, "height", max(dp(60), val[1] + dp(10))))
    scroll.add_widget(lbl)
    box.add_widget(scroll)

    btn = make_button("OK", None, color=ACCENT, height=42)
    box.add_widget(btn)

    popup = Popup(title=title, content=box, size_hint=(0.92, None), height=dp(300))
    btn.bind(on_release=popup.dismiss)
    popup.open()


class ManaVaultScreen(Screen):
    """Base screen containing standard scrolling content area."""

    def __init__(self, app_ref, **kwargs):
        self.app_ref = app_ref
        super().__init__(**kwargs)
        self.content = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            spacing=dp(10),
            padding=[dp(12), dp(10)],
        )
        self.content.bind(minimum_height=self.content.setter("height"))
        scroll = ScrollView(do_scroll_x=False, bar_width=dp(4))
        scroll.add_widget(self.content)
        self.add_widget(scroll)

    def heading(self, text):
        self.content.add_widget(make_label(text, height=36, font_size=18, bold=True))

    def add_field_label(self, title, subtitle=None):
        self.content.add_widget(make_label(title, height=22, font_size=13, color=ACCENT, bold=True))
        if subtitle:
            self.content.add_widget(make_label(subtitle, height=18, font_size=11, color=MUTED))

    def refresh(self):
        pass


# ==============================================================================
# TELA 1: GERENCIAR DECKS
# ==============================================================================
class DecksScreen(ManaVaultScreen):
    """Gerenciamento de Arquétipos / Decks."""

    def __init__(self, app_ref, **kwargs):
        super().__init__(app_ref, **kwargs)
        self.heading("Gerenciamento de Decks")

        self.add_field_label("Criar Novo Deck", "Digite o nome do seu arquétipo para começar:")
        self.name_input = make_input("Ex: Izzet Murktide, Mono Black, Burn...")
        self.content.add_widget(self.name_input)
        self.content.add_widget(make_button("Criar Novo Deck", self.create_deck, color=ACCENT))

        self.summary_label = make_label("", height=24, font_size=13, color=MUTED)
        self.content.add_widget(self.summary_label)

        self.add_field_label("Decks Cadastrados")
        self.deck_rows = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        self.deck_rows.bind(minimum_height=self.deck_rows.setter("height"))
        self.content.add_widget(self.deck_rows)

    def create_deck(self, *_):
        name = self.name_input.text.strip()
        decks = self.app_ref.store.data["decks"]
        if not name:
            self.app_ref.set_status("Digite um nome para o deck.")
            return
        if name in decks:
            self.app_ref.set_status("Esse deck já existe.")
            return

        decks[name] = {"main": {}, "side": {}, "tags": "", "game_plan": ""}
        self.app_ref.record_change(f"Deck criado: {name}")
        if self.app_ref.save_data():
            self.name_input.text = ""
            self.app_ref.set_status(f"Deck '{name}' criado com sucesso.")
            self.app_ref.refresh_all()

    def confirm_delete(self, deck_name, *_):
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        box.add_widget(make_label(f"Deseja realmente excluir o deck '{deck_name}' e todas as suas cartas?", height=54))
        actions = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        popup = Popup(title="Confirmar Exclusão", content=box, size_hint=(0.88, None), height=dp(180))
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
            self.app_ref.set_status(f"Deck '{deck_name}' excluído.")

    def open_deck_cards(self, deck_name, *_):
        self.app_ref.screens["deck_cards"].deck_spinner.text = deck_name
        self.app_ref.show_screen("deck_cards")

    def open_deck_analysis(self, deck_name, *_):
        self.app_ref.screens["analysis"].deck_spinner.text = deck_name
        self.app_ref.show_screen("analysis")

    def refresh(self):
        self.deck_rows.clear_widgets()
        data = self.app_ref.store.data
        decks = data.get("decks", {})
        total_cards = sum(
            int(card.get("qty", 0) if isinstance(card, dict) else card)
            for deck in decks.values()
            for section in ("main", "side")
            for card in deck.get(section, {}).values()
        )
        self.summary_label.text = f"{len(decks)} decks cadastrados  ·  {total_cards} cartas no total"

        if not decks:
            self.deck_rows.add_widget(make_label("Nenhum deck cadastrado ainda.", height=40, color=MUTED))
            return

        for deck_name, deck in sorted(decks.items(), key=lambda item: normalize_keyword_search(item[0])):
            main_count = sum(int(c.get("qty", 0) if isinstance(c, dict) else c) for c in deck.get("main", {}).values())
            side_count = sum(int(c.get("qty", 0) if isinstance(c, dict) else c) for c in deck.get("side", {}).values())
            tags = deck.get("tags", "")

            card_box = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(94), spacing=dp(2), padding=[dp(10), dp(6)])
            card_box.canvas.before.clear()
            with card_box.canvas.before:
                from kivy.graphics import Color, RoundedRectangle
                Color(*SURFACE)
                rect = RoundedRectangle(size=card_box.size, pos=card_box.pos, radius=[dp(6)])
                card_box.bind(size=lambda inst, v, r=rect: setattr(r, "size", v), pos=lambda inst, v, r=rect: setattr(r, "pos", v))

            title_row = BoxLayout(size_hint_y=None, height=dp(26))
            title_row.add_widget(make_label(deck_name, height=24, font_size=15, bold=True))
            card_box.add_widget(title_row)

            sub_text = f"Principal (Main): {main_count} cartas  ·  Reserva (Side): {side_count} cartas"
            if tags:
                sub_text += f"  ·  Tags: {tags}"
            card_box.add_widget(make_label(sub_text, height=20, font_size=12, color=MUTED))

            btn_row = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(6))
            btn_row.add_widget(make_button("Ver Cartas", partial(self.open_deck_cards, deck_name), color=ACCENT, height=32, font_size=12))
            btn_row.add_widget(make_button("Análise", partial(self.open_deck_analysis, deck_name), color=SURFACE_LIGHT, height=32, font_size=12))
            btn_row.add_widget(make_button("Excluir", partial(self.confirm_delete, deck_name), color=DANGER, height=32, font_size=12))
            card_box.add_widget(btn_row)

            self.deck_rows.add_widget(card_box)


# ==============================================================================
# TELA 2: CARTAS DO DECK (LISTA SEPARADA DA ANÁLISE)
# ==============================================================================
class DeckCardsScreen(ManaVaultScreen):
    """Lista de cartas por deck com visualização clara, filtros e ações rápidas."""

    def __init__(self, app_ref, **kwargs):
        super().__init__(app_ref, **kwargs)
        self.heading("Cartas do Deck")

        # Seletor do Deck
        self.add_field_label("Deck Selecionado:")
        self.deck_spinner = make_spinner("Selecione um deck", [])
        self.deck_spinner.bind(text=lambda *_: self.render_cards_list())
        self.content.add_widget(self.deck_spinner)

        # Botão direto para adicionar cartas
        add_btn = make_button("➕ Adicionar Nova Carta a Este Deck", self.jump_to_add_card, color=ACCENT, height=42)
        self.content.add_widget(add_btn)

        # Filtros de visualização
        self.add_field_label("Filtrar Cartas do Deck:")
        self.search_input = make_input("Buscar carta por nome...")
        self.search_input.bind(text=lambda *_: self.render_cards_list())
        self.content.add_widget(self.search_input)

        filter_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        self.section_filter = make_spinner("Todas as seções", ["Todas as seções", "Mainboard (Principal)", "Sideboard (Reserva)"])
        self.section_filter.bind(text=lambda *_: self.render_cards_list())
        self.type_filter = make_spinner("Todos os tipos", ["Todos os tipos"] + CARD_TYPES)
        self.type_filter.bind(text=lambda *_: self.render_cards_list())
        filter_row.add_widget(self.section_filter)
        filter_row.add_widget(self.type_filter)
        self.content.add_widget(filter_row)

        self.summary_badge = make_label("", height=24, font_size=13, color=MUTED)
        self.content.add_widget(self.summary_badge)

        # Lista de cartas
        self.cards_list = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        self.cards_list.bind(minimum_height=self.cards_list.setter("height"))
        self.content.add_widget(self.cards_list)

    def jump_to_add_card(self, *_):
        self.app_ref.screens["add_cards"].deck_spinner.text = self.deck_spinner.text
        self.app_ref.show_screen("add_cards")

    def adjust_qty(self, section, card_name, delta, *_):
        deck_name = self.deck_spinner.text
        deck = self.app_ref.store.data.get("decks", {}).get(deck_name)
        if not deck or section not in deck:
            return
        card = deck[section].get(card_name)
        if not card:
            return
        current_qty = int(card.get("qty", 0) if isinstance(card, dict) else card)
        new_qty = current_qty + delta
        if new_qty <= 0:
            deck[section].pop(card_name, None)
            self.app_ref.record_change(f"Carta removida: {card_name} de {deck_name}")
        else:
            if isinstance(card, dict):
                card["qty"] = new_qty
            else:
                deck[section][card_name] = {"qty": new_qty, "cmc": 0, "color": "C", "type": "Outros", "description": ""}
            self.app_ref.record_change(f"Qtd ajustada: {card_name} ({new_qty} cópias) em {deck_name}")
        if self.app_ref.save_data():
            self.app_ref.refresh_all()

    def edit_card_notes_dialog(self, section, card_name, *_):
        deck_name = self.deck_spinner.text
        deck = self.app_ref.store.data.get("decks", {}).get(deck_name, {})
        card = deck.get(section, {}).get(card_name, {})
        current_notes = card.get("description", "") if isinstance(card, dict) else ""

        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        box.add_widget(make_label(f"Observações: {card_name}", height=28, font_size=14, bold=True))
        text_input = make_input("Função no deck (ex: remoção chave contra aggro)", current_notes, multiline=True, height=90)
        box.add_widget(text_input)

        actions = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        popup = Popup(title="Observações da Carta", content=box, size_hint=(0.92, None), height=dp(230))

        def save_notes(*_):
            if isinstance(card, dict):
                card["description"] = text_input.text.strip()
            popup.dismiss()
            if self.app_ref.save_data():
                self.app_ref.set_status("Observações salvas.")
                self.render_cards_list()

        actions.add_widget(make_button("Cancelar", popup.dismiss, color=SURFACE))
        actions.add_widget(make_button("Salvar", save_notes, color=ACCENT))
        box.add_widget(actions)
        popup.open()

    def render_cards_list(self):
        self.cards_list.clear_widgets()
        deck_name = self.deck_spinner.text
        deck = self.app_ref.store.data.get("decks", {}).get(deck_name)
        if not deck:
            self.summary_badge.text = "Crie ou selecione um deck para visualizar as cartas."
            return

        term = normalize_keyword_search(self.search_input.text).strip()
        type_filter = self.type_filter.text
        sec_choice = self.section_filter.text

        sections_to_show = []
        if sec_choice in ("Todas as seções", "Mainboard (Principal)"):
            sections_to_show.append(("main", "Mainboard (Deck Principal)"))
        if sec_choice in ("Todas as seções", "Sideboard (Reserva)"):
            sections_to_show.append(("side", "Sideboard (Reserva)"))

        total_cards_count = 0
        total_unique_count = 0

        for section_key, section_title in sections_to_show:
            cards = deck.get(section_key, {})
            matching_cards = []
            for c_name, c_info in sorted(cards.items(), key=lambda i: normalize_keyword_search(i[0])):
                if not isinstance(c_info, dict):
                    c_info = {"qty": int(c_info), "cmc": 0, "color": "C", "type": "Outros", "description": ""}

                if term and term not in normalize_keyword_search(c_name):
                    continue
                if type_filter != "Todos os tipos" and c_info.get("type", "Outros") != type_filter:
                    continue
                matching_cards.append((c_name, c_info))

            if matching_cards:
                sec_qty_sum = sum(c[1].get("qty", 1) for c in matching_cards)
                total_cards_count += sec_qty_sum
                total_unique_count += len(matching_cards)

                self.cards_list.add_widget(make_label(f"▶ {section_title} ({sec_qty_sum} cartas)", height=30, font_size=15, bold=True, color=ACCENT))

                for c_name, c_info in matching_cards:
                    qty = c_info.get("qty", 1)
                    cmc = c_info.get("cmc", 0)
                    color_code = c_info.get("color", "C")
                    color_label = format_card_color(color_code)
                    c_type = c_info.get("type", "Outros")
                    desc = c_info.get("description", "")

                    card_box = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(98 if desc else 78), spacing=dp(3), padding=[dp(10), dp(6)])
                    card_box.canvas.before.clear()
                    with card_box.canvas.before:
                        from kivy.graphics import Color, RoundedRectangle
                        Color(*SURFACE)
                        rect = RoundedRectangle(size=card_box.size, pos=card_box.pos, radius=[dp(6)])
                        card_box.bind(size=lambda inst, v, r=rect: setattr(r, "size", v), pos=lambda inst, v, r=rect: setattr(r, "pos", v))

                    # Linha 1: Nome da carta em destaque e Quantidade de cópias legível
                    title_line = BoxLayout(size_hint_y=None, height=dp(24))
                    title_line.add_widget(make_label(f"{c_name}", height=22, font_size=15, bold=True))
                    title_line.add_widget(make_label(f"{qty} cópia(s)", height=22, font_size=13, bold=True, color=ACCENT, halign="right"))
                    card_box.add_widget(title_line)

                    # Linha 2: Tipo de Carta, Custo de Mana (CMC) e Cor da Carta em texto claro
                    details_text = f"Tipo: {c_type}   ·   Custo de Mana: {cmc}   ·   Cor: {color_label}"
                    card_box.add_widget(make_label(details_text, height=20, font_size=12, color=MUTED))

                    # Linha 3 (opcional): Observação / Função no deck
                    if desc:
                        card_box.add_widget(make_label(f"Função/Obs: {desc}", height=18, font_size=11, color=TEXT_COLOR))

                    # Linha 4: Botões de toque rápido
                    btn_row = BoxLayout(size_hint_y=None, height=dp(28), spacing=dp(6))
                    btn_row.add_widget(make_button("-1 Cópia", partial(self.adjust_qty, section_key, c_name, -1), color=SURFACE_LIGHT, height=26, font_size=11))
                    btn_row.add_widget(make_button("+1 Cópia", partial(self.adjust_qty, section_key, c_name, 1), color=SURFACE_LIGHT, height=26, font_size=11))
                    btn_row.add_widget(make_button("Observação", partial(self.edit_card_notes_dialog, section_key, c_name), color=SURFACE_LIGHT, height=26, font_size=11))
                    btn_row.add_widget(make_button("Excluir", partial(self.adjust_qty, section_key, c_name, -qty), color=DANGER, height=26, font_size=11))
                    card_box.add_widget(btn_row)

                    self.cards_list.add_widget(card_box)

        self.summary_badge.text = f"{total_cards_count} cartas exibidas ({total_unique_count} nomes distintos)"

        if total_cards_count == 0:
            self.cards_list.add_widget(make_label("Nenhuma carta encontrada com os filtros atuais.", height=38, color=MUTED))

    def refresh(self):
        decks = self.app_ref.store.data.get("decks", {})
        names = sorted(decks.keys(), key=normalize_keyword_search)
        prev = self.deck_spinner.text
        self.deck_spinner.values = names
        if prev in names:
            self.deck_spinner.text = prev
        elif names:
            self.deck_spinner.text = names[0]
        else:
            self.deck_spinner.text = "Nenhum deck criado"
        self.render_cards_list()


# ==============================================================================
# TELA 3: ADICIONAR / ATUALIZAR CARTAS
# ==============================================================================
class AddCardsScreen(ManaVaultScreen):
    """Formulário intuitivo com legendas claras para adicionar cartas."""

    def __init__(self, app_ref, **kwargs):
        super().__init__(app_ref, **kwargs)
        self.heading("Adicionar / Atualizar Carta")

        self.add_field_label("Deck onde a carta será inserida:")
        self.deck_spinner = make_spinner("Selecione um deck", [])
        self.content.add_widget(self.deck_spinner)

        self.add_field_label("Seção do Deck:")
        self.section_spinner = make_spinner("Mainboard (Deck Principal)", ["Mainboard (Deck Principal)", "Sideboard (Reserva)"])
        self.content.add_widget(self.section_spinner)

        self.add_field_label("Nome da Carta:", "Digite o nome e toque em Buscar para preencher os dados automaticamente:")
        name_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        self.name_input = make_input("Ex: Lightning Bolt, Counterspell, Raio...")
        self.name_input.bind(focus=self.on_name_focus_changed)
        name_row.add_widget(self.name_input)
        name_row.add_widget(make_button("Buscar Dados", self.try_autofill, color=SURFACE_LIGHT, height=46, font_size=12))
        self.content.add_widget(name_row)

        self.add_field_label("Tipo de Carta:")
        self.type_spinner = make_spinner("Criatura", CARD_TYPES)
        self.content.add_widget(self.type_spinner)

        self.add_field_label("Quantidade de Cópias:", "Ex: 4 para um playset completo:")
        self.qty_input = make_input("Quantidade de cópias (número)", "1")
        self.content.add_widget(self.qty_input)

        self.add_field_label("Custo de Mana Convertido (CMC):", "Ex: 1 para Raio, 2 para Urza, 0 para Terreno:")
        self.cmc_input = make_input("Custo de mana total da carta (número)", "0")
        self.content.add_widget(self.cmc_input)

        self.add_field_label("Cor da Carta:")
        self.color_spinner = make_spinner("Incolor (C) - Artefatos / Terrenos", list(COLOR_OPTIONS_MAP.keys()))
        self.content.add_widget(self.color_spinner)

        self.custom_color_input = make_input("Código de cor customizado (ex: UR, WUB, BR)", "C")
        # Shown only if needed or kept subtle
        self.content.add_widget(self.custom_color_input)
        self.color_spinner.bind(text=self.on_color_spinner_changed)

        self.add_field_label("Função / Observação no Deck (Opcional):", "Ex: Remoção rápida, finalizador, acelerador de mana...")
        self.desc_input = make_input("Anotações da função desta carta", multiline=False)
        self.content.add_widget(self.desc_input)

        actions_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        actions_row.add_widget(make_button("Salvar Carta", self.save_card, color=ACCENT))
        actions_row.add_widget(make_button("Checar Regras", self.check_legality, color=SURFACE_LIGHT))
        self.content.add_widget(actions_row)

        view_deck_btn = make_button("Ver Lista de Cartas Deste Deck", self.view_deck_cards, color=SURFACE, height=40)
        self.content.add_widget(view_deck_btn)

        self.feedback_label = make_label("", height=24, font_size=13, color=MUTED)
        self.content.add_widget(self.feedback_label)

    def on_color_spinner_changed(self, instance, text):
        code = COLOR_OPTIONS_MAP.get(text, "C")
        if code != "MULTI":
            self.custom_color_input.text = code
        else:
            if self.custom_color_input.text in ("C", "W", "U", "B", "R", "G"):
                self.custom_color_input.text = "UR"

    def on_name_focus_changed(self, instance, focused):
        if not focused:
            self.try_autofill()

    def try_autofill(self, *_):
        card_name = self.name_input.text.strip()
        if not card_name:
            return
        decks = self.app_ref.store.data.get("decks", {})
        found = find_card_defaults(decks, card_name)
        if found:
            self.cmc_input.text = str(found.get("cmc", 0))
            raw_color = str(found.get("color", "C")).upper()
            self.custom_color_input.text = raw_color

            # Select matching color option
            matched_opt = None
            for opt_text, opt_code in COLOR_OPTIONS_MAP.items():
                if opt_code == raw_color:
                    matched_opt = opt_text
                    break
            if matched_opt:
                self.color_spinner.text = matched_opt
            else:
                self.color_spinner.text = "Multicolorida (Duas ou mais cores)"

            card_type = str(found.get("type", "Criatura"))
            if card_type in CARD_TYPES:
                self.type_spinner.text = card_type
            if not self.desc_input.text.strip():
                self.desc_input.text = str(found.get("description", ""))

            self.feedback_label.text = f"Dados preenchidos automaticamente para '{card_name}'."
            self.feedback_label.color = ACCENT

    def save_card(self, *_):
        deck_name = self.deck_spinner.text
        if deck_name not in self.app_ref.store.data.get("decks", {}):
            self.app_ref.set_status("Selecione um deck válido.")
            return

        card_name = self.name_input.text.strip()
        if not card_name:
            self.app_ref.set_status("Preencha o nome da carta.")
            return

        try:
            qty = int(self.qty_input.text.strip() or "1")
            cmc = int(self.cmc_input.text.strip() or "0")
        except ValueError:
            self.app_ref.set_status("Quantidade e CMC devem ser números inteiros.")
            return

        if qty <= 0:
            self.app_ref.set_status("A quantidade de cópias deve ser maior que zero.")
            return

        section_key = "side" if "Sideboard" in self.section_spinner.text else "main"
        deck = self.app_ref.store.data["decks"][deck_name]
        deck.setdefault(section_key, {})

        chosen_color = COLOR_OPTIONS_MAP.get(self.color_spinner.text, "C")
        if chosen_color == "MULTI":
            color = self.custom_color_input.text.strip().upper() or "C"
        else:
            color = chosen_color

        card_type = self.type_spinner.text if self.type_spinner.text in CARD_TYPES else "Outros"
        description = self.desc_input.text.strip()

        deck[section_key][card_name] = {
            "qty": qty,
            "cmc": cmc,
            "color": color,
            "type": card_type,
            "description": description,
        }

        sec_label = "Sideboard" if section_key == "side" else "Mainboard"
        self.app_ref.record_change(f"Carta salva: {card_name} ({qty} cópias) no {sec_label} de {deck_name}")
        if self.app_ref.save_data():
            self.name_input.text = ""
            self.desc_input.text = ""
            self.qty_input.text = "1"
            self.feedback_label.text = f"{qty} cópia(s) de '{card_name}' adicionada(s) ao {sec_label}!"
            self.feedback_label.color = SUCCESS
            self.app_ref.set_status(f"Carta '{card_name}' salva.")
            self.app_ref.refresh_all()

    def check_legality(self, *_):
        deck_name = self.deck_spinner.text
        deck = self.app_ref.store.data.get("decks", {}).get(deck_name)
        if not deck:
            self.app_ref.set_status("Selecione um deck para verificar legalidade.")
            return
        res = check_deck_legality(deck)
        if res["valid"]:
            msg = (
                f"Deck: {deck_name}\n\n"
                f"✓ O deck cumpre todas as regras básicas oficiais:\n"
                f"• Mainboard: {res['total_main']} cartas (mínimo exigido: 60)\n"
                f"• Sideboard: {res['total_side']} cartas (máximo permitido: 15)\n"
                f"• Nenhuma carta não-terreno ultrapassa o limite de 4 cópias."
            )
            show_info_dialog("Legalidade: Válido!", msg)
        else:
            msg = (
                f"Deck: {deck_name}\n\n"
                f"Avisos de legalidade encontrados:\n\n"
                + "\n\n".join(f"• {issue}" for issue in res["issues"])
            )
            show_info_dialog("Legalidade: Avisos", msg)

    def view_deck_cards(self, *_):
        self.app_ref.screens["deck_cards"].deck_spinner.text = self.deck_spinner.text
        self.app_ref.show_screen("deck_cards")

    def refresh(self):
        decks = self.app_ref.store.data.get("decks", {})
        names = sorted(decks.keys(), key=normalize_keyword_search)
        prev = self.deck_spinner.text
        self.deck_spinner.values = names
        if prev in names:
            self.deck_spinner.text = prev
        elif names:
            self.deck_spinner.text = names[0]
        else:
            self.deck_spinner.text = "Nenhum deck criado"


# ==============================================================================
# TELA 4: ANÁLISE DO DECK (CURVA, DISTRIBUIÇÕES, IMPORT/EXPORT, PLANO)
# ==============================================================================
class DeckAnalysisScreen(ManaVaultScreen):
    """Análise Visual, Curva de Mana, Distribuição de Tipos/Cores e Estratégia."""

    def __init__(self, app_ref, **kwargs):
        super().__init__(app_ref, **kwargs)
        self.heading("Análise do Deck")

        # Seleção do Deck
        self.add_field_label("Deck Analisado:")
        self.deck_spinner = make_spinner("Selecione um deck", [])
        self.deck_spinner.bind(text=lambda *_: self.on_deck_changed())
        self.content.add_widget(self.deck_spinner)

        # Resumo Geral
        self.overview_label = make_label("", height=46, font_size=13, color=MUTED)
        self.content.add_widget(self.overview_label)

        # Barra de Ações do Deck
        self.add_field_label("Ferramentas de Decklist:")
        tools_row1 = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(6))
        tools_row1.add_widget(make_button("Ver Cartas do Deck", self.jump_to_cards, color=ACCENT, height=40, font_size=12))
        tools_row1.add_widget(make_button("Regras de Legalidade", self.check_legality, color=SURFACE_LIGHT, height=40, font_size=12))
        self.content.add_widget(tools_row1)

        tools_row2 = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(6))
        tools_row2.add_widget(make_button("Importar Decklist", self.import_decklist_dialog, color=SURFACE_LIGHT, height=40, font_size=12))
        tools_row2.add_widget(make_button("Copiar Decklist", self.copy_decklist, color=SURFACE_LIGHT, height=40, font_size=12))
        self.content.add_widget(tools_row2)

        # Curva de Mana Visual
        self.add_field_label("Curva de Mana (Custo Convertido - CMC):", "Quantidade de cartas distribuída por valor de mana:")
        self.mana_curve_container = BoxLayout(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(110),
            spacing=dp(6),
            padding=[dp(4), dp(4)],
        )
        self.content.add_widget(self.mana_curve_container)

        # Distribuição de Cores e Tipos
        self.add_field_label("Distribuição de Tipos e Cores:")
        self.dist_label = Label(
            text="",
            size_hint_y=None,
            font_size="13sp",
            color=TEXT_COLOR,
            halign="left",
            valign="top",
        )
        self.dist_label.bind(width=lambda inst, val: setattr(inst, "text_size", (val, None)))
        self.dist_label.bind(texture_size=lambda inst, val: setattr(inst, "height", max(dp(50), val[1] + dp(8))))
        self.content.add_widget(self.dist_label)

        # Tags e Plano de Jogo do Deck
        self.add_field_label("Tags e Plano de Jogo:", "Defina arquétipos e anotações de estratégia/sideboard:")
        self.deck_tags_input = make_input("Tags do deck (ex: Modern, Aggro, Burn)")
        self.content.add_widget(self.deck_tags_input)
        self.deck_plan_input = make_input("Plano de jogo / Estratégia de sideboard contra arquétipos...", multiline=True, height=84)
        self.content.add_widget(self.deck_plan_input)
        self.content.add_widget(make_button("Salvar Tags e Plano de Jogo", self.save_deck_metadata, color=SURFACE_LIGHT, height=40, font_size=13))

    def jump_to_cards(self, *_):
        self.app_ref.screens["deck_cards"].deck_spinner.text = self.deck_spinner.text
        self.app_ref.show_screen("deck_cards")

    def on_deck_changed(self):
        deck_name = self.deck_spinner.text
        deck = self.app_ref.store.data.get("decks", {}).get(deck_name, {})
        self.deck_tags_input.text = deck.get("tags", "")
        self.deck_plan_input.text = deck.get("game_plan", "")
        self.render_analysis()

    def save_deck_metadata(self, *_):
        deck_name = self.deck_spinner.text
        deck = self.app_ref.store.data.get("decks", {}).get(deck_name)
        if not deck:
            self.app_ref.set_status("Selecione um deck válido.")
            return
        deck["tags"] = self.deck_tags_input.text.strip()
        deck["game_plan"] = self.deck_plan_input.text.strip()
        self.app_ref.record_change(f"Tags/plano atualizados: {deck_name}")
        if self.app_ref.save_data():
            self.app_ref.set_status("Tags e plano de jogo salvos.")

    def check_legality(self, *_):
        deck_name = self.deck_spinner.text
        deck = self.app_ref.store.data.get("decks", {}).get(deck_name)
        if not deck:
            return
        res = check_deck_legality(deck)
        if res["valid"]:
            show_info_dialog("Legalidade: Válido!", f"✓ Deck '{deck_name}' cumpre todas as regras oficiais (60+ mainboard, max 15 sideboard, 4 cópias máx).")
        else:
            show_info_dialog("Avisos de Legalidade", "\n\n".join(res["issues"]))

    def copy_decklist(self, *_):
        deck_name = self.deck_spinner.text
        deck = self.app_ref.store.data.get("decks", {}).get(deck_name)
        if not deck:
            self.app_ref.set_status("Selecione um deck para copiar.")
            return
        text = format_decklist(deck_name, deck)
        Clipboard.copy(text)
        self.app_ref.set_status("Decklist copiada para a área de transferência.")
        show_info_dialog("Decklist Copiada!", f"O texto da decklist foi copiado para sua área de transferência:\n\n{text[:200]}...")

    def import_decklist_dialog(self, *_):
        deck_name = self.deck_spinner.text
        if deck_name not in self.app_ref.store.data.get("decks", {}):
            self.app_ref.set_status("Selecione um deck primeiro.")
            return

        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        box.add_widget(make_label("Cole o texto da decklist (ex: 4 Lightning Bolt...):", height=28, font_size=13))
        text_input = make_input("", multiline=True, height=130)
        box.add_widget(text_input)

        actions = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        popup = Popup(title=f"Importar para '{deck_name}'", content=box, size_hint=(0.92, None), height=dp(270))

        def do_import(*_):
            raw = text_input.text.strip()
            parsed = parse_decklist_text(raw)
            if not parsed.get("main") and not parsed.get("side"):
                self.app_ref.set_status("Nenhuma carta reconhecida no texto.")
                return
            deck = self.app_ref.store.data["decks"][deck_name]
            for sec in ("main", "side"):
                deck.setdefault(sec, {})
                for c_name, c_data in parsed.get(sec, {}).items():
                    existing = deck[sec].get(c_name, {})
                    existing_qty = int(existing.get("qty", 0) if isinstance(existing, dict) else existing)
                    new_qty = existing_qty + c_data["qty"]
                    cmc = existing.get("cmc", c_data.get("cmc", 0)) if isinstance(existing, dict) else c_data.get("cmc", 0)
                    color = existing.get("color", c_data.get("color", "C")) if isinstance(existing, dict) else c_data.get("color", "C")
                    c_type = existing.get("type", c_data.get("type", "Outros")) if isinstance(existing, dict) else c_data.get("type", "Outros")
                    deck[sec][c_name] = {
                        "qty": new_qty,
                        "cmc": cmc,
                        "color": color,
                        "type": c_type,
                        "description": existing.get("description", "") if isinstance(existing, dict) else "",
                    }
            self.app_ref.record_change(f"Decklist importada para: {deck_name}")
            popup.dismiss()
            if self.app_ref.save_data():
                self.app_ref.set_status("Decklist importada com sucesso.")
                self.app_ref.refresh_all()

        actions.add_widget(make_button("Cancelar", popup.dismiss, color=SURFACE))
        actions.add_widget(make_button("Importar", do_import, color=ACCENT))
        box.add_widget(actions)
        popup.open()

    def render_analysis(self):
        deck_name = self.deck_spinner.text
        deck = self.app_ref.store.data.get("decks", {}).get(deck_name)
        if not deck:
            self.overview_label.text = "Crie ou selecione um deck para visualizar a análise."
            self.mana_curve_container.clear_widgets()
            self.dist_label.text = ""
            return

        summary = analyze_deck(deck)
        legality_badge = "✓ Deck Válido" if summary["valid"] else f"⚠ {len(summary['warnings'])} avisos de regras"
        self.overview_label.text = (
            f"Mainboard (Principal): {summary['main_count']} cartas  ·  Sideboard (Reserva): {summary['side_count']} cartas\n"
            f"Status de Regras: {legality_badge}"
        )
        self.overview_label.color = SUCCESS if summary["valid"] else WARNING

        # Curva de mana visual
        self.mana_curve_container.clear_widgets()
        curve = summary["mana_curve"]
        max_cmc = max(max(curve.keys(), default=0), 6)
        max_qty = max(curve.values(), default=1)
        if max_qty == 0:
            max_qty = 1

        for cmc in range(max_cmc + 1):
            qty = curve.get(cmc, 0)
            col = BoxLayout(orientation="vertical", spacing=dp(2))
            col.add_widget(make_label(f"{qty}" if qty > 0 else "0", height=18, font_size=11, color=TEXT_COLOR if qty > 0 else MUTED, halign="center"))

            bar_container = BoxLayout(size_hint_y=None, height=dp(60))
            bar_height_ratio = qty / max_qty if max_qty > 0 else 0
            inner_bar = BoxLayout(size_hint_y=max(0.08, bar_height_ratio))
            inner_bar.canvas.before.clear()
            with inner_bar.canvas.before:
                from kivy.graphics import Color, RoundedRectangle
                Color(*(ACCENT if qty > 0 else SURFACE_LIGHT))
                r = RoundedRectangle(size=inner_bar.size, pos=inner_bar.pos, radius=[dp(3)])
                inner_bar.bind(size=lambda inst, v, r=r: setattr(r, "size", v), pos=lambda inst, v, r=r: setattr(r, "pos", v))
            bar_container.add_widget(inner_bar)
            col.add_widget(bar_container)

            col.add_widget(make_label(f"{cmc}+" if cmc == max_cmc and cmc >= 6 else f"CMC {cmc}", height=18, font_size=10, color=MUTED, halign="center"))
            self.mana_curve_container.add_widget(col)

        # Tipos e Cores
        types_text = "  ·  ".join(f"{k}: {v}" for k, v in summary["types"].items()) or "Nenhum"
        colors_readable = []
        for c, qty in summary["colors"].items():
            colors_readable.append(f"{format_card_color(c)}: {qty}")
        colors_text = "  ·  ".join(colors_readable) or "Nenhuma"

        self.dist_label.text = f"Tipos de Cartas:\n{types_text}\n\nDistribuição por Cores:\n{colors_text}"

    def refresh(self):
        decks = self.app_ref.store.data.get("decks", {})
        names = sorted(decks.keys(), key=normalize_keyword_search)
        prev = self.deck_spinner.text
        self.deck_spinner.values = names
        if prev in names:
            self.deck_spinner.text = prev
        elif names:
            self.deck_spinner.text = names[0]
        else:
            self.deck_spinner.text = "Nenhum deck criado"
        self.on_deck_changed()


# ==============================================================================
# TELA 5: REGISTRO DE PARTIDAS
# ==============================================================================
class MatchesScreen(ManaVaultScreen):
    """Registro e Edição de Partidas com Arquétipos e Placar."""

    def __init__(self, app_ref, **kwargs):
        super().__init__(app_ref, **kwargs)
        self.edit_index = None
        self.heading("Registro de Partidas")

        self.add_field_label("Seu Deck:")
        self.deck_spinner = make_spinner("Selecione seu deck", [])
        self.content.add_widget(self.deck_spinner)

        self.add_field_label("Adversário:")
        self.opponent_name_input = make_input("Nome do jogador adversário (opcional)")
        self.content.add_widget(self.opponent_name_input)

        opp_deck_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        self.opponent_deck_spinner = make_spinner("Deck Adversário", [])
        opp_deck_row.add_widget(self.opponent_deck_spinner)
        opp_deck_row.add_widget(make_button("+ Novo Deck", self.add_opponent_deck_dialog, color=SURFACE_LIGHT, height=46, font_size=12))
        self.content.add_widget(opp_deck_row)

        self.add_field_label("Posição e Resultado:")
        pos_res_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        self.play_draw_spinner = make_spinner("Você (Play)", PLAY_DRAW_OPTIONS)
        self.result_spinner = make_spinner("Win", RESULT_OPTIONS)
        pos_res_row.add_widget(self.play_draw_spinner)
        pos_res_row.add_widget(self.result_spinner)
        self.content.add_widget(pos_res_row)

        self.add_field_label("Placar de Games e Tipo de Evento:")
        score_event_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        self.score_spinner = make_spinner("2 x 0", SCORE_OPTIONS)
        self.score_spinner.bind(text=self.sync_result_from_score)
        self.event_spinner = make_spinner("Casual", EVENT_TYPES)
        score_event_row.add_widget(self.score_spinner)
        score_event_row.add_widget(self.event_spinner)
        self.content.add_widget(score_event_row)

        self.add_field_label("Data da Partida (AAAA-MM-DD):")
        self.date_input = make_input("Data da partida", date.today().isoformat())
        self.content.add_widget(self.date_input)

        self.add_field_label("Tags (separadas por vírgula):")
        self.tags_input = make_input("Ex: fnm, mulligan5, side_pesado")
        self.content.add_widget(self.tags_input)

        self.add_field_label("Anotações da Partida:", "Destaques, plano de sideboard, erros ou jogadas-chave:")
        self.notes_input = make_input("Anotações detalhadas da partida...", multiline=True, height=84)
        self.content.add_widget(self.notes_input)

        self.save_button = make_button("Registrar Partida", self.save_match, color=ACCENT)
        self.content.add_widget(self.save_button)

        self.cancel_button = make_button("Cancelar Edição", self.cancel_editing, color=SURFACE, height=40)

    def sync_result_from_score(self, instance, text):
        res = result_from_score(text, fallback=self.result_spinner.text)
        self.result_spinner.text = res

    def add_opponent_deck_dialog(self, *_):
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        box.add_widget(make_label("Nome do novo deck/arquétipo adversário:", height=26, font_size=13))
        name_input = make_input("Ex: Mono Red Burn, Tron, Murktide...")
        box.add_widget(name_input)

        actions = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        popup = Popup(title="Novo Deck Adversário", content=box, size_hint=(0.92, None), height=dp(190))

        def add_opp(*_):
            opp_name = name_input.text.strip()
            if not opp_name:
                return
            opp_list = self.app_ref.store.data.setdefault("opponent_decks", [])
            if opp_name not in opp_list:
                opp_list.append(opp_name)
                opp_list.sort(key=normalize_keyword_search)
                self.app_ref.save_data()
            self.refresh_opponent_decks()
            self.opponent_deck_spinner.text = opp_name
            popup.dismiss()

        actions.add_widget(make_button("Cancelar", popup.dismiss, color=SURFACE))
        actions.add_widget(make_button("Adicionar", add_opp, color=ACCENT))
        box.add_widget(actions)
        popup.open()

    def refresh_opponent_decks(self):
        opp_list = sorted(self.app_ref.store.data.get("opponent_decks", []), key=normalize_keyword_search)
        if not opp_list:
            opp_list = ["Desconhecido", "Mono Red", "Izzet Murktide", "Control", "Combo"]
            self.app_ref.store.data["opponent_decks"] = opp_list
        prev = self.opponent_deck_spinner.text
        self.opponent_deck_spinner.values = opp_list
        if prev in opp_list:
            self.opponent_deck_spinner.text = prev
        elif opp_list:
            self.opponent_deck_spinner.text = opp_list[0]

    def load_match_for_edit(self, match, index):
        self.edit_index = index
        self.deck_spinner.text = match.get("deck", "")
        self.opponent_name_input.text = match.get("opponent_name", "")
        opp_deck = match.get("opponent_deck", "")
        if opp_deck and opp_deck not in self.opponent_deck_spinner.values:
            self.opponent_deck_spinner.values = list(self.opponent_deck_spinner.values) + [opp_deck]
        self.opponent_deck_spinner.text = opp_deck
        self.play_draw_spinner.text = match.get("play_draw", "Você (Play)")
        self.result_spinner.text = match.get("result", "Win")
        self.score_spinner.text = match.get("games_score", "2 x 0")
        self.event_spinner.text = match.get("event_type", "Casual")
        self.date_input.text = match.get("match_date", date.today().isoformat())
        tags = match.get("tags", [])
        self.tags_input.text = ", ".join(normalize_tags(tags))
        self.notes_input.text = match.get("notes", "")

        self.save_button.text = "Salvar Alterações da Partida"
        if self.cancel_button not in self.content.children:
            self.content.add_widget(self.cancel_button)
        self.app_ref.show_screen("matches")

    def cancel_editing(self, *_):
        self.edit_index = None
        self.save_button.text = "Registrar Partida"
        if self.cancel_button in self.content.children:
            self.content.remove_widget(self.cancel_button)
        self.clear_form()

    def clear_form(self):
        self.opponent_name_input.text = ""
        self.tags_input.text = ""
        self.notes_input.text = ""
        self.date_input.text = date.today().isoformat()
        self.score_spinner.text = "2 x 0"
        self.result_spinner.text = "Win"

    def save_match(self, *_):
        deck_name = self.deck_spinner.text
        if deck_name not in self.app_ref.store.data.get("decks", {}):
            self.app_ref.set_status("Selecione um deck válido antes de salvar.")
            return

        date_str = self.date_input.text.strip()
        try:
            valid_date = date.fromisoformat(date_str).isoformat()
        except ValueError:
            self.app_ref.set_status("A data precisa estar no formato AAAA-MM-DD.")
            return

        opp_deck = self.opponent_deck_spinner.text.strip() or "Desconhecido"
        opp_name = self.opponent_name_input.text.strip() or "Desconhecido"
        score = self.score_spinner.text.strip() or "2 x 0"
        res = result_from_score(score, fallback=self.result_spinner.text)

        match = {
            "deck": deck_name,
            "opponent_name": opp_name,
            "opponent_deck": opp_deck,
            "result": res,
            "notes": self.notes_input.text.strip(),
            "play_draw": self.play_draw_spinner.text,
            "games_score": score,
            "event_type": self.event_spinner.text,
            "match_date": valid_date,
            "tags": normalize_tags(self.tags_input.text),
        }

        opp_decks = self.app_ref.store.data.setdefault("opponent_decks", [])
        if opp_deck and opp_deck not in opp_decks and opp_deck != "Deck Adversário":
            opp_decks.append(opp_deck)

        matches = self.app_ref.store.data.setdefault("matches", [])
        if self.edit_index is None:
            matches.append(match)
            action = "Partida registrada"
        else:
            matches[self.edit_index] = match
            action = "Partida atualizada"

        self.app_ref.record_change(f"{action}: {deck_name} vs {opp_deck}")
        if self.app_ref.save_data():
            self.cancel_editing()
            self.app_ref.set_status(f"{action} com sucesso.")
            self.app_ref.refresh_all()
            self.app_ref.show_screen("results")

    def refresh(self):
        decks = self.app_ref.store.data.get("decks", {})
        names = sorted(decks.keys(), key=normalize_keyword_search)
        prev = self.deck_spinner.text
        self.deck_spinner.values = names
        if prev in names:
            self.deck_spinner.text = prev
        elif names:
            self.deck_spinner.text = names[0]
        else:
            self.deck_spinner.text = "Nenhum deck criado"
        self.refresh_opponent_decks()


# ==============================================================================
# TELA 6: RESULTADOS & ESTATÍSTICAS
# ==============================================================================
class ResultsScreen(ManaVaultScreen):
    """Resultados, Métricas (Play/Draw WR, Matchups, Auditoria) e Histórico."""

    def __init__(self, app_ref, **kwargs):
        super().__init__(app_ref, **kwargs)
        self.heading("Resultados & Estatísticas")

        # Filtros
        self.add_field_label("Filtros do Histórico:")
        f1 = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        self.deck_filter = make_spinner("Todos os decks", ["Todos os decks"])
        self.opponent_filter = make_spinner("Todos os oponentes", ["Todos os oponentes"])
        f1.add_widget(self.deck_filter)
        f1.add_widget(self.opponent_filter)
        self.content.add_widget(f1)

        f2 = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        self.event_filter = make_spinner("Todos os eventos", ["Todos os eventos"] + EVENT_TYPES)
        self.position_filter = make_spinner("Todos", ["Todos"] + PLAY_DRAW_OPTIONS)
        f2.add_widget(self.event_filter)
        f2.add_widget(self.position_filter)
        self.content.add_widget(f2)

        filter_btn_row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
        filter_btn_row.add_widget(make_button("Aplicar Filtros", self.refresh_results, color=ACCENT, height=38, font_size=12))
        filter_btn_row.add_widget(make_button("Limpar Filtros", self.reset_filters, color=SURFACE_LIGHT, height=38, font_size=12))
        self.content.add_widget(filter_btn_row)

        # Estatísticas Completas
        self.add_field_label("Resumo Estatístico Completo:")
        self.stats_summary_label = Label(
            text="",
            size_hint_y=None,
            font_size="13sp",
            color=TEXT_COLOR,
            halign="left",
            valign="top",
        )
        self.stats_summary_label.bind(width=lambda inst, val: setattr(inst, "text_size", (val, None)))
        self.stats_summary_label.bind(texture_size=lambda inst, val: setattr(inst, "height", max(dp(90), val[1] + dp(12))))
        self.content.add_widget(self.stats_summary_label)

        # Backup de Dados
        self.add_field_label("Backup de Dados (JSON):")
        backup_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        backup_row.add_widget(make_button("Importar Backup", self.app_ref.import_backup, color=SURFACE_LIGHT, height=42, font_size=13))
        backup_row.add_widget(make_button("Exportar Backup", self.app_ref.export_backup, color=ACCENT, height=42, font_size=13))
        self.content.add_widget(backup_row)

        # Histórico de Partidas
        self.add_field_label("Histórico de Partidas:")
        self.matches_list = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        self.matches_list.bind(minimum_height=self.matches_list.setter("height"))
        self.content.add_widget(self.matches_list)

    def reset_filters(self, *_):
        self.deck_filter.text = "Todos os decks"
        self.opponent_filter.text = "Todos os oponentes"
        self.event_filter.text = "Todos os eventos"
        self.position_filter.text = "Todos"
        self.refresh_results()

    def confirm_delete_match(self, original_index, match, *_):
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        box.add_widget(make_label(f"Excluir partida de {match.get('deck', '')} vs {match.get('opponent_deck', '')}?", height=50))
        actions = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        popup = Popup(title="Confirmar Exclusão", content=box, size_hint=(0.88, None), height=dp(180))
        actions.add_widget(make_button("Cancelar", popup.dismiss, color=SURFACE))
        actions.add_widget(make_button("Excluir", partial(self.delete_match, original_index, popup), color=DANGER))
        box.add_widget(actions)
        popup.open()

    def delete_match(self, original_index, popup, *_):
        matches = self.app_ref.store.data.get("matches", [])
        if 0 <= original_index < len(matches):
            m = matches.pop(original_index)
            self.app_ref.record_change(f"Partida removida: {m.get('deck', '')} vs {m.get('opponent_deck', '')}")
            popup.dismiss()
            if self.app_ref.save_data():
                self.app_ref.refresh_all()
                self.app_ref.set_status("Partida removida.")

    def show_match_details_dialog(self, original_index, match, *_):
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        tags_str = ", ".join(normalize_tags(match.get("tags", []))) or "Nenhuma tag."
        text = (
            f"Data: {match.get('match_date', 'Sem data')}\n"
            f"Formato/Evento: {match.get('event_type', 'Casual')}\n"
            f"Seu Deck: {match.get('deck', '')}\n"
            f"Adversário: {match.get('opponent_deck', '')} ({match.get('opponent_name', '')})\n"
            f"Resultado: {'Vitória' if match.get('result') == 'Win' else 'Derrota' if match.get('result') == 'Loss' else 'Empate'}\n"
            f"Placar: {match.get('games_score', 'N/D')}  |  Posição: {match.get('play_draw', 'N/D')}\n"
            f"Tags: {tags_str}\n\n"
            f"Anotações:\n{match.get('notes', 'Nenhuma anotação registrada.')}"
        )
        scroll = ScrollView(do_scroll_x=False)
        lbl = Label(text=text, size_hint_y=None, font_size="13sp", color=TEXT_COLOR, halign="left", valign="top")
        lbl.bind(width=lambda inst, val: setattr(inst, "text_size", (val, None)))
        lbl.bind(texture_size=lambda inst, val: setattr(inst, "height", max(dp(120), val[1] + dp(12))))
        scroll.add_widget(lbl)
        box.add_widget(scroll)

        actions = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(6))
        popup = Popup(title="Detalhes da Partida", content=box, size_hint=(0.92, None), height=dp(340))

        def edit_from_dialog(*_):
            popup.dismiss()
            self.app_ref.screens["matches"].load_match_for_edit(match, original_index)

        def del_from_dialog(*_):
            popup.dismiss()
            self.confirm_delete_match(original_index, match)

        actions.add_widget(make_button("Fechar", popup.dismiss, color=SURFACE))
        actions.add_widget(make_button("Editar", edit_from_dialog, color=SURFACE_LIGHT))
        actions.add_widget(make_button("Excluir", del_from_dialog, color=DANGER))
        box.add_widget(actions)
        popup.open()

    def refresh_results(self, *_):
        matches = self.app_ref.store.data.get("matches", [])
        changes = self.app_ref.store.data.get("changes", [])

        filtered_indexed = [
            (idx, m)
            for idx, m in enumerate(matches)
            if filter_matches(
                [m],
                deck=self.deck_filter.text,
                opponent=self.opponent_filter.text,
                event=self.event_filter.text,
                play_draw=self.position_filter.text,
            )
        ]
        filtered_matches = [m for _, m in filtered_indexed]

        stats = calculate_full_stats(filtered_matches, changes)
        self.stats_summary_label.text = stats["summary_text"]

        self.matches_list.clear_widgets()
        if not filtered_indexed:
            self.matches_list.add_widget(make_label("Nenhuma partida encontrada com os filtros selecionados.", height=38, color=MUTED))
            return

        for original_idx, match in reversed(filtered_indexed[-100:]):
            res = match.get("result", "Win")
            res_color = SUCCESS if res == "Win" else DANGER if res == "Loss" else WARNING
            res_label = "VITÓRIA" if res == "Win" else "DERROTA" if res == "Loss" else "EMPATE"

            opp = f"{match.get('opponent_deck', 'Desconhecido')}"
            if match.get("opponent_name"):
                opp += f" ({match.get('opponent_name')})"

            card_box = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(86), spacing=dp(2), padding=[dp(10), dp(6)])
            card_box.canvas.before.clear()
            with card_box.canvas.before:
                from kivy.graphics import Color, RoundedRectangle
                Color(*SURFACE)
                r = RoundedRectangle(size=card_box.size, pos=card_box.pos, radius=[dp(6)])
                card_box.bind(size=lambda inst, v, r=r: setattr(r, "size", v), pos=lambda inst, v, r=r: setattr(r, "pos", v))

            title_row = BoxLayout(size_hint_y=None, height=dp(24))
            title_row.add_widget(make_label(f"{match.get('deck', '')} vs {opp}", height=22, font_size=14, bold=True))
            card_box.add_widget(title_row)

            sub_text = f"Resultado: {res_label} [{match.get('games_score', 'N/D')}] · {match.get('play_draw', 'N/D')} · {match.get('match_date', '')}"
            sub_lbl = make_label(sub_text, height=20, font_size=12, color=res_color)
            card_box.add_widget(sub_lbl)

            btn_row = BoxLayout(size_hint_y=None, height=dp(32), spacing=dp(6))
            btn_row.add_widget(make_button("Ver Detalhes", partial(self.show_match_details_dialog, original_idx, match), color=SURFACE_LIGHT, height=30, font_size=11))
            btn_row.add_widget(make_button("Editar", lambda *_, i=original_idx, m=match: self.app_ref.screens["matches"].load_match_for_edit(m, i), color=SURFACE_LIGHT, height=30, font_size=11))
            btn_row.add_widget(make_button("Excluir", partial(self.confirm_delete_match, original_idx, match), color=DANGER, height=30, font_size=11))
            card_box.add_widget(btn_row)

            self.matches_list.add_widget(card_box)

    def refresh(self):
        decks = sorted(self.app_ref.store.data.get("decks", {}).keys(), key=normalize_keyword_search)
        opp_decks = sorted(self.app_ref.store.data.get("opponent_decks", []), key=normalize_keyword_search)

        prev_deck = self.deck_filter.text
        self.deck_filter.values = ["Todos os decks"] + decks
        self.deck_filter.text = prev_deck if prev_deck in self.deck_filter.values else "Todos os decks"

        prev_opp = self.opponent_filter.text
        self.opponent_filter.values = ["Todos os oponentes"] + opp_decks
        self.opponent_filter.text = prev_opp if prev_opp in self.opponent_filter.values else "Todos os oponentes"

        self.refresh_results()


# ==============================================================================
# TELA 7: CONSULTA DE EFEITOS
# ==============================================================================
class RulesScreen(ManaVaultScreen):
    """Consulta de 26 Regras de Combate MTG com orientações detalhadas."""

    def __init__(self, app_ref, **kwargs):
        super().__init__(app_ref, **kwargs)
        self.heading("Consulta de Efeitos (MTG)")

        self.add_field_label("Buscar Regra ou Efeito de Combate:")
        self.search_input = make_input("Ex: Atropelar, Golpe Duplo, Iniciativa...")
        self.search_input.bind(text=lambda *_: self.refresh())
        self.content.add_widget(self.search_input)

        self.rules_list = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(10))
        self.rules_list.bind(minimum_height=self.rules_list.setter("height"))
        self.content.add_widget(self.rules_list)

        rules_path = Path(__file__).with_name("keyword_rules.json")
        try:
            self.rules = json.loads(rules_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.rules = []

    def refresh(self):
        self.rules_list.clear_widgets()
        query = normalize_keyword_search(self.search_input.text).strip()

        count = 0
        for rule in self.rules:
            name = rule.get("name", "")
            english = rule.get("english", "")
            summary = rule.get("summary", "")
            sections = rule.get("sections", [])

            searchable = f"{name} {english} {summary} " + " ".join(f"{h} {d}" for h, d in sections)
            if query and query not in normalize_keyword_search(searchable):
                continue

            count += 1
            card_box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4), padding=[dp(12), dp(8)])
            card_box.canvas.before.clear()
            with card_box.canvas.before:
                from kivy.graphics import Color, RoundedRectangle
                Color(*SURFACE)
                r = RoundedRectangle(size=card_box.size, pos=card_box.pos, radius=[dp(6)])
                card_box.bind(size=lambda inst, v, r=r: setattr(r, "size", v), pos=lambda inst, v, r=r: setattr(r, "pos", v))

            title = f"{name} ({english})" if english else name
            card_box.add_widget(make_label(title, height=26, font_size=15, bold=True, color=ACCENT))

            sum_lbl = Label(text=summary, size_hint_y=None, font_size="13sp", color=TEXT_COLOR, halign="left", valign="top")
            sum_lbl.bind(width=lambda inst, val: setattr(inst, "text_size", (val, None)))
            sum_lbl.bind(texture_size=lambda inst, val: setattr(inst, "height", max(dp(30), val[1] + dp(4))))
            card_box.add_widget(sum_lbl)

            for heading, description in sections:
                h_norm = normalize_keyword_search(heading)
                h_color = WARNING if "bloqueador" in h_norm else ACCENT if "atacar" in h_norm else SUCCESS if "bloquear" in h_norm else MUTED
                card_box.add_widget(make_label(f"▶ {heading}", height=22, font_size=12, bold=True, color=h_color))

                desc_lbl = Label(text=description, size_hint_y=None, font_size="12sp", color=MUTED, halign="left", valign="top")
                desc_lbl.bind(width=lambda inst, val: setattr(inst, "text_size", (val, None)))
                desc_lbl.bind(texture_size=lambda inst, val: setattr(inst, "height", max(dp(24), val[1] + dp(4))))
                card_box.add_widget(desc_lbl)

            card_box.bind(minimum_height=card_box.setter("height"))
            self.rules_list.add_widget(card_box)

        if count == 0:
            self.rules_list.add_widget(make_label("Nenhum efeito encontrado com a busca.", height=38, color=MUTED))


# ==============================================================================
# MAIN APP CLASS (COM MENU LATERAL DIREITO E SEM BARRA INFERIOR APERTADA)
# ==============================================================================
class ManaVaultApp(App):
    title = "ManaVault"

    SCREEN_TITLES = {
        "decks": "Gerenciar Decks",
        "deck_cards": "Cartas do Deck",
        "add_cards": "Adicionar Cartas",
        "analysis": "Análise do Deck",
        "matches": "Registro de Partidas",
        "results": "Resultados & Estatísticas",
        "rules": "Consulta de Efeitos",
    }

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

        root = BoxLayout(orientation="vertical", spacing=dp(4), padding=[dp(6), dp(4)])

        # Barra Superior: Nome da tela à esquerda e Botão de Menu na Extrema Direita!
        top_bar = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6), padding=[dp(4), dp(2)])
        self.header_title = make_label("ManaVault  ·  Decks", height=42, font_size=16, bold=True, color=ACCENT)
        top_bar.add_widget(self.header_title)

        # Botão Menu na Extrema Direita
        self.menu_button = Button(
            text="☰  Menu",
            size_hint=(None, None),
            size=(dp(96), dp(40)),
            background_normal="",
            background_color=SURFACE_LIGHT,
            color=TEXT_COLOR,
            font_size="13sp",
            bold=True,
        )
        self.menu_button.bind(on_release=self.open_navigation_menu)
        top_bar.add_widget(self.menu_button)
        root.add_widget(top_bar)

        # Gerenciador de Telas
        self.manager = ScreenManager(transition=NoTransition())
        self.screens = {
            "decks": DecksScreen(self, name="decks"),
            "deck_cards": DeckCardsScreen(self, name="deck_cards"),
            "add_cards": AddCardsScreen(self, name="add_cards"),
            "analysis": DeckAnalysisScreen(self, name="analysis"),
            "matches": MatchesScreen(self, name="matches"),
            "results": ResultsScreen(self, name="results"),
            "rules": RulesScreen(self, name="rules"),
        }
        for s in self.screens.values():
            self.manager.add_widget(s)
        root.add_widget(self.manager)

        # Barra de status sutil no rodapé (sem botões que comprimam a tela)
        self.status_label = make_label("Offline  ·  Dados Locais Seguros", height=20, font_size=11, color=MUTED, halign="center")
        root.add_widget(self.status_label)

        self.show_screen("decks")
        self.refresh_all()

        if self.load_error:
            self.set_status(f"{self.load_error}")

        return root

    def open_navigation_menu(self, *_):
        """Abre o menu elegante na lateral direita com todas as opções de tela."""
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=[dp(12), dp(10)])
        scroll = ScrollView(do_scroll_x=False)
        items_layout = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(6))
        items_layout.bind(minimum_height=items_layout.setter("height"))

        menu_items = [
            ("decks", "🗂️  1. Gerenciar Decks"),
            ("deck_cards", "🃏  2. Cartas do Deck (Lista)"),
            ("add_cards", "➕  3. Adicionar Cartas"),
            ("analysis", "📊  4. Análise do Deck (Curva)"),
            ("matches", "⚔️  5. Registro de Partidas"),
            ("results", "🏆  6. Resultados & Estatísticas"),
            ("rules", "📖  7. Consulta de Efeitos MTG"),
        ]

        popup = Popup(
            title="Navegação ManaVault",
            size_hint=(0.88, None),
            height=dp(420),
            auto_dismiss=True,
        )

        for screen_name, title in menu_items:
            is_active = self.manager.current == screen_name
            btn = Button(
                text=title,
                size_hint_y=None,
                height=dp(44),
                background_normal="",
                background_color=ACCENT if is_active else SURFACE,
                color=TEXT_COLOR,
                font_size="13sp",
                bold=True,
                halign="left",
            )
            btn.bind(on_release=partial(self._navigate_from_menu, screen_name, popup))
            items_layout.add_widget(btn)

        scroll.add_widget(items_layout)
        box.add_widget(scroll)

        close_btn = make_button("✖  Fechar Menu", popup.dismiss, color=SURFACE_LIGHT, height=38, font_size=12)
        box.add_widget(close_btn)

        popup.content = box
        popup.open()

    def _navigate_from_menu(self, screen_name, popup, *_):
        popup.dismiss()
        self.show_screen(screen_name)

    def set_status(self, message):
        if hasattr(self, "status_label"):
            self.status_label.text = message

    def record_change(self, message):
        changes = self.store.data.setdefault("changes", [])
        changes.append({"date": datetime.now().strftime("%d/%m/%Y %H:%M"), "message": message})
        self.store.data["changes"] = changes[-500:]

    def save_data(self):
        if self.storage_blocked:
            self.set_status("Dados protegidos. Importe um backup válido.")
            return False
        try:
            self.store.save()
        except OSError as exc:
            self.set_status(f"Erro ao salvar dados: {exc}")
            return False
        return True

    def refresh_all(self):
        for screen in getattr(self, "screens", {}).values():
            screen.refresh()

    def show_screen(self, screen_name, *_):
        self.manager.current = screen_name
        title_suffix = self.SCREEN_TITLES.get(screen_name, "ManaVault")
        if hasattr(self, "header_title"):
            self.header_title.text = f"ManaVault  ·  {title_suffix}"
        if screen_name in self.screens:
            self.screens[screen_name].refresh()

    def import_backup(self, *_):
        if Chooser is None or SharedStorage is None:
            self.set_status("A seleção de arquivos nativa está disponível no APK Android.")
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
            self.set_status("Backup importado com sucesso!")
            self.refresh_all()
        except (OSError, ValueError) as exc:
            self.set_status(f"Importação cancelada: {exc}")

    def export_backup(self, *_):
        if self.storage_blocked:
            self.set_status("Importe um backup válido antes de exportar.")
            return
        if SharedStorage is None or ShareSheet is None:
            self.set_status("A exportação nativa está disponível no APK Android.")
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
            self.set_status(f"Não foi possível exportar: {exc}")


if __name__ == "__main__":
    ManaVaultApp().run()
