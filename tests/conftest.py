from __future__ import annotations

from pathlib import Path

import pytest

from stardict_writer import write_dictionary

FRUIT = {
    "apple": "A round fruit.",
    "Apple": "A technology company.",
    "apples": "More than one apple.",
    "apricot": "An orange fruit.",
    "banana": "A long yellow fruit.",
    "cherry": "A small red fruit.",
}

ANIMALS = {
    "aardvark": "<k>aardvark</k>\nAn <abr>Afr.</abr> burrowing <i>mammal</i>.",
    "apple": "<k>apple</k>\nNot an animal.",
    "badger": "<k>badger</k>\nA <c c=\"red\">nocturnal</c> mammal.",
}


@pytest.fixture
def fruit_ifo(tmp_path: Path) -> Path:
    return write_dictionary(tmp_path / "dic" / "fruit", "fruit", FRUIT, bookname="Fruit",
                            synonyms={"pomme": "apple"})


@pytest.fixture
def animals_ifo(tmp_path: Path) -> Path:
    return write_dictionary(tmp_path / "dic" / "animals", "animals", ANIMALS,
                            bookname="Animals", sametypesequence="x", dictzip=True, chunk_len=16)
