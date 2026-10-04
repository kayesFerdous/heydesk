from heydesk.runtime.items import Item, render_items
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


def test_attributes_are_shown_on_the_line_and_explained_once() -> None:
    doctor = Item(
        code="dr-akter",
        name="Dr. Akter",
        price=80000,
        category="Cardiology",
        attributes={"days": ["sat", "sun"], "languages": ["english", "bangla"], "years": 12},
    )

    text = render_items([doctor], "Doctors", "BDT", max_in_prompt=60)

    assert text.startswith(
        "Doctors. Each line: name [code] price (tags): description {extra facts}"
    )
    assert (
        "- Dr. Akter [dr-akter] ৳800 {days: sat, sun, languages: english, bangla, years: 12}"
        in text
    )
    assert "{extra facts}" not in render_items(MENU, "Menu", "BDT", max_in_prompt=60)
