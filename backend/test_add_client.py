from add_client import is_uk_mobile


def test_is_uk_mobile():
    assert is_uk_mobile("+447700900123") is True
    assert is_uk_mobile("07700900123") is False
    assert is_uk_mobile("+441514960000") is False
    assert is_uk_mobile("+44770090012") is False
    assert is_uk_mobile("+4477009001ab") is False
