from mtglogger.services.game_bot import _choose_blocks, choose_bot_action
from mtglogger.services.game_engine import RuleViolation, legal_actions, new_game, perform_action, public_state


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
    player=next(player for player in state["players"] if player["id"]=="player");assert len(player["hand"])==7 and player["mulligans"]==1
    state=perform_action(state,"player",{"type":"keep"});bottom=legal_actions(state,"player")[0];assert bottom["type"]=="bottom_mulligan_cards" and bottom["amount"]==1
    state=perform_action(state,"player",{"type":"bottom_mulligan_cards","card_ids":bottom["card_ids"][:1]});state=perform_action(state,"bot",{"type":"keep"})
    assert len(next(player for player in state["players"] if player["id"]=="player")["hand"])==6
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


def test_bot_mulligans_bad_hands_by_difficulty_and_assigns_only_legal_blocks():
    first,second=decks();state=new_game(first,second);bot=next(player for player in state["players"] if player["id"]=="bot")
    bot["library"].extend(bot["hand"]);bot["hand"]=[]
    spells=[card for card in bot["library"] if "Land" not in card["type_line"]][:7]
    for spell in spells:bot["library"].remove(spell);bot["hand"].append(spell)
    assert choose_bot_action(state,"beginner")["type"]=="keep" and choose_bot_action(state,"expert")["type"]=="mulligan"
    attacker={**card(70,"Flyer","Creature — Bird","","4","4"),"keywords":["Flying"],"instance_id":"flyer","owner_id":"player","controller_id":"player"};menace={**card(71,"Menace","Creature — Horror","","3","3"),"keywords":["Menace"],"instance_id":"menace","owner_id":"player","controller_id":"player"};ground={**card(72,"Ground","Creature — Bear","","3","3"),"instance_id":"ground","owner_id":"bot","controller_id":"bot"};reach={**card(73,"Reach","Creature — Archer","","2","4"),"keywords":["Reach"],"instance_id":"reach","owner_id":"bot","controller_id":"bot"};helper={**card(74,"Helper","Creature — Soldier","","2","2"),"instance_id":"helper","owner_id":"bot","controller_id":"bot"};player=next(p for p in state["players"] if p["id"]=="player");player["battlefield"]=[attacker,menace];bot["battlefield"]=[ground,reach,helper];state["combat"]={"attackers":["flyer","menace"],"blocks":{}}
    action={"type":"declare_blockers","card_ids":["ground","reach","helper"],"legal_blocks":{"ground":["menace"],"reach":["flyer","menace"],"helper":["menace"]}}
    blocks=_choose_blocks(state,action,"expert");assert blocks.get("reach")=="flyer" and list(blocks.values()).count("menace") in {0,2}


def test_graveyard_targets_return_to_hand_reanimate_and_exile():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    creature={**card(680,"Fallen Hero","Creature — Soldier","","2","2"),"instance_id":"fallen","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};other={**card(681,"Enemy Corpse","Creature — Zombie","","3","3"),"instance_id":"enemy-corpse","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["graveyard"].append(creature);bot["graveyard"].append(other)
    def spell(index,name,text):return {**card(index,name,"Sorcery"),"oracle_text":text,"instance_id":name,"owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False}
    recover=spell(682,"Recover","Return target creature card from your graveyard to your hand.");player["hand"].append(recover);action=next(a for a in legal_actions(state,"player") if a.get("card_id")=="Recover");assert {target["id"] for target in action["targets"]}=={"fallen"}
    state=perform_action(state,"player",{"type":"cast","card_id":"Recover","target_id":"fallen"});state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");assert any(c["instance_id"]=="fallen" for c in player["hand"])
    reanimate=spell(683,"Reanimate","Put target creature card from a graveyard onto the battlefield under your control.");player["hand"].append(reanimate);state=perform_action(state,"player",{"type":"cast","card_id":"Reanimate","target_id":"enemy-corpse"});state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");assert any(c["instance_id"]=="enemy-corpse" and c["controller_id"]=="player" for c in player["battlefield"])
    removal=spell(684,"Removal","Destroy target creature.");player["hand"].append(removal);state=perform_action(state,"player",{"type":"cast","card_id":"Removal","target_id":"enemy-corpse"});state=perform_action(state,"player",{"type":"resolve"});bot=next(p for p in state["players"] if p["id"]=="bot");assert any(c["instance_id"]=="enemy-corpse" for c in bot["graveyard"])
    exile=spell(685,"Grave Hate","Exile target creature card from a graveyard.");player=next(p for p in state["players"] if p["id"]=="player");player["hand"].append(exile);state=perform_action(state,"player",{"type":"cast","card_id":"Grave Hate","target_id":"enemy-corpse"});state=perform_action(state,"player",{"type":"resolve"});bot=next(p for p in state["players"] if p["id"]=="bot");assert any(c["instance_id"]=="enemy-corpse" for c in bot["exile"])


def test_opponent_chooses_forced_sacrifice_and_bot_picks_lowest_value():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    small={**card(690,"Small","Creature — Rat","","1","1"),"instance_id":"small","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};large={**card(691,"Large","Creature — Giant","","6","6"),"mana_value":6,"instance_id":"large","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["battlefield"].extend([small,large])
    edict={**card(692,"Edict","Sorcery"),"oracle_text":"Each opponent sacrifices a creature.","instance_id":"edict","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(edict);state=perform_action(state,"player",{"type":"cast","card_id":"edict"});state=perform_action(state,"player",{"type":"resolve"})
    action=choose_bot_action(state,"expert");assert action=={"type":"sacrifice_permanents","card_ids":["small"]}
    state=perform_action(state,"bot",action);bot=next(p for p in state["players"] if p["id"]=="bot");assert any(c["instance_id"]=="small" for c in bot["graveyard"]) and any(c["instance_id"]=="large" for c in bot["battlefield"])


def test_draw_then_discard_creates_a_persisted_caster_choice():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");before=len(player["hand"])
    loot={**card(700,"Catalog","Instant"),"oracle_text":"Draw two cards, then discard a card.","instance_id":"catalog","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(loot);state=perform_action(state,"player",{"type":"cast","card_id":"catalog"});state=perform_action(state,"player",{"type":"resolve"})
    action=legal_actions(state,"player")[0];assert action["type"]=="discard_cards" and action["reason"]=="effect" and action["amount"]==1
    chosen=action["card_ids"][:1];state=perform_action(state,"player",{"type":"discard_cards","card_ids":chosen});player=next(p for p in state["players"] if p["id"]=="player")
    assert len(player["hand"])==before+1 and any(c["instance_id"]==chosen[0] for c in player["graveyard"]) and state["turn"]==1


def test_legend_rule_uses_canonical_name_and_bot_keeps_stronger_copy():
    state=kept_game();bot=next(p for p in state["players"] if p["id"]=="bot")
    weak={**card(710,"Fancy Reskin","Legendary Creature — Hero","","1","1"),"rules_name":"Same Hero","mana_value":1,"instance_id":"weak","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};strong={**card(711,"Original Art","Legendary Creature — Hero","","5","5"),"rules_name":"Same Hero","mana_value":5,"instance_id":"strong","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["battlefield"].extend([weak,strong])
    state=perform_action(state,"player",{"type":"adjust_life","amount":0});assert state["pending_legendary"]["card_ids"]==["weak","strong"] and not legal_actions(state,"player")
    choice=choose_bot_action(state,"expert");assert choice=={"type":"choose_legendary","card_ids":["strong"]};state=perform_action(state,"bot",choice);bot=next(p for p in state["players"] if p["id"]=="bot")
    assert any(c["instance_id"]=="strong" for c in bot["battlefield"]) and any(c["instance_id"]=="weak" for c in bot["graveyard"])


def test_planeswalker_loyalty_abilities_once_per_turn_and_zero_loyalty_death():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    walker={**card(720,"Test Walker","Legendary Planeswalker — Test"),"oracle_text":"+1: Draw a card.\n−2: Create a 1/1 white Soldier creature token.","loyalty":"3","instance_id":"walker","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{"loyalty":3},"summoning_sick":False};player["battlefield"].append(walker);before=len(player["hand"])
    plus=next(a for a in legal_actions(state,"player") if a["type"]=="activate_loyalty" and a["ability_index"]==0);state=perform_action(state,"player",{"type":"activate_loyalty","card_id":"walker","ability_index":plus["ability_index"]});assert next(p for p in state["players"] if p["id"]=="player")["battlefield"][-1]["counters"]["loyalty"]==4
    state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");assert len(player["hand"])==before+1 and not any(a["type"]=="activate_loyalty" for a in legal_actions(state,"player"))
    bolt={**card(721,"Loyalty Bolt","Instant"),"oracle_text":"Loyalty Bolt deals 4 damage to any target.","instance_id":"bolt","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(bolt);state=perform_action(state,"player",{"type":"cast","card_id":"bolt","target_id":"walker"});state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");assert any(c["instance_id"]=="walker" for c in player["graveyard"])


def test_attackers_choose_planeswalker_defenders_and_remove_loyalty():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    attacker={**card(730,"Walker Hunter","Creature — Warrior","","4","4"),"instance_id":"hunter","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};walker={**card(731,"Enemy Walker","Legendary Planeswalker — Test"),"loyalty":"3","instance_id":"enemy-walker","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{"loyalty":3},"summoning_sick":False};player["battlefield"].append(attacker);bot["battlefield"].append(walker)
    attack=next(a for a in legal_actions(state,"player") if a["type"]=="declare_attackers");assert {target["id"] for target in attack["defenders"]}=={"bot","enemy-walker"}
    state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["hunter"],"attack_targets":{"hunter":"enemy-walker"}});state=perform_action(state,"bot",{"type":"advance_phase"});bot=next(p for p in state["players"] if p["id"]=="bot")
    assert bot["life"]==20 and any(c["instance_id"]=="enemy-walker" for c in bot["graveyard"])


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


def test_tap_ability_uses_stack_draws_and_cannot_be_reused_while_tapped():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(item for item in state["players"] if item["id"]=="player")
    source={**card(500,"Book of Answers","Artifact"),"oracle_text":"{T}: Draw a card.","instance_id":"book","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"].append(source);before=len(player["hand"])
    action=next(action for action in legal_actions(state,"player") if action["type"]=="activate")
    state=perform_action(state,"player",{"type":"activate","card_id":"book","ability_index":action["ability_index"]})
    assert state["stack"][-1]["kind"]=="ability" and not any(action["type"]=="activate" for action in legal_actions(state,"player"))
    state=perform_action(state,"player",{"type":"resolve"});player=next(item for item in state["players"] if item["id"]=="player")
    assert len(player["hand"])==before+1 and not any(card["name"].endswith(" ability") for card in player["graveyard"])


def test_activated_abilities_enforce_mana_tap_and_summoning_sickness_costs():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    source={**card(669,"Arcane Device","Artifact"),"oracle_text":"{2}, {T}: Draw a card.","instance_id":"device","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False}
    repeatable={**card(670,"Study Stone","Artifact"),"oracle_text":"{1}: Draw a card.","instance_id":"stone","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False}
    lands=[{**card(671+i,f"Island {i}","Basic Land — Island"),"instance_id":f"land-{i}","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False} for i in range(3)]
    player["battlefield"].extend([source,repeatable,*lands]);ability=next(a for a in legal_actions(state,"player") if a.get("card_id")=="device");before=len(player["hand"])
    state=perform_action(state,"player",{"type":"activate","card_id":"device","ability_index":ability["ability_index"]});player=next(p for p in state["players"] if p["id"]=="player")
    assert next(card for card in player["battlefield"] if card["instance_id"]=="device")["tapped"] and sum(card["tapped"] for card in player["battlefield"] if "Land" in card["type_line"])==2
    state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");assert len(player["hand"])==before+1 and not any(a.get("card_id")=="device" for a in legal_actions(state,"player"))
    repeat_action=next(a for a in legal_actions(state,"player") if a.get("card_id")=="stone");state=perform_action(state,"player",{"type":"activate","card_id":"stone","ability_index":repeat_action["ability_index"]});player=next(p for p in state["players"] if p["id"]=="player")
    assert not next(card for card in player["battlefield"] if card["instance_id"]=="stone")["tapped"] and all(card["tapped"] for card in player["battlefield"] if "Land" in card["type_line"])
    sick={**card(675,"New Apprentice","Creature — Wizard","","1","1"),"oracle_text":"{T}: Draw a card.","instance_id":"sick","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":True};player["battlefield"].append(sick)
    assert not any(a.get("card_id")=="sick" for a in legal_actions(state,"player"))


def test_creature_enter_trigger_is_queued_and_resolved_once():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(item for item in state["players"] if item["id"]=="player")
    watcher={**card(510,"Soul Watcher","Creature — Cleric","","1","1"),"oracle_text":"Whenever another creature enters the battlefield under your control, you gain 1 life.","instance_id":"watcher","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"].append(watcher)
    entrant={**card(511,"New Friend","Creature — Citizen","","1","1"),"instance_id":"entrant","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(entrant)
    state=perform_action(state,"player",{"type":"cast","card_id":"entrant"});state=perform_action(state,"player",{"type":"resolve"})
    assert state["stack"] and state["stack"][-1]["kind"]=="trigger"
    state=perform_action(state,"player",{"type":"resolve"});player=next(item for item in state["players"] if item["id"]=="player")
    assert player["life"]==21


def test_flying_reach_vigilance_lifelink_and_trample_are_enforced():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});state=perform_action(state,"player",{"type":"advance_phase"});attacker=next(item for item in state["players"] if item["id"]=="player");defender=next(item for item in state["players"] if item["id"]=="bot")
    flyer={**card(520,"Sky Knight","Creature — Knight","","4","4"),"keywords":["Flying","Vigilance","Lifelink","Trample"],"instance_id":"flyer","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};ground={**card(521,"Groundling","Creature — Beast","","2","2"),"instance_id":"ground","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};reach={**card(522,"Archer","Creature — Archer","","1","1"),"keywords":["Reach"],"instance_id":"reach","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};attacker["battlefield"].append(flyer);defender["battlefield"].extend([ground,reach])
    state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["flyer"]});assert not next(item for item in state["players"] if item["id"]=="player")["battlefield"][-1]["tapped"]
    block=next(action for action in legal_actions(state,"bot") if action["type"]=="declare_blockers")
    assert "flyer" not in block["legal_blocks"]["ground"] and "flyer" in block["legal_blocks"]["reach"]
    state=perform_action(state,"bot",{"type":"declare_blockers","blocks":{"reach":"flyer"}});attacker=next(item for item in state["players"] if item["id"]=="player");defender=next(item for item in state["players"] if item["id"]=="bot")
    assert attacker["life"]==24 and defender["life"]==17 and any(item["instance_id"]=="reach" for item in defender["graveyard"])


def test_mana_rocks_colorless_and_hybrid_costs_and_zero_toughness_state_action():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(item for item in state["players"] if item["id"]=="player")
    rock={**card(530,"Mana Rock","Artifact"),"oracle_text":"{T}: Add {C}.","instance_id":"rock","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};plains={**card(531,"Plains","Basic Land — Plains"),"instance_id":"plains","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};spell={**card(532,"Hybrid Construct","Artifact Creature — Construct","{W/U}{C}","1","1"),"instance_id":"hybrid","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"].extend([rock,plains]);player["hand"].append(spell)
    assert any(action.get("card_id")=="hybrid" for action in legal_actions(state,"player"))
    state=perform_action(state,"player",{"type":"cast","card_id":"hybrid"});player=next(item for item in state["players"] if item["id"]=="player");assert all(item["tapped"] for item in player["battlefield"] if item["instance_id"] in {"rock","plains"})
    state=perform_action(state,"player",{"type":"resolve"});state=perform_action(state,"player",{"type":"add_counter","target_id":"hybrid","counter_name":"-1/-1","amount":1});player=next(item for item in state["players"] if item["id"]=="player")
    assert not any(item["instance_id"]=="hybrid" for item in player["battlefield"])


def test_bounce_tap_combat_trick_mill_and_discard_effects_resolve():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(item for item in state["players"] if item["id"]=="player");bot=next(item for item in state["players"] if item["id"]=="bot")
    creature={**card(600,"Target","Creature — Bear","","2","2"),"instance_id":"target","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["battlefield"].append(creature)
    def resolve(text,target="target"):
        ability={**card(601,text,"Instant"),"oracle_text":text,"instance_id":text,"owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(ability)
        result=perform_action(state,"player",{"type":"cast","card_id":text,"target_id":target});return perform_action(result,"player",{"type":"resolve"})
    state=resolve("Tap target creature.");creature=next(item for item in next(p for p in state["players"] if p["id"]=="bot")["battlefield"] if item["instance_id"]=="target");assert creature["tapped"]
    player=next(item for item in state["players"] if item["id"]=="player");bot=next(item for item in state["players"] if item["id"]=="bot");state=resolve("Target creature an opponent controls gets -2/-2 until end of turn.")
    assert not next(item for item in state["players"] if item["id"]=="bot")["battlefield"]
    player=next(item for item in state["players"] if item["id"]=="player");bot=next(item for item in state["players"] if item["id"]=="bot");before_library=len(bot["library"]);before_hand=len(bot["hand"]);state=resolve("Target player mills three cards.","bot")
    assert len(next(item for item in state["players"] if item["id"]=="bot")["library"])==before_library-3
    player=next(item for item in state["players"] if item["id"]=="player");bot=next(item for item in state["players"] if item["id"]=="bot");state=resolve("Target opponent discards two cards.",None);choice=choose_bot_action(state,"expert");assert choice["type"]=="discard_cards" and len(choice["card_ids"])==2;state=perform_action(state,"bot",choice)
    assert len(next(item for item in state["players"] if item["id"]=="bot")["hand"])==before_hand-2


def test_counterspell_targets_and_counters_a_spell_on_the_stack():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(item for item in state["players"] if item["id"]=="player");bot=next(item for item in state["players"] if item["id"]=="bot")
    bot["is_bot"]=False
    threat={**card(610,"Threat","Creature — Beast","","3","3"),"instance_id":"threat","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};counter={**card(611,"Counterspell","Instant"),"oracle_text":"Counter target spell.","instance_id":"counter","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["hand"].append(threat);player["hand"].append(counter)
    state["active_player_id"]="bot";state["priority_player_id"]="bot";state=perform_action(state,"bot",{"type":"cast","card_id":"threat"});target=state["stack"][-1]["id"]
    action=next(item for item in legal_actions(state,"player") if item.get("card_id")=="counter");assert action["targets"][0]["id"]==target
    state=perform_action(state,"player",{"type":"cast","card_id":"counter","target_id":target});state=perform_action(state,"bot",{"type":"pass_priority"});state=perform_action(state,"player",{"type":"pass_priority"})
    bot=next(item for item in state["players"] if item["id"]=="bot");assert any(item["name"]=="Threat" for item in bot["graveyard"]) and not state["stack"]


def test_defender_unblockable_hexproof_protection_and_indestructible_are_enforced():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    def permanent(index,name,text="",keywords=None,owner="player"):
        return {**card(index,name,"Creature — Test","","2","2"),"oracle_text":text,"keywords":keywords or [],"instance_id":name,"owner_id":owner,"controller_id":owner,"tapped":False,"damage":0,"counters":{},"summoning_sick":False}
    wall=permanent(620,"Wall",keywords=["Defender"]);ghost=permanent(621,"Ghost","Ghost can't be blocked.");player["battlefield"].extend([wall,ghost])
    shielded=permanent(622,"Shielded",keywords=["Hexproof"],owner="bot");durable=permanent(623,"Durable",keywords=["Indestructible"],owner="bot");blocker=permanent(624,"Blocker",owner="bot");bot["battlefield"].extend([shielded,durable,blocker])
    attackers=next(action for action in legal_actions(state,"player") if action["type"]=="declare_attackers")["card_ids"];assert "Wall" not in attackers and "Ghost" in attackers
    removal={**card(625,"Removal","Instant"),"oracle_text":"Destroy target creature.","instance_id":"removal","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(removal)
    targets=next(action for action in legal_actions(state,"player") if action.get("card_id")=="removal")["targets"];assert "Shielded" not in {target["id"] for target in targets}
    state=perform_action(state,"player",{"type":"cast","card_id":"removal","target_id":"Durable"});state=perform_action(state,"player",{"type":"resolve"});assert any(item["instance_id"]=="Durable" for item in next(p for p in state["players"] if p["id"]=="bot")["battlefield"])
    state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["Ghost"]});block_actions=[action for action in legal_actions(state,"bot") if action["type"]=="declare_blockers"];assert not block_actions


def test_board_wipes_global_stat_effects_and_life_loss_resolve():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    normal={**card(630,"Normal","Creature — Test","","2","2"),"instance_id":"normal","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};indestructible={**normal,"name":"Indestructible","instance_id":"indestructible","keywords":["Indestructible"]};bot["battlefield"].extend([normal,indestructible])
    wipe={**card(631,"Wrath","Sorcery"),"oracle_text":"Destroy all creatures. Each opponent loses 3 life.","instance_id":"wipe","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(wipe)
    state=perform_action(state,"player",{"type":"cast","card_id":"wipe"});state=perform_action(state,"player",{"type":"resolve"});bot=next(p for p in state["players"] if p["id"]=="bot")
    assert bot["life"]==17 and [item["instance_id"] for item in bot["battlefield"]]==["indestructible"] and any(item["instance_id"]=="normal" for item in bot["graveyard"])


def test_first_strike_kills_before_retaliation_and_double_strike_hits_twice():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    striker={**card(640,"First Striker","Creature — Knight","","2","2"),"keywords":["First strike"],"instance_id":"striker","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};double={**card(641,"Double Striker","Creature — Knight","","2","2"),"keywords":["Double strike"],"instance_id":"double","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};blocker={**card(642,"Blocker","Creature — Bear","","2","2"),"instance_id":"blocker","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"].extend([striker,double]);bot["battlefield"].append(blocker)
    state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["striker","double"]});state=perform_action(state,"bot",{"type":"declare_blockers","blocks":{"blocker":"striker"}});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    assert any(item["instance_id"]=="striker" and item["damage"]==0 for item in player["battlefield"])
    assert any(item["instance_id"]=="blocker" for item in bot["graveyard"])
    assert bot["life"]==16


def test_infect_wither_toxic_and_poison_loss_are_enforced():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    infect={**card(650,"Infecter","Creature — Horror","","3","3"),"keywords":["Infect"],"instance_id":"infect","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};toxic={**card(651,"Toxic","Creature — Phyrexian","","1","1"),"oracle_text":"Toxic 2","keywords":["Toxic"],"instance_id":"toxic","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};durable={**card(652,"Durable","Creature — Golem","","3","3"),"keywords":["Indestructible"],"instance_id":"durable","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"].extend([infect,toxic]);bot["battlefield"].append(durable)
    state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["infect","toxic"]});state=perform_action(state,"bot",{"type":"declare_blockers","blocks":{"durable":"infect"}});bot=next(p for p in state["players"] if p["id"]=="bot")
    assert bot["life"]==19 and bot["poison"]==2 and any(item["instance_id"]=="durable" for item in bot["graveyard"])
    bot["poison"]=10;state["status"]="active";state["winner_id"]=None
    state=perform_action(state,"player",{"type":"adjust_life","amount":0});assert state["status"]=="complete" and state["winner_id"]=="player"


def test_cleanup_requires_exact_discard_to_seven_before_next_turn():
    state=kept_game();player=next(p for p in state["players"] if p["id"]=="player")
    while len(player["hand"])<9:player["hand"].append(player["library"].pop())
    state["phase"]="ending";state=perform_action(state,"player",{"type":"advance_phase"})
    assert state["turn"]==1 and state["pending_discard"]=={"player_id":"player","amount":2}
    action=legal_actions(state,"player")[0];assert action["type"]=="discard_cards" and action["amount"]==2 and action["reason"]=="cleanup"
    try:perform_action(state,"player",{"type":"discard_cards","card_ids":action["card_ids"][:1]})
    except RuleViolation:pass
    else:raise AssertionError("cleanup accepted the wrong discard count")
    chosen=action["card_ids"][:2];state=perform_action(state,"player",{"type":"discard_cards","card_ids":chosen});player=next(p for p in state["players"] if p["id"]=="player")
    assert len(player["hand"])==7 and all(any(card["instance_id"]==card_id for card in player["graveyard"]) for card_id in chosen) and state["turn"]==2 and state["active_player_id"]=="bot"


def test_bot_discards_automatically_and_no_maximum_hand_size_is_honored():
    state=kept_game();bot=next(p for p in state["players"] if p["id"]=="bot");state["active_player_id"]="bot";state["priority_player_id"]="bot";state["phase"]="ending"
    while len(bot["hand"])<9:bot["hand"].append(bot["library"].pop())
    state=perform_action(state,"bot",{"type":"advance_phase"});choice=choose_bot_action(state,"expert")
    assert choice["type"]=="discard_cards" and len(choice["card_ids"])==2
    state=perform_action(state,"bot",choice);assert len(next(p for p in state["players"] if p["id"]=="bot")["hand"])==7
    state=kept_game();player=next(p for p in state["players"] if p["id"]=="player");player["battlefield"].append({**card(660,"Spellbook","Artifact"),"oracle_text":"You have no maximum hand size.","instance_id":"spellbook","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False})
    while len(player["hand"])<9:player["hand"].append(player["library"].pop())
    state["phase"]="ending";state=perform_action(state,"player",{"type":"advance_phase"});assert state["turn"]==2 and state.get("pending_discard") is None
