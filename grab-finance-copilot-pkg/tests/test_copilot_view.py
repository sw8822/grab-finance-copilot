"""GrabFi Copilot rendering tests."""

from views.copilot_view import _escape_currency_markdown


def test_currency_dollars_do_not_open_math_mode():
    answer = (
        "**Profit for the Year** was $200 million, an improvement of "
        "**$358 million** from -$158 million."
    )

    rendered = _escape_currency_markdown(answer)

    assert r"\$200 million" in rendered
    assert r"**\$358 million**" in rendered
    assert r"-\$158 million" in rendered
    assert "**Profit for the Year**" in rendered


def test_currency_escaping_is_idempotent():
    answer = r"Revenue was \$3,370 million."

    assert _escape_currency_markdown(answer) == answer
