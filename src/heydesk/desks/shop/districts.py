"""Bangladesh's 64 districts, so Python, not the model, says which delivery area an address is
in (D98). The model names the district from the address ("Mirpur 10" → Dhaka), and an owner
names an area's districts; `district()` checks each is one of these, spelled as here."""

import unicodedata

# Each district's English name, as saved, and its Bangla one
DISTRICTS = {
    "Bagerhat": "বাগেরহাট",
    "Bandarban": "বান্দরবান",
    "Barguna": "বরগুনা",
    "Barishal": "বরিশাল",
    "Bhola": "ভোলা",
    "Bogura": "বগুড়া",
    "Brahmanbaria": "ব্রাহ্মণবাড়িয়া",
    "Chandpur": "চাঁদপুর",
    "Chapainawabganj": "চাঁপাইনবাবগঞ্জ",
    "Chattogram": "চট্টগ্রাম",
    "Chuadanga": "চুয়াডাঙ্গা",
    "Cox's Bazar": "কক্সবাজার",
    "Cumilla": "কুমিল্লা",
    "Dhaka": "ঢাকা",
    "Dinajpur": "দিনাজপুর",
    "Faridpur": "ফরিদপুর",
    "Feni": "ফেনী",
    "Gaibandha": "গাইবান্ধা",
    "Gazipur": "গাজীপুর",
    "Gopalganj": "গোপালগঞ্জ",
    "Habiganj": "হবিগঞ্জ",
    "Jamalpur": "জামালপুর",
    "Jashore": "যশোর",
    "Jhalokati": "ঝালকাঠি",
    "Jhenaidah": "ঝিনাইদহ",
    "Joypurhat": "জয়পুরহাট",
    "Khagrachhari": "খাগড়াছড়ি",
    "Khulna": "খুলনা",
    "Kishoreganj": "কিশোরগঞ্জ",
    "Kurigram": "কুড়িগ্রাম",
    "Kushtia": "কুষ্টিয়া",
    "Lakshmipur": "লক্ষ্মীপুর",
    "Lalmonirhat": "লালমনিরহাট",
    "Madaripur": "মাদারীপুর",
    "Magura": "মাগুরা",
    "Manikganj": "মানিকগঞ্জ",
    "Meherpur": "মেহেরপুর",
    "Moulvibazar": "মৌলভীবাজার",
    "Munshiganj": "মুন্সীগঞ্জ",
    "Mymensingh": "ময়মনসিংহ",
    "Naogaon": "নওগাঁ",
    "Narail": "নড়াইল",
    "Narayanganj": "নারায়ণগঞ্জ",
    "Narsingdi": "নরসিংদী",
    "Natore": "নাটোর",
    "Netrokona": "নেত্রকোণা",
    "Nilphamari": "নীলফামারী",
    "Noakhali": "নোয়াখালী",
    "Pabna": "পাবনা",
    "Panchagarh": "পঞ্চগড়",
    "Patuakhali": "পটুয়াখালী",
    "Pirojpur": "পিরোজপুর",
    "Rajbari": "রাজবাড়ী",
    "Rajshahi": "রাজশাহী",
    "Rangamati": "রাঙ্গামাটি",
    "Rangpur": "রংপুর",
    "Satkhira": "সাতক্ষীরা",
    "Shariatpur": "শরীয়তপুর",
    "Sherpur": "শেরপুর",
    "Sirajganj": "সিরাজগঞ্জ",
    "Sunamganj": "সুনামগঞ্জ",
    "Sylhet": "সিলেট",
    "Tangail": "টাঙ্গাইল",
    "Thakurgaon": "ঠাকুরগাঁও",
}

# The old English spellings, and how buyers often write them
OTHER_NAMES = {
    "barisal": "Barishal",
    "bogra": "Bogura",
    "chapai nawabganj": "Chapainawabganj",
    "chittagong": "Chattogram",
    "ctg": "Chattogram",
    "comilla": "Cumilla",
    "jessore": "Jashore",
    "jhalakathi": "Jhalokati",
    "jhalakati": "Jhalokati",
    "khagrachari": "Khagrachhari",
    "laxmipur": "Lakshmipur",
    "maulvibazar": "Moulvibazar",
    "netrakona": "Netrokona",
    "narshingdi": "Narsingdi",
}


def _key(name: str) -> str:
    """Lower case, no apostrophe or "district"; Bangla in one Unicode form (ড় is typed two
    ways)."""
    text = unicodedata.normalize("NFC", name.lower().replace("'", ""))
    words = text.replace("-", " ").replace(",", " ").split()
    return " ".join(w for w in words if w not in ("district", "zila", "zilla", "jela", "জেলা"))


_BY_KEY = (
    {_key(name): name for name in DISTRICTS}
    | {_key(bangla): name for name, bangla in DISTRICTS.items()}
    | OTHER_NAMES
)


def district(said: str) -> str:
    """The district `said` names, in English or Bangla, spelled as in `DISTRICTS`. Raises
    ValueError."""
    found = _BY_KEY.get(_key(said))
    if found is None:
        raise ValueError(f"{said.strip()!r} isn't a district of Bangladesh.")
    return found
