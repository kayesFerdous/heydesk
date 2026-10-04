from heydesk.runtime.text import chunk_text, words


def test_words_are_lowercase_in_any_script_and_marks_stay_with_their_letters() -> None:
    assert words("Chicken Biryanis, ৳450!") == ["chicken", "biryani", "450"]  # ৳ is a symbol
    assert words("Dr. Hasan: fee 900") == ["dr", "hasan", "fee", "900"]
    # Python's \w would split this at each vowel sign
    assert words("আমাদের ক্লিনিক রাত ১০টা পর্যন্ত খোলা") == [
        "আমাদের",
        "ক্লিনিক",
        "রাত",
        "১০টা",
        "পর্যন্ত",
        "খোলা",
    ]


def test_a_plural_finds_its_singular() -> None:
    assert words("biryanis") == words("biryani") == ["biryani"]
    assert words("gas") == ["gas"]  # short words are left alone


def test_a_short_text_is_one_chunk() -> None:
    assert chunk_text("We open at 9.\n\nWe close at 10.") == ["We open at 9.\n\nWe close at 10."]
    assert chunk_text("  \n\n ") == []


def test_chunks_break_at_paragraphs_and_share_a_tail() -> None:
    paragraphs = [f"Paragraph {n}. " + "word " * 30 for n in range(6)]  # about 160 characters each

    chunks = chunk_text("\n\n".join(paragraphs), size=400, overlap=60)

    assert len(chunks) > 1 and all(len(c) <= 400 + 60 + 2 for c in chunks)
    joined = "\n\n".join(chunks)
    assert all(f"Paragraph {n}." in joined for n in range(6))  # nothing lost
    first_tail = chunks[0][-30:].split()[-1]
    assert first_tail in chunks[1]  # the next chunk starts with the end of this one


def test_a_heading_stays_with_the_paragraph_below_it() -> None:
    text = "# Opening hours\n\nSat to Thu, 9 am to 9 pm.\n\n# Fees\n\nGeneral visit: 400 taka."

    chunks = chunk_text(text, size=60, overlap=0)

    assert chunks == [
        "# Opening hours\nSat to Thu, 9 am to 9 pm.",
        "# Fees\nGeneral visit: 400 taka.",
    ]


def test_a_long_paragraph_is_cut_at_sentences_and_a_huge_word_run_at_words() -> None:
    sentences = " ".join(f"Sentence number {n} ends here." for n in range(40))
    chunks = chunk_text(sentences, size=200, overlap=0)
    assert len(chunks) > 3 and all(c.endswith("here.") for c in chunks)  # cut at full stops

    run = chunk_text("word " * 500, size=200, overlap=0)
    assert all(len(c) <= 200 for c in run)
    bangla = chunk_text("আমরা খুলি সকাল নয়টায়। বন্ধ করি রাত দশটায়।", size=30, overlap=0)
    assert bangla == ["আমরা খুলি সকাল নয়টায়।", "বন্ধ করি রাত দশটায়।"]  # the danda ends a sentence


def test_each_heading_starts_its_own_chunk_even_when_both_would_fit_in_one() -> None:
    text = "# Hours\n\nOpen nine to ten.\n\nClosed Fridays.\n\n# Fees\n\nA visit is 400 taka."

    chunks = chunk_text(text, size=1200, overlap=150)

    assert chunks == [
        "# Hours\nOpen nine to ten.\n\nClosed Fridays.",
        "# Fees\nA visit is 400 taka.",
    ]
    assert chunk_text("#1 priority is you.\n\nSecond line.") == [
        "#1 priority is you.\n\nSecond line."
    ]
