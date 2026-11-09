from app.profiles import capabilities


def test_samsung_profile():
    caps = capabilities("samsung")
    assert caps["profile"] == "samsung_one_ui"
    assert caps["samsung_good_lock"] is True
    assert caps["static_wallpaper_one_click"] is False


def test_generic_profile():
    caps = capabilities("Google")
    assert caps["profile"] == "generic_android"
    assert caps["samsung_good_lock"] is False
