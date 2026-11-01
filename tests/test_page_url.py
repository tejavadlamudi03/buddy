import os
from pathlib import Path

import buddy.__main__ as main

PAGE = Path(main.__file__).parent / "static/index.html"
SETTINGS = {"port": 8765, "token": "secret"}


def test_the_token_stays_out_of_what_the_server_sees():
    address, _, fragment = main._page_url(SETTINGS).partition("#")
    assert address.startswith("http://localhost:8765/?v=")
    assert "secret" not in address
    assert fragment == "token=secret"


def test_a_changed_page_gets_a_new_address(monkeypatch):
    class Changed(type(PAGE)):
        def stat(self, **kwargs):
            real = os.stat(PAGE)
            return os.stat_result((*real[:8], real.st_mtime + 60, real.st_ctime))

    before = main._page_url(SETTINGS)
    monkeypatch.setattr(main, "Path", Changed)
    assert main._page_url(SETTINGS) != before
