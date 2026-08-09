from mtglogger.services.deck_builder import BuildCandidate, build_deck


def card(
    index:int,
    name:str,
    *,
    available:int=4,
    type_line:str="Creature — Wizard",
    oracle_text:str="",
    colors:str="U",
    mana_value:float=2,
    legalities:dict[str,str]|None=None,
):
    return BuildCandidate(
        inventory_id=f"inventory-{index}",scryfall_id=f"printing-{index}",name=name,
        set_code="tst",collector_number=str(index),image_url=None,available=available,
        type_line=type_line,oracle_text=oracle_text,color_identity=colors,mana_cost="",
        mana_value=mana_value,rarity="common",legalities=legalities or {"modern":"legal","commander":"legal"},keywords=[],
    )


def test_constructed_builder_obeys_colors_legality_copies_and_physical_supply():
    pool=[]
    for index in range(12):
        pool.append(card(index,f"Blue threat {index}",available=2,oracle_text="Draw a card."))
    pool.append(card(20,"Illegal card",colors="U",legalities={"modern":"not_legal"}))
    pool.append(card(21,"Red card",colors="R"))
    pool.append(card(22,"Island",available=30,type_line="Basic Land — Island",colors="",mana_value=0))

    proposal=build_deck(pool,"Modern",["U"],"balanced","Blue collection deck")

    names={entry["name"] for entry in proposal["cards"]}
    assert "Illegal card" not in names
    assert "Red card" not in names
    assert sum(entry["quantity"] for entry in proposal["cards"] if entry["name"]=="Island")<=30
    assert all(entry["quantity"]<=2 for entry in proposal["cards"] if entry["role"]!="land")
    assert proposal["warnings"]


def test_commander_builder_selects_a_matching_leader_and_singletons():
    pool=[card(1,"Talrand",available=2,type_line="Legendary Creature — Merfolk Wizard",oracle_text="Whenever you cast an instant or sorcery spell, create a token.",mana_value=4)]
    pool.extend(card(index,f"Spell {index}",available=3,type_line="Instant",oracle_text="Draw a card. Create a token.") for index in range(2,75))
    pool.append(card(100,"Island",available=40,type_line="Basic Land — Island",colors="",mana_value=0))

    proposal=build_deck(pool,"Commander",["U"],"spells","Talrand auto deck")

    leader=next(entry for entry in proposal["cards"] if entry["role"]=="leader")
    assert leader["name"]=="Talrand"
    assert leader["quantity"]==1
    assert all(entry["quantity"]==1 for entry in proposal["cards"] if entry["role"]!="land")
    assert proposal["theme"]=="spells"
    assert proposal["complete"] is True
    assert proposal["total_cards"]==100


def test_builder_reports_missing_lands_instead_of_inventing_cards():
    pool=[card(index,f"Creature {index}") for index in range(20)]
    proposal=build_deck(pool,"Modern",["U"],"aggro")
    assert proposal["complete"] is False
    assert proposal["land_count"]==0
    assert any("lands" in warning for warning in proposal["warnings"])
