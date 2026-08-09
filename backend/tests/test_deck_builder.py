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
    mana_cost:str="{U}",
    set_name:str="Test Set",
    set_code:str="tst",
    oracle_id:str|None=None,
):
    return BuildCandidate(
        inventory_id=f"inventory-{index}",scryfall_id=f"printing-{index}",name=name,
        set_code=set_code,set_name=set_name,collector_number=str(index),image_url=None,available=available,
        type_line=type_line,oracle_text=oracle_text,color_identity=colors,mana_cost=mana_cost,
        mana_value=mana_value,rarity="common",legalities=legalities or {"modern":"legal","commander":"legal"},keywords=[],
        oracle_id=oracle_id or f"oracle-{index}",
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


def test_strict_collection_focus_only_uses_matching_sets():
    pool=[card(index,f"Avatar card {index}",set_name="Avatar: The Last Airbender",set_code="tla") for index in range(20)]
    pool.extend(card(100+index,f"Other card {index}",set_name="Unrelated Set",set_code="oth") for index in range(20))
    pool.append(card(200,"Avatar Island",available=30,type_line="Basic Land — Island",colors="",mana_value=0,mana_cost="",set_name="Avatar: The Last Airbender",set_code="tla"))

    proposal=build_deck(pool,"Modern",["U"],"balanced",focus="Avatar",focus_mode="strict")

    assert proposal["matched_sets"]==["Avatar: The Last Airbender (TLA)"]
    assert proposal["cards"]
    assert all("Avatar" in entry["set_name"] for entry in proposal["cards"])


def test_preferred_collection_focus_selects_matches_before_equal_outsiders():
    pool=[]
    for index in range(9):
        pool.append(card(index,f"Avatar spell {index}",available=4,set_name="Avatar: The Last Airbender",set_code="tla"))
        pool.append(card(50+index,f"Other spell {index}",available=4,set_name="Other Set",set_code="oth"))
    pool.append(card(200,"Island",available=30,type_line="Basic Land — Island",colors="",mana_value=0,mana_cost=""))

    proposal=build_deck(pool,"Modern",["U"],"balanced",focus="Avatar",focus_mode="prefer")
    focused=sum(entry["quantity"] for entry in proposal["cards"] if entry["set_code"]=="tla")
    outsiders=sum(entry["quantity"] for entry in proposal["cards"] if entry["set_code"]=="oth")

    assert focused>outsiders


def test_copy_limit_is_shared_across_multiple_printings_of_same_card():
    pool=[card(1,"Shared Spell",available=4,oracle_id="same-oracle"),card(2,"Shared Spell",available=4,oracle_id="same-oracle")]
    pool.extend(card(index,f"Unique spell {index}") for index in range(3,18))
    pool.append(card(100,"Island",available=30,type_line="Basic Land — Island",colors="",mana_value=0,mana_cost=""))

    proposal=build_deck(pool,"Modern",["U"])

    assert sum(entry["quantity"] for entry in proposal["cards"] if entry["name"]=="Shared Spell")<=4


def test_synergy_benchmark_rewards_coherent_card_packages():
    coherent=[]
    disconnected=[]
    for index in range(16):
        coherent.append(card(index,f"Token engine {index}",oracle_text="Create a 1/1 creature token. Whenever a creature token enters, draw a card."))
        disconnected.append(card(100+index,f"Unrelated card {index}",oracle_text="Scry 1."))
    coherent.append(card(50,"Island",available=30,type_line="Basic Land — Island",colors="",mana_value=0,mana_cost=""))
    disconnected.append(card(150,"Island",available=30,type_line="Basic Land — Island",colors="",mana_value=0,mana_cost=""))

    coherent_proposal=build_deck(coherent,"Modern",["U"],"tokens")
    disconnected_proposal=build_deck(disconnected,"Modern",["U"],"balanced")

    assert coherent_proposal["synergy_score"]>disconnected_proposal["synergy_score"]
    assert coherent_proposal["quality_score"]>disconnected_proposal["quality_score"]
    assert coherent_proposal["mana_sources"]["U"]>0
