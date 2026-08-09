import pytest

from mtglogger.services.game_bot import _choose_blocks, choose_bot_action, run_bot
from mtglogger.services.game_engine import RuleViolation, _has_keyword, legal_actions, new_game, perform_action, public_state


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


def deal_combat_damage(state):
    return perform_action(state,state["active_player_id"],{"type":"resolve_combat_damage"})


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
    state=perform_action(state,"bot",{"type":"advance_phase"});state=deal_combat_damage(state)
    bot=next(player for player in state["players"] if player["id"]=="bot")
    assert bot["life"]==18


def test_attacker_orders_multiple_blockers_before_combat_damage():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    attacker={**card(90,"Heavy Hitter","Creature — Giant","","5","5"),"instance_id":"attacker","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};first={**card(91,"First Blocker","Creature — Beast","","3","3"),"instance_id":"first","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};second={**card(92,"Second Blocker","Creature — Beast","","3","3"),"instance_id":"second","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"].append(attacker);bot["battlefield"].extend([first,second])
    state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["attacker"]});state=perform_action(state,"bot",{"type":"declare_blockers","blocks":{"first":"attacker","second":"attacker"}});action=legal_actions(state,"player")[0]
    assert action["type"]=="order_blockers" and {card["instance_id"] for card in action["groups"][0]["blockers"]}=={"first","second"}
    state=perform_action(state,"player",{"type":"order_blockers","block_orders":{"attacker":["second","first"]}});state=deal_combat_damage(state);player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    assert any(card["instance_id"]=="attacker" for card in player["graveyard"]) and any(card["instance_id"]=="second" for card in bot["graveyard"]);survivor=next(card for card in bot["battlefield"] if card["instance_id"]=="first");assert survivor["damage"]==2

    state=kept_game();player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot");state["active_player_id"]="bot";state["priority_player_id"]="bot";bot["battlefield"].append(attacker);player["battlefield"].extend([first,second]);state["pending_damage_order"]={"player_id":"bot","groups":{"attacker":["first","second"]}}
    choice=choose_bot_action(state,"expert");assert choice["type"]=="order_blockers" and set(choice["block_orders"]["attacker"])=={"first","second"}


def test_players_receive_a_post_block_combat_trick_window_before_damage():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    attacker={**card(93,"Trickster","Creature — Rogue","","3","3"),"instance_id":"trickster","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};blocker={**card(94,"Equal Blocker","Creature — Beast","","3","3"),"instance_id":"equal","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};trick={**card(95,"Sudden Strength","Instant"),"oracle_text":"Target creature gets +2/+2 until end of turn.","instance_id":"trick","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"].append(attacker);player["hand"].append(trick);bot["battlefield"].append(blocker)
    state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["trickster"]});state=perform_action(state,"bot",{"type":"declare_blockers","blocks":{"equal":"trickster"}});actions=legal_actions(state,"player")
    assert any(action["type"]=="resolve_combat_damage" for action in actions) and any(action.get("card_id")=="trick" for action in actions)
    state=perform_action(state,"player",{"type":"cast","card_id":"trick","target_id":"trickster"});state=perform_action(state,"player",{"type":"resolve"});state=deal_combat_damage(state);player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    assert any(card["instance_id"]=="trickster" for card in player["battlefield"]) and any(card["instance_id"]=="equal" for card in bot["graveyard"])


def test_private_games_require_both_players_to_pass_after_blocks_before_damage():
    first,second=decks();state=new_game(first,second,opponent_is_bot=False);state=perform_action(state,"player",{"type":"keep"});state=perform_action(state,"bot",{"type":"keep"});state["phase"]="combat";player=next(p for p in state["players"] if p["id"]=="player");guest=next(p for p in state["players"] if p["id"]=="bot")
    attacker={**card(96,"Private Attacker","Creature — Warrior","","3","3"),"instance_id":"private-attacker","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};blocker={**card(97,"Private Blocker","Creature — Warrior","","2","2"),"instance_id":"private-blocker","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"].append(attacker);guest["battlefield"].append(blocker)
    state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["private-attacker"]});state=perform_action(state,"bot",{"type":"declare_blockers","blocks":{"private-blocker":"private-attacker"}});assert state["combat"]["damage_pending"] and state["priority_player_id"]=="player"
    state=perform_action(state,"player",{"type":"pass_priority"});assert state["combat"]["attackers"] and state["priority_player_id"]=="bot";state=perform_action(state,"bot",{"type":"pass_priority"});guest=next(p for p in state["players"] if p["id"]=="bot")
    assert not state["combat"]["attackers"] and any(card["instance_id"]=="private-blocker" for card in guest["graveyard"])


def test_bot_prioritizes_playing_land_then_casting_spells():
    state=kept_game();state["active_player_id"]="bot";state["priority_player_id"]="bot";state["phase"]="precombat_main"
    bot=next(player for player in state["players"] if player["id"]=="bot")
    land=next(card for card in bot["library"] if "Land" in card["type_line"]);bot["library"].remove(land);bot["hand"].append(land)
    assert choose_bot_action(state,"expert")["type"]=="play_land"


def test_expert_bot_avoids_bad_attacks_and_targets_the_largest_threat():
    state=kept_game();state["active_player_id"]="bot";state["priority_player_id"]="bot";state["phase"]="combat";bot=next(p for p in state["players"] if p["id"]=="bot");enemy=next(p for p in state["players"] if p["id"]=="player");bot["battlefield"]=[];enemy["battlefield"]=[]
    weak={**card(100,"Risky Attacker","Creature — Goblin","","2","2"),"instance_id":"risky","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};flyer={**card(101,"Evasive Attacker","Creature — Bird","","2","2"),"keywords":["Flying"],"instance_id":"evasive","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};wall={**card(102,"Large Defender","Creature — Giant","","5","5"),"instance_id":"large-defender","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["battlefield"].extend([weak,flyer]);enemy["battlefield"].append(wall)
    choice=choose_bot_action(state,"expert");assert choice["type"]=="declare_attackers" and choice["attacker_ids"]==["evasive"]
    enemy["life"]=2;choice=choose_bot_action(state,"expert");assert set(choice["attacker_ids"])=={"risky","evasive"}

    state=kept_game();state["active_player_id"]="bot";state["priority_player_id"]="bot";state["phase"]="precombat_main";bot=next(p for p in state["players"] if p["id"]=="bot");enemy=next(p for p in state["players"] if p["id"]=="player");bot["hand"]=[];bot["land_plays_remaining"]=0;enemy["battlefield"]=[]
    small={**card(103,"Small Threat","Creature — Citizen","","1","1"),"instance_id":"small-threat","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};large={**card(104,"Huge Threat","Creature — Dragon","","8","8"),"keywords":["Flying","Trample"],"instance_id":"huge-threat","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};removal={**card(105,"Bot Removal","Instant"),"oracle_text":"Destroy target creature.","instance_id":"bot-removal","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};enemy["battlefield"].extend([small,large]);bot["hand"].append(removal)
    choice=choose_bot_action(state,"expert");assert choice["type"]=="cast" and choice["target_id"]=="huge-threat"


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
    state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["hunter"],"attack_targets":{"hunter":"enemy-walker"}});state=perform_action(state,"bot",{"type":"advance_phase"});state=deal_combat_damage(state);bot=next(p for p in state["players"] if p["id"]=="bot")
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


def test_commander_setup_tax_recast_and_owner_command_zone_choice():
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
    player=next(item for item in state["players"] if item["id"]=="player");assert any(card["instance_id"]==commander_id for card in player["graveyard"])
    choice=legal_actions(state,"player");assert {action["type"] for action in choice}>={"move_commander","keep_commander"}
    state=perform_action(state,"player",{"type":"move_commander","card_id":commander_id});player=next(item for item in state["players"] if item["id"]=="player");assert player["command"] and player["commander_casts"]==1
    for index in range(2):
        land=next(item for item in player["library"] if "Land" in item["type_line"]);player["library"].remove(land);player["battlefield"].append(land)
    recast=next(action for action in legal_actions(state,"player") if action.get("card_id")==commander_id)
    assert recast["commander_tax"]==2


def test_twenty_one_unblocked_commander_damage_ends_game():
    first,second=decks();state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});state=perform_action(state,"player",{"type":"advance_phase"})
    player=next(item for item in state["players"] if item["id"]=="player")
    commander={**card(400,"Huge Commander","Legendary Creature — Giant","","21","21"),"instance_id":"huge-commander","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False,"commander":True};player["battlefield"].append(commander)
    state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["huge-commander"]});state=perform_action(state,"bot",{"type":"advance_phase"});state=deal_combat_damage(state)
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


def test_targeted_triggers_pause_for_the_controllers_legal_target_choice():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    first={**card(515,"First Choice","Creature — Bear","","2","2"),"instance_id":"first-choice","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};second={**card(516,"Second Choice","Creature — Bear","","2","2"),"instance_id":"second-choice","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};mage={**card(517,"Target Mage","Creature — Wizard","","2","2"),"oracle_text":"When Target Mage enters, tap target creature.","instance_id":"target-mage","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["battlefield"].extend([first,second]);player["hand"].append(mage)
    state=perform_action(state,"player",{"type":"cast","card_id":"target-mage"});state=perform_action(state,"player",{"type":"resolve"});action=legal_actions(state,"player")[0]
    assert action["type"]=="choose_trigger_target" and {target["id"] for target in action["targets"]}>={"first-choice","second-choice"} and not state["stack"]
    state=perform_action(state,"player",{"type":"choose_trigger_target","target_id":"second-choice"});assert state["stack"][-1]["kind"]=="trigger" and state["stack"][-1]["target_id"]=="second-choice";state=perform_action(state,"player",{"type":"resolve"});bot=next(p for p in state["players"] if p["id"]=="bot")
    assert not next(card for card in bot["battlefield"] if card["instance_id"]=="first-choice")["tapped"] and next(card for card in bot["battlefield"] if card["instance_id"]=="second-choice")["tapped"]

    ability={**mage,"name":"Bot Trigger","oracle_text":"Tap target creature.","type_line":"Ability","mana_cost":""};state["priority_player_id"]="bot";state["pending_trigger_targets"]=[{"controller_id":"bot","source_name":"Bot Source","card":ability,"trigger":{"id":"bot-trigger","kind":"trigger","card":ability,"controller_id":"bot","target_id":None,"source_id":"source"}}]
    choice=choose_bot_action(state,"expert");target=next(target for target in legal_actions(state,"bot")[0]["targets"] if target["id"]==choice["target_id"]);assert choice["type"]=="choose_trigger_target" and target["controller_id"]=="player"


def test_flying_reach_vigilance_lifelink_and_trample_are_enforced():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});state=perform_action(state,"player",{"type":"advance_phase"});attacker=next(item for item in state["players"] if item["id"]=="player");defender=next(item for item in state["players"] if item["id"]=="bot")
    flyer={**card(520,"Sky Knight","Creature — Knight","","4","4"),"keywords":["Flying","Vigilance","Lifelink","Trample"],"instance_id":"flyer","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};ground={**card(521,"Groundling","Creature — Beast","","2","2"),"instance_id":"ground","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};reach={**card(522,"Archer","Creature — Archer","","1","1"),"keywords":["Reach"],"instance_id":"reach","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};attacker["battlefield"].append(flyer);defender["battlefield"].extend([ground,reach])
    state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["flyer"]});assert not next(item for item in state["players"] if item["id"]=="player")["battlefield"][-1]["tapped"]
    block=next(action for action in legal_actions(state,"bot") if action["type"]=="declare_blockers")
    assert "flyer" not in block["legal_blocks"]["ground"] and "flyer" in block["legal_blocks"]["reach"]
    state=perform_action(state,"bot",{"type":"declare_blockers","blocks":{"reach":"flyer"}});state=deal_combat_damage(state);attacker=next(item for item in state["players"] if item["id"]=="player");defender=next(item for item in state["players"] if item["id"]=="bot")
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


def test_counters_temporary_keywords_and_enters_tapped_are_enforced():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    creature={**card(690,"Young Hero","Creature — Human","","1","1"),"instance_id":"hero","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":True};player["battlefield"].append(creature)
    boon={**card(691,"Heroic Boon","Instant"),"oracle_text":"Put two +1/+1 counters on target creature. Target creature gains haste and indestructible until end of turn.","instance_id":"boon","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(boon)
    action=next(a for a in legal_actions(state,"player") if a.get("card_id")=="boon");assert {target["id"] for target in action["targets"]}=={"hero"}
    state=perform_action(state,"player",{"type":"cast","card_id":"boon","target_id":"hero"});state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");hero=next(c for c in player["battlefield"] if c["instance_id"]=="hero")
    assert hero["counters"]["+1/+1"]==2 and set(hero["temporary_keywords"])=={"haste","indestructible"}
    state=perform_action(state,"player",{"type":"advance_phase"});assert "hero" in next(a for a in legal_actions(state,"player") if a["type"]=="declare_attackers")["card_ids"]

    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    tapped={**card(692,"Sleepy Golem","Artifact Creature — Golem","","3","3"),"oracle_text":"Sleepy Golem enters the battlefield tapped.","instance_id":"sleepy","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(tapped)
    state=perform_action(state,"player",{"type":"cast","card_id":"sleepy"});state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");assert next(c for c in player["battlefield"] if c["instance_id"]=="sleepy")["tapped"]


def test_counterspell_targets_and_counters_a_spell_on_the_stack():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(item for item in state["players"] if item["id"]=="player");bot=next(item for item in state["players"] if item["id"]=="bot")
    bot["is_bot"]=False
    threat={**card(610,"Threat","Creature — Beast","","3","3"),"instance_id":"threat","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};counter={**card(611,"Counterspell","Instant"),"oracle_text":"Counter target spell.","instance_id":"counter","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["hand"].append(threat);player["hand"].append(counter)
    state["active_player_id"]="bot";state["priority_player_id"]="bot";state=perform_action(state,"bot",{"type":"cast","card_id":"threat"});target=state["stack"][-1]["id"]
    action=next(item for item in legal_actions(state,"player") if item.get("card_id")=="counter");assert action["targets"][0]["id"]==target
    state=perform_action(state,"player",{"type":"cast","card_id":"counter","target_id":target});state=perform_action(state,"bot",{"type":"pass_priority"});state=perform_action(state,"player",{"type":"pass_priority"})
    bot=next(item for item in state["players"] if item["id"]=="bot");assert any(item["name"]=="Threat" for item in bot["graveyard"]) and not state["stack"]


def test_spells_fizzle_when_their_only_target_becomes_illegal():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    target={**card(720,"Future Hexproof","Creature — Wizard","","2","2"),"instance_id":"future-hexproof","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};removal={**card(721,"Doom Attempt","Instant"),"oracle_text":"Destroy target creature.","instance_id":"doom-attempt","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["battlefield"].append(target);player["hand"].append(removal)
    state=perform_action(state,"player",{"type":"cast","card_id":"doom-attempt","target_id":"future-hexproof"});bot=next(p for p in state["players"] if p["id"]=="bot");next(card for card in bot["battlefield"] if card["instance_id"]=="future-hexproof")["temporary_keywords"]=["hexproof"]
    state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    assert any(card["instance_id"]=="future-hexproof" for card in bot["battlefield"]) and any(card["instance_id"]=="doom-attempt" for card in player["graveyard"]) and "no longer legal" in state["log"][-1]["message"]


def test_ward_requires_a_persisted_pay_or_counter_decision():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    warded={**card(724,"Warded Sage","Creature — Wizard","","2","2"),"oracle_text":"Ward {2}","instance_id":"warded","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};removal={**card(725,"Ward Breaker","Instant"),"oracle_text":"Destroy target creature.","instance_id":"ward-breaker","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};lands=[]
    for index in range(2):land=next(card for card in player["library"] if "Land" in card["type_line"]);player["library"].remove(land);land["instance_id"]=f"ward-land-{index}";land["tapped"]=False;lands.append(land)
    player["battlefield"].extend(lands);player["hand"].append(removal);bot["battlefield"].append(warded);state=perform_action(state,"player",{"type":"cast","card_id":"ward-breaker","target_id":"warded"});actions=legal_actions(state,"player")
    assert {action["type"] for action in actions}>={"pay_ward","decline_ward"} and state["pending_ward"]["mana_cost"]=="{2}"
    state=perform_action(state,"player",{"type":"pay_ward"});player=next(p for p in state["players"] if p["id"]=="player");assert all(land["tapped"] for land in player["battlefield"] if land["instance_id"].startswith("ward-land"));state=perform_action(state,"player",{"type":"resolve"});bot=next(p for p in state["players"] if p["id"]=="bot");assert any(card["instance_id"]=="warded" for card in bot["graveyard"])

    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot");player["hand"].append(removal);bot["battlefield"].append(warded);state=perform_action(state,"player",{"type":"cast","card_id":"ward-breaker","target_id":"warded"});assert "pay_ward" not in {action["type"] for action in legal_actions(state,"player")}
    state=perform_action(state,"player",{"type":"decline_ward"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot");assert not state["stack"] and any(card["instance_id"]=="ward-breaker" for card in player["graveyard"]) and any(card["instance_id"]=="warded" for card in bot["battlefield"])

    state=kept_game();bot=next(p for p in state["players"] if p["id"]=="bot");state["active_player_id"]="bot";state["priority_player_id"]="bot";bot_lands=[]
    for index in range(2):land=next(card for card in bot["library"] if "Land" in card["type_line"]);bot["library"].remove(land);land["instance_id"]=f"bot-ward-land-{index}";land["tapped"]=False;bot_lands.append(land)
    bot["battlefield"].extend(bot_lands);state["stack"]=[{"id":"bot-ward-spell","card":removal,"controller_id":"bot","target_id":"warded"}];state["pending_ward"]={"player_id":"bot","stack_id":"bot-ward-spell","mana_cost":"{2}","source_name":"Warded Sage"}
    assert choose_bot_action(state,"expert")["type"]=="pay_ward" and choose_bot_action(state,"beginner")["type"]=="decline_ward"


def test_life_and_discard_ward_costs_are_validated_and_paid():
    def ward_state(text,index):
        state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot");warded={**card(index,"Alternative Ward","Creature — Wizard","","2","2"),"oracle_text":text,"instance_id":"alternative-ward","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};removal={**card(index+1,"Alternative Removal","Instant"),"oracle_text":"Destroy target creature.","instance_id":"alternative-removal","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(removal);bot["battlefield"].append(warded);return perform_action(state,"player",{"type":"cast","card_id":"alternative-removal","target_id":"alternative-ward"})

    state=ward_state("Ward—Pay 3 life.",730);action=next(a for a in legal_actions(state,"player") if a["type"]=="pay_ward");assert action["cost_type"]=="life" and action["amount"]==3
    state=perform_action(state,"player",{"type":"pay_ward"});assert next(p for p in state["players"] if p["id"]=="player")["life"]==17

    state=ward_state("Ward—Discard a card.",732);action=next(a for a in legal_actions(state,"player") if a["type"]=="pay_ward");chosen=action["card_ids"][0];before=len(next(p for p in state["players"] if p["id"]=="player")["hand"]);assert action["cost_type"]=="discard" and action["amount"]==1
    state=perform_action(state,"player",{"type":"pay_ward","card_ids":[chosen]});player=next(p for p in state["players"] if p["id"]=="player");assert len(player["hand"])==before-1 and any(card["instance_id"]==chosen for card in player["graveyard"])



def test_countered_commander_spell_can_stay_in_graveyard_or_move_to_command_zone():
    first,second=decks();state=new_game(first,second,opponent_is_bot=False,player_format="Commander");state=perform_action(state,"player",{"type":"keep"});state=perform_action(state,"bot",{"type":"keep"});state["phase"]="precombat_main";player=next(p for p in state["players"] if p["id"]=="player");guest=next(p for p in state["players"] if p["id"]=="bot")
    commander={**card(722,"Test Commander","Legendary Creature — Wizard","","2","2"),"instance_id":"test-commander","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False,"commander":True};counter={**card(723,"Command Denial","Instant"),"oracle_text":"Counter target spell.","instance_id":"command-denial","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["command"].append(commander);guest["hand"].append(counter)
    state=perform_action(state,"player",{"type":"cast","card_id":"test-commander","source":"command"});spell_id=state["stack"][-1]["id"];state=perform_action(state,"bot",{"type":"cast","card_id":"command-denial","target_id":spell_id});state=perform_action(state,"player",{"type":"pass_priority"});state=perform_action(state,"bot",{"type":"pass_priority"});player=next(p for p in state["players"] if p["id"]=="player")
    assert any(card["instance_id"]=="test-commander" for card in player["graveyard"]) and not player["command"]
    assert {action["type"] for action in legal_actions(state,"player")}>={"move_commander","keep_commander"}
    kept=perform_action(state,"player",{"type":"keep_commander","card_id":"test-commander"});kept_player=next(p for p in kept["players"] if p["id"]=="player");assert any(card["instance_id"]=="test-commander" for card in kept_player["graveyard"])
    moved=perform_action(state,"player",{"type":"move_commander","card_id":"test-commander"});moved_player=next(p for p in moved["players"] if p["id"]=="player");assert any(card["instance_id"]=="test-commander" for card in moved_player["command"]) and not any(card["instance_id"]=="test-commander" for card in moved_player["graveyard"])


def test_bot_automatically_returns_a_dead_commander_to_the_command_zone():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    commander={**card(724,"Bot Commander","Legendary Creature — Warrior","","4","4"),"instance_id":"bot-commander","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False,"commander":True};bot["battlefield"].append(commander)
    removal={**card(725,"Commander Doom","Sorcery"),"oracle_text":"Destroy target creature.","instance_id":"commander-doom","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(removal)
    state=perform_action(state,"player",{"type":"cast","card_id":"commander-doom","target_id":"bot-commander"});state=perform_action(state,"player",{"type":"resolve"})
    state=run_bot(state,"expert",limit=5);bot=next(p for p in state["players"] if p["id"]=="bot")
    assert any(card["instance_id"]=="bot-commander" for card in bot["command"])


def test_commander_can_remain_in_graveyard_while_its_dies_trigger_uses_the_stack():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    commander={**card(726,"Last Gift Commander","Legendary Creature — Cleric","","2","2"),"oracle_text":"When Last Gift Commander dies, you gain 3 life.","instance_id":"last-gift","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False,"commander":True};bot["battlefield"].append(commander)
    removal={**card(727,"Last Gift Doom","Sorcery"),"oracle_text":"Destroy target creature.","instance_id":"last-gift-doom","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(removal);life=bot["life"]
    state=perform_action(state,"player",{"type":"cast","card_id":"last-gift-doom","target_id":"last-gift"});state=perform_action(state,"player",{"type":"resolve"})
    assert state["stack"][-1]["card"]["name"]=="Last Gift Commander trigger"
    state=perform_action(state,"bot",{"type":"keep_commander","card_id":"last-gift"});state=perform_action(state,"player",{"type":"resolve"});bot=next(p for p in state["players"] if p["id"]=="bot")
    assert bot["life"]==life+3 and any(card["instance_id"]=="last-gift" for card in bot["graveyard"])


def test_library_search_reveals_only_matching_cards_and_puts_chosen_land_onto_battlefield_tapped():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    forest={**card(728,"Forest","Basic Land — Forest"),"instance_id":"search-forest","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};island={**card(729,"Island","Basic Land — Island"),"instance_id":"search-island","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};creature={**card(730,"Search Bear","Creature — Bear","","2","2"),"instance_id":"search-bear","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["library"]=[creature,island,forest]
    ramp={**card(731,"Test Ramp","Sorcery"),"oracle_text":"Search your library for a basic land card, put it onto the battlefield tapped, then shuffle.","instance_id":"test-ramp","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(ramp)
    state=perform_action(state,"player",{"type":"cast","card_id":"test-ramp"});state=perform_action(state,"player",{"type":"resolve"});action=next(action for action in legal_actions(state,"player") if action["type"]=="search_library")
    assert set(action["card_ids"])=={"search-forest","search-island"} and action["max_amount"]==1 and action["destination"]=="battlefield"
    state=perform_action(state,"player",{"type":"search_library","card_ids":["search-forest"]});player=next(p for p in state["players"] if p["id"]=="player")
    found=next(card for card in player["battlefield"] if card["instance_id"]=="search-forest");assert found["tapped"] and not state.get("pending_library_search") and len(player["library"])==2


def test_library_search_can_put_a_tutored_card_in_hand_or_on_top_and_hides_choices_from_opponent():
    def setup(oracle:str):
        state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");aura={**card(732,"Search Aura","Enchantment — Aura"),"instance_id":"search-aura","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["library"]=[aura];spell={**card(733,"Test Tutor","Sorcery"),"oracle_text":oracle,"instance_id":"test-tutor","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(spell);state=perform_action(state,"player",{"type":"cast","card_id":"test-tutor"});return perform_action(state,"player",{"type":"resolve"})
    hand_state=setup("Search your library for an Aura card, reveal it, put it into your hand, then shuffle.");public=public_state(hand_state,"bot");assert public["pending_library_search"]["card_ids"]==[]
    hand_state=perform_action(hand_state,"player",{"type":"search_library","card_ids":["search-aura"]});player=next(p for p in hand_state["players"] if p["id"]=="player");assert any(card["instance_id"]=="search-aura" for card in player["hand"])
    top_state=setup("Search your library for an Aura card, reveal it, then shuffle and put that card on top.");top_state=perform_action(top_state,"player",{"type":"search_library","card_ids":["search-aura"]});player=next(p for p in top_state["players"] if p["id"]=="player");assert player["library"][-1]["instance_id"]=="search-aura"


def test_bot_completes_library_search_without_revealing_its_library_to_the_human():
    state=kept_game();state["active_player_id"]="bot";state["priority_player_id"]="bot";state["phase"]="precombat_main";bot=next(p for p in state["players"] if p["id"]=="bot")
    forest={**card(734,"Forest","Basic Land — Forest"),"instance_id":"bot-search-forest","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["library"]=[forest];ramp={**card(735,"Bot Ramp","Sorcery"),"oracle_text":"Search your library for a basic land card, put it onto the battlefield tapped, then shuffle.","instance_id":"bot-ramp","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["hand"].append(ramp)
    state=perform_action(state,"bot",{"type":"cast","card_id":"bot-ramp"});state=perform_action(state,"bot",{"type":"resolve"});state=run_bot(state,"expert",limit=4);bot=next(p for p in state["players"] if p["id"]=="bot")
    assert any(card["instance_id"]=="bot-search-forest" and card["tapped"] for card in bot["battlefield"])


def test_enter_the_battlefield_tutor_waits_for_its_trigger_and_basic_subtype_search_excludes_nonbasics():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    basic={**card(736,"Forest","Basic Land — Forest"),"instance_id":"basic-forest","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};nonbasic={**card(737,"Fancy Forest","Land — Forest"),"instance_id":"fancy-forest","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["library"]=[basic,nonbasic]
    guide={**card(738,"Forest Guide","Creature — Scout","","2","2"),"oracle_text":"When Forest Guide enters, search your library for a basic Forest card, reveal it, put it into your hand, then shuffle.","instance_id":"forest-guide","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(guide)
    state=perform_action(state,"player",{"type":"cast","card_id":"forest-guide"});state=perform_action(state,"player",{"type":"resolve"})
    assert not state.get("pending_library_search") and state["stack"][-1]["card"]["name"]=="Forest Guide trigger"
    state=perform_action(state,"player",{"type":"resolve"});action=next(action for action in legal_actions(state,"player") if action["type"]=="search_library")
    assert action["card_ids"]==["basic-forest"]


def test_static_and_conditional_combat_restrictions_control_attack_and_block_legality():
    state=kept_game();player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot");state["phase"]="combat"
    terror={**card(739,"Deep Terror","Creature — Serpent","","6","6"),"oracle_text":"Deep Terror can't attack unless there are seven or more cards in your graveyard.","instance_id":"deep-terror","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};crasher={**card(740,"Glacial Crasher","Creature — Elemental","","5","5"),"oracle_text":"Glacial Crasher can't attack unless there is a Mountain on the battlefield.","instance_id":"glacial-crasher","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};wall={**card(741,"Test Wall","Creature — Wall","","0","4"),"keywords":["Defender"],"instance_id":"test-wall","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"]=[terror,crasher,wall];player["graveyard"]=player["graveyard"][:6]
    assert not any(action["type"]=="declare_attackers" for action in legal_actions(state,"player"))
    while len(player["graveyard"])<7:player["graveyard"].append({**card(742+len(player["graveyard"]),"Spent Spell","Sorcery"),"instance_id":f"spent-{len(player['graveyard'])}"})
    mountain={**card(750,"Mountain","Basic Land — Mountain"),"instance_id":"condition-mountain","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["battlefield"].append(mountain)
    action=next(action for action in legal_actions(state,"player") if action["type"]=="declare_attackers");assert {"deep-terror","glacial-crasher"}.issubset(action["card_ids"]) and "test-wall" not in action["card_ids"]
    nonblocker={**card(751,"Vampire Interloper","Creature — Vampire","","2","1"),"oracle_text":"Flying\nThis creature can't block.","instance_id":"nonblocker","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["battlefield"].append(nonblocker);state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["deep-terror"]});assert not any(action["type"]=="declare_blockers" for action in legal_actions(state,"bot"))


def test_temporary_cant_block_effect_expires_at_the_next_turn_and_alone_restriction_is_enforced():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    ember={**card(752,"Ember Beast","Creature — Beast","","3","4"),"oracle_text":"Ember Beast can't attack or block alone.","instance_id":"ember-beast","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};ally={**card(753,"Ally","Creature — Soldier","","2","2"),"instance_id":"combat-ally","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};blocker={**card(754,"Target Blocker","Creature — Soldier","","2","2"),"instance_id":"target-blocker","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"].extend([ember,ally]);bot["battlefield"].append(blocker)
    spell={**card(755,"Stun Attack","Sorcery"),"oracle_text":"Target creature can't block this turn.","instance_id":"stun-attack","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(spell);cast=next(action for action in legal_actions(state,"player") if action.get("card_id")=="stun-attack");assert any(target["id"]=="target-blocker" for target in cast["targets"]);state=perform_action(state,"player",{"type":"cast","card_id":"stun-attack","target_id":"target-blocker"});state=perform_action(state,"player",{"type":"resolve"});state["phase"]="combat"
    try:perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["ember-beast"]});assert False
    except RuleViolation:pass
    state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["ember-beast","combat-ally"]});assert not any(action["type"]=="declare_blockers" for action in legal_actions(state,"bot"))
    state["phase"]="ending";state["active_player_id"]="player";state["priority_player_id"]="player";state["combat"]={"attackers":[],"blocks":{},"attack_targets":{},"block_orders":{},"damage_pending":False};state=perform_action(state,"player",{"type":"advance_phase"});bot=next(p for p in state["players"] if p["id"]=="bot");assert "cant_block_until_turn" not in next(card for card in bot["battlefield"] if card["instance_id"]=="target-blocker")


def test_power_four_companion_unlocks_conditional_attacking_and_blocking():
    state=kept_game();player=next(p for p in state["players"] if p["id"]=="player");state["phase"]="combat";tiger={**card(756,"Tiger-Dillo","Creature — Beast","","4","4"),"oracle_text":"Tiger-Dillo can't attack or block unless you control another creature with power 4 or greater.","instance_id":"tiger-dillo","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"]=[tiger]
    assert not any(action["type"]=="declare_attackers" for action in legal_actions(state,"player"))
    companion={**card(757,"Large Friend","Creature — Giant","","4","4"),"instance_id":"large-friend","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"].append(companion);action=next(action for action in legal_actions(state,"player") if action["type"]=="declare_attackers");assert "tiger-dillo" in action["card_ids"]


def test_spirit_only_blocking_restriction_and_bot_alone_attacks_are_enforced():
    state=kept_game();player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot");state["active_player_id"]="player";state["priority_player_id"]="player";state["phase"]="combat"
    spirit_attacker={**card(758,"Spirit Attacker","Creature — Spirit","","2","2"),"instance_id":"spirit-attacker","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"]=[spirit_attacker];spirit_blocker={**card(759,"Spirit Keeper","Token Creature — Spirit","","1","1"),"oracle_text":"Spirit Keeper can't block or be blocked by non-Spirit creatures.","instance_id":"spirit-blocker","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["battlefield"]=[spirit_blocker]
    state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["spirit-attacker"]});block=next(action for action in legal_actions(state,"bot") if action["type"]=="declare_blockers");assert block["legal_blocks"]["spirit-blocker"]==["spirit-attacker"]
    next(p for p in state["players"] if p["id"]=="player")["battlefield"][0]["type_line"]="Creature — Citizen";assert not any(action["type"]=="declare_blockers" for action in legal_actions(state,"bot"))
    fresh=kept_game();fresh["active_player_id"]="bot";fresh["priority_player_id"]="bot";fresh["phase"]="combat";enemy=next(p for p in fresh["players"] if p["id"]=="bot");enemy["battlefield"]=[{**card(760,"Bot Ember","Creature — Beast","","3","4"),"oracle_text":"Bot Ember can't attack or block alone.","instance_id":"bot-ember","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False}]
    choice=choose_bot_action(fresh,"expert");assert choice["type"]=="declare_attackers" and choice["attacker_ids"]==[]


def test_clue_and_food_tokens_have_functional_sacrifice_abilities():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    for index in range(2):player["battlefield"].append({**card(761+index,"Island","Basic Land — Island"),"instance_id":f"token-island-{index}","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False})
    maker={**card(763,"Evidence and Rations","Sorcery"),"oracle_text":"Create a Clue token. Create a Food token.","instance_id":"evidence-rations","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(maker);state=perform_action(state,"player",{"type":"cast","card_id":"evidence-rations"});state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");clue=next(card for card in player["battlefield"] if "Clue" in card["type_line"]);food=next(card for card in player["battlefield"] if "Food" in card["type_line"])
    before_hand=len(player["hand"]);clue_action=next(action for action in legal_actions(state,"player") if action.get("card_id")==clue["instance_id"]);state=perform_action(state,"player",{"type":"activate","card_id":clue["instance_id"],"ability_index":clue_action["ability_index"],"cost_card_ids":[]});assert not any(card["instance_id"]==clue["instance_id"] for card in next(p for p in state["players"] if p["id"]=="player")["battlefield"]);state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");assert len(player["hand"])==before_hand+1
    for permanent in player["battlefield"]:permanent["tapped"]=False
    before_life=player["life"];food_action=next(action for action in legal_actions(state,"player") if action.get("card_id")==food["instance_id"]);state=perform_action(state,"player",{"type":"activate","card_id":food["instance_id"],"ability_index":food_action["ability_index"],"cost_card_ids":[]});state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");assert player["life"]==before_life+3 and not any("Food" in card["type_line"] for card in player["battlefield"])


def test_treasure_produces_colored_mana_and_is_sacrificed_during_payment():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");player["battlefield"]=[]
    maker={**card(764,"Make Treasure","Sorcery"),"oracle_text":"Create a Treasure token.","instance_id":"make-treasure","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(maker);state=perform_action(state,"player",{"type":"cast","card_id":"make-treasure"});state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");treasure=next(card for card in player["battlefield"] if "Treasure" in card["type_line"])
    red_spell={**card(765,"Red Treasure Spell","Sorcery","{R}"),"oracle_text":"You gain 1 life.","instance_id":"red-treasure-spell","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(red_spell);assert any(action.get("card_id")=="red-treasure-spell" for action in legal_actions(state,"player"));state=perform_action(state,"player",{"type":"cast","card_id":"red-treasure-spell"});player=next(p for p in state["players"] if p["id"]=="player");assert not any(card["instance_id"]==treasure["instance_id"] for card in player["battlefield"])


def test_bot_uses_a_clue_when_it_has_mana_and_no_better_spell():
    state=kept_game();state["active_player_id"]="bot";state["priority_player_id"]="bot";state["phase"]="combat";bot=next(p for p in state["players"] if p["id"]=="bot");bot["hand"]=[];bot["battlefield"]=[]
    for index in range(2):bot["battlefield"].append({**card(766+index,"Island","Basic Land — Island"),"instance_id":f"bot-clue-island-{index}","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False})
    clue={**card(768,"Clue Token","Token Artifact — Clue"),"oracle_text":"{2}, Sacrifice this artifact: Draw a card.","mana_value":0,"instance_id":"bot-clue","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False,"token":True};bot["battlefield"].append(clue);choice=choose_bot_action(state,"expert");assert choice["type"]=="activate" and choice["card_id"]=="bot-clue"


def test_regeneration_spell_replaces_one_destruction_but_cant_regenerate_bypasses_it():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    creature={**card(769,"Regeneration Target","Creature — Thrull","","3","3"),"instance_id":"regen-target","owner_id":"player","controller_id":"player","tapped":False,"damage":2,"counters":{},"summoning_sick":False};player["battlefield"].append(creature);regen={**card(770,"Test Regeneration","Instant"),"oracle_text":"Regenerate target creature. Draw a card.","instance_id":"test-regeneration","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(regen);before=len(player["hand"])
    state=perform_action(state,"player",{"type":"cast","card_id":"test-regeneration","target_id":"regen-target"});state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");target=next(card for card in player["battlefield"] if card["instance_id"]=="regen-target");assert target["regeneration_shields"]==1 and len(player["hand"])==before
    doom={**card(771,"Test Doom","Sorcery"),"oracle_text":"Destroy target creature.","instance_id":"regen-doom","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(doom);state=perform_action(state,"player",{"type":"cast","card_id":"regen-doom","target_id":"regen-target"});state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");target=next(card for card in player["battlefield"] if card["instance_id"]=="regen-target");assert target["tapped"] and target["damage"]==0 and target["regeneration_shields"]==0
    dust={**card(772,"Flesh to Test","Sorcery"),"oracle_text":"Destroy target creature. It can't be regenerated.","instance_id":"flesh-test","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(dust);target["regeneration_shields"]=1;state=perform_action(state,"player",{"type":"cast","card_id":"flesh-test","target_id":"regen-target"});state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");assert not any(card["instance_id"]=="regen-target" for card in player["battlefield"])


def test_self_regeneration_and_aura_granted_sacrifice_regeneration_are_legal_abilities():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    swamp={**card(773,"Swamp","Basic Land — Swamp"),"instance_id":"regen-swamp","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};thrull={**card(774,"Dutiful Thrull","Creature — Thrull","","1","1"),"oracle_text":"{B}: Regenerate this creature.","instance_id":"dutiful-thrull","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"].extend([swamp,thrull]);ability=next(action for action in legal_actions(state,"player") if action.get("card_id")=="dutiful-thrull");state=perform_action(state,"player",{"type":"activate","card_id":"dutiful-thrull","ability_index":ability["ability_index"],"cost_card_ids":[]});state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");assert next(card for card in player["battlefield"] if card["instance_id"]=="dutiful-thrull")["regeneration_shields"]==1
    for permanent in player["battlefield"]:permanent["tapped"]=False
    aura={**card(775,"Consecrated Test","Enchantment — Aura"),"oracle_text":"Enchant creature\nEnchanted creature gets +2/+2 and has flying and \"Sacrifice two other creatures: Regenerate this creature.\"","instance_id":"consecrated-test","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};fodder=[{**card(776+i,f"Fodder {i}","Creature — Citizen","","1","1"),"instance_id":f"regen-fodder-{i}","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False} for i in range(2)];player["hand"].append(aura);player["battlefield"].extend(fodder);state=perform_action(state,"player",{"type":"cast","card_id":"consecrated-test","target_id":"dutiful-thrull"});state=perform_action(state,"player",{"type":"resolve"});granted=[action for action in legal_actions(state,"player") if action.get("card_id")=="dutiful-thrull" and action.get("cost_amount")==2][0];state=perform_action(state,"player",{"type":"activate","card_id":"dutiful-thrull","ability_index":granted["ability_index"],"cost_card_ids":["regen-fodder-0","regen-fodder-1"]});state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");assert not any(card["instance_id"].startswith("regen-fodder") for card in player["battlefield"]) and next(card for card in player["battlefield"] if card["instance_id"]=="dutiful-thrull")["regeneration_shields"]==2


def test_regeneration_removes_a_creature_from_combat_and_expires_next_turn():
    state=kept_game();player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot");state["phase"]="combat";attacker={**card(778,"Regenerating Attacker","Creature — Warrior","","3","3"),"instance_id":"regen-attacker","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False,"regeneration_shields":1};blocker={**card(779,"Large Blocker","Creature — Giant","","5","5"),"instance_id":"regen-blocker","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"].append(attacker);bot["battlefield"].append(blocker);state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["regen-attacker"]});state=perform_action(state,"bot",{"type":"declare_blockers","blocks":{"regen-blocker":"regen-attacker"}});state=perform_action(state,"player",{"type":"resolve_combat_damage"});player=next(p for p in state["players"] if p["id"]=="player");survivor=next(card for card in player["battlefield"] if card["instance_id"]=="regen-attacker");assert survivor["tapped"] and survivor["damage"]==0 and "regen-attacker" not in state["combat"]["attackers"]
    survivor["regeneration_shields"]=1;state["phase"]="ending";state["priority_player_id"]="player";state=perform_action(state,"player",{"type":"advance_phase"});survivor=next(card for card in next(p for p in state["players"] if p["id"]=="player")["battlefield"] if card["instance_id"]=="regen-attacker");assert "regeneration_shields" not in survivor


def test_basic_landcycling_discards_as_a_cost_and_searches_on_resolution():
    state=kept_game();player=next(p for p in state["players"] if p["id"]=="player");player["battlefield"]=[]
    for index in range(2):player["battlefield"].append({**card(780+index,"Island","Basic Land — Island"),"instance_id":f"cycling-mana-{index}","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False})
    target={**card(782,"Plains","Basic Land — Plains"),"instance_id":"cycling-target","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};cycler={**card(783,"Distant Route","Sorcery","{5}{W}"),"oracle_text":"Basic landcycling {2}","instance_id":"basic-landcycler","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["library"].append(target);player["hand"].append(cycler)
    cycle=next(action for action in legal_actions(state,"player") if action["type"]=="cycle" and action["card_id"]=="basic-landcycler");assert cycle["mana_cost"]=="{2}";state=perform_action(state,"player",cycle);player=next(p for p in state["players"] if p["id"]=="player");assert cycler in player["graveyard"] and cycler not in player["hand"] and all(card["tapped"] for card in player["battlefield"])
    state=perform_action(state,"player",{"type":"resolve"});search=next(action for action in legal_actions(state,"player") if action["type"]=="search_library");assert "cycling-target" in search["card_ids"];state=perform_action(state,"player",{"type":"search_library","card_ids":["cycling-target"]});player=next(p for p in state["players"] if p["id"]=="player");assert target in player["hand"]


def test_cycling_triggers_stack_above_the_cycling_ability_and_bot_cycles_when_mana_starved():
    state=kept_game();player=next(p for p in state["players"] if p["id"]=="player");player["battlefield"]=[]
    watcher={**card(784,"Cycle Watcher","Creature — Human","","2","2"),"oracle_text":"Whenever you cycle a card, you gain 1 life.","instance_id":"cycle-watcher","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};mana={**card(785,"Island","Basic Land — Island"),"instance_id":"cycle-mana","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};cycler={**card(786,"Plain Cycler","Creature — Bird","{5}{U}","4","4"),"oracle_text":"Cycling {1}","instance_id":"plain-cycler","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"].extend([watcher,mana]);player["hand"].append(cycler);life=player["life"]
    state=perform_action(state,"player",{"type":"cycle","card_id":"plain-cycler"});assert len(state["stack"])==2 and "trigger" in state["stack"][-1]["card"]["name"].casefold();state=perform_action(state,"player",{"type":"resolve"});assert next(p for p in state["players"] if p["id"]=="player")["life"]==life+1
    bot_state=kept_game();bot_state["active_player_id"]="bot";bot_state["priority_player_id"]="bot";bot=next(p for p in bot_state["players"] if p["id"]=="bot");bot["battlefield"]=[{**card(787,"Swamp","Basic Land — Swamp"),"instance_id":"bot-cycle-mana","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False}];bot["hand"]=[{**card(788,"Bot Route","Sorcery","{6}{B}"),"oracle_text":"Swampcycling {1}","instance_id":"bot-route","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False}];choice=choose_bot_action(bot_state,"expert");assert choice["type"]=="cycle" and choice["card_id"]=="bot-route"


def test_flashback_uses_its_alternate_cost_and_exiles_after_resolution():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");player["battlefield"]=[{**card(789,"Mountain","Basic Land — Mountain"),"instance_id":"flashback-mountain","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False}]
    spell={**card(790,"Remembered Spark","Sorcery","{5}{R}"),"oracle_text":"You gain 2 life.\nFlashback {R}","instance_id":"remembered-spark","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["graveyard"].append(spell);life=player["life"];action=next(action for action in legal_actions(state,"player") if action.get("source")=="flashback");assert action["label"].startswith("Flashback") and "{R}" in action["label"] and "{5}{R}" not in action["label"]
    state=perform_action(state,"player",action);player=next(p for p in state["players"] if p["id"]=="player");assert spell not in player["graveyard"] and state["stack"][-1]["flashback"];state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");assert spell in player["exile"] and player["life"]==life+2


def test_countered_flashback_is_exiled_and_behold_is_a_validated_additional_cost():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot");player["battlefield"]=[{**card(791,"Mountain","Basic Land — Mountain"),"instance_id":"behold-mountain-0","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False},{**card(792,"Mountain","Basic Land — Mountain"),"instance_id":"behold-mountain-1","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False}]
    elements=[{**card(793+i,f"Element {i}","Creature — Elemental","","1","1"),"instance_id":f"behold-element-{i}","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False} for i in range(3)];player["hand"].extend(elements[:2]);player["battlefield"].append(elements[2]);spell={**card(796,"Kindled Memory","Sorcery","{3}{R}"),"oracle_text":"Draw a card.\nFlashback—{1}{R}, Behold three Elementals.","instance_id":"kindled-memory","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["graveyard"].append(spell);action=next(action for action in legal_actions(state,"player") if action.get("card_id")=="kindled-memory");assert action["cost_kind"]=="behold" and action["cost_amount"]==3 and len(action["cost_options"])==3
    with pytest.raises(RuleViolation):perform_action(state,"player",{"type":"cast","card_id":"kindled-memory","source":"flashback"})
    state=perform_action(state,"player",{**action,"cost_card_ids":[element["instance_id"] for element in elements]});assert all(any(element in zone for zone in (next(p for p in state["players"] if p["id"]=="player")["hand"],next(p for p in state["players"] if p["id"]=="player")["battlefield"])) for element in elements)
    counter={**card(797,"Memory Denial","Instant",""),"oracle_text":"Counter target spell.","instance_id":"memory-denial","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot=next(p for p in state["players"] if p["id"]=="bot");bot["hand"].append(counter);state["priority_player_id"]="bot";state=perform_action(state,"bot",{"type":"cast","card_id":"memory-denial","target_id":state["stack"][-1]["id"]});state=perform_action(state,"bot",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");assert spell in player["exile"] and spell not in player["graveyard"]


def test_expert_bot_casts_available_flashback_spells_from_its_graveyard():
    state=kept_game();state["active_player_id"]="bot";state["priority_player_id"]="bot";state["phase"]="precombat_main";bot=next(p for p in state["players"] if p["id"]=="bot");bot["hand"]=[];bot["battlefield"]=[{**card(798,"Mountain","Basic Land — Mountain"),"instance_id":"bot-flashback-mana","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False}];spell={**card(799,"Bot Memory","Sorcery","{5}{R}"),"oracle_text":"Draw a card.\nFlashback {R}","instance_id":"bot-memory","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["graveyard"].append(spell);choice=choose_bot_action(state,"expert");assert choice["type"]=="cast" and choice["card_id"]=="bot-memory" and choice["source"]=="flashback"


def test_kicker_offers_both_costs_and_replaces_the_base_effect_when_paid():
    def setup():
        state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");player["battlefield"]=[{**card(800+i,"Island","Basic Land — Island"),"instance_id":f"kicker-island-{i}","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False} for i in range(3)];spell={**card(803,"Deep Study","Sorcery","{U}"),"oracle_text":"Kicker {2}\nDraw a card. If this spell was kicked, draw three cards instead.","instance_id":"deep-study","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(spell);return state,spell
    state,spell=setup();actions=[action for action in legal_actions(state,"player") if action.get("card_id")=="deep-study"];assert len(actions)==2 and {bool(action.get("kicked")) for action in actions}=={False,True};player=next(p for p in state["players"] if p["id"]=="player");before=len(player["hand"]);state=perform_action(state,"player",next(action for action in actions if not action.get("kicked")));state=perform_action(state,"player",{"type":"resolve"});assert len(next(p for p in state["players"] if p["id"]=="player")["hand"])==before
    state,spell=setup();action=next(action for action in legal_actions(state,"player") if action.get("card_id")=="deep-study" and action.get("kicked"));player=next(p for p in state["players"] if p["id"]=="player");before=len(player["hand"]);state=perform_action(state,"player",action);assert state["stack"][-1]["kicked"];state=perform_action(state,"player",{"type":"resolve"});assert len(next(p for p in state["players"] if p["id"]=="player")["hand"])==before+2


def test_kicked_enter_trigger_only_happens_when_the_creature_was_kicked():
    def setup():
        state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");player["battlefield"]=[{**card(804+i,"Forest","Basic Land — Forest"),"instance_id":f"kicker-forest-{i}","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False} for i in range(2)];creature={**card(806,"Renewal Beast","Creature — Beast","{G}","2","2"),"oracle_text":"Kicker {1}\nWhen Renewal Beast enters, if it was kicked, you gain 3 life.","instance_id":"renewal-beast","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(creature);return state
    state=setup();player=next(p for p in state["players"] if p["id"]=="player");life=player["life"];normal=next(action for action in legal_actions(state,"player") if action.get("card_id")=="renewal-beast" and not action.get("kicked"));state=perform_action(state,"player",normal);state=perform_action(state,"player",{"type":"resolve"});assert not state["stack"] and next(p for p in state["players"] if p["id"]=="player")["life"]==life
    state=setup();player=next(p for p in state["players"] if p["id"]=="player");life=player["life"];kicked=next(action for action in legal_actions(state,"player") if action.get("card_id")=="renewal-beast" and action.get("kicked"));state=perform_action(state,"player",kicked);state=perform_action(state,"player",{"type":"resolve"});assert state["stack"] and "trigger" in state["stack"][-1]["card"]["name"].casefold();state=perform_action(state,"player",{"type":"resolve"});assert next(p for p in state["players"] if p["id"]=="player")["life"]==life+3


def test_expert_bot_pays_kicker_when_the_enhanced_cast_is_affordable():
    state=kept_game();state["active_player_id"]="bot";state["priority_player_id"]="bot";state["phase"]="precombat_main";bot=next(p for p in state["players"] if p["id"]=="bot");bot["hand"]=[];bot["land_plays_remaining"]=0;bot["battlefield"]=[{**card(807+i,"Mountain","Basic Land — Mountain"),"instance_id":f"bot-kicker-land-{i}","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False} for i in range(3)];spell={**card(810,"Bot Kicker","Sorcery","{R}"),"oracle_text":"Kicker {2}\nDraw a card. If this spell was kicked, draw three cards instead.","instance_id":"bot-kicker","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["hand"].append(spell);choice=choose_bot_action(state,"expert");assert choice["type"]=="cast" and choice["card_id"]=="bot-kicker" and choice["kicked"]


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


def test_static_lord_token_and_opponent_stat_effects_update_dynamically():
    state=kept_game();player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot");player["battlefield"]=[];bot["battlefield"]=[]
    lord={**card(635,"Elf Captain","Creature — Elf","","2","2"),"oracle_text":"Other Elf creatures you control get +1/+1.","instance_id":"elf-captain","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};elf={**card(636,"Elf Friend","Creature — Elf","","2","2"),"instance_id":"elf-friend","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};human={**card(637,"Human Friend","Creature — Human","","2","2"),"instance_id":"human-friend","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};anthem={**card(638,"Token Banner","Artifact"),"oracle_text":"Creature tokens you control get +1/+1.","instance_id":"token-banner","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};token={**card(639,"Soldier Token","Token Creature — Soldier","","1","1"),"instance_id":"soldier-token","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False,"token":True};curse={**card(640,"Weakening Field","Enchantment"),"oracle_text":"Creatures your opponents control get -1/-1.","instance_id":"weakening-field","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"].extend([lord,elf,human,anthem,token]);bot["battlefield"].append(curse)
    visible=public_state(state);cards={card["instance_id"]:card for card in next(p for p in visible["players"] if p["id"]=="player")["battlefield"]};assert (cards["elf-friend"]["effective_power"],cards["elf-friend"]["effective_toughness"])==(2,2) and (cards["human-friend"]["effective_power"],cards["human-friend"]["effective_toughness"])==(1,1) and (cards["soldier-token"]["effective_power"],cards["soldier-token"]["effective_toughness"])==(1,1)
    player["battlefield"].remove(lord);visible=public_state(state);elf_view=next(card for card in next(p for p in visible["players"] if p["id"]=="player")["battlefield"] if card["instance_id"]=="elf-friend");assert (elf_view["effective_power"],elf_view["effective_toughness"])==(1,1)
    curse["oracle_text"]="Creatures your opponents control get -2/-2.";state=perform_action(state,"bot",{"type":"adjust_life","amount":0});player=next(p for p in state["players"] if p["id"]=="player");assert all(card["instance_id"] not in {"elf-friend","human-friend","soldier-token"} for card in player["battlefield"])


def test_first_strike_kills_before_retaliation_and_double_strike_hits_twice():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    striker={**card(640,"First Striker","Creature — Knight","","2","2"),"keywords":["First strike"],"instance_id":"striker","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};double={**card(641,"Double Striker","Creature — Knight","","2","2"),"keywords":["Double strike"],"instance_id":"double","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};blocker={**card(642,"Blocker","Creature — Bear","","2","2"),"instance_id":"blocker","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"].extend([striker,double]);bot["battlefield"].append(blocker)
    state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["striker","double"]});state=perform_action(state,"bot",{"type":"declare_blockers","blocks":{"blocker":"striker"}});state=deal_combat_damage(state);player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    assert any(item["instance_id"]=="striker" and item["damage"]==0 for item in player["battlefield"])
    assert any(item["instance_id"]=="blocker" for item in bot["graveyard"])
    assert bot["life"]==16


def test_infect_wither_toxic_and_poison_loss_are_enforced():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    infect={**card(650,"Infecter","Creature — Horror","","3","3"),"keywords":["Infect"],"instance_id":"infect","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};toxic={**card(651,"Toxic","Creature — Phyrexian","","1","1"),"oracle_text":"Toxic 2","keywords":["Toxic"],"instance_id":"toxic","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};durable={**card(652,"Durable","Creature — Golem","","3","3"),"keywords":["Indestructible"],"instance_id":"durable","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"].extend([infect,toxic]);bot["battlefield"].append(durable)
    state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["infect","toxic"]});state=perform_action(state,"bot",{"type":"declare_blockers","blocks":{"durable":"infect"}});state=deal_combat_damage(state);bot=next(p for p in state["players"] if p["id"]=="bot")
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


def test_first_player_skips_draw_then_upkeep_triggers_resolve_before_later_draws():
    state=kept_game();player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot");player_hand,len_before=len(player["hand"]),len(player["library"])
    state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    assert state["phase"]=="precombat_main" and len(player["hand"])==player_hand and len(player["library"])==len_before and state["first_turn_draw_skipped"] and not state["beginning_draw_pending"];bot=next(p for p in state["players"] if p["id"]=="bot")
    upkeep={**card(940,"Upkeep Scholar","Creature — Wizard","","1","1"),"oracle_text":"At the beginning of your upkeep, draw a card.","instance_id":"upkeep-scholar","owner_id":"bot","controller_id":"bot","tapped":True,"damage":0,"counters":{},"summoning_sick":True};bot["battlefield"].append(upkeep);bot_hand=len(bot["hand"]);bot_library=len(bot["library"]);state["phase"]="ending"
    state=perform_action(state,"player",{"type":"advance_phase"});bot=next(p for p in state["players"] if p["id"]=="bot")
    assert state["turn"]==2 and state["phase"]=="beginning" and state["beginning_draw_pending"] and not bot["battlefield"][0]["tapped"] and len(bot["hand"])==bot_hand and state["stack"][-1]["card"]["name"]=="Upkeep Scholar trigger"
    state=perform_action(state,"bot",{"type":"resolve"});bot=next(p for p in state["players"] if p["id"]=="bot");assert len(bot["hand"])==bot_hand+1 and len(bot["library"])==bot_library-1 and state["phase"]=="beginning"
    state=perform_action(state,"bot",{"type":"advance_phase"});bot=next(p for p in state["players"] if p["id"]=="bot");assert state["phase"]=="precombat_main" and len(bot["hand"])==bot_hand+2 and len(bot["library"])==bot_library-2 and not state["beginning_draw_pending"]


def test_empty_library_loss_waits_until_draw_after_upkeep_resolves():
    state=kept_game();player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot");upkeep={**card(941,"Last Upkeep","Enchantment"),"oracle_text":"At the beginning of your upkeep, you gain 3 life.","instance_id":"last-upkeep","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["battlefield"].append(upkeep);bot["library"]=[];bot_life=bot["life"];state["phase"]="ending"
    state=perform_action(state,"player",{"type":"advance_phase"});assert state["status"]=="active" and state["stack"]
    state=perform_action(state,"bot",{"type":"resolve"});assert next(p for p in state["players"] if p["id"]=="bot")["life"]==bot_life+3 and state["status"]=="active"
    state=perform_action(state,"bot",{"type":"advance_phase"});assert state["status"]=="complete" and state["winner_id"]=="player"


def test_bot_discards_automatically_and_no_maximum_hand_size_is_honored():
    state=kept_game();bot=next(p for p in state["players"] if p["id"]=="bot");state["active_player_id"]="bot";state["priority_player_id"]="bot";state["phase"]="ending"
    while len(bot["hand"])<9:bot["hand"].append(bot["library"].pop())
    state=perform_action(state,"bot",{"type":"advance_phase"});choice=choose_bot_action(state,"expert")
    assert choice["type"]=="discard_cards" and len(choice["card_ids"])==2
    state=perform_action(state,"bot",choice);assert len(next(p for p in state["players"] if p["id"]=="bot")["hand"])==7
    state=kept_game();player=next(p for p in state["players"] if p["id"]=="player");player["battlefield"].append({**card(660,"Spellbook","Artifact"),"oracle_text":"You have no maximum hand size.","instance_id":"spellbook","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False})
    while len(player["hand"])<9:player["hand"].append(player["library"].pop())
    state["phase"]="ending";state=perform_action(state,"player",{"type":"advance_phase"});assert state["turn"]==2 and state.get("pending_discard") is None


def test_scry_reveals_only_the_top_cards_and_persists_ordered_top_bottom_choices():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    spell={**card(800,"Clear the Mind","Sorcery"),"oracle_text":"Scry 3.","instance_id":"scry-spell","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(spell);expected=[card["instance_id"] for card in reversed(player["library"][-3:])]
    state=perform_action(state,"player",{"type":"cast","card_id":"scry-spell"});state=perform_action(state,"player",{"type":"resolve"});action=legal_actions(state,"player")[0]
    assert action["type"]=="scry" and action["card_ids"]==expected and [card["instance_id"] for card in action["cards"]]==expected and legal_actions(state,"bot")==[]
    state=perform_action(state,"player",{"type":"scry","top_ids":[expected[1]],"bottom_ids":[expected[2],expected[0]]});player=next(p for p in state["players"] if p["id"]=="player")
    assert player["library"][-1]["instance_id"]==expected[1] and player["library"][0]["instance_id"]==expected[0] and state.get("pending_scry") is None


def test_bot_makes_and_completes_scry_decisions():
    state=kept_game();bot=next(p for p in state["players"] if p["id"]=="bot");state["active_player_id"]="bot";state["priority_player_id"]="bot";ids=[card["instance_id"] for card in reversed(bot["library"][-2:])];state["pending_scry"]={"player_id":"bot","amount":2,"card_ids":ids}
    choice=choose_bot_action(state,"expert");assert choice["type"]=="scry" and set(choice["top_ids"]+choice["bottom_ids"])==set(ids)
    state=perform_action(state,"bot",choice);assert state.get("pending_scry") is None


def test_surveil_orders_kept_cards_and_moves_selected_cards_to_graveyard():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    spell={**card(810,"Unexplained Vision","Sorcery"),"oracle_text":"Surveil 2.","instance_id":"surveil-spell","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(spell);expected=[card["instance_id"] for card in reversed(player["library"][-2:])]
    state=perform_action(state,"player",{"type":"cast","card_id":"surveil-spell"});state=perform_action(state,"player",{"type":"resolve"});action=legal_actions(state,"player")[0];assert action["type"]=="surveil" and action["card_ids"]==expected
    state=perform_action(state,"player",{"type":"surveil","top_ids":[expected[1]],"graveyard_ids":[expected[0]]});player=next(p for p in state["players"] if p["id"]=="player")
    assert player["library"][-1]["instance_id"]==expected[1] and player["graveyard"][-1]["instance_id"]==expected[0] and state.get("pending_scry") is None


def test_landfall_cast_and_end_step_triggers_use_the_stack():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    landfall={**card(820,"Life Gardener","Creature — Druid","","1","1"),"oracle_text":"Landfall — Whenever a land enters the battlefield under your control, you gain 1 life.","instance_id":"life-gardener","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False}
    scholar={**card(821,"Spell Scholar","Creature — Wizard","","1","1"),"oracle_text":"Whenever you cast a noncreature spell, draw a card.","instance_id":"spell-scholar","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False}
    reveler={**card(822,"Dusk Reveler","Creature — Bard","","1","1"),"oracle_text":"At the beginning of your end step, you gain 2 life.","instance_id":"dusk-reveler","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False}
    land={**card(823,"Forest","Basic Land — Forest"),"instance_id":"trigger-land","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False}
    spell={**card(824,"Quiet Thought","Sorcery"),"instance_id":"quiet-thought","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False}
    player["battlefield"].extend([landfall,scholar,reveler]);player["hand"].extend([land,spell]);starting_life=player["life"]
    state=perform_action(state,"player",{"type":"play_land","card_id":"trigger-land"});assert state["stack"][-1]["card"]["name"]=="Life Gardener trigger"
    state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");assert player["life"]==starting_life+1
    before=len(player["hand"]);state=perform_action(state,"player",{"type":"cast","card_id":"quiet-thought"});assert [item["card"]["name"] for item in state["stack"][-2:]]==["Quiet Thought","Spell Scholar trigger"]
    state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");assert len(player["hand"])==before
    state=perform_action(state,"player",{"type":"resolve"});state["phase"]="postcombat_main";state=perform_action(state,"player",{"type":"advance_phase"});assert state["phase"]=="ending" and state["stack"][-1]["card"]["name"]=="Dusk Reveler trigger"
    state=perform_action(state,"player",{"type":"resolve"});assert next(p for p in state["players"] if p["id"]=="player")["life"]==starting_life+3


def test_attack_triggers_count_attackers_once_or_individually_and_choose_targets():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    banner={**card(830,"Battle Banner","Enchantment"),"oracle_text":"Whenever one or more creatures you control attack, you gain 1 life.","instance_id":"battle-banner","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False}
    mentor={**card(831,"Attack Mentor","Creature — Soldier","","2","2"),"oracle_text":"Whenever a creature you control attacks, you gain 1 life.","instance_id":"attack-mentor","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False}
    raider={**card(832,"Target Raider","Creature — Warrior","","2","2"),"oracle_text":"Whenever Target Raider attacks, tap target creature.","instance_id":"target-raider","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False}
    ally={**card(833,"Ally Attacker","Creature — Soldier","","2","2"),"instance_id":"ally-attacker","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False}
    blocker={**card(834,"Resting Blocker","Creature — Beast","","3","3"),"instance_id":"resting-blocker","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False}
    player["battlefield"].extend([banner,mentor,raider,ally]);bot["battlefield"].append(blocker);starting_life=player["life"]
    state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["target-raider","ally-attacker"]});action=legal_actions(state,"player")[0]
    assert action["type"]=="choose_trigger_target" and any(target["id"]=="resting-blocker" for target in action["targets"])
    state=perform_action(state,"player",{"type":"choose_trigger_target","target_id":"resting-blocker"});assert len(state["stack"])==4
    while state["stack"]:state=perform_action(state,"player",{"type":"resolve"})
    player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    assert player["life"]==starting_life+3 and next(card for card in bot["battlefield"] if card["instance_id"]=="resting-blocker")["tapped"]


def test_combat_damage_to_player_triggers_after_unblocked_damage():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    infiltrator={**card(840,"Lore Infiltrator","Creature — Rogue","","2","2"),"oracle_text":"Whenever Lore Infiltrator deals combat damage to a player, draw a card.","instance_id":"lore-infiltrator","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"].append(infiltrator);before=len(player["hand"])
    state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["lore-infiltrator"]});state=perform_action(state,"bot",{"type":"advance_phase"});state=perform_action(state,"player",{"type":"resolve_combat_damage"})
    assert state["stack"][-1]["card"]["name"]=="Lore Infiltrator trigger" and next(p for p in state["players"] if p["id"]=="bot")["life"]==18
    state=perform_action(state,"player",{"type":"resolve"});assert len(next(p for p in state["players"] if p["id"]=="player")["hand"])==before+1


def test_prowess_cast_trigger_applies_to_its_source_until_end_of_turn():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    mage={**card(850,"Prowess Mage","Creature — Wizard","","2","2"),"oracle_text":"Prowess (Whenever you cast a noncreature spell, this creature gets +1/+1 until end of turn.)","keywords":["Prowess"],"instance_id":"prowess-mage","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False}
    spell={**card(851,"Practice Spell","Sorcery"),"instance_id":"practice-spell","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"].append(mage);player["hand"].append(spell)
    state=perform_action(state,"player",{"type":"cast","card_id":"practice-spell"});assert state["stack"][-1]["card"]["name"]=="Prowess Mage trigger"
    state=perform_action(state,"player",{"type":"resolve"});mage=next(card for card in next(p for p in state["players"] if p["id"]=="player")["battlefield"] if card["instance_id"]=="prowess-mage")
    assert (mage["temporary_power"],mage["temporary_toughness"])==(1,1) and (public_state(state)["players"][0]["battlefield"][-1]["effective_power"],public_state(state)["players"][0]["battlefield"][-1]["effective_toughness"])==(3,3)


def test_shield_counters_prevent_damage_and_destroy_effects_once():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    shielded={**card(860,"Shielded Guard","Creature — Soldier","","2","2"),"instance_id":"shielded-guard","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{"shield":1},"summoning_sick":False}
    bolt={**card(861,"Test Bolt","Instant","{R}"),"oracle_text":"Test Bolt deals 3 damage to target creature.","instance_id":"test-bolt","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False}
    doom={**card(862,"Test Doom","Sorcery"),"oracle_text":"Destroy target creature.","instance_id":"test-doom","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};mountain={**card(863,"Mountain","Basic Land — Mountain"),"instance_id":"mountain","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False}
    bot["battlefield"].append(shielded);player["battlefield"].append(mountain);player["hand"].extend([bolt,doom])
    state=perform_action(state,"player",{"type":"cast","card_id":"test-bolt","target_id":"shielded-guard"});state=perform_action(state,"player",{"type":"resolve"});bot=next(p for p in state["players"] if p["id"]=="bot");guard=next(card for card in bot["battlefield"] if card["instance_id"]=="shielded-guard")
    assert guard["damage"]==0 and guard["counters"]["shield"]==0
    state=perform_action(state,"player",{"type":"cast","card_id":"test-doom","target_id":"shielded-guard"});state=perform_action(state,"player",{"type":"resolve"});bot=next(p for p in state["players"] if p["id"]=="bot")
    assert any(card["instance_id"]=="shielded-guard" for card in bot["graveyard"])


def test_shield_counter_replaces_board_wipe_destruction_but_not_exile():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    guard={**card(870,"Wipe Guard","Creature — Soldier","","2","2"),"instance_id":"wipe-guard","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{"shield":1},"summoning_sick":False};victim={**card(871,"Wipe Victim","Creature — Citizen","","2","2"),"instance_id":"wipe-victim","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};wipe={**card(872,"Test Wrath","Sorcery"),"oracle_text":"Destroy all creatures.","instance_id":"test-wrath","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};exile={**card(873,"Test Farewell","Sorcery"),"oracle_text":"Exile all creatures.","instance_id":"test-farewell","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False}
    bot["battlefield"].extend([guard,victim]);player["hand"].extend([wipe,exile]);state=perform_action(state,"player",{"type":"cast","card_id":"test-wrath"});state=perform_action(state,"player",{"type":"resolve"});bot=next(p for p in state["players"] if p["id"]=="bot")
    surviving_guard=next(card for card in bot["battlefield"] if card["instance_id"]=="wipe-guard");assert [card["instance_id"] for card in bot["battlefield"]]==["wipe-guard"] and surviving_guard["counters"]["shield"]==0 and any(card["instance_id"]=="wipe-victim" for card in bot["graveyard"])
    state=perform_action(state,"player",{"type":"cast","card_id":"test-farewell"});state=perform_action(state,"player",{"type":"resolve"});bot=next(p for p in state["players"] if p["id"]=="bot");assert not bot["battlefield"] and any(card["instance_id"]=="wipe-guard" for card in bot["exile"])


def test_protection_prevents_matching_combat_damage_and_illegal_blocks_not_shroud():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    red={**card(880,"Red Attacker","Creature — Warrior","{R}","3","3"),"instance_id":"red-attacker","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};protected={**card(881,"White Defender","Creature — Knight","{W}","2","2"),"oracle_text":"Protection from red","instance_id":"white-defender","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};green={**card(882,"Green Blocker","Creature — Elf","{G}","2","2"),"instance_id":"green-blocker","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};protected_attacker={**card(883,"Protected Attacker","Creature — Knight","{W}","2","2"),"oracle_text":"Protection from green","instance_id":"protected-attacker","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};shrouded={**card(884,"Shrouded Blocker","Creature — Beast","{G}","2","2"),"keywords":["Shroud"],"instance_id":"shrouded-blocker","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False}
    player["battlefield"].extend([red,protected_attacker]);bot["battlefield"].extend([protected,green,shrouded]);state=perform_action(state,"player",{"type":"declare_attackers","attacker_ids":["red-attacker","protected-attacker"]});block_action=next(action for action in legal_actions(state,"bot") if action["type"]=="declare_blockers")
    assert "red-attacker" in block_action["legal_blocks"]["white-defender"] and "protected-attacker" not in block_action["legal_blocks"]["green-blocker"] and "red-attacker" in block_action["legal_blocks"]["shrouded-blocker"]
    state=perform_action(state,"bot",{"type":"declare_blockers","blocks":{"white-defender":"red-attacker"}});state=deal_combat_damage(state);player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    assert next(card for card in bot["battlefield"] if card["instance_id"]=="white-defender")["damage"]==0 and any(card["instance_id"]=="red-attacker" and card["damage"]==2 for card in player["battlefield"])


def test_expert_bot_uses_continuous_effects_and_counters_for_combat_decisions():
    state=kept_game();state["active_player_id"]="bot";state["priority_player_id"]="bot";state["phase"]="combat";bot=next(p for p in state["players"] if p["id"]=="bot");enemy=next(p for p in state["players"] if p["id"]=="player");bot["battlefield"]=[];enemy["battlefield"]=[]
    goblin={**card(890,"Small Goblin","Creature — Goblin","","2","2"),"instance_id":"small-goblin","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};lord={**card(891,"Goblin Lord","Creature — Goblin","","2","2"),"oracle_text":"Other Goblin creatures you control get +3/+3.","instance_id":"goblin-lord","owner_id":"bot","controller_id":"bot","tapped":True,"damage":0,"counters":{},"summoning_sick":False};wall={**card(892,"Four Four","Creature — Giant","","4","4"),"instance_id":"four-four","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["battlefield"].extend([goblin,lord]);enemy["battlefield"].append(wall)
    choice=choose_bot_action(state,"expert");assert choice["type"]=="declare_attackers" and choice["attacker_ids"]==["small-goblin"]
    curse={**card(893,"Goblin Curse","Enchantment"),"oracle_text":"Creatures your opponents control get -4/-4.","instance_id":"goblin-curse","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};enemy["battlefield"].append(curse)
    choice=choose_bot_action(state,"expert");assert choice["type"]=="declare_attackers" and choice["attacker_ids"]==[]

    state=kept_game();bot=next(p for p in state["players"] if p["id"]=="bot");enemy=next(p for p in state["players"] if p["id"]=="player");attacker={**card(894,"Counter Threat","Creature — Beast","","4","4"),"instance_id":"counter-threat","owner_id":"player","controller_id":"player","tapped":True,"damage":0,"counters":{},"summoning_sick":False};small={**card(895,"Small Blocker","Creature — Citizen","","2","2"),"instance_id":"small-blocker","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};grown={**card(896,"Grown Blocker","Creature — Citizen","","2","2"),"instance_id":"grown-blocker","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{"+1/+1":3},"summoning_sick":False};enemy["battlefield"]=[attacker];bot["battlefield"]=[small,grown];state["combat"]={"attackers":["counter-threat"],"blocks":{}}
    action={"type":"declare_blockers","card_ids":["small-blocker","grown-blocker"],"legal_blocks":{"small-blocker":["counter-threat"],"grown-blocker":["counter-threat"]}}
    assert _choose_blocks(state,action,"expert")=={"grown-blocker":"counter-threat"}


def test_expert_bot_avoids_wasting_destroy_spells_on_resilient_threats():
    state=kept_game();state["active_player_id"]="bot";state["priority_player_id"]="bot";state["phase"]="precombat_main";bot=next(p for p in state["players"] if p["id"]=="bot");enemy=next(p for p in state["players"] if p["id"]=="player");bot["hand"]=[];bot["land_plays_remaining"]=0;enemy["battlefield"]=[]
    indestructible={**card(900,"Immortal Giant","Creature — Giant","","9","9"),"keywords":["Indestructible"],"instance_id":"immortal-giant","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};shielded={**card(901,"Shielded Dragon","Creature — Dragon","","8","8"),"instance_id":"shielded-dragon","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{"shield":1},"summoning_sick":False};vulnerable={**card(902,"Vulnerable Angel","Creature — Angel","","5","5"),"instance_id":"vulnerable-angel","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};removal={**card(903,"Bot Doom","Sorcery"),"oracle_text":"Destroy target creature.","instance_id":"bot-doom","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False}
    enemy["battlefield"].extend([indestructible,shielded,vulnerable]);bot["hand"].append(removal);choice=choose_bot_action(state,"expert")
    assert choice["type"]=="cast" and choice["target_id"]=="vulnerable-angel"


def test_activated_abilities_pay_source_sacrifice_life_and_counter_costs():
    state=kept_game();player=next(p for p in state["players"] if p["id"]=="player")
    martyr={**card(910,"Test Martyr","Creature — Cleric","","1","1"),"oracle_text":"Sacrifice Test Martyr: Draw two cards.\nWhen Test Martyr dies, you gain 2 life.","instance_id":"test-martyr","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};device={**card(911,"Charge Device","Artifact"),"oracle_text":"Remove two charge counters from Charge Device: Draw a card.","instance_id":"charge-device","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{"charge":2},"summoning_sick":False};bargain={**card(912,"Life Bargain","Artifact"),"oracle_text":"Pay 3 life: Draw a card.","instance_id":"life-bargain","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False}
    player["battlefield"].extend([martyr,device,bargain]);before_hand=len(player["hand"]);before_life=player["life"]
    martyr_action=next(action for action in legal_actions(state,"player") if action.get("card_id")=="test-martyr");assert martyr_action["self_sacrifice"]
    state=perform_action(state,"player",{"type":"activate","card_id":"test-martyr","ability_index":martyr_action["ability_index"]});player=next(p for p in state["players"] if p["id"]=="player")
    assert any(card["instance_id"]=="test-martyr" for card in player["graveyard"]) and [item["card"]["name"] for item in state["stack"]]==["Test Martyr ability","Test Martyr trigger"]
    state=perform_action(state,"player",{"type":"resolve"});state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");assert player["life"]==before_life+2 and len(player["hand"])==before_hand+2
    counter_action=next(action for action in legal_actions(state,"player") if action.get("card_id")=="charge-device");state=perform_action(state,"player",{"type":"activate","card_id":"charge-device","ability_index":counter_action["ability_index"]});player=next(p for p in state["players"] if p["id"]=="player");assert next(card for card in player["battlefield"] if card["instance_id"]=="charge-device")["counters"]["charge"]==0 and not any(action.get("card_id")=="charge-device" for action in legal_actions(state,"player"))
    state=perform_action(state,"player",{"type":"resolve"});life_action=next(action for action in legal_actions(state,"player") if action.get("card_id")=="life-bargain");state=perform_action(state,"player",{"type":"activate","card_id":"life-bargain","ability_index":life_action["ability_index"]});assert next(p for p in state["players"] if p["id"]=="player")["life"]==before_life-1


def test_selectable_activated_costs_require_and_pay_exact_legal_choices():
    from mtglogger.schemas import GameAction

    assert GameAction(type="activate",cost_card_ids=["chosen"]).model_dump()["cost_card_ids"]==["chosen"]
    state=kept_game();player=next(p for p in state["players"] if p["id"]=="player");altar={**card(920,"Choice Altar","Artifact"),"oracle_text":"Sacrifice another creature: Draw a card.\nDiscard a card: Draw a card.","instance_id":"choice-altar","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};fodder={**card(921,"Fodder","Creature — Citizen","","1","1"),"instance_id":"fodder","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"].extend([altar,fodder]);discard_id=player["hand"][0]["instance_id"]
    actions=[action for action in legal_actions(state,"player") if action.get("card_id")=="choice-altar"];sacrifice=next(action for action in actions if action["cost_kind"]=="sacrifice");discard=next(action for action in actions if action["cost_kind"]=="discard")
    assert sacrifice["cost_options"]==["fodder"] and sacrifice["cost_amount"]==1 and discard_id in discard["cost_options"]
    try:perform_action(state,"player",{"type":"activate","card_id":"choice-altar","ability_index":sacrifice["ability_index"],"cost_card_ids":[]})
    except RuleViolation:pass
    else:raise AssertionError("selectable activation accepted a missing cost choice")
    state=perform_action(state,"player",{"type":"activate","card_id":"choice-altar","ability_index":sacrifice["ability_index"],"cost_card_ids":["fodder"]});player=next(p for p in state["players"] if p["id"]=="player");assert any(card["instance_id"]=="fodder" for card in player["graveyard"])
    state=perform_action(state,"player",{"type":"resolve"});discard=next(action for action in legal_actions(state,"player") if action.get("card_id")=="choice-altar" and action["cost_kind"]=="discard");state=perform_action(state,"player",{"type":"activate","card_id":"choice-altar","ability_index":discard["ability_index"],"cost_card_ids":[discard_id]});player=next(p for p in state["players"] if p["id"]=="player");assert any(card["instance_id"]==discard_id for card in player["graveyard"])


def test_expert_bot_uses_profitable_costly_abilities_but_never_pays_lethal_life():
    state=kept_game();state["active_player_id"]="bot";state["priority_player_id"]="bot";state["phase"]="precombat_main";bot=next(p for p in state["players"] if p["id"]=="bot");bot["hand"]=[];bot["land_plays_remaining"]=0;bot["life"]=2
    fatal={**card(930,"Fatal Bargain","Artifact"),"oracle_text":"Pay 2 life: Draw a card.","instance_id":"fatal-bargain","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};relic={**card(931,"Scholar Relic","Artifact"),"oracle_text":"Sacrifice Scholar Relic: Draw three cards.","instance_id":"scholar-relic","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["battlefield"].append(fatal)
    assert any(action.get("card_id")=="fatal-bargain" for action in legal_actions(state,"bot")) and choose_bot_action(state,"expert")["type"]=="advance_phase"
    bot["battlefield"].append(relic);choice=choose_bot_action(state,"expert");assert choice["type"]=="activate" and choice["card_id"]=="scholar-relic"

    state=kept_game();state["active_player_id"]="bot";state["priority_player_id"]="bot";state["phase"]="precombat_main";bot=next(p for p in state["players"] if p["id"]=="bot");bot["hand"]=[];bot["land_plays_remaining"]=0
    altar={**card(932,"Bot Altar","Artifact"),"oracle_text":"Sacrifice a creature: Draw two cards.","instance_id":"bot-altar","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};cheap={**card(933,"Cheap Token","Token Creature — Citizen","","1","1"),"instance_id":"cheap-token","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False,"token":True};valuable={**card(934,"Valuable Dragon","Creature — Dragon","","6","6"),"instance_id":"valuable-dragon","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["battlefield"]=[altar,cheap,valuable];choice=choose_bot_action(state,"expert")
    assert choice["type"]=="activate" and choice["card_id"]=="bot-altar" and choice["cost_card_ids"]==["cheap-token"]


def test_choose_one_spells_require_a_legal_mode_and_resolve_only_that_mode():
    def setup():
        state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");charm={**card(940,"Test Charm","Instant"),"oracle_text":"Choose one —\n• Draw two cards.\n• Test Charm deals 3 damage to any target.","instance_id":"test-charm","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(charm);return state

    state=setup();action=next(action for action in legal_actions(state,"player") if action.get("card_id")=="test-charm")
    assert action["mode_count"]==1 and [mode["index"] for mode in action["modes"]]==[0,1]
    try:perform_action(state,"player",{"type":"cast","card_id":"test-charm"})
    except RuleViolation:pass
    else:raise AssertionError("modal spell accepted without a chosen mode")
    player=next(p for p in state["players"] if p["id"]=="player");library_before=len(player["library"])
    state=perform_action(state,"player",{"type":"cast","card_id":"test-charm","chosen_modes":[1],"target_id":"bot"});assert state["stack"][-1]["mode_indices"]==[1]
    state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    assert bot["life"]==17 and len(player["library"])==library_before
    state=setup();player=next(p for p in state["players"] if p["id"]=="player");library_before=len(player["library"])
    state=perform_action(state,"player",{"type":"cast","card_id":"test-charm","chosen_modes":[0]});state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    assert len(player["library"])==library_before-2 and bot["life"]==20


def test_modal_modes_without_legal_targets_are_hidden_and_bot_chooses_lethal_mode():
    state=kept_game();state["active_player_id"]="bot";state["priority_player_id"]="bot";state["phase"]="precombat_main";bot=next(p for p in state["players"] if p["id"]=="bot");enemy=next(p for p in state["players"] if p["id"]=="player");bot["hand"]=[];bot["land_plays_remaining"]=0;enemy["battlefield"]=[];enemy["life"]=3
    command={**card(941,"Bot Command","Sorcery"),"oracle_text":"Choose one —\n• Draw a card.\n• Destroy target creature.\n• Bot Command deals 3 damage to any target.","instance_id":"bot-command","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["hand"].append(command)
    action=next(action for action in legal_actions(state,"bot") if action.get("card_id")=="bot-command");assert [mode["index"] for mode in action["modes"]]==[0,2]
    choice=choose_bot_action(state,"expert");assert choice["type"]=="cast" and choice["chosen_modes"]==[2] and choice["target_id"]=="player"


def test_choose_two_modes_keep_independent_targets_and_resolve_each_effect():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    victim={**card(950,"Mode Victim","Creature — Citizen","","2","2"),"instance_id":"mode-victim","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["battlefield"].append(victim)
    command={**card(951,"Test Command","Sorcery"),"oracle_text":"Choose two —\n• Draw two cards.\n• Destroy target creature.\n• Test Command deals 3 damage to any target.","instance_id":"test-command","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(command)
    action=next(action for action in legal_actions(state,"player") if action.get("card_id")=="test-command");assert action["mode_min"]==action["mode_max"]==2 and not action["mode_repeatable"]
    try:perform_action(state,"player",{"type":"cast","card_id":"test-command","chosen_modes":[1,2],"mode_targets":["mode-victim"]})
    except RuleViolation:pass
    else:raise AssertionError("modal spell accepted an incomplete per-mode target list")
    state=perform_action(state,"player",{"type":"cast","card_id":"test-command","chosen_modes":[1,2],"mode_targets":["mode-victim","bot"]});assert state["stack"][-1]["mode_targets"]==["mode-victim","bot"]
    state=perform_action(state,"player",{"type":"resolve"});bot=next(p for p in state["players"] if p["id"]=="bot");player=next(p for p in state["players"] if p["id"]=="player")
    assert bot["life"]==17 and any(card["instance_id"]=="mode-victim" for card in bot["graveyard"]) and any(card["instance_id"]=="test-command" for card in player["graveyard"])


def test_repeatable_modal_spell_accepts_the_same_mode_more_than_once():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    command={**card(952,"Repeat Command","Sorcery"),"oracle_text":"Choose two. You may choose the same mode more than once.\n• Repeat Command deals 2 damage to any target.\n• You gain 2 life.","instance_id":"repeat-command","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(command)
    action=next(action for action in legal_actions(state,"player") if action.get("card_id")=="repeat-command");assert action["mode_repeatable"] and action["mode_min"]==action["mode_max"]==2
    state=perform_action(state,"player",{"type":"cast","card_id":"repeat-command","chosen_modes":[0,0],"mode_targets":["bot","bot"]});state=perform_action(state,"player",{"type":"resolve"});bot=next(p for p in state["players"] if p["id"]=="bot")
    assert bot["life"]==16


def test_multimode_spells_queue_every_distinct_ward_cost():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    player["battlefield"]=[{**card(960+index,f"Ward Land {index}","Basic Land — Plains"),"instance_id":f"ward-land-{index}","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False} for index in range(2)]
    warded=[{**card(965+index,f"Warded Target {index}","Creature — Citizen","","2","2"),"oracle_text":"Ward {1}","instance_id":f"warded-{index}","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False} for index in range(2)];bot["battlefield"]=warded
    command={**card(970,"Double Doom","Sorcery"),"oracle_text":"Choose two —\n• Destroy target creature.\n• Exile target creature.","instance_id":"double-doom","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(command)
    state=perform_action(state,"player",{"type":"cast","card_id":"double-doom","chosen_modes":[0,1],"mode_targets":["warded-0","warded-1"]});assert state["pending_ward"]["source_name"]=="Warded Target 0" and len(state["pending_ward"]["remaining"])==1
    state=perform_action(state,"player",{"type":"pay_ward"});assert state["pending_ward"]["source_name"]=="Warded Target 1"
    state=perform_action(state,"player",{"type":"pay_ward"});assert state["pending_ward"] is None and sum(card["tapped"] for card in next(p for p in state["players"] if p["id"]=="player")["battlefield"])==2


def test_one_or_more_modes_can_require_distinct_targets():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    command={**card(971,"Different Command","Sorcery"),"oracle_text":"Choose one or more — Each mode must target a different player.\n• Target player mills 2 cards.\n• Different Command deals 2 damage to any target.","instance_id":"different-command","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(command)
    action=next(action for action in legal_actions(state,"player") if action.get("card_id")=="different-command");assert action["mode_min"]==1 and action["mode_max"]==2 and action["mode_distinct_targets"]
    try:perform_action(state,"player",{"type":"cast","card_id":"different-command","chosen_modes":[0,1],"mode_targets":["bot","bot"]})
    except RuleViolation:pass
    else:raise AssertionError("modal spell accepted duplicate targets despite its different-target restriction")


def test_triggered_modal_text_on_a_permanent_is_not_a_cast_mode():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    permanent={**card(972,"Modal Visitor","Creature — Citizen","","2","2"),"oracle_text":"When Modal Visitor enters, choose one —\n• You gain 2 life.\n• Draw a card.","instance_id":"modal-visitor","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(permanent)
    action=next(action for action in legal_actions(state,"player") if action.get("card_id")=="modal-visitor");assert "modes" not in action


def test_x_spell_range_payment_stack_value_and_damage_are_authoritative():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    player["battlefield"]=[{**card(980+index,f"Mountain {index}","Basic Land — Mountain"),"instance_id":f"x-land-{index}","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False} for index in range(5)]
    blaze={**card(990,"Test Blaze","Sorcery"),"mana_cost":"{X}{R}","oracle_text":"Test Blaze deals X damage to any target.","instance_id":"test-blaze","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(blaze)
    action=next(action for action in legal_actions(state,"player") if action.get("card_id")=="test-blaze");assert action["x_min"]==0 and action["x_max"]==4 and {target["id"] for target in action["targets"]}>={"player","bot"}
    try:perform_action(state,"player",{"type":"cast","card_id":"test-blaze","target_id":"bot","x_value":5})
    except RuleViolation:pass
    else:raise AssertionError("X spell accepted more mana than was available")
    state=perform_action(state,"player",{"type":"cast","card_id":"test-blaze","target_id":"bot","x_value":4});assert state["stack"][-1]["x_value"]==4 and sum(card["tapped"] for card in next(p for p in state["players"] if p["id"]=="player")["battlefield"])==5
    state=perform_action(state,"player",{"type":"resolve"});assert next(p for p in state["players"] if p["id"]=="bot")["life"]==16


def test_x_activated_ability_and_x_entry_counters_resolve_from_stack_value():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    lands=[{**card(995+index,f"Island {index}","Basic Land — Island"),"instance_id":f"ability-land-{index}","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False} for index in range(3)];device={**card(999,"Mill Device","Artifact"),"oracle_text":"{X}, {T}: Target player mills X cards.","instance_id":"mill-device","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"]=[*lands,device]
    action=next(action for action in legal_actions(state,"player") if action.get("card_id")=="mill-device");assert action["x_max"]==3
    before=len(bot["library"]);state=perform_action(state,"player",{"type":"activate","card_id":"mill-device","ability_index":action["ability_index"],"target_id":"bot","x_value":3});assert state["stack"][-1]["x_value"]==3
    state=perform_action(state,"player",{"type":"resolve"});bot=next(p for p in state["players"] if p["id"]=="bot");assert len(bot["library"])==before-3
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");player["battlefield"]=[{**card(1005+index,f"Forest {index}","Basic Land — Forest"),"instance_id":f"counter-land-{index}","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False} for index in range(3)]
    hydra={**card(1010,"Test Hydra","Creature — Hydra","","0","0"),"mana_cost":"{X}{G}","oracle_text":"Test Hydra enters the battlefield with X +1/+1 counters on it.","instance_id":"test-hydra","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(hydra)
    state=perform_action(state,"player",{"type":"cast","card_id":"test-hydra","x_value":2});state=perform_action(state,"player",{"type":"resolve"});hydra=next(card for card in next(p for p in state["players"] if p["id"]=="player")["battlefield"] if card["instance_id"]=="test-hydra");assert hydra["counters"]["+1/+1"]==2


def test_expert_bot_chooses_a_lethal_payable_x_value():
    state=kept_game();state["active_player_id"]="bot";state["priority_player_id"]="bot";state["phase"]="precombat_main";bot=next(p for p in state["players"] if p["id"]=="bot");enemy=next(p for p in state["players"] if p["id"]=="player");bot["hand"]=[];bot["land_plays_remaining"]=0;enemy["life"]=3
    bot["battlefield"]=[{**card(1020+index,f"Mountain {index}","Basic Land — Mountain"),"instance_id":f"bot-x-land-{index}","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False} for index in range(6)];blaze={**card(1030,"Bot Blaze","Sorcery"),"mana_cost":"{X}{R}","oracle_text":"Bot Blaze deals X damage to any target.","instance_id":"bot-blaze","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["hand"].append(blaze)
    choice=choose_bot_action(state,"expert");assert choice["type"]=="cast" and choice["x_value"]==3 and choice["target_id"]=="player"


def test_x_value_is_reused_across_draw_life_tokens_and_temporary_stats():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    player["battlefield"]=[{**card(1040+index,f"Island {index}","Basic Land — Island"),"instance_id":f"multi-x-land-{index}","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False} for index in range(3)];recipient={**card(1045,"X Recipient","Creature — Wizard","","1","1"),"instance_id":"x-recipient","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"].append(recipient)
    flourish={**card(1046,"X Flourish","Sorcery"),"mana_cost":"{X}{U}","oracle_text":"Draw X cards. You gain X life. Create X 1/1 blue Bird creature tokens. Target creature gets +X/+X until end of turn.","instance_id":"x-flourish","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(flourish);before_library=len(player["library"]);before_life=player["life"]
    state=perform_action(state,"player",{"type":"cast","card_id":"x-flourish","target_id":"x-recipient","x_value":2});state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");recipient=next(card for card in player["battlefield"] if card["instance_id"]=="x-recipient")
    assert len(player["library"])==before_library-2 and player["life"]==before_life+2 and sum(card.get("token",False) for card in player["battlefield"])==2 and (recipient["temporary_power"],recipient["temporary_toughness"])==(2,2)


def test_aura_casting_targets_attaches_grants_bonuses_and_cleans_up_with_creature():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    land={**card(1050,"Plains","Basic Land — Plains"),"instance_id":"aura-land","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};creature={**card(1051,"Aura Bearer","Creature — Soldier","","2","2"),"instance_id":"aura-bearer","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};aura={**card(1052,"Test Wings","Enchantment — Aura","{W}"),"oracle_text":"Enchant creature you control\nEnchanted creature gets +2/+2 and has flying.","instance_id":"test-wings","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"]=[land,creature];player["hand"].append(aura)
    action=next(action for action in legal_actions(state,"player") if action.get("card_id")=="test-wings");assert [target["id"] for target in action["targets"]]==["aura-bearer"]
    state=perform_action(state,"player",{"type":"cast","card_id":"test-wings","target_id":"aura-bearer"});state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");aura=next(card for card in player["battlefield"] if card["instance_id"]=="test-wings");assert aura["attached_to"]=="aura-bearer"
    visible=public_state(state);bearer=next(card for card in next(p for p in visible["players"] if p["id"]=="player")["battlefield"] if card["instance_id"]=="aura-bearer");assert (bearer["effective_power"],bearer["effective_toughness"])==(4,4) and _has_keyword(next(card for card in player["battlefield"] if card["instance_id"]=="aura-bearer"),"Flying")
    state["phase"]="combat";attack=next(action for action in legal_actions(state,"player") if action["type"]=="declare_attackers");assert "aura-bearer" in attack["card_ids"]
    state=perform_action(state,"player",{"type":"move_zone","target_id":"aura-bearer","destination":"graveyard"});player=next(p for p in state["players"] if p["id"]=="player");assert {card["instance_id"] for card in player["graveyard"]}>={"aura-bearer","test-wings"}


def test_equipment_cost_timing_retargeting_bonuses_and_detachment_are_enforced():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    lands=[{**card(1060+index,f"Plains {index}","Basic Land — Plains"),"instance_id":f"equip-land-{index}","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False} for index in range(2)];small={**card(1063,"Small Bearer","Creature — Soldier","","1","1"),"instance_id":"small-bearer","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};large={**card(1064,"Large Bearer","Creature — Giant","","4","4"),"instance_id":"large-bearer","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};equipment={**card(1065,"Test Blade","Artifact — Equipment"),"oracle_text":"Equipped creature gets +1/+1 and has trample.\nEquip {2}","instance_id":"test-blade","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"]=[*lands,small,large,equipment]
    action=next(action for action in legal_actions(state,"player") if action["type"]=="equip");assert {target["id"] for target in action["targets"]}=={"small-bearer","large-bearer"}
    state=perform_action(state,"player",{"type":"equip","card_id":"test-blade","target_id":"large-bearer"});player=next(p for p in state["players"] if p["id"]=="player");assert state["stack"][-1]["kind"]=="equip_ability" and "attached_to" not in next(card for card in player["battlefield"] if card["instance_id"]=="test-blade") and sum(card["tapped"] for card in player["battlefield"] if "Land" in card["type_line"])==2
    state=perform_action(state,"player",{"type":"resolve"});player=next(p for p in state["players"] if p["id"]=="player");assert next(card for card in player["battlefield"] if card["instance_id"]=="test-blade")["attached_to"]=="large-bearer"
    visible=public_state(state);large_visible=next(card for card in next(p for p in visible["players"] if p["id"]=="player")["battlefield"] if card["instance_id"]=="large-bearer");assert (large_visible["effective_power"],large_visible["effective_toughness"])==(5,5)
    state=perform_action(state,"player",{"type":"move_zone","target_id":"large-bearer","destination":"graveyard"});player=next(p for p in state["players"] if p["id"]=="player");equipment=next(card for card in player["battlefield"] if card["instance_id"]=="test-blade");assert "attached_to" not in equipment and equipment not in player["graveyard"]
    state["phase"]="combat";assert not any(action["type"]=="equip" for action in legal_actions(state,"player"))


def test_expert_bot_equips_its_strongest_available_creature_once():
    state=kept_game();state["active_player_id"]="bot";state["priority_player_id"]="bot";state["phase"]="precombat_main";bot=next(p for p in state["players"] if p["id"]=="bot");bot["hand"]=[];bot["land_plays_remaining"]=0
    land={**card(1070,"Swamp","Basic Land — Swamp"),"instance_id":"bot-equip-land","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};small={**card(1071,"Bot Small","Creature — Rat","","1","1"),"instance_id":"bot-small","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};large={**card(1072,"Bot Large","Creature — Demon","","5","5"),"instance_id":"bot-large","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};equipment={**card(1073,"Bot Blade","Artifact — Equipment"),"oracle_text":"Equipped creature gets +1/+1.\nEquip {1}","instance_id":"bot-blade","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["battlefield"]=[land,small,large,equipment]
    choice=choose_bot_action(state,"expert");assert choice["type"]=="equip" and choice["target_id"]=="bot-large"


def test_attachment_restrictions_change_authoritative_combat_legality():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    creature={**card(1080,"Pacified Attacker","Creature — Warrior","","3","3"),"instance_id":"pacified-attacker","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};aura={**card(1081,"Test Pacifism","Enchantment — Aura"),"oracle_text":"Enchant creature\nEnchanted creature can't attack or block.","instance_id":"test-pacifism","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["battlefield"]=[creature];player["hand"].append(aura)
    state=perform_action(state,"player",{"type":"cast","card_id":"test-pacifism","target_id":"pacified-attacker"});state=perform_action(state,"player",{"type":"resolve"});state["phase"]="combat"
    assert not any(action["type"]=="declare_attackers" for action in legal_actions(state,"player"))


def test_production_priority_requires_both_players_to_pass_before_resolution():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player")
    spell={**card(1090,"Priority Draw","Instant"),"oracle_text":"Draw a card.","instance_id":"priority-draw","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(spell);before=len(player["hand"])
    state=perform_action(state,"player",{"type":"cast","card_id":"priority-draw"},allow_direct_resolution=False);assert state["priority_player_id"]=="bot" and len(state["stack"])==1 and "resolve" not in {action["type"] for action in legal_actions(state,"bot",allow_direct_resolution=False)}
    state=perform_action(state,"bot",{"type":"pass_priority"},allow_direct_resolution=False);assert state["priority_player_id"]=="player" and state["consecutive_passes"]==1 and len(state["stack"])==1
    state=perform_action(state,"player",{"type":"pass_priority"},allow_direct_resolution=False);player=next(p for p in state["players"] if p["id"]=="player");assert not state["stack"] and len(player["hand"])==before and state["priority_player_id"]==state["active_player_id"]


def test_bot_spell_stops_for_human_response_and_counterspell_uses_real_priority():
    state=kept_game();state["active_player_id"]="bot";state["priority_player_id"]="bot";state["phase"]="precombat_main";bot=next(p for p in state["players"] if p["id"]=="bot");player=next(p for p in state["players"] if p["id"]=="player");bot["hand"]=[];bot["land_plays_remaining"]=0
    land={**card(1100,"Mountain","Basic Land — Mountain"),"instance_id":"bot-priority-land","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bolt={**card(1101,"Bot Priority Bolt","Instant","{R}"),"oracle_text":"Bot Priority Bolt deals 3 damage to any target.","instance_id":"bot-priority-bolt","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};counter={**card(1102,"Human Counter","Instant"),"oracle_text":"Counter target spell.","instance_id":"human-counter","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};bot["battlefield"]=[land];bot["hand"].append(bolt);player["hand"].append(counter)
    state=run_bot(state,"expert");assert state["priority_player_id"]=="player" and [item["card"]["name"] for item in state["stack"]]==["Bot Priority Bolt"]
    response=next(action for action in legal_actions(state,"player",allow_direct_resolution=False) if action.get("card_id")=="human-counter");assert response["targets"][0]["id"]==state["stack"][-1]["id"]
    state=perform_action(state,"player",{"type":"cast","card_id":"human-counter","target_id":state["stack"][-1]["id"]},allow_direct_resolution=False);state=run_bot(state,"expert");assert state["priority_player_id"]=="player" and state["consecutive_passes"]==1 and len(state["stack"])==2
    state=perform_action(state,"player",{"type":"pass_priority"},allow_direct_resolution=False);player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot");assert not state["stack"] and any(card["instance_id"]=="bot-priority-bolt" for card in bot["graveyard"]) and player["life"]==20


def test_production_phase_advance_allows_opponent_response_then_advances_on_pass():
    state=kept_game();assert state["phase"]=="beginning"
    state=perform_action(state,"player",{"type":"advance_phase"},allow_direct_resolution=False);assert state["phase"]=="beginning" and state["pending_phase_advance"] and state["priority_player_id"]=="bot" and state["consecutive_passes"]==1
    state=perform_action(state,"bot",{"type":"pass_priority"},allow_direct_resolution=False);assert state["phase"]=="precombat_main" and not state["pending_phase_advance"]


def test_bot_completes_its_entire_mulligan_sequence_without_an_extra_human_action():
    first,second=decks();state=new_game(first,second);state=perform_action(state,"player",{"type":"keep"},allow_direct_resolution=False);state=run_bot(state,"expert",20);bot=next(player for player in state["players"] if player["id"]=="bot")
    assert bot["kept_hand"] and state["status"]=="active" and len(bot["hand"])==7-bot["mulligans"]


def test_expert_bot_uses_counterspell_response_and_returns_priority_to_human():
    state=kept_game();state=perform_action(state,"player",{"type":"advance_phase"});player=next(p for p in state["players"] if p["id"]=="player");bot=next(p for p in state["players"] if p["id"]=="bot")
    human_spell={**card(1110,"Human Draw","Instant"),"oracle_text":"Draw two cards.","instance_id":"human-draw","owner_id":"player","controller_id":"player","tapped":False,"damage":0,"counters":{},"summoning_sick":False};counter={**card(1111,"Bot Counter","Instant"),"oracle_text":"Counter target spell.","instance_id":"bot-counter","owner_id":"bot","controller_id":"bot","tapped":False,"damage":0,"counters":{},"summoning_sick":False};player["hand"].append(human_spell);bot["hand"].append(counter)
    state=perform_action(state,"player",{"type":"cast","card_id":"human-draw"},allow_direct_resolution=False);state=run_bot(state,"expert");assert state["priority_player_id"]=="player" and [item["card"]["name"] for item in state["stack"]]==["Human Draw","Bot Counter"] and state["stack"][-1]["target_id"]==state["stack"][0]["id"]
