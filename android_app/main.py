"""Offline ManaVault Android interface built with Kivy.
Complete professional UI with card photo capture, Scryfall integration and image viewer.
"""

import json
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import date, datetime
from functools import partial
from pathlib import Path

from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.image import AsyncImage, Image
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import NoTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.utils import get_color_from_hex, platform

from data_store import (
    DEFAULT_GEMINI_API_KEY,
    DataStore,
    analyze_deck,
    calculate_full_stats,
    check_deck_legality,
    empty_data,
    fetch_scryfall_card_info,
    filter_matches,
    find_card_defaults,
    find_card_in_catalog_or_decks,
    format_decklist,
    identify_card_from_photo,
    normalize_keyword_search,
    normalize_tags,
    parse_decklist_text,
    search_card_database,
)

try:
    from androidstorage4kivy import Chooser, ShareSheet, SharedStorage
except ImportError:
    Chooser = ShareSheet = SharedStorage = None


# Modern Obsidian & Mana Dark Theme
COLOR_BG = get_color_from_hex("#0D0F14")
COLOR_SURFACE_1 = get_color_from_hex("#161922")
COLOR_SURFACE_2 = get_color_from_hex("#202430")
COLOR_SURFACE_3 = get_color_from_hex("#2B3040")
COLOR_BORDER = get_color_from_hex("#2C3142")

COLOR_TEXT_PRIMARY = get_color_from_hex("#FFFFFF")
COLOR_TEXT_MUTED = get_color_from_hex("#9CA3AF")
COLOR_TEXT_FAINT = get_color_from_hex("#6B7280")

COLOR_ACCENT = get_color_from_hex("#4F8BFF")
COLOR_ACCENT_PURPLE = get_color_from_hex("#7C5CFC")
COLOR_SUCCESS = get_color_from_hex("#22C55E")
COLOR_WARNING = get_color_from_hex("#F59E0B")
COLOR_DANGER = get_color_from_hex("#EF4444")

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
    "Incolor (C) - Terrenos / Artefatos": "C",
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


# ==============================================================================
# UI HELPER FACTORIES
# ==============================================================================
def create_card_box(spacing=dp(8), padding=dp(12), bg_color=COLOR_SURFACE_1, radius=8):
    box = BoxLayout(
        orientation="vertical",
        size_hint_y=None,
        spacing=spacing,
        padding=padding,
    )
    with box.canvas.before:
        Color(*bg_color)
        rect = RoundedRectangle(size=box.size, pos=box.pos, radius=[dp(radius)])
        box.bind(
            size=lambda inst, v, r=rect: setattr(r, "size", v),
            pos=lambda inst, v, r=rect: setattr(r, "pos", v),
        )
    box.bind(minimum_height=box.setter("height"))
    return box


def make_label(text, height=28, font_size=13, color=COLOR_TEXT_PRIMARY, bold=False, halign="left"):
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


def make_button(text, callback=None, color=COLOR_ACCENT, text_color=COLOR_TEXT_PRIMARY, height=44, font_size=13, radius=8):
    btn = Button(
        text=text,
        size_hint_y=None,
        height=dp(height),
        background_normal="",
        background_color=(0, 0, 0, 0),
        color=text_color,
        font_size=f"{font_size}sp",
        bold=True,
    )
    with btn.canvas.before:
        Color(*color)
        r = RoundedRectangle(size=btn.size, pos=btn.pos, radius=[dp(radius)])
        btn.bind(
            size=lambda inst, v, rect=r: setattr(rect, "size", v),
            pos=lambda inst, v, rect=r: setattr(rect, "pos", v),
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
        font_size="14sp",
        padding=[dp(12), dp(11)],
        background_normal="",
        background_active="",
        background_color=COLOR_SURFACE_2,
        foreground_color=COLOR_TEXT_PRIMARY,
        hint_text_color=COLOR_TEXT_MUTED,
        cursor_color=COLOR_ACCENT,
    )


def make_spinner(default_text, values, height=46):
    sp = Spinner(
        text=str(default_text),
        values=list(values),
        size_hint_y=None,
        height=dp(height),
        background_normal="",
        background_color=(0, 0, 0, 0),
        color=COLOR_TEXT_PRIMARY,
        font_size="13sp",
        bold=True,
    )
    with sp.canvas.before:
        Color(*COLOR_SURFACE_2)
        r = RoundedRectangle(size=sp.size, pos=sp.pos, radius=[dp(8)])
        sp.bind(
            size=lambda inst, v, rect=r: setattr(rect, "size", v),
            pos=lambda inst, v, rect=r: setattr(rect, "pos", v),
        )
    return sp


def show_info_dialog(title, message):
    box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(14))
    scroll = ScrollView(do_scroll_x=False)
    lbl = Label(
        text=message,
        size_hint_y=None,
        font_size="13sp",
        color=COLOR_TEXT_PRIMARY,
        halign="left",
        valign="top",
    )
    lbl.bind(width=lambda inst, val: setattr(inst, "text_size", (val, None)))
    lbl.bind(texture_size=lambda inst, val: setattr(inst, "height", max(dp(60), val[1] + dp(12))))
    scroll.add_widget(lbl)
    box.add_widget(scroll)

    btn = make_button("OK", None, color=COLOR_ACCENT, height=42)
    box.add_widget(btn)

    popup = Popup(title=title, content=box, size_hint=(0.92, None), height=dp(320))
    btn.bind(on_release=popup.dismiss)
    popup.open()


class ManaVaultScreen(Screen):
    """Tela base com scrollview suave e padding ergonômico."""

    def __init__(self, app_ref, **kwargs):
        self.app_ref = app_ref
        super().__init__(**kwargs)
        self.content = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            spacing=dp(12),
            padding=[dp(12), dp(10)],
        )
        self.content.bind(minimum_height=self.content.setter("height"))
        scroll = ScrollView(do_scroll_x=False, bar_width=dp(4))
        scroll.add_widget(self.content)
        self.add_widget(scroll)

    def heading(self, text, icon=None):
        full_text = f"{icon}  {text}" if icon else text
        self.content.add_widget(make_label(full_text, height=36, font_size=18, bold=True, color=COLOR_TEXT_PRIMARY))

    def add_section_header(self, icon, title, subtitle=None):
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(1))
        box.add_widget(make_label(f"{icon}  {title}", height=22, font_size=13, color=COLOR_ACCENT, bold=True))
        if subtitle:
            box.add_widget(make_label(subtitle, height=18, font_size=11, color=COLOR_TEXT_MUTED))
        box.bind(minimum_height=box.setter("height"))
        self.content.add_widget(box)

    def refresh(self):
        pass


# ==============================================================================
# TELA 1: GERENCIAR DECKS
# ==============================================================================
class DecksScreen(ManaVaultScreen):
    """Gerenciamento de Arquétipos / Decks com cards visuais elegantes."""

    def __init__(self, app_ref, **kwargs):
        super().__init__(app_ref, **kwargs)
        self.heading("Gerenciador de Decks", icon="🗂️")

        create_card = create_card_box(padding=dp(12), spacing=dp(8))
        create_card.add_widget(make_label("➕  Criar Novo Deck", height=24, font_size=14, bold=True, color=COLOR_ACCENT))
        create_card.add_widget(make_label("Digite o nome do arquétipo para começar:", height=18, font_size=11, color=COLOR_TEXT_MUTED))
        self.name_input = make_input("Ex: Izzet Murktide, Mono Red Burn, Tron...")
        create_card.add_widget(self.name_input)
        create_card.add_widget(make_button("Criar Deck", self.create_deck, color=COLOR_ACCENT, height=44))
        self.content.add_widget(create_card)

        self.summary_badge = make_label("", height=24, font_size=12, color=COLOR_TEXT_MUTED, halign="center")
        self.content.add_widget(self.summary_badge)

        self.add_section_header("📚", "Seus Decks Cadastrados")
        self.deck_rows = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(10))
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
        actions.add_widget(make_button("Cancelar", popup.dismiss, color=COLOR_SURFACE_2))
        actions.add_widget(make_button("Excluir", partial(self.delete_deck, deck_name, popup), color=COLOR_DANGER))
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
        self.summary_badge.text = f"{len(decks)} decks cadastrados   ·   {total_cards} cartas no total"

        if not decks:
            empty_card = create_card_box(padding=dp(16))
            empty_card.add_widget(make_label("Nenhum deck cadastrado ainda.\nUse o campo acima para criar seu primeiro deck!", height=48, color=COLOR_TEXT_MUTED, halign="center"))
            self.deck_rows.add_widget(empty_card)
            return

        for deck_name, deck in sorted(decks.items(), key=lambda item: normalize_keyword_search(item[0])):
            main_count = sum(int(c.get("qty", 0) if isinstance(c, dict) else c) for c in deck.get("main", {}).values())
            side_count = sum(int(c.get("qty", 0) if isinstance(c, dict) else c) for c in deck.get("side", {}).values())
            tags = deck.get("tags", "")

            deck_card = create_card_box(padding=dp(12), spacing=dp(6))

            top_line = BoxLayout(size_hint_y=None, height=dp(26))
            top_line.add_widget(make_label(deck_name, height=24, font_size=16, bold=True))
            count_lbl = make_label(f"{main_count + side_count} cartas", height=24, font_size=12, bold=True, color=COLOR_ACCENT, halign="right")
            top_line.add_widget(count_lbl)
            deck_card.add_widget(top_line)

            info_text = f"Principal (Main): {main_count}   ·   Reserva (Side): {side_count}"
            if tags:
                info_text += f"   ·   Tags: {tags}"
            deck_card.add_widget(make_label(info_text, height=20, font_size=12, color=COLOR_TEXT_MUTED))

            btn_row = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))
            btn_row.add_widget(make_button("🃏  Ver Cartas", partial(self.open_deck_cards, deck_name), color=COLOR_ACCENT, height=34, font_size=12))
            btn_row.add_widget(make_button("📊  Análise", partial(self.open_deck_analysis, deck_name), color=COLOR_SURFACE_2, height=34, font_size=12))
            btn_row.add_widget(make_button("🗑️  Excluir", partial(self.confirm_delete, deck_name), color=COLOR_DANGER, height=34, font_size=12))
            deck_card.add_widget(btn_row)

            self.deck_rows.add_widget(deck_card)


# ==============================================================================
# TELA 2: CARTAS DO DECK (COM MINIATURAS E VISUALIZADOR DE FOTO)
# ==============================================================================
class DeckCardsScreen(ManaVaultScreen):
    """Lista de cartas com miniatura da foto, filtros e visualizador em alta resolução."""

    def __init__(self, app_ref, **kwargs):
        super().__init__(app_ref, **kwargs)
        self.heading("Cartas do Deck", icon="🃏")
        self.active_section = "all"

        top_card = create_card_box(padding=dp(10), spacing=dp(8))
        top_card.add_widget(make_label("Deck em Visualização:", height=20, font_size=12, color=COLOR_TEXT_MUTED))
        self.deck_spinner = make_spinner("Selecione um deck", [])
        self.deck_spinner.bind(text=lambda *_: self.render_cards_list())
        top_card.add_widget(self.deck_spinner)

        add_btn = make_button("➕  Adicionar Carta a Este Deck", self.jump_to_add_card, color=COLOR_ACCENT, height=40, font_size=13)
        top_card.add_widget(add_btn)
        self.content.add_widget(top_card)

        filter_card = create_card_box(padding=dp(10), spacing=dp(8))
        self.search_input = make_input("🔍  Buscar carta pelo nome...")
        self.search_input.bind(text=lambda *_: self.render_cards_list())
        filter_card.add_widget(self.search_input)

        pills_row = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(6))
        self.pill_all = make_button("Todas", partial(self.set_section_filter, "all"), color=COLOR_ACCENT, height=32, font_size=12)
        self.pill_main = make_button("Principal", partial(self.set_section_filter, "main"), color=COLOR_SURFACE_2, height=32, font_size=12)
        self.pill_side = make_button("Reserva", partial(self.set_section_filter, "side"), color=COLOR_SURFACE_2, height=32, font_size=12)
        pills_row.add_widget(self.pill_all)
        pills_row.add_widget(self.pill_main)
        pills_row.add_widget(self.pill_side)
        filter_card.add_widget(pills_row)

        self.type_filter = make_spinner("Todos os tipos de carta", ["Todos os tipos de carta"] + CARD_TYPES, height=40)
        self.type_filter.bind(text=lambda *_: self.render_cards_list())
        filter_card.add_widget(self.type_filter)
        self.content.add_widget(filter_card)

        self.count_badge = make_label("", height=22, font_size=12, color=COLOR_TEXT_MUTED, halign="center")
        self.content.add_widget(self.count_badge)

        self.cards_list = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        self.cards_list.bind(minimum_height=self.cards_list.setter("height"))
        self.content.add_widget(self.cards_list)

    def set_section_filter(self, section, *_):
        self.active_section = section
        self.pill_all.canvas.before.clear()
        self.pill_main.canvas.before.clear()
        self.pill_side.canvas.before.clear()

        for btn, sec in ((self.pill_all, "all"), (self.pill_main, "main"), (self.pill_side, "side")):
            is_active = self.active_section == sec
            col = COLOR_ACCENT if is_active else COLOR_SURFACE_2
            with btn.canvas.before:
                Color(*col)
                r = RoundedRectangle(size=btn.size, pos=btn.pos, radius=[dp(6)])
                btn.bind(size=lambda inst, v, rect=r: setattr(rect, "size", v), pos=lambda inst, v, rect=r: setattr(rect, "pos", v))

        self.render_cards_list()

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
                deck[section][card_name] = {"qty": new_qty, "cmc": 0, "color": "C", "type": "Outros", "description": "", "image_uri": ""}
            self.app_ref.record_change(f"Qtd ajustada: {card_name} ({new_qty} cópias) em {deck_name}")
        if self.app_ref.save_data():
            self.app_ref.refresh_all()

    def edit_card_notes_dialog(self, section, card_name, *_):
        deck_name = self.deck_spinner.text
        deck = self.app_ref.store.data.get("decks", {}).get(deck_name, {})
        card = deck.get(section, {}).get(card_name, {})
        current_notes = card.get("description", "") if isinstance(card, dict) else ""

        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(12))
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

        actions.add_widget(make_button("Cancelar", popup.dismiss, color=COLOR_SURFACE_2))
        actions.add_widget(make_button("Salvar", save_notes, color=COLOR_ACCENT))
        box.add_widget(actions)
        popup.open()

    def show_card_image_dialog(self, card_name, card_info, section_key, *_):
        """Abre o visualizador da carta com a foto em alta resolução."""
        image_uri = card_info.get("image_uri", "") if isinstance(card_info, dict) else ""
        qty = card_info.get("qty", 1) if isinstance(card_info, dict) else 1
        cmc = card_info.get("cmc", 0) if isinstance(card_info, dict) else 0
        color = card_info.get("color", "C") if isinstance(card_info, dict) else "C"
        c_type = card_info.get("type", "Outros") if isinstance(card_info, dict) else "Outros"
        desc = card_info.get("description", "") if isinstance(card_info, dict) else ""

        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))

        # Título da carta
        t_box = BoxLayout(size_hint_y=None, height=dp(26))
        t_box.add_widget(make_label(card_name, height=24, font_size=16, bold=True))
        t_box.add_widget(make_label(f"{qty} cópia(s)", height=24, font_size=13, bold=True, color=COLOR_ACCENT, halign="right"))
        box.add_widget(t_box)

        # Imagem da carta grande
        img_container = BoxLayout(size_hint_y=None, height=dp(280))
        if image_uri:
            full_img = AsyncImage(source=image_uri, allow_stretch=True, keep_ratio=True)
            img_container.add_widget(full_img)
        else:
            no_img_box = create_card_box(bg_color=COLOR_SURFACE_2, padding=dp(20))
            no_img_box.add_widget(make_label("📷  Nenhuma foto anexada a esta carta.\nVocê pode tirar uma foto ou baixar a arte oficial!", height=54, color=COLOR_TEXT_MUTED, halign="center"))
            img_container.add_widget(no_img_box)
        box.add_widget(img_container)

        # Dados da carta
        info_lbl = make_label(f"Tipo: {c_type}   ·   Custo de Mana: {cmc}   ·   Cor: {format_card_color(color)}", height=20, font_size=12, color=COLOR_TEXT_MUTED)
        box.add_widget(info_lbl)
        if desc:
            box.add_widget(make_label(f"Obs: {desc}", height=20, font_size=11, color=COLOR_TEXT_PRIMARY))

        actions = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(6))
        popup = Popup(title=f"Visualizador: {card_name}", content=box, size_hint=(0.94, None), height=dp(440))

        def open_add_to_edit(*_):
            popup.dismiss()
            add_sc = self.app_ref.screens["add_cards"]
            add_sc.deck_spinner.text = self.deck_spinner.text
            add_sc.name_input.text = card_name
            add_sc.qty_input.text = str(qty)
            add_sc.cmc_input.text = str(cmc)
            add_sc.desc_input.text = desc
            add_sc.set_card_image(image_uri)
            self.app_ref.show_screen("add_cards")

        actions.add_widget(make_button("Fechar", popup.dismiss, color=COLOR_SURFACE_2))
        actions.add_widget(make_button("📷  Trocar Foto / Editar", open_add_to_edit, color=COLOR_ACCENT))
        box.add_widget(actions)
        popup.open()

    def render_cards_list(self):
        self.cards_list.clear_widgets()
        deck_name = self.deck_spinner.text
        deck = self.app_ref.store.data.get("decks", {}).get(deck_name)
        if not deck:
            self.count_badge.text = "Crie ou selecione um deck para visualizar as cartas."
            return

        term = normalize_keyword_search(self.search_input.text).strip()
        type_filter = self.type_filter.text

        sections_to_show = []
        if self.active_section in ("all", "main"):
            sections_to_show.append(("main", "Mainboard (Deck Principal)"))
        if self.active_section in ("all", "side"):
            sections_to_show.append(("side", "Sideboard (Reserva)"))

        total_cards_count = 0
        total_unique_count = 0

        for section_key, section_title in sections_to_show:
            cards = deck.get(section_key, {})
            matching_cards = []
            for c_name, c_info in sorted(cards.items(), key=lambda i: normalize_keyword_search(i[0])):
                if not isinstance(c_info, dict):
                    c_info = {"qty": int(c_info), "cmc": 0, "color": "C", "type": "Outros", "description": "", "image_uri": ""}

                if term and term not in normalize_keyword_search(c_name):
                    continue
                if type_filter != "Todos os tipos de carta" and c_info.get("type", "Outros") != type_filter:
                    continue
                matching_cards.append((c_name, c_info))

            if matching_cards:
                sec_qty_sum = sum(c[1].get("qty", 1) for c in matching_cards)
                total_cards_count += sec_qty_sum
                total_unique_count += len(matching_cards)

                self.cards_list.add_widget(make_label(f"▶  {section_title}  ({sec_qty_sum} cartas)", height=28, font_size=14, bold=True, color=COLOR_ACCENT))

                for c_name, c_info in matching_cards:
                    qty = c_info.get("qty", 1)
                    cmc = c_info.get("cmc", 0)
                    color_code = c_info.get("color", "C")
                    color_label = format_card_color(color_code)
                    c_type = c_info.get("type", "Outros")
                    desc = c_info.get("description", "")
                    img_uri = c_info.get("image_uri", "")

                    card_box = create_card_box(padding=dp(8), spacing=dp(4))

                    # Conteúdo principal com miniatura à esquerda e dados à direita
                    content_row = BoxLayout(size_hint_y=None, height=dp(64), spacing=dp(8))

                    # Miniatura da Foto
                    thumb_container = BoxLayout(size_hint=(None, None), size=(dp(46), dp(62)))
                    if img_uri:
                        thumb_img = AsyncImage(source=img_uri, allow_stretch=True, keep_ratio=True)
                        thumb_container.add_widget(thumb_img)
                    else:
                        # Placeholder com ícone
                        no_thumb = make_button("📷", partial(self.show_card_image_dialog, c_name, c_info, section_key), color=COLOR_SURFACE_2, height=62, font_size=16)
                        no_thumb.size_hint = (1, 1)
                        thumb_container.add_widget(no_thumb)
                    content_row.add_widget(thumb_container)

                    # Coluna de texto
                    text_col = BoxLayout(orientation="vertical", spacing=dp(1))

                    t_line = BoxLayout(size_hint_y=None, height=dp(22))
                    t_line.add_widget(make_label(f"{c_name}", height=20, font_size=14, bold=True))
                    qty_badge = make_label(f"{qty}x", height=20, font_size=13, bold=True, color=COLOR_ACCENT, halign="right")
                    t_line.add_widget(qty_badge)
                    text_col.add_widget(t_line)

                    details_text = f"{c_type}  ·  CMC: {cmc}  ·  {color_label}"
                    text_col.add_widget(make_label(details_text, height=18, font_size=11, color=COLOR_TEXT_MUTED))

                    if desc:
                        text_col.add_widget(make_label(f"Obs: {desc}", height=16, font_size=10, color=COLOR_TEXT_PRIMARY))
                    else:
                        text_col.add_widget(make_label("Toque para ver a foto", height=16, font_size=10, color=COLOR_TEXT_FAINT))

                    content_row.add_widget(text_col)
                    card_box.add_widget(content_row)

                    # Linha de Ações
                    btn_row = BoxLayout(size_hint_y=None, height=dp(28), spacing=dp(6))
                    btn_row.add_widget(make_button("🔍 Ver Foto", partial(self.show_card_image_dialog, c_name, c_info, section_key), color=COLOR_ACCENT, height=26, font_size=11))
                    btn_row.add_widget(make_button("-1 Cópia", partial(self.adjust_qty, section_key, c_name, -1), color=COLOR_SURFACE_2, height=26, font_size=11))
                    btn_row.add_widget(make_button("+1 Cópia", partial(self.adjust_qty, section_key, c_name, 1), color=COLOR_SURFACE_2, height=26, font_size=11))
                    btn_row.add_widget(make_button("📝 Obs", partial(self.edit_card_notes_dialog, section_key, c_name), color=COLOR_SURFACE_2, height=26, font_size=11))
                    btn_row.add_widget(make_button("🗑️", partial(self.adjust_qty, section_key, c_name, -qty), color=COLOR_DANGER, height=26, font_size=11))
                    card_box.add_widget(btn_row)

                    self.cards_list.add_widget(card_box)

        self.count_badge.text = f"{total_cards_count} cartas exibidas ({total_unique_count} nomes distintos)"

        if total_cards_count == 0:
            empty_box = create_card_box(padding=dp(16))
            empty_box.add_widget(make_label("Nenhuma carta encontrada para os filtros selecionados.", height=36, color=COLOR_TEXT_MUTED, halign="center"))
            self.cards_list.add_widget(empty_box)

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
# TELA 3: ADICIONAR / ATUALIZAR CARTAS (COM SUPORTE A FOTOS E SCRYFALL)
# ==============================================================================
class AddCardsScreen(ManaVaultScreen):
    """Formulário completo com captura de foto da câmera e busca de arte oficial."""

    def __init__(self, app_ref, **kwargs):
        super().__init__(app_ref, **kwargs)
        self.heading("Adicionar / Atualizar Carta", icon="➕")
        self.current_image_uri = ""

        # Card 1: Destino
        c1 = create_card_box(padding=dp(12), spacing=dp(6))
        c1.add_widget(make_label("1. Onde a carta será guardada?", height=22, font_size=13, bold=True, color=COLOR_ACCENT))
        self.deck_spinner = make_spinner("Selecione um deck", [])
        c1.add_widget(self.deck_spinner)

        self.section_spinner = make_spinner("Mainboard (Deck Principal)", ["Mainboard (Deck Principal)", "Sideboard (Reserva)"])
        c1.add_widget(self.section_spinner)
        self.content.add_widget(c1)

        # Card 2: Foto da Carta
        c_photo = create_card_box(padding=dp(12), spacing=dp(8))
        c_photo.add_widget(make_label("2. Foto / Arte da Carta", height=22, font_size=13, bold=True, color=COLOR_ACCENT))
        c_photo.add_widget(make_label("Tire uma foto da sua carta física ou busque a arte oficial:", height=18, font_size=11, color=COLOR_TEXT_MUTED))

        # Preview da Imagem
        self.image_preview_container = BoxLayout(size_hint_y=None, height=dp(140))
        self.image_preview_widget = AsyncImage(source="", allow_stretch=True, keep_ratio=True)
        self.no_image_lbl = make_label("📷  Nenhuma foto selecionada ainda.", height=30, color=COLOR_TEXT_MUTED, halign="center")
        self.image_preview_container.add_widget(self.no_image_lbl)
        c_photo.add_widget(self.image_preview_container)

        c_photo.add_widget(make_label("🎯 Dica: Aproxime a câmera e posicione o NOME bem no MEIO da foto!", height=22, font_size=11, bold=True, color=COLOR_ACCENT))

        # Botões de Ação da Foto
        photo_btn_row1 = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(6))
        photo_btn_row1.add_widget(make_button("📸  Tirar Foto (Câmera)", self.take_photo_camera, color=COLOR_ACCENT, height=40, font_size=12))
        photo_btn_row1.add_widget(make_button("🖼️  Escolher da Galeria", self.choose_from_gallery, color=COLOR_SURFACE_2, height=40, font_size=12))
        c_photo.add_widget(photo_btn_row1)

        photo_btn_row2 = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(6))
        photo_btn_row2.add_widget(make_button("🔮  Identificar Foto (Gemini)", self.scan_current_photo, color=COLOR_ACCENT, height=36, font_size=11))
        photo_btn_row2.add_widget(make_button("🌐  Buscar na Scryfall", self.fetch_official_art, color=COLOR_SURFACE_2, height=36, font_size=11))
        c_photo.add_widget(photo_btn_row2)

        photo_btn_row3 = BoxLayout(size_hint_y=None, height=dp(32), spacing=dp(6))
        photo_btn_row3.add_widget(make_button("⚙️  Configurar IA (Opcional)", self.show_ai_settings_dialog, color=COLOR_SURFACE_3, height=30, font_size=10))
        photo_btn_row3.add_widget(make_button("❌  Remover Foto", self.remove_photo, color=COLOR_SURFACE_3, height=30, font_size=10))
        c_photo.add_widget(photo_btn_row3)
        self.content.add_widget(c_photo)

        # Card 3: Identificação da Carta
        c2 = create_card_box(padding=dp(12), spacing=dp(6))
        c2.add_widget(make_label("3. Identificação da Carta", height=22, font_size=13, bold=True, color=COLOR_ACCENT))

        name_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        self.name_input = make_input("Nome da carta (ex: Lightning Bolt, Raio...)")
        self.name_input.bind(focus=self.on_name_focus_changed, text=self.on_name_text_changed)
        name_row.add_widget(self.name_input)
        name_row.add_widget(make_button("🔍  Buscar", self.try_autofill, color=COLOR_SURFACE_2, height=46, font_size=12))
        c2.add_widget(name_row)

        # Barra de Sugestões Rápidas (Catálogo Offline)
        self.suggestions_box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        self.suggestions_box.bind(minimum_height=self.suggestions_box.setter("height"))
        c2.add_widget(self.suggestions_box)

        c2.add_widget(make_label("Tipo de Carta:", height=18, font_size=12, color=COLOR_TEXT_MUTED))
        self.type_spinner = make_spinner("Criatura", CARD_TYPES)
        c2.add_widget(self.type_spinner)
        self.content.add_widget(c2)

        # Card 4: Atributos de Jogo
        c3 = create_card_box(padding=dp(12), spacing=dp(6))
        c3.add_widget(make_label("4. Quantidade, Custo de Mana e Cor", height=22, font_size=13, bold=True, color=COLOR_ACCENT))

        c3.add_widget(make_label("Quantidade de Cópias:", height=18, font_size=12, color=COLOR_TEXT_MUTED))
        qty_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        qty_row.add_widget(make_button("-", lambda *_: self.adjust_input_number(self.qty_input, -1), color=COLOR_SURFACE_2, height=44, font_size=16))
        self.qty_input = make_input("Qtd", "1")
        qty_row.add_widget(self.qty_input)
        qty_row.add_widget(make_button("+", lambda *_: self.adjust_input_number(self.qty_input, 1), color=COLOR_SURFACE_2, height=44, font_size=16))
        c3.add_widget(qty_row)

        c3.add_widget(make_label("Custo de Mana Convertido (CMC):", height=18, font_size=12, color=COLOR_TEXT_MUTED))
        cmc_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        cmc_row.add_widget(make_button("-", lambda *_: self.adjust_input_number(self.cmc_input, -1, min_val=0), color=COLOR_SURFACE_2, height=44, font_size=16))
        self.cmc_input = make_input("CMC", "0")
        cmc_row.add_widget(self.cmc_input)
        cmc_row.add_widget(make_button("+", lambda *_: self.adjust_input_number(self.cmc_input, 1, min_val=0), color=COLOR_SURFACE_2, height=44, font_size=16))
        c3.add_widget(cmc_row)

        c3.add_widget(make_label("Cor da Carta:", height=18, font_size=12, color=COLOR_TEXT_MUTED))
        self.color_spinner = make_spinner("Incolor (C) - Terrenos / Artefatos", list(COLOR_OPTIONS_MAP.keys()))
        self.color_spinner.bind(text=self.on_color_spinner_changed)
        c3.add_widget(self.color_spinner)

        self.custom_color_input = make_input("Código de cor customizado (ex: UR, WUB, BR)", "C")
        c3.add_widget(self.custom_color_input)
        self.content.add_widget(c3)

        # Card 5: Observações Opcionais
        c5 = create_card_box(padding=dp(12), spacing=dp(6))
        c5.add_widget(make_label("5. Observações / Função no Deck (Opcional)", height=22, font_size=13, bold=True, color=COLOR_ACCENT))
        self.desc_input = make_input("Ex: Remoção rápida, finalizador, acelerador de mana...")
        c5.add_widget(self.desc_input)
        self.content.add_widget(c5)

        # Botão Salvar
        self.content.add_widget(make_button("💾  Salvar Carta no Deck", self.save_card, color=COLOR_ACCENT, height=48, font_size=14))

        sec_row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        sec_row.add_widget(make_button("⚖️  Checar Regras", self.check_legality, color=COLOR_SURFACE_2, height=40, font_size=12))
        sec_row.add_widget(make_button("📋  Ver Cartas do Deck", self.view_deck_cards, color=COLOR_SURFACE_2, height=40, font_size=12))
        self.content.add_widget(sec_row)

        self.feedback_label = make_label("", height=24, font_size=12, color=COLOR_TEXT_MUTED, halign="center")
        self.content.add_widget(self.feedback_label)

    def set_card_image(self, uri):
        """Atualiza a imagem selecionada e o preview."""
        self.current_image_uri = str(uri or "").strip()
        self.image_preview_container.clear_widgets()
        if self.current_image_uri:
            self.image_preview_widget.source = self.current_image_uri
            self.image_preview_widget.reload()
            self.image_preview_container.add_widget(self.image_preview_widget)
        else:
            self.image_preview_container.add_widget(self.no_image_lbl)

    def remove_photo(self, *_):
        self.set_card_image("")
        self.feedback_label.text = "Foto removida desta carta."
        self.feedback_label.color = COLOR_TEXT_MUTED

    def take_photo_camera(self, *_):
        """Dispara a câmera nativa do Android para fotografar a carta em alta resolução."""
        if platform == "android":
            try:
                from android.permissions import Permission, request_permissions

                def on_perm(perms, grants):
                    if all(grants):
                        self._launch_android_camera()
                    else:
                        self.feedback_label.text = "Permissão de câmera é necessária para fotografar a carta."
                        self.feedback_label.color = COLOR_DANGER

                request_permissions([Permission.CAMERA, Permission.READ_EXTERNAL_STORAGE], on_perm)
            except Exception:
                self._launch_android_camera()
        else:
            self.choose_from_gallery()

    def _launch_android_camera(self):
        try:
            from jnius import autoclass, cast
            from android import activity

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            currentActivity = PythonActivity.mActivity
            ContentValues = autoclass("android.content.ContentValues")
            MediaStoreImagesMedia = autoclass("android.provider.MediaStore$Images$Media")
            Intent = autoclass("android.content.Intent")
            MediaStore = autoclass("android.provider.MediaStore")

            intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
            self._pending_camera_uri = None

            try:
                values = ContentValues()
                values.put(MediaStoreImagesMedia.TITLE, f"ManaVault_card_{int(time.time())}")
                values.put(MediaStoreImagesMedia.MIME_TYPE, "image/jpeg")

                content_resolver = currentActivity.getContentResolver()
                self._pending_camera_uri = content_resolver.insert(MediaStoreImagesMedia.EXTERNAL_CONTENT_URI, values)
                if self._pending_camera_uri is not None:
                    parcelable_uri = cast("android.os.Parcelable", self._pending_camera_uri)
                    intent.putExtra(MediaStore.EXTRA_OUTPUT, parcelable_uri)
                    intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
            except Exception:
                pass

            activity.bind(on_activity_result=self._on_camera_activity_result)
            currentActivity.startActivityForResult(intent, 0xCA01)
        except Exception as e:
            self.feedback_label.text = f"Erro ao abrir câmera: {e}"
            self.feedback_label.color = COLOR_DANGER

    def _on_camera_activity_result(self, request_code, result_code, data):
        if request_code == 0xCA01:
            try:
                from android import activity
                activity.unbind(on_activity_result=self._on_camera_activity_result)
            except Exception:
                pass

            if result_code == -1:  # Activity.RESULT_OK
                pending_uri = getattr(self, "_pending_camera_uri", None)
                private_file = None
                if pending_uri and SharedStorage is not None:
                    try:
                        private_file = SharedStorage().copy_from_shared(pending_uri.toString())
                    except Exception:
                        private_file = None

                # Fallback: Se não conseguiu copiar do Uri, tenta ler bitmap retornado direto da Intent
                if (not private_file or not Path(private_file).exists() or Path(private_file).stat().st_size == 0) and data is not None:
                    try:
                        from jnius import autoclass
                        extras = data.getExtras()
                        if extras is not None:
                            bitmap = extras.get("data")
                            if bitmap is not None:
                                dest_dir = self.app_ref.images_dir
                                dest_dir.mkdir(parents=True, exist_ok=True)
                                thumb_path = dest_dir / f"card_cam_{int(time.time() * 1000)}.jpg"
                                FileOutputStream = autoclass("java.io.FileOutputStream")
                                CompressFormat = autoclass("android.graphics.Bitmap$CompressFormat")
                                fos = FileOutputStream(str(thumb_path))
                                bitmap.compress(CompressFormat.JPEG, 92, fos)
                                fos.flush()
                                fos.close()
                                private_file = str(thumb_path)
                    except Exception:
                        pass

                if private_file and Path(private_file).exists() and Path(private_file).stat().st_size > 0:
                    Clock.schedule_once(lambda dt: self._on_photo_ready(private_file), 0)
                else:
                    Clock.schedule_once(lambda dt: self.app_ref.set_status("Foto capturada, mas não foi possível ler a imagem."), 0)
            else:
                # Cancelado: remove o registro vazio gerado no MediaStore se houver
                pending_uri = getattr(self, "_pending_camera_uri", None)
                if pending_uri:
                    try:
                        from jnius import autoclass
                        PythonActivity = autoclass("org.kivy.android.PythonActivity")
                        PythonActivity.mActivity.getContentResolver().delete(pending_uri, None, None)
                    except Exception:
                        pass

    def choose_from_gallery(self, *_):
        """Abre a galeria ou seletor de imagens do celular."""
        if Chooser is not None and SharedStorage is not None:
            self.chooser = Chooser(self._on_gallery_chosen_android)
            self.chooser.choose_content("image/*")
        else:
            # Fallback desktop testing
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                file_path = filedialog.askopenfilename(
                    title="Escolha uma foto da carta",
                    filetypes=[("Imagens", "*.png;*.jpg;*.jpeg;*.webp")],
                )
                root.destroy()
                if file_path:
                    self._on_photo_ready(file_path)
            except Exception as e:
                self.app_ref.set_status(f"Seleção de foto disponível no APK: {e}")

    def _on_gallery_chosen_android(self, shared_files):
        if not shared_files:
            return
        try:
            private_file = SharedStorage().copy_from_shared(shared_files[0])
            if private_file:
                Clock.schedule_once(lambda dt: self._on_photo_ready(private_file), 0)
        except Exception as e:
            Clock.schedule_once(lambda dt: self.app_ref.set_status(f"Erro ao carregar foto: {e}"), 0)

    def _on_photo_ready(self, source_path):
        """Salva a imagem localmente, exibe o preview e dispara o reconhecimento automático."""
        try:
            dest_dir = self.app_ref.images_dir
            dest_dir.mkdir(parents=True, exist_ok=True)
            filename = f"card_{int(time.time() * 1000)}.jpg"
            dest_path = dest_dir / filename

            # Salva o arquivo permanentemente
            dest_path.write_bytes(Path(source_path).read_bytes())
            self.set_card_image(str(dest_path))

            # Executa a identificação automática da carta na foto
            self.scan_current_photo()
        except Exception as e:
            self.feedback_label.text = f"Erro ao processar foto: {e}"
            self.feedback_label.color = COLOR_DANGER

    def scan_current_photo(self, *_):
        """Pesquisa a carta na foto usando IA Gemini primeiro; se não achar, testa Scryfall."""
        photo_path = self.current_image_uri
        if not photo_path or not Path(photo_path).is_file():
            self.feedback_label.text = "Tire uma foto ou escolha da galeria primeiro para identificar."
            self.feedback_label.color = COLOR_WARNING
            return

        self.feedback_label.text = "🔮 Pesquisando foto com IA Gemini primeiro..."
        self.feedback_label.color = COLOR_ACCENT

        def worker():
            gemini_key = self.app_ref.get_gemini_key()
            decks = self.app_ref.store.data.get("decks", {})
            card_info, best_text = identify_card_from_photo(photo_path, gemini_api_key=gemini_key, decks=decks)
            Clock.schedule_once(lambda dt: self._on_card_identified(card_info, best_text), 0)

        threading.Thread(target=worker, daemon=True).start()

    def _on_card_identified(self, card_info, best_text):
        """Preenche automaticamente os campos da carta reconhecida."""
        self._suppress_suggestions = True
        try:
            if card_info:
                card_name = card_info.get("name", "")
                printed_name = card_info.get("printed_name", "")

                self.name_input.text = card_name
                self.cmc_input.text = str(card_info.get("cmc", 0))
                self.custom_color_input.text = card_info.get("color", "C")

                matched_opt = None
                for opt_text, opt_code in COLOR_OPTIONS_MAP.items():
                    if opt_code == card_info.get("color"):
                        matched_opt = opt_text
                        break
                if matched_opt:
                    self.color_spinner.text = matched_opt
                else:
                    self.color_spinner.text = "Multicolorida (Duas ou mais cores)"

                card_type = card_info.get("type", "Outros")
                if card_type in CARD_TYPES:
                    self.type_spinner.text = card_type

                self.suggestions_box.clear_widgets()
                display_title = f"{card_name} ({printed_name})" if printed_name and printed_name != card_name else card_name
                source_name = "IA Gemini" if card_info.get("source") == "gemini" else "Scryfall"
                self.feedback_label.text = f"✓ Identificado pela {source_name}: {display_title}! Atributos preenchidos."
                self.feedback_label.color = COLOR_SUCCESS
            elif best_text:
                self.name_input.text = best_text
                self._suppress_suggestions = False
                self.on_name_text_changed(None, best_text)
                self.feedback_label.text = f"Texto lido: '{best_text}'. Toque na sugestão abaixo ou em Buscar."
                self.feedback_label.color = COLOR_WARNING
            else:
                self.suggestions_box.clear_widgets()
                self.feedback_label.text = "Não foi possível identificar a carta pela foto nem pela Scryfall. Preencha manualmente ou tente outra foto."
                self.feedback_label.color = COLOR_WARNING
        finally:
            self._suppress_suggestions = False

    def show_ai_settings_dialog(self, *_):
        """Exibe popup para o usuário configurar opcionalmente a chave do Gemini Vision."""
        content = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(14))
        content.add_widget(make_label(
            "O ManaVault já identifica cartas gratuitamente via OCR integrado.\n"
            "Se desejar usar a IA multimodal Gemini Vision para máxima precisão\n"
            "(inclusive artes alternativas e foils), insira sua chave gratuita:",
            height=60, font_size=11, color=COLOR_TEXT_MUTED, halign="center"
        ))

        current_key = self.app_ref.get_gemini_key()
        key_input = make_input("Chave Gemini API (ex: AIzaSy...)", current_key)
        key_input.password = True
        content.add_widget(key_input)

        btn_row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        popup = Popup(
            title="Configuração de IA Visual (Opcional)",
            content=content,
            size_hint=(0.92, 0.46),
            background_color=(0.1, 0.12, 0.16, 0.96),
        )

        def save_and_close(*_):
            new_key = key_input.text.strip()
            self.app_ref.set_gemini_key(new_key)
            popup.dismiss()
            if new_key:
                self.feedback_label.text = "✓ Chave Gemini Vision salva! IA ativada para fotos."
                self.feedback_label.color = COLOR_SUCCESS
            else:
                self.feedback_label.text = "Chave removida. Usando OCR integrado gratuito."
                self.feedback_label.color = COLOR_TEXT_MUTED

        btn_row.add_widget(make_button("💾  Salvar", save_and_close, color=COLOR_ACCENT, height=40, font_size=12))
        btn_row.add_widget(make_button("Cancelar", popup.dismiss, color=COLOR_SURFACE_2, height=40, font_size=12))
        content.add_widget(btn_row)
        popup.open()

    def fetch_official_art(self, *_):
        """Busca os dados e a foto oficial da carta na Scryfall em segundo plano."""
        query = self.name_input.text.strip()
        if not query:
            self.feedback_label.text = "Digite o nome da carta para buscar a arte oficial."
            self.feedback_label.color = COLOR_WARNING
            return

        self.feedback_label.text = "Buscando dados e arte oficial na Scryfall..."
        self.feedback_label.color = COLOR_ACCENT

        def worker():
            info = fetch_scryfall_card_info(query)
            Clock.schedule_once(lambda dt: self._on_scryfall_result(info), 0)

        threading.Thread(target=worker, daemon=True).start()

    def _on_scryfall_result(self, info):
        if not info:
            self.feedback_label.text = "Carta não encontrada no catálogo nem na Scryfall. Preencha manualmente."
            self.feedback_label.color = COLOR_WARNING
            return

        self._suppress_suggestions = True
        try:
            self.name_input.text = info["name"]
            self.cmc_input.text = str(info["cmc"])
            self.custom_color_input.text = info["color"]

            matched_opt = None
            for opt_text, opt_code in COLOR_OPTIONS_MAP.items():
                if opt_code == info["color"]:
                    matched_opt = opt_text
                    break
            if matched_opt:
                self.color_spinner.text = matched_opt
            else:
                self.color_spinner.text = "Multicolorida (Duas ou mais cores)"

            if info["type"] in CARD_TYPES:
                self.type_spinner.text = info["type"]

            img_url = info.get("image_url", "")
            if img_url:
                self.set_card_image(img_url)
                self.feedback_label.text = "✓ Dados e arte oficial carregados!"
                self.feedback_label.color = COLOR_SUCCESS
            else:
                self.feedback_label.text = "✓ Dados carregados do catálogo!"
                self.feedback_label.color = COLOR_SUCCESS

            self.suggestions_box.clear_widgets()
        finally:
            self._suppress_suggestions = False

    def adjust_input_number(self, target_input, delta, min_val=1):
        try:
            val = int(target_input.text.strip() or "0")
        except ValueError:
            val = min_val
        val = max(min_val, val + delta)
        target_input.text = str(val)

    def on_color_spinner_changed(self, instance, text):
        code = COLOR_OPTIONS_MAP.get(text, "C")
        if code != "MULTI":
            self.custom_color_input.text = code
        else:
            if self.custom_color_input.text in ("C", "W", "U", "B", "R", "G"):
                self.custom_color_input.text = "UR"

    def on_name_text_changed(self, instance, text):
        """Disparado enquanto o usuário digita: exibe sugestões instantâneas do catálogo offline e decks."""
        if getattr(self, "_suppress_suggestions", False):
            return
        query = str(text or "").strip()
        self.suggestions_box.clear_widgets()
        if len(query) < 2:
            return

        decks = self.app_ref.store.data.get("decks", {})
        matches = search_card_database(query, decks=decks, limit=5)
        if not matches:
            return

        for card_data in matches:
            c_name = card_data.get("name", "")
            c_alt = card_data.get("alt_name", "")
            c_type = card_data.get("type", "Outros")
            c_cmc = card_data.get("cmc", 0)
            c_col = card_data.get("color", "C")

            label = f"✨ {c_name}"
            if c_alt and c_alt != c_name:
                label += f" ({c_alt})"
            label += f"  ·  {c_type}  ·  CMC {c_cmc}  ·  {c_col}"

            btn = make_button(
                label,
                partial(self.select_suggested_card, card_data),
                color=COLOR_SURFACE_2,
                text_color=COLOR_TEXT_PRIMARY,
                height=34,
                font_size=11,
                radius=6,
            )
            self.suggestions_box.add_widget(btn)

    def select_suggested_card(self, card_data, *_):
        """Ao tocar numa sugestão, preenche imediatamente os atributos da carta a partir do catálogo."""
        self._suppress_suggestions = True
        try:
            name = card_data.get("name", "")
            alt = card_data.get("alt_name", "")
            self.name_input.text = name
            self.cmc_input.text = str(card_data.get("cmc", 0))
            color_code = str(card_data.get("color", "C"))
            self.custom_color_input.text = color_code

            matched_opt = None
            for opt_text, opt_code in COLOR_OPTIONS_MAP.items():
                if opt_code == color_code:
                    matched_opt = opt_text
                    break
            if matched_opt:
                self.color_spinner.text = matched_opt
            else:
                self.color_spinner.text = "Multicolorida (Duas ou mais cores)"

            c_type = str(card_data.get("type", "Criatura"))
            if c_type in CARD_TYPES:
                self.type_spinner.text = c_type

            img = card_data.get("image_url", card_data.get("image_uri", ""))
            if img and not self.current_image_uri:
                self.set_card_image(img)

            self.suggestions_box.clear_widgets()

            display_name = f"{name} ({alt})" if alt and alt != name else name
            self.feedback_label.text = f"✓ Carta '{display_name}' selecionada do catálogo offline!"
            self.feedback_label.color = COLOR_SUCCESS
        finally:
            self._suppress_suggestions = False

    def on_name_focus_changed(self, instance, focused):
        if not focused:
            self.try_autofill()

    def try_autofill(self, *_):
        card_name = self.name_input.text.strip()
        if not card_name:
            if self.current_image_uri and Path(self.current_image_uri).is_file():
                self.scan_current_photo()
            return
        self.suggestions_box.clear_widgets()
        decks = self.app_ref.store.data.get("decks", {})
        found = find_card_defaults(decks, card_name)
        if found:
            self._suppress_suggestions = True
            try:
                found_name = found.get("name", card_name)
                self.name_input.text = found_name
                self.cmc_input.text = str(found.get("cmc", 0))
                raw_color = str(found.get("color", "C")).upper()
                self.custom_color_input.text = raw_color

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

                existing_img = found.get("image_uri", "")
                if existing_img and not self.current_image_uri:
                    self.set_card_image(existing_img)

                self.feedback_label.text = f"✓ Dados recuperados do catálogo para '{found_name}'."
                self.feedback_label.color = COLOR_SUCCESS
            finally:
                self._suppress_suggestions = False
        else:
            # Se não estiver no catálogo offline nem decks locais, tenta Scryfall online
            self.fetch_official_art()

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
            "image_uri": self.current_image_uri,
        }

        sec_label = "Sideboard" if section_key == "side" else "Mainboard"
        self.app_ref.record_change(f"Carta salva: {card_name} ({qty} cópias) com foto no {sec_label} de {deck_name}")
        if self.app_ref.save_data():
            self._suppress_suggestions = True
            try:
                self.name_input.text = ""
                self.desc_input.text = ""
                self.qty_input.text = "1"
                self.set_card_image("")
                self.suggestions_box.clear_widgets()
            finally:
                self._suppress_suggestions = False
            self.feedback_label.text = f"✓ '{card_name}' ({qty}x) salva com sucesso no {sec_label}!"
            self.feedback_label.color = COLOR_SUCCESS
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
# TELA 4: ANÁLISE DO DECK (DASHBOARD VISUAL, CURVA, METADATA, FERRAMENTAS)
# ==============================================================================
class DeckAnalysisScreen(ManaVaultScreen):
    """Dashboard de Análise Visual: Curva de Mana, Distribuição, Decklist e Plano."""

    def __init__(self, app_ref, **kwargs):
        super().__init__(app_ref, **kwargs)
        self.heading("Análise do Deck", icon="📊")

        top_card = create_card_box(padding=dp(10), spacing=dp(6))
        top_card.add_widget(make_label("Deck em Análise:", height=20, font_size=12, color=COLOR_TEXT_MUTED))
        self.deck_spinner = make_spinner("Selecione um deck", [])
        self.deck_spinner.bind(text=lambda *_: self.on_deck_changed())
        top_card.add_widget(self.deck_spinner)
        self.content.add_widget(top_card)

        self.overview_card = create_card_box(padding=dp(12), spacing=dp(8))
        self.stat_main_lbl = make_label("📦  Mainboard: 0 cartas", height=22, font_size=13, bold=True)
        self.stat_side_lbl = make_label("🎒  Sideboard: 0 cartas", height=22, font_size=13, bold=True)
        self.stat_lands_lbl = make_label("🌿  Terrenos: 0", height=22, font_size=13, color=COLOR_TEXT_MUTED)
        self.stat_legal_lbl = make_label("⚖️  Regras: OK", height=22, font_size=13, bold=True, color=COLOR_SUCCESS)

        grid = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        r1 = BoxLayout(size_hint_y=None, height=dp(24))
        r1.add_widget(self.stat_main_lbl)
        r1.add_widget(self.stat_side_lbl)
        r2 = BoxLayout(size_hint_y=None, height=dp(24))
        r2.add_widget(self.stat_lands_lbl)
        r2.add_widget(self.stat_legal_lbl)
        grid.add_widget(r1)
        grid.add_widget(r2)
        grid.bind(minimum_height=grid.setter("height"))

        self.overview_card.add_widget(grid)
        self.content.add_widget(self.overview_card)

        curve_card = create_card_box(padding=dp(12), spacing=dp(8))
        curve_card.add_widget(make_label("📈  Curva de Mana (CMC)", height=22, font_size=14, bold=True, color=COLOR_ACCENT))
        curve_card.add_widget(make_label("Distribuição proporcional de mágicas por valor de mana:", height=18, font_size=11, color=COLOR_TEXT_MUTED))

        self.mana_curve_container = BoxLayout(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(110),
            spacing=dp(6),
            padding=[dp(4), dp(4)],
        )
        curve_card.add_widget(self.mana_curve_container)
        self.content.add_widget(curve_card)

        dist_card = create_card_box(padding=dp(12), spacing=dp(8))
        dist_card.add_widget(make_label("🎨  Cores e Tipos de Cartas", height=22, font_size=14, bold=True, color=COLOR_ACCENT))

        self.dist_label = Label(
            text="",
            size_hint_y=None,
            font_size="13sp",
            color=COLOR_TEXT_PRIMARY,
            halign="left",
            valign="top",
        )
        self.dist_label.bind(width=lambda inst, val: setattr(inst, "text_size", (val, None)))
        self.dist_label.bind(texture_size=lambda inst, val: setattr(inst, "height", max(dp(44), val[1] + dp(8))))
        dist_card.add_widget(self.dist_label)
        self.content.add_widget(dist_card)

        tools_card = create_card_box(padding=dp(12), spacing=dp(8))
        tools_card.add_widget(make_label("🛠️  Ferramentas de Decklist", height=22, font_size=14, bold=True, color=COLOR_ACCENT))

        t_row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
        t_row.add_widget(make_button("📋  Ver Cartas", self.jump_to_cards, color=COLOR_ACCENT, height=38, font_size=12))
        t_row.add_widget(make_button("📥  Importar", self.import_decklist_dialog, color=COLOR_SURFACE_2, height=38, font_size=12))
        t_row.add_widget(make_button("📤  Copiar", self.copy_decklist, color=COLOR_SURFACE_2, height=38, font_size=12))
        tools_card.add_widget(t_row)
        self.content.add_widget(tools_card)

        plan_card = create_card_box(padding=dp(12), spacing=dp(8))
        plan_card.add_widget(make_label("🏷️  Tags & Plano de Jogo", height=22, font_size=14, bold=True, color=COLOR_ACCENT))
        self.deck_tags_input = make_input("Tags (ex: Modern, Aggro, Burn)")
        plan_card.add_widget(self.deck_tags_input)
        self.deck_plan_input = make_input("Plano de jogo / Guia de sideboard contra arquétipos...", multiline=True, height=84)
        plan_card.add_widget(self.deck_plan_input)
        plan_card.add_widget(make_button("Salvar Tags e Plano", self.save_deck_metadata, color=COLOR_SURFACE_2, height=38, font_size=12))
        self.content.add_widget(plan_card)

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
                    img_uri = existing.get("image_uri", "") if isinstance(existing, dict) else ""
                    deck[sec][c_name] = {
                        "qty": new_qty,
                        "cmc": cmc,
                        "color": color,
                        "type": c_type,
                        "description": existing.get("description", "") if isinstance(existing, dict) else "",
                        "image_uri": img_uri,
                    }
            self.app_ref.record_change(f"Decklist importada para: {deck_name}")
            popup.dismiss()
            if self.app_ref.save_data():
                self.app_ref.set_status("Decklist importada com sucesso.")
                self.app_ref.refresh_all()

        actions.add_widget(make_button("Cancelar", popup.dismiss, color=COLOR_SURFACE_2))
        actions.add_widget(make_button("Importar", do_import, color=COLOR_ACCENT))
        box.add_widget(actions)
        popup.open()

    def render_analysis(self):
        deck_name = self.deck_spinner.text
        deck = self.app_ref.store.data.get("decks", {}).get(deck_name)
        if not deck:
            self.stat_main_lbl.text = "📦  Mainboard: 0"
            self.stat_side_lbl.text = "🎒  Sideboard: 0"
            self.stat_lands_lbl.text = "🌿  Terrenos: 0"
            self.stat_legal_lbl.text = "⚖️  Regras: -"
            self.mana_curve_container.clear_widgets()
            self.dist_label.text = "Crie ou selecione um deck para visualizar as métricas."
            return

        summary = analyze_deck(deck)
        lands_count = summary["types"].get("Terreno", 0)
        non_lands_count = summary["main_count"] - lands_count

        self.stat_main_lbl.text = f"📦  Main: {summary['main_count']} cartas"
        self.stat_side_lbl.text = f"🎒  Side: {summary['side_count']} cartas"
        self.stat_lands_lbl.text = f"🌿  Terrenos: {lands_count} ({non_lands_count} mágicas)"

        if summary["valid"]:
            self.stat_legal_lbl.text = "⚖️  Regras: Válido ✓"
            self.stat_legal_lbl.color = COLOR_SUCCESS
        else:
            self.stat_legal_lbl.text = f"⚖️  {len(summary['warnings'])} aviso(s) ⚠"
            self.stat_legal_lbl.color = COLOR_WARNING

        self.mana_curve_container.clear_widgets()
        curve = summary["mana_curve"]
        max_cmc = max(max(curve.keys(), default=0), 6)
        max_qty = max(curve.values(), default=1)
        if max_qty == 0:
            max_qty = 1

        for cmc in range(max_cmc + 1):
            qty = curve.get(cmc, 0)
            col = BoxLayout(orientation="vertical", spacing=dp(2))
            col.add_widget(make_label(f"{qty}" if qty > 0 else "0", height=18, font_size=11, color=COLOR_TEXT_PRIMARY if qty > 0 else COLOR_TEXT_MUTED, halign="center"))

            bar_container = BoxLayout(size_hint_y=None, height=dp(60))
            bar_height_ratio = qty / max_qty if max_qty > 0 else 0
            inner_bar = BoxLayout(size_hint_y=max(0.08, bar_height_ratio))
            with inner_bar.canvas.before:
                Color(*(COLOR_ACCENT if qty > 0 else COLOR_SURFACE_2))
                r = RoundedRectangle(size=inner_bar.size, pos=inner_bar.pos, radius=[dp(3)])
                inner_bar.bind(size=lambda inst, v, rect=r: setattr(rect, "size", v), pos=lambda inst, v, rect=r: setattr(rect, "pos", v))
            bar_container.add_widget(inner_bar)
            col.add_widget(bar_container)

            col.add_widget(make_label(f"{cmc}+" if cmc == max_cmc and cmc >= 6 else f"{cmc}", height=18, font_size=11, color=COLOR_TEXT_MUTED, halign="center"))
            self.mana_curve_container.add_widget(col)

        types_text = "   ·   ".join(f"{k}: {v}" for k, v in summary["types"].items()) or "Nenhum"
        colors_readable = []
        for c, qty in summary["colors"].items():
            colors_readable.append(f"{format_card_color(c)}: {qty}")
        colors_text = "   ·   ".join(colors_readable) or "Nenhuma"

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
    """Registro e Edição de Partidas com botões fáceis de tocar."""

    def __init__(self, app_ref, **kwargs):
        super().__init__(app_ref, **kwargs)
        self.edit_index = None
        self.heading("Registro de Partidas", icon="⚔️")

        c1 = create_card_box(padding=dp(12), spacing=dp(6))
        c1.add_widget(make_label("1. Jogadores & Decks", height=22, font_size=13, bold=True, color=COLOR_ACCENT))
        self.deck_spinner = make_spinner("Selecione seu deck", [])
        c1.add_widget(self.deck_spinner)

        self.opponent_name_input = make_input("Nome do jogador adversário (opcional)")
        c1.add_widget(self.opponent_name_input)

        opp_deck_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        self.opponent_deck_spinner = make_spinner("Deck Adversário", [])
        opp_deck_row.add_widget(self.opponent_deck_spinner)
        opp_deck_row.add_widget(make_button("➕  Novo", self.add_opponent_deck_dialog, color=COLOR_SURFACE_2, height=46, font_size=12))
        c1.add_widget(opp_deck_row)
        self.content.add_widget(c1)

        c2 = create_card_box(padding=dp(12), spacing=dp(6))
        c2.add_widget(make_label("2. Resultado & Placar de Games", height=22, font_size=13, bold=True, color=COLOR_ACCENT))

        res_row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        self.btn_win = make_button("🏆  Vitória", partial(self.set_result_quick, "Win"), color=COLOR_SUCCESS, height=38, font_size=13)
        self.btn_loss = make_button("❌  Derrota", partial(self.set_result_quick, "Loss"), color=COLOR_SURFACE_2, height=38, font_size=13)
        res_row.add_widget(self.btn_win)
        res_row.add_widget(self.btn_loss)
        c2.add_widget(res_row)

        self.result_spinner = make_spinner("Win", RESULT_OPTIONS, height=38)
        c2.add_widget(self.result_spinner)

        score_pos_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        self.score_spinner = make_spinner("2 x 0", SCORE_OPTIONS)
        self.score_spinner.bind(text=self.sync_result_from_score)
        self.play_draw_spinner = make_spinner("Você (Play)", PLAY_DRAW_OPTIONS)
        score_pos_row.add_widget(self.score_spinner)
        score_pos_row.add_widget(self.play_draw_spinner)
        c2.add_widget(score_pos_row)

        self.event_spinner = make_spinner("Casual", EVENT_TYPES)
        c2.add_widget(self.event_spinner)
        self.content.add_widget(c2)

        c3 = create_card_box(padding=dp(12), spacing=dp(6))
        c3.add_widget(make_label("3. Data & Anotações de Sideboard", height=22, font_size=13, bold=True, color=COLOR_ACCENT))
        self.date_input = make_input("Data da partida (AAAA-MM-DD)", date.today().isoformat())
        c3.add_widget(self.date_input)

        self.tags_input = make_input("Tags (ex: fnm, mulligan5, side_pesado)")
        c3.add_widget(self.tags_input)

        self.notes_input = make_input("Anotações da partida...", multiline=True, height=72)
        c3.add_widget(self.notes_input)
        self.content.add_widget(c3)

        self.save_button = make_button("💾  Registrar Partida", self.save_match, color=COLOR_ACCENT, height=48, font_size=14)
        self.content.add_widget(self.save_button)

        self.cancel_button = make_button("Cancelar Edição", self.cancel_editing, color=COLOR_SURFACE_2, height=40)

    def set_result_quick(self, res_value, *_):
        self.result_spinner.text = res_value
        if res_value == "Win":
            self.score_spinner.text = "2 x 0"
            self.btn_win.canvas.before.clear()
            with self.btn_win.canvas.before:
                Color(*COLOR_SUCCESS)
                RoundedRectangle(size=self.btn_win.size, pos=self.btn_win.pos, radius=[dp(8)])
            self.btn_loss.canvas.before.clear()
            with self.btn_loss.canvas.before:
                Color(*COLOR_SURFACE_2)
                RoundedRectangle(size=self.btn_loss.size, pos=self.btn_loss.pos, radius=[dp(8)])
        else:
            self.score_spinner.text = "0 x 2"
            self.btn_win.canvas.before.clear()
            with self.btn_win.canvas.before:
                Color(*COLOR_SURFACE_2)
                RoundedRectangle(size=self.btn_win.size, pos=self.btn_win.pos, radius=[dp(8)])
            self.btn_loss.canvas.before.clear()
            with self.btn_loss.canvas.before:
                Color(*COLOR_DANGER)
                RoundedRectangle(size=self.btn_loss.size, pos=self.btn_loss.pos, radius=[dp(8)])

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

        actions.add_widget(make_button("Cancelar", popup.dismiss, color=COLOR_SURFACE_2))
        actions.add_widget(make_button("Adicionar", add_opp, color=COLOR_ACCENT))
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

        self.save_button.text = "💾  Salvar Alterações da Partida"
        if self.cancel_button not in self.content.children:
            self.content.add_widget(self.cancel_button)
        self.app_ref.show_screen("matches")

    def cancel_editing(self, *_):
        self.edit_index = None
        self.save_button.text = "💾  Registrar Partida"
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
# TELA 6: RESULTADOS & ESTATÍSTICAS (DASHBOARD COMPLETO)
# ==============================================================================
class ResultsScreen(ManaVaultScreen):
    """Resultados, Dashboard de Win Rate, Play vs Draw, Matchups e Histórico."""

    def __init__(self, app_ref, **kwargs):
        super().__init__(app_ref, **kwargs)
        self.heading("Resultados & Estatísticas", icon="🏆")

        filter_card = create_card_box(padding=dp(10), spacing=dp(6))
        filter_card.add_widget(make_label("Filtros do Histórico:", height=18, font_size=12, color=COLOR_TEXT_MUTED))

        f1 = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(6))
        self.deck_filter = make_spinner("Todos os decks", ["Todos os decks"])
        self.opponent_filter = make_spinner("Todos os oponentes", ["Todos os oponentes"])
        f1.add_widget(self.deck_filter)
        f1.add_widget(self.opponent_filter)
        filter_card.add_widget(f1)

        f2 = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(6))
        self.event_filter = make_spinner("Todos os eventos", ["Todos os eventos"] + EVENT_TYPES)
        self.position_filter = make_spinner("Todos", ["Todos"] + PLAY_DRAW_OPTIONS)
        f2.add_widget(self.event_filter)
        f2.add_widget(self.position_filter)
        filter_card.add_widget(f2)

        filter_btn_row = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(6))
        filter_btn_row.add_widget(make_button("Filtrar", self.refresh_results, color=COLOR_ACCENT, height=34, font_size=12))
        filter_btn_row.add_widget(make_button("Limpar", self.reset_filters, color=COLOR_SURFACE_2, height=34, font_size=12))
        filter_card.add_widget(filter_btn_row)
        self.content.add_widget(filter_card)

        self.stats_card = create_card_box(padding=dp(12), spacing=dp(8))
        self.stats_summary_label = Label(
            text="",
            size_hint_y=None,
            font_size="13sp",
            color=COLOR_TEXT_PRIMARY,
            halign="left",
            valign="top",
        )
        self.stats_summary_label.bind(width=lambda inst, val: setattr(inst, "text_size", (val, None)))
        self.stats_summary_label.bind(texture_size=lambda inst, val: setattr(inst, "height", max(dp(90), val[1] + dp(12))))
        self.stats_card.add_widget(self.stats_summary_label)
        self.content.add_widget(self.stats_card)

        backup_card = create_card_box(padding=dp(10), spacing=dp(6))
        backup_card.add_widget(make_label("💾  Backup de Dados (JSON)", height=20, font_size=12, bold=True, color=COLOR_ACCENT))
        b_row = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(6))
        b_row.add_widget(make_button("Importar Backup", self.app_ref.import_backup, color=COLOR_SURFACE_2, height=36, font_size=12))
        b_row.add_widget(make_button("Exportar Backup", self.app_ref.export_backup, color=COLOR_ACCENT, height=36, font_size=12))
        backup_card.add_widget(b_row)
        self.content.add_widget(backup_card)

        self.add_section_header("📜", "Histórico de Partidas Recentes")
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
        actions.add_widget(make_button("Cancelar", popup.dismiss, color=COLOR_SURFACE_2))
        actions.add_widget(make_button("Excluir", partial(self.delete_match, original_index, popup), color=COLOR_DANGER))
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
            f"Placar: {match.get('games_score', 'N/D')}   ·   Posição: {match.get('play_draw', 'N/D')}\n"
            f"Tags: {tags_str}\n\n"
            f"Anotações:\n{match.get('notes', 'Nenhuma anotação registrada.')}"
        )
        scroll = ScrollView(do_scroll_x=False)
        lbl = Label(text=text, size_hint_y=None, font_size="13sp", color=COLOR_TEXT_PRIMARY, halign="left", valign="top")
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

        actions.add_widget(make_button("Fechar", popup.dismiss, color=COLOR_SURFACE_2))
        actions.add_widget(make_button("Editar", edit_from_dialog, color=COLOR_ACCENT))
        actions.add_widget(make_button("Excluir", del_from_dialog, color=COLOR_DANGER))
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
            empty_box = create_card_box(padding=dp(16))
            empty_box.add_widget(make_label("Nenhuma partida encontrada com os filtros selecionados.", height=36, color=COLOR_TEXT_MUTED, halign="center"))
            self.matches_list.add_widget(empty_box)
            return

        for original_idx, match in reversed(filtered_indexed[-100:]):
            res = match.get("result", "Win")
            res_color = COLOR_SUCCESS if res == "Win" else COLOR_DANGER if res == "Loss" else COLOR_WARNING
            res_label = "VITÓRIA" if res == "Win" else "DERROTA" if res == "Loss" else "EMPATE"

            opp = f"{match.get('opponent_deck', 'Desconhecido')}"
            if match.get("opponent_name"):
                opp += f" ({match.get('opponent_name')})"

            card_box = create_card_box(padding=dp(10), spacing=dp(3))

            t_row = BoxLayout(size_hint_y=None, height=dp(24))
            t_row.add_widget(make_label(f"{match.get('deck', '')} vs {opp}", height=22, font_size=14, bold=True))
            badge = make_label(f"[{res_label}]", height=22, font_size=12, bold=True, color=res_color, halign="right")
            t_row.add_widget(badge)
            card_box.add_widget(t_row)

            sub_text = f"Placar: {match.get('games_score', 'N/D')}   ·   {match.get('play_draw', 'N/D')}   ·   {match.get('match_date', '')}"
            card_box.add_widget(make_label(sub_text, height=20, font_size=12, color=COLOR_TEXT_MUTED))

            btn_row = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(6))
            btn_row.add_widget(make_button("Ver Detalhes", partial(self.show_match_details_dialog, original_idx, match), color=COLOR_SURFACE_2, height=28, font_size=11))
            btn_row.add_widget(make_button("Editar", lambda *_, i=original_idx, m=match: self.app_ref.screens["matches"].load_match_for_edit(m, i), color=COLOR_SURFACE_2, height=28, font_size=11))
            btn_row.add_widget(make_button("Excluir", partial(self.confirm_delete_match, original_idx, match), color=COLOR_DANGER, height=28, font_size=11))
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
    """Consulta de 26 Regras de Combate MTG com visual moderno."""

    def __init__(self, app_ref, **kwargs):
        super().__init__(app_ref, **kwargs)
        self.heading("Consulta de Efeitos", icon="📖")

        search_card = create_card_box(padding=dp(10), spacing=dp(6))
        search_card.add_widget(make_label("Buscar Regra ou Efeito de Combate:", height=20, font_size=12, color=COLOR_TEXT_MUTED))
        self.search_input = make_input("Ex: Atropelar, Golpe Duplo, Iniciativa, Voar...")
        self.search_input.bind(text=lambda *_: self.refresh())
        search_card.add_widget(self.search_input)
        self.content.add_widget(search_card)

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
            card_box = create_card_box(padding=dp(12), spacing=dp(4))

            title = f"{name} ({english})" if english else name
            card_box.add_widget(make_label(title, height=26, font_size=15, bold=True, color=COLOR_ACCENT))

            sum_lbl = Label(text=summary, size_hint_y=None, font_size="13sp", color=COLOR_TEXT_PRIMARY, halign="left", valign="top")
            sum_lbl.bind(width=lambda inst, val: setattr(inst, "text_size", (val, None)))
            sum_lbl.bind(texture_size=lambda inst, val: setattr(inst, "height", max(dp(26), val[1] + dp(4))))
            card_box.add_widget(sum_lbl)

            for heading, description in sections:
                h_norm = normalize_keyword_search(heading)
                h_color = COLOR_WARNING if "bloqueador" in h_norm else COLOR_ACCENT if "atacar" in h_norm else COLOR_SUCCESS if "bloquear" in h_norm else COLOR_TEXT_MUTED
                card_box.add_widget(make_label(f"▶  {heading}", height=22, font_size=12, bold=True, color=h_color))

                desc_lbl = Label(text=description, size_hint_y=None, font_size="12sp", color=COLOR_TEXT_MUTED, halign="left", valign="top")
                desc_lbl.bind(width=lambda inst, val: setattr(inst, "text_size", (val, None)))
                desc_lbl.bind(texture_size=lambda inst, val: setattr(inst, "height", max(dp(22), val[1] + dp(4))))
                card_box.add_widget(desc_lbl)

            self.rules_list.add_widget(card_box)

        if count == 0:
            empty_box = create_card_box(padding=dp(16))
            empty_box.add_widget(make_label("Nenhum efeito encontrado com a busca.", height=36, color=COLOR_TEXT_MUTED, halign="center"))
            self.rules_list.add_widget(empty_box)


# ==============================================================================
# MAIN APP CLASS
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
        Window.clearcolor = COLOR_BG
        self.store = DataStore(Path(self.user_data_dir) / "magic_data.json")
        self.images_dir = Path(self.user_data_dir) / "card_images"
        self.images_dir.mkdir(parents=True, exist_ok=True)

        self.storage_blocked = False
        self.load_error = ""

        try:
            self.store.load()
        except ValueError as exc:
            self.storage_blocked = True
            self.load_error = str(exc)

        root = BoxLayout(orientation="vertical", spacing=dp(4), padding=[dp(6), dp(4)])

        top_bar = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8), padding=[dp(6), dp(2)])
        with top_bar.canvas.before:
            Color(*COLOR_SURFACE_1)
            r = RoundedRectangle(size=top_bar.size, pos=top_bar.pos, radius=[dp(8)])
            top_bar.bind(size=lambda inst, v, rect=r: setattr(rect, "size", v), pos=lambda inst, v, rect=r: setattr(rect, "pos", v))

        self.header_title = make_label("ManaVault  ·  Decks", height=42, font_size=15, bold=True, color=COLOR_ACCENT)
        top_bar.add_widget(self.header_title)

        self.menu_button = make_button("☰  Menu", self.open_navigation_menu, color=COLOR_SURFACE_2, text_color=COLOR_TEXT_PRIMARY, height=38, font_size=13)
        self.menu_button.size_hint_x = None
        self.menu_button.width = dp(94)
        top_bar.add_widget(self.menu_button)
        root.add_widget(top_bar)

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

        self.status_label = make_label("Offline  ·  Armazenamento Local Protegido", height=18, font_size=10, color=COLOR_TEXT_FAINT, halign="center")
        root.add_widget(self.status_label)

        self.show_screen("decks")
        self.refresh_all()

        if self.load_error:
            self.set_status(f"{self.load_error}")

        return root

    def open_navigation_menu(self, *_):
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=[dp(12), dp(12)])
        scroll = ScrollView(do_scroll_x=False)
        items_layout = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(6))
        items_layout.bind(minimum_height=items_layout.setter("height"))

        menu_items = [
            ("decks", "🗂️  1. Gerenciar Decks"),
            ("deck_cards", "🃏  2. Cartas do Deck (Lista & Fotos)"),
            ("add_cards", "➕  3. Adicionar Cartas & Fotos"),
            ("analysis", "📊  4. Análise do Deck (Curva)"),
            ("matches", "⚔️  5. Registro de Partidas"),
            ("results", "🏆  6. Resultados & Estatísticas"),
            ("rules", "📖  7. Consulta de Efeitos MTG"),
        ]

        popup = Popup(
            title="Navegação ManaVault",
            size_hint=(0.88, None),
            height=dp(430),
            auto_dismiss=True,
        )

        for screen_name, title in menu_items:
            is_active = self.manager.current == screen_name
            btn = make_button(
                title,
                partial(self._navigate_from_menu, screen_name, popup),
                color=COLOR_ACCENT if is_active else COLOR_SURFACE_2,
                height=44,
                font_size=13,
            )
            items_layout.add_widget(btn)

        scroll.add_widget(items_layout)
        box.add_widget(scroll)

        close_btn = make_button("✖  Fechar Menu", popup.dismiss, color=COLOR_SURFACE_3, height=38, font_size=12)
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
            self.set_status("A seleção nativa de arquivos está disponível no APK Android.")
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

    def on_start(self):
        self.request_android_permissions()

    def request_android_permissions(self):
        if platform == "android":
            try:
                from android.permissions import Permission, request_permissions
                request_permissions([
                    Permission.CAMERA,
                    Permission.READ_EXTERNAL_STORAGE,
                    Permission.WRITE_EXTERNAL_STORAGE,
                ])
            except Exception:
                pass

    def get_gemini_key(self):
        k = str(self.store.data.get("gemini_api_key", "")).strip()
        return k or DEFAULT_GEMINI_API_KEY

    def set_gemini_key(self, key):
        self.store.data["gemini_api_key"] = str(key or "").strip()
        self.store.save()


if __name__ == "__main__":
    ManaVaultApp().run()
