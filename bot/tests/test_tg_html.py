"""_tg_html: Telegram HTML rendering, incl. Markdown masked links -> <a> tags.

Profile-link / metric suffixes are emitted as Markdown '[label](url)' (Discord renders these
natively). Telegram uses HTML parse mode, so _tg_html must convert them to <a href> or the
user sees the raw '[label](url)' with the URL exposed.
"""
from bot.connectors.telegram_connector import _tg_html


def test_markdown_link_becomes_anchor():
    out = _tg_html("🎓 [Google Scholar](https://scholar.google.com/citations?user=ONXC8_gAAAAJ)")
    assert '<a href="https://scholar.google.com/citations?user=ONXC8_gAAAAJ">Google Scholar</a>' in out
    assert "[Google Scholar]" not in out


def test_link_url_with_ampersand_is_escaped_in_href():
    out = _tg_html("[X](https://x.njit.edu/a?b=1&c=2)")
    assert '<a href="https://x.njit.edu/a?b=1&amp;c=2">X</a>' in out


def test_underscore_in_url_not_italicized():
    out = _tg_html("[GH](https://github.com/some_user_name)")
    assert '<a href="https://github.com/some_user_name">GH</a>' in out
    assert "<i>" not in out


def test_multiple_links_separated_by_middot():
    out = _tg_html("🎓 [Scholar](https://a.com) · 💻 [GitHub](https://b.com)")
    assert '<a href="https://a.com">Scholar</a>' in out
    assert '<a href="https://b.com">GitHub</a>' in out


def test_bold_and_italic_still_work():
    assert "<b>hi</b>" in _tg_html("**hi**")
    assert "<i>yo</i>" in _tg_html("_yo_")
