from set_business_details import ask_lines, is_colour, is_ref_letters


def test_is_ref_letters():
    assert is_ref_letters("DPL") is True
    assert is_ref_letters("A") is False
    assert is_ref_letters("APTPH") is False
    assert is_ref_letters("A1") is False
    assert is_ref_letters("apt") is False


def test_ask_lines_stops_at_an_empty_line(monkeypatch):
    answers = iter(["1 Test Street", " Liverpool ", "", "never read"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    assert ask_lines("Address") == "1 Test Street\nLiverpool"


def test_is_colour():
    assert is_colour("#b8975a") is True
    assert is_colour("#B8975A") is True
    assert is_colour("b8975a") is False
    assert is_colour("#b8975") is False
    assert is_colour("#gggggg") is False
