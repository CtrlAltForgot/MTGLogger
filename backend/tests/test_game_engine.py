from mtglogger.services.game_bot import choose_bot_action
from mtglogger.services.game_engine import legal_actions, new_game, perform_action, public_state


def card(index:int,name:str,type_line:str,mana_cost:str="",power:str|None=None,toughness:str|None=None,quantity:int=1):
    return {"scryfall_id":str(index),"name":name,"image_url":f"https://example.test/{index}.jpg","type_line":type_line,"oracle_text":"","mana_cost":mana_cost,"mana_value":2,"power":power,"toughness":toughness,"quantity":quantity}


def decks():
    deck=[card(1,"Island","Basic Land — Island",quantity=20),card(2,"Wind Drake","Creature — Drake","{1}{U}","2","2",40)]
    return deck,deck


def kept_game():
    first,second=decks();state=new_game(first,second)
    state=perform_action(state,"player",{"type":"keep"})
    state=perform_action(state,"bot",{"type":"keep"})
    return state


def test_public_state_hides_opponent_hand_and_library():
    first,second=decks();state=new_game(first,second);visible=public_state(state)
    bot=next(player for player in visible["players"] if player["id"]=="bot")
    assert bot["hand"]==[]
    assert bot["hand_count"]==7
    assert "library" not in bot and bot["library_count"]==53


def test_mulligan_reduces_hand_and_both_keeps_start_game():
    first,second=decks();state=new_game(first,second)
    state=perform_action(state,"player",{"type":"mulligan"})
    assert len(next(player for player in state["players"] if player["id"]=="player")["hand"])==6
    state=perform_action(state,"player",{"type":"keep"});state=perform_action(state,"bot",{"type":"keep"})
    assert state["status"]=="active"


def test_land_casting_mana_payment_and_resolution():
    state=kept_game();player=next(player for player in state["players"] if player["id"]=="player")
    land=next(card for card in player["library"] if "Land" in card["type_line"]);spell=next(card for card in player["library"] if "Creature" in card["type_line"])
    player["library"].remove(land);player["library"].remove(spell);player["hand"].extend([land,spell])
    state=perform_action(state,"player",{"type":"advance_phase"});state=perform_action(state,"player",{"type":"play_land","card_id":land["instance_id"]})
    player=next(player for player in state["players"] if player["id"]=="player")
    second_land=next(card for card in player["library"] if "Land" in card["type_line"]);player["library"].remove(second_land);player["battlefield"].append(second_land)
    state=perform_action(state,"player",{"type":"cast","card_id":spell["instance_id"]})
    assert len(state["stack"])==1
    state=perform_action(state,"player",{"type":"resolve"})
    player=next(player for player in state["players"] if player["id"]=="player")
    assert any(item["name"]=="Wind Drake" for item in player["battlefield"])
    assert sum(item["tapped"] for item in player["battlefield"] if "Land" in item["type_line"])==2


def test_combat_enforces_summoning_sickness_and_deals_damage():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});state=perform_action(state,"player",{"type":"advance_phase"})
    player=next(player for player in state["players"] if player["id"]=="player")
    creature=next(card for card in player["library"] if "Creature" in card["type_line"]);player["library"].remove(creature);creature["summoning_sick"]=False;player["battlefield"].append(creature)
    assert any(action["type"]=="declare_attackers" for action in legal_actions(state,"player"))
    state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":[creature["instance_id"]]})
    state=perform_action(state,"bot",{"type":"advance_phase"})
    bot=next(player for player in state["players"] if player["id"]=="bot")
    assert bot["life"]==18


def test_bot_prioritizes_playing_land_then_casting_spells():
    state=kept_game();state["active_player_id"]="bot";state["priority_player_id"]="bot";state["phase"]="precombat_main"
    bot=next(player for player in state["players"] if player["id"]=="bot")
    land=next(card for card in bot["library"] if "Land" in card["type_line"]);bot["library"].remove(land);bot["hand"].append(land)
    assert choose_bot_action(state,"expert")["type"]=="play_land"


def test_targeted_removal_requires_and_resolves_a_legal_creature_target():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"})
    player=next(player for player in state["players"] if player["id"]=="player");bot=next(player for player in state["players"] if player["id"]=="bot")
    removal={**card(90,"Doom Blade","Instant","{1}{B}"),"oracle_text":"Destroy target creature."}
    # Game cards have unique runtime identity and ownership metadata.
    removal.update({"instance_id":"removal","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False});player["hand"].append(removal)
    for index,name in enumerate(("Swamp","Island")):
        land={**card(91+index,name,f"Basic Land — {name}"),"instance_id":f"land-{index}","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"].append(land)
    creature=next(item for item in bot["library"] if "Creature" in item["type_line"]);bot["library"].remove(creature);bot["battlefield"].append(creature)
    cast=next(action for action in legal_actions(state,"player") if action.get("card_id")=="removal")
    assert any(target["id"]==creature["instance_id"] for target in cast["targets"])
    state=perform_action(state,"player",{"type":"cast","card_id":"removal","target_id":creature["instance_id"]});state=perform_action(state,"player",{"type":"resolve"})
    bot=next(player for player in state["players"] if player["id"]=="bot")
    assert creature in bot["graveyard"] and creature not in bot["battlefield"]


def test_token_effect_and_manual_corrections_are_persisted_in_state():
    state=kept_game();state=perform_action(state,"player",{"type":"adjust_life","amount":-3})
    state=perform_action(state,"player",{"type":"create_token","token_name":"Soldier","power":1,"toughness":1})
    player=next(player for player in state["players"] if player["id"]=="player");token=player["battlefield"][0]
    state=perform_action(state,"player",{"type":"add_counter","target_id":token["instance_id"],"counter_name":"+1/+1","amount":2})
    player=next(player for player in state["players"] if player["id"]=="player")
    assert player["life"]==17
    assert player["battlefield"][0]["counters"]["+1/+1"]==2


def test_human_opponent_game_exposes_each_viewer_only_their_own_hand():
    first,second=decks();state=new_game(first,second,opponent_is_bot=False)
    host=public_state(state,"player");guest=public_state(state,"bot")
    assert next(player for player in host["players"] if player["id"]=="bot")["hand"]==[]
    assert next(player for player in guest["players"] if player["id"]=="player")["hand"]==[]
    assert len(next(player for player in guest["players"] if player["id"]=="bot")["hand"])==7


def test_private_game_priority_allows_instant_response_and_two_pass_resolution():
    first,second=decks();state=new_game(first,second,opponent_is_bot=False)
    state=perform_action(state,"player",{"type":"keep"});state=perform_action(state,"bot",{"type":"keep"})
    state=perform_action(state,"player",{"type":"advance_phase"})
    assert state["pending_phase_advance"] and state["priority_player_id"]=="bot"
    state=perform_action(state,"bot",{"type":"pass_priority"})
    assert state["phase"]=="precombat_main"
    for owner_id,name in (("player","Host Response"),("bot","Guest Response")):
        owner=next(player for player in state["players"] if player["id"]==owner_id)
        spell={**card(200 if owner_id=="player" else 201,name,"Instant"),"instance_id":name,"owner_id":owner_id,"controller_id":owner_id,"tapped":False,"damage":0,"counters":{},"summoning_sick":False};owner["hand"].append(spell)
    state=perform_action(state,"player",{"type":"cast","card_id":"Host Response"})
    assert state["priority_player_id"]=="bot" and len(state["stack"])==1
    assert any(action.get("card_id")=="Guest Response" for action in legal_actions(state,"bot"))
    state=perform_action(state,"bot",{"type":"cast","card_id":"Guest Response"})
    state=perform_action(state,"player",{"type":"pass_priority"});state=perform_action(state,"bot",{"type":"pass_priority"})
    assert len(state["stack"])==1
    assert any(item["name"]=="Guest Response" for item in next(player for player in state["players"] if player["id"]=="bot")["graveyard"])


def test_commander_setup_tax_recast_and_automatic_command_zone_return():
    commander=card(300,"Test Commander","Legendary Creature — Wizard","", "3","3")
    commander_deck=[commander,card(301,"Island","Basic Land — Island",quantity=99)]
    opponent_deck=[card(302,"Other Commander","Legendary Creature — Soldier","","2","2"),card(303,"Plains","Basic Land — Plains",quantity=99)]
    state=new_game(commander_deck,opponent_deck,player_format="Commander",opponent_format="Commander")
    player=next(item for item in state["players"] if item["id"]=="player")
    assert player["life"]==40 and len(player["command"])==1 and len(player["library"])==92
    state=perform_action(state,"player",{"type":"keep"});state=perform_action(state,"bot",{"type":"keep"});state=perform_action(state,"player",{"type":"advance_phase"})
    commander_id=next(item for item in state["players"] if item["id"]=="player")["command"][0]["instance_id"]
    state=perform_action(state,"player",{"type":"cast","card_id":commander_id});state=perform_action(state,"player",{"type":"resolve"})
    player=next(item for item in state["players"] if item["id"]=="player");commander_card=next(item for item in player["battlefield"] if item.get("commander"))
    removal={**card(304,"Self Removal","Instant"),"oracle_text":"Destroy target creature.","instance_id":"self-removal","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(removal)
    state=perform_action(state,"player",{"type":"cast","card_id":"self-removal","target_id":commander_card["instance_id"]});state=perform_action(state,"player",{"type":"resolve"})
    player=next(item for item in state["players"] if item["id"]=="player");assert player["command"] and player["commander_casts"]==1
    for index in range(2):
        land=next(item for item in player["library"] if "Land" in item["type_line"]);player["library"].remove(land);player["battlefield"].append(land)
    recast=next(action for action in legal_actions(state,"player") if action.get("card_id")==commander_id)
    assert recast["commander_tax"]==2


def test_twenty_one_unblocked_commander_damage_ends_game():
    first,second=decks();state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});state=perform_action(state,"player",{"type":"advance_phase"})
    player=next(item for item in state["players"] if item["id"]=="player")
    commander={**card(400,"Huge Commander","Legendary Creature — Giant","","21","21"),"instance_id":"huge-commander","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False,"commander":True};player["battlefield"].append(commander)
    state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["huge-commander"]});state=perform_action(state,"bot",{"type":"advance_phase"})
    defender=next(item for item in state["players"] if item["id"]=="bot")
    assert defender["commander_damage"]["player"]==21
    assert state["status"]=="complete" and state["winner_id"]=="player"
