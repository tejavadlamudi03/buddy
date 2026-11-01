from buddy.apps import App
from buddy.ocr import Item, find
from pathlib import Path

ITEMS = [Item("File", 10, 10), Item("Save As", 50, 10), Item("Save", 90, 10), Item("Cancel", 500, 500)]


def test_exact_match_beats_partial():
    assert find(ITEMS, "save").x == 90


def test_whole_words_and_case():
    assert find(ITEMS, "CANCEL").x == 500
    assert find(ITEMS, "as").x == 50
    assert find(ITEMS, "ave") is None


def test_close_spelling_for_long_words_only():
    assert find([Item("Bluetooth", 1, 1)], "blue tooth").x == 1
    assert find([Item("File", 1, 1)], "fill") is None


def test_symbols_can_be_found():
    assert find([Item("7", 1, 1), Item("+", 2, 2), Item("=", 3, 3)], "+").x == 2
    assert find([Item("7", 1, 1), Item("=", 3, 3)], "=").x == 3


def test_windows_match_their_own_app_only():
    writer = App("LibreOffice Writer", Path("libreoffice-writer.desktop"), "libreoffice-writer", "libreoffice")
    calc = App("LibreOffice Calc", Path("libreoffice-calc.desktop"), "libreoffice-calc", "libreoffice")
    window = "libreoffice libreoffice-writer"
    assert writer.owns(window) and not calc.owns(window)
    viewer = App("Document Viewer", Path("org.gnome.Evince.desktop"), "", "evince")
    assert viewer.owns("evince Evince")
    fonts = App("Fonts", Path("org.gnome.font-viewer.desktop"), "", "gnome-font-viewer")
    assert fonts.owns("gnome-font-viewer gnome-font-viewer")
