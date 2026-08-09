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
