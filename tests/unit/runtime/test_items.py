from heydesk.runtime.items import render_items
from tests.conftest import MENU


def test_a_short_list_goes_into_the_prompt_whole() -> None:
    text = render_items(MENU, "Menu", "BDT", max_in_prompt=60)

    assert text.startswith("Menu. Each line: name [code] price (tags): description\nRice:\n")
    assert (
        "- Chicken Biryani [chk-biryani] ৳450 (popular, spicy): "
        "Basmati rice slow-cooked with chicken, potato and egg." in text
    )
    assert "- Mango Lassi [mango-lassi] ৳150 (vegetarian) NOT AVAILABLE" in text
    assert text.index("Rice:") < text.index("Curry:") < text.index("Drinks:")


def test_a_long_list_points_to_search() -> None:
    text = render_items(MENU, "Menu", "BDT", max_in_prompt=5)

    assert text == (
        "Menu: 10 items, too many to list here. Use search_items to find them.\n"
        "Categories: Rice, Curry, Breads, Drinks, Desserts"
    )
