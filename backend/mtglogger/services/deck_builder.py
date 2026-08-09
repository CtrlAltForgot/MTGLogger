import json
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache


COLOR_ORDER = "WUBRG"
COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green", "C": "Colorless"}


@dataclass(frozen=True)
class FormatRule:
    label: str
    legality: str | None
    size: int
    singleton: bool
    leader: str | None
    base_lands: int


FORMAT_RULES = {
    "standard": FormatRule("Standard", "standard", 60, False, None, 24),
    "pioneer": FormatRule("Pioneer", "pioneer", 60, False, None, 24),
    "modern": FormatRule("Modern", "modern", 60, False, None, 24),
    "pauper": FormatRule("Pauper", "pauper", 60, False, None, 24),
    "legacy": FormatRule("Legacy", "legacy", 60, False, None, 23),
    "vintage": FormatRule("Vintage", "vintage", 60, False, None, 23),
    "commander": FormatRule("Commander", "commander", 100, True, "commander", 37),
    "brawl": FormatRule("Brawl", "brawl", 60, True, "commander", 24),
    "oathbreaker": FormatRule("Oathbreaker", "oathbreaker", 60, True, "oathbreaker", 24),
    "duel commander": FormatRule("Duel Commander", "duel", 100, True, "commander", 37),
    "pauper commander": FormatRule("Pauper Commander", "paupercommander", 100, True, "commander", 37),
    "casual / kitchen table": FormatRule("Casual / Kitchen Table", None, 60, False, None, 24),
}


@dataclass
class BuildCandidate:
    inventory_id: str
    scryfall_id: str
    name: str
    set_code: str
    collector_number: str
    image_url: str | None
    available: int
    type_line: str
    oracle_text: str
    color_identity: str
    mana_cost: str
    mana_value: float
    rarity: str
    legalities: dict[str, str]
    keywords: list[str]
    oracle_id: str = ""
    set_name: str = ""

    @property
    def is_land(self) -> bool:
        return "Land" in self.type_line

    @property
    def is_basic(self) -> bool:
        return "Basic Land" in self.type_line


THEMES = {
    "tokens": ("create a", "token", "populate"),
    "artifacts": ("artifact", "treasure", "equipment"),
    "enchantments": ("enchantment", "aura", "constellation"),
    "graveyard": ("graveyard", "mill", "discard", "return target"),
    "counters": ("counter on", "proliferate", "+1/+1 counter"),
    "lifegain": ("gain life", "lifelink", "whenever you gain"),
    "sacrifice": ("sacrifice", "dies", "when this creature dies"),
    "spells": ("instant or sorcery", "noncreature spell", "prowess", "magecraft"),
    "creatures": ("creature you control", "creatures you control", "creature card"),
}

IGNORED_SUBTYPES={"wizard","warrior","human","soldier","cleric","rogue"}


def normalized_words(value:str)->set[str]:
    return {word for word in re.sub(r"[^a-z0-9]+"," ",value.casefold()).split() if len(word)>=2}


def focus_matches(card:BuildCandidate,focus:str)->bool:
    terms=normalized_words(focus)
    if not terms:return True
    haystack=normalized_words(f"{card.set_code} {card.set_name}")
    return terms.issubset(haystack) or all(any(term in word for word in haystack) for term in terms)


@lru_cache(maxsize=32_768)
def _synergy_tags(type_line:str,oracle_text:str,keywords:tuple[str,...])->frozenset[str]:
    text=f"{type_line} {oracle_text} {' '.join(keywords)}".casefold()
    tags={theme for theme,terms in THEMES.items() if any(term in text for term in terms)}
    if "—" in type_line:
        subtypes=re.split(r"\s+",type_line.split("—",1)[1].casefold())
        tags.update(f"tribal:{subtype}" for subtype in subtypes if len(subtype)>2 and subtype not in IGNORED_SUBTYPES)
    for keyword in keywords:
        normalized=keyword.strip().casefold().replace(" ","-")
        if normalized:tags.add(f"keyword:{normalized}")
    if "legendary" in type_line.casefold() or "legendary" in text:tags.add("legends")
    if "equipment" in text:tags.add("equipment")
    if "landfall" in text or "land enters" in text:tags.add("lands")
    return frozenset(tags)


def synergy_tags(card:BuildCandidate)->frozenset[str]:
    return _synergy_tags(card.type_line,card.oracle_text,tuple(card.keywords))


def pair_synergy(left:BuildCandidate,right:BuildCandidate)->float:
    left_tags,right_tags=synergy_tags(left),synergy_tags(right)
    shared=left_tags&right_tags
    score=sum(4 if tag.startswith("tribal:") else 2.5 for tag in shared)
    left_text=left.oracle_text.casefold();right_text=right.oracle_text.casefold()
    for tag in left_tags:
        if tag.startswith("tribal:") and tag.split(":",1)[1] in right_text:score+=4
    for tag in right_tags:
        if tag.startswith("tribal:") and tag.split(":",1)[1] in left_text:score+=4
    complementary=(
        ("sacrifice","dies"),("discard","graveyard"),("mill","graveyard"),
        ("create","token"),("cast","instant or sorcery"),("equipment","equipped"),
        ("gain life","whenever you gain life"),("counter on","proliferate"),
    )
    for producer,payoff in complementary:
        if (producer in left_text and payoff in right_text) or (producer in right_text and payoff in left_text):score+=5
    return min(20,score)


def normalize_format(value: str) -> FormatRule:
    key=value.strip().casefold()
    if key not in FORMAT_RULES:
        raise ValueError(f"Unsupported deck format: {value}")
    return FORMAT_RULES[key]


def normalize_colors(values: list[str]) -> tuple[str, ...]:
    colors={value.upper() for value in values}
    if not colors or not colors.issubset(set(COLOR_ORDER+"C")):
        raise ValueError("Choose one or more of W, U, B, R, G, or C")
    if "C" in colors and len(colors)>1:
        colors.remove("C")
    return tuple(color for color in COLOR_ORDER+"C" if color in colors)


def classify_roles(card: BuildCandidate) -> set[str]:
    if card.is_land:return {"land"}
    text=f"{card.type_line} {card.oracle_text} {' '.join(card.keywords)}".casefold()
    roles:set[str]=set()
    if "creature" in card.type_line.casefold() or "planeswalker" in card.type_line.casefold():roles.add("threat")
    if any(term in text for term in ("draw a card","draw two","draw three","whenever you draw","look at the top")):roles.add("draw")
    if any(term in text for term in ("add {","add one mana","treasure token","search your library for a basic land","search your library for a land")):roles.add("ramp")
    if any(term in text for term in ("destroy target","exile target","deals damage to any target","counter target spell","return target creature","-3/-3","-4/-4")):roles.add("interaction")
    if any(term in text for term in ("destroy all","exile all","all creatures get -","each creature")):roles.add("board wipe")
    if any(term in text for term in ("hexproof","indestructible","protection from","phase out","counter target spell")):roles.add("protection")
    if not roles:roles.add("utility")
    return roles


def theme_hits(card: BuildCandidate, theme: str) -> int:
    text=f"{card.type_line} {card.oracle_text} {' '.join(card.keywords)}".casefold()
    return sum(text.count(term) for term in THEMES.get(theme, ()))


def choose_theme(cards: list[BuildCandidate], strategy: str) -> str:
    if strategy in THEMES:return strategy
    scores={theme:sum(min(3,theme_hits(card,theme)) for card in cards if not card.is_land) for theme in THEMES}
    return max(scores,key=scores.get) if scores and max(scores.values())>0 else "good-stuff"


def card_score(card: BuildCandidate, strategy: str, theme: str) -> tuple[float,list[str]]:
    roles=classify_roles(card)
    score=10.0
    reasons:list[str]=[]
    hits=theme_hits(card,theme)
    if hits:
        score+=min(12,hits*3);reasons.append(f"supports the {theme} plan")
    role_weights={"draw":8,"ramp":7,"interaction":8,"board wipe":9,"protection":5,"threat":4,"utility":1}
    score+=sum(role_weights.get(role,0) for role in roles)
    if roles-{"threat","utility"}:reasons.append("fills "+", ".join(sorted(roles-{"threat","utility"})))
    mv=card.mana_value
    if strategy=="aggro":score+=max(-5,8-mv*2)+(4 if "threat" in roles else 0)
    elif strategy=="control":score+=(5 if roles&{"interaction","draw","board wipe"} else 0)+min(4,mv*.4)
    elif strategy=="midrange":score+=max(0,5-abs(mv-3.5)*1.5)
    else:score+=max(0,4-abs(mv-3)*.8)
    if 1<=mv<=4:score+=2
    if not reasons:reasons.append("improves curve and card quality")
    return score,reasons


def legal_for(card: BuildCandidate, rule: FormatRule, colors: tuple[str,...]) -> bool:
    chosen=set(colors)-{"C"}
    identity=set(card.color_identity or "")
    if colors==("C",):
        if identity:return False
    elif not identity.issubset(chosen):return False
    if rule.legality and card.legalities.get(rule.legality) not in {"legal","restricted"}:return False
    if rule.legality=="pauper" and card.rarity not in {"common", ""}:return False
    return True


def leader_score(card: BuildCandidate, colors: tuple[str,...], theme: str) -> float:
    chosen=set(colors)-{"C"};identity=set(card.color_identity or "")
    exact=identity==chosen
    return (25 if exact else 5)+theme_hits(card,theme)*5+max(0,6-card.mana_value)


def choose_leader(cards:list[BuildCandidate],rule:FormatRule,colors:tuple[str,...],theme:str)->BuildCandidate|None:
    if not rule.leader:return None
    if rule.leader=="oathbreaker":eligible=[card for card in cards if "Planeswalker" in card.type_line]
    else:eligible=[card for card in cards if "Legendary" in card.type_line and "Creature" in card.type_line]
    chosen=set(colors)-{"C"}
    eligible=[card for card in eligible if set(card.color_identity or "")==chosen]
    if not eligible:return None
    return max(eligible,key=lambda card:leader_score(card,colors,theme))


def land_score(card:BuildCandidate,colors:tuple[str,...])->float:
    chosen=set(colors)-{"C"};text=f"{card.name} {card.type_line} {card.oracle_text}".casefold()
    basics={"W":"plains","U":"island","B":"swamp","R":"mountain","G":"forest"}
    produced=sum(1 for color in chosen if basics[color] in text or f"{{{color.casefold()}}}" in text)
    score=10+produced*7
    if produced>=2:score+=8
    if card.is_basic:score+=4
    if "enters the battlefield tapped" in text:score-=3
    return score


def card_key(card:BuildCandidate)->str:
    return card.oracle_id or card.name.casefold()


def copy_capacity(card:BuildCandidate,rule:FormatRule,used:int)->int:
    restricted=bool(rule.legality and card.legalities.get(rule.legality)=="restricted")
    limit=1 if (rule.singleton and not card.is_basic) or restricted else (card.available if card.is_basic else 4)
    return max(0,min(card.available,limit-used))


def produced_colors(card:BuildCandidate)->set[str]:
    text=f"{card.name} {card.type_line} {card.oracle_text}".casefold()
    basics={"W":"plains","U":"island","B":"swamp","R":"mountain","G":"forest"}
    return {color for color,name in basics.items() if name in text or f"{{{color.casefold()}}}" in text}


def curve_bucket(mana_value:float)->str:
    if mana_value<=1:return "0–1"
    if mana_value>=5:return "5+"
    return str(int(mana_value))


def build_deck(candidates:list[BuildCandidate],format_name:str,colors:list[str],strategy:str="balanced",name:str="Auto-built deck",focus:str="",focus_mode:str="prefer")->dict:
    rule=normalize_format(format_name);normalized_colors=normalize_colors(colors)
    strategy=strategy.strip().casefold() or "balanced";focus=focus.strip();focus_mode=focus_mode.casefold()
    if strategy not in {"balanced","aggro","midrange","control",*THEMES}:raise ValueError(f"Unsupported strategy: {strategy}")
    if focus_mode not in {"prefer","strict"}:raise ValueError("Focus mode must be prefer or strict")
    base_legal=[card for card in candidates if card.available>0 and legal_for(card,rule,normalized_colors)]
    focused=[card for card in base_legal if focus_matches(card,focus)] if focus else base_legal
    matched_sets=sorted({f"{card.set_name} ({card.set_code.upper()})" for card in focused}) if focus else []
    legal=focused if focus and focus_mode=="strict" else base_legal
    spells=[card for card in legal if not card.is_land];lands=[card for card in legal if card.is_land]
    theme=choose_theme(focused if focused else spells,strategy);legal_count=len(legal)
    # Pairwise rescoring is intentionally performed on a broad contender set,
    # not every bulk common in a large collection. Preserve all focus matches,
    # leaders and the strongest cards for each structural role.
    ranked=sorted(spells,key=lambda card:card_score(card,strategy,theme)[0],reverse=True)
    contenders={card.inventory_id:card for card in ranked[:600]}
    for role in ("ramp","draw","interaction","board wipe","protection","threat"):
        role_cards=(card for card in ranked if role in classify_roles(card))
        for card in list(role_cards)[:80]:contenders[card.inventory_id]=card
    for card in spells:
        if (focus and focus_matches(card,focus)) or (rule.leader and ("Legendary" in card.type_line or "Planeswalker" in card.type_line)):
            contenders[card.inventory_id]=card
    spells=list(contenders.values())
    selected:dict[str,dict]={};used=Counter();warnings:list[str]=[];pair_cache:dict[tuple[str,str],float]={}

    def synergy_between(left:BuildCandidate,right:BuildCandidate)->float:
        key=tuple(sorted((left.inventory_id,right.inventory_id)))
        if key not in pair_cache:pair_cache[key]=pair_synergy(left,right)
        return pair_cache[key]

    def selected_nonlands()->list[BuildCandidate]:
        return [entry["card"] for entry in selected.values() if not entry["card"].is_land]

    def dynamic_score(card:BuildCandidate)->tuple[float,list[str]]:
        base,reasons=card_score(card,strategy,theme)
        partners=selected_nonlands()
        pairings=sorted(((synergy_between(card,other),other.name) for other in partners if card_key(card)!=card_key(other)),reverse=True)
        synergy=(pairings[0][0] if pairings else 0)+(sum(score for score,_ in pairings[:4])/max(1,len(pairings[:4]))*.35)
        if pairings and pairings[0][0]>=3:reasons=[*reasons,f"pairs strongly with {pairings[0][1]}"]
        if focus and focus_matches(card,focus):base+=18 if focus_mode=="prefer" else 4;reasons=[f"matches the {focus} collection focus",*reasons]
        return base+synergy,reasons

    def available_capacity(card:BuildCandidate)->int:
        already_selected=selected.get(card.inventory_id,{}).get("quantity",0)
        return min(card.available-already_selected,copy_capacity(card,rule,used[card_key(card)]))

    def add(card:BuildCandidate,quantity:int,role:str,score:float,reasons:list[str])->int:
        quantity=min(quantity,available_capacity(card))
        if quantity<=0:return 0
        entry=selected.get(card.inventory_id)
        if entry:entry["quantity"]+=quantity;entry["score"]=max(entry["score"],score)
        else:selected[card.inventory_id]={"card":card,"quantity":quantity,"role":role,"score":score,"reasons":reasons}
        used[card_key(card)]+=quantity
        return quantity

    leader=None
    if rule.leader:
        if rule.leader=="oathbreaker":leader_pool=[card for card in spells if "Planeswalker" in card.type_line]
        else:leader_pool=[card for card in spells if "Legendary" in card.type_line and "Creature" in card.type_line]
        chosen=set(normalized_colors)-{"C"};leader_pool=[card for card in leader_pool if set(card.color_identity or "")==chosen]
        if leader_pool:
            leader=max(leader_pool,key=lambda card:leader_score(card,normalized_colors,theme)+(18 if focus and focus_matches(card,focus) else 0))
            add(leader,1,"leader",leader_score(leader,normalized_colors,theme),[f"best available {rule.leader} for the selected colors",f"anchors the {theme} plan"])
        else:warnings.append(f"No legal {rule.leader} matching the selected colors is unassigned.")
    signature=None
    if rule.leader=="oathbreaker":
        signatures=[card for card in spells if card is not leader and ("Instant" in card.type_line or "Sorcery" in card.type_line)]
        if signatures:
            signature=max(signatures,key=lambda card:dynamic_score(card)[0]);score,reasons=dynamic_score(signature)
            add(signature,1,"signature spell",score,["best available signature spell",*reasons])
        else:warnings.append("No legal instant or sorcery is available for the Oathbreaker's signature spell.")

    pool=[card for card in spells if card.inventory_id not in selected]
    spell_target=rule.size-rule.base_lands-(1 if leader else 0);spell_count=1 if signature else 0
    quotas={"ramp":10,"draw":10,"interaction":10,"board wipe":3,"protection":4} if rule.size==100 else {"ramp":4,"draw":5,"interaction":6,"board wipe":2,"protection":2}

    def best_available(role:str|None=None)->tuple[BuildCandidate,float,list[str]]|None:
        options=[]
        for card in pool:
            if available_capacity(card)<=0:continue
            if role and role not in classify_roles(card):continue
            score,reasons=dynamic_score(card);options.append((score,-card.mana_value,card.name,card,reasons))
        if not options:return None
        _,_,_,card,reasons=max(options,key=lambda item:item[:3]);score,_=dynamic_score(card)
        return card,score,reasons

    for role,quota in quotas.items():
        role_count=0
        while spell_count<spell_target and role_count<quota:
            choice=best_available(role)
            if not choice:break
            card,score,reasons=choice;quantity=add(card,min(quota-role_count,spell_target-spell_count),role,score,reasons)
            if not quantity:break
            role_count+=quantity;spell_count+=quantity
    while spell_count<spell_target:
        choice=best_available()
        if not choice:break
        card,score,reasons=choice;roles=classify_roles(card)
        priority={"threat":0,"interaction":1,"draw":2,"ramp":3,"protection":4,"board wipe":5,"utility":6}
        role=min(roles,key=lambda value:priority.get(value,9));quantity=add(card,spell_target-spell_count,role,score,reasons)
        if not quantity:break
        spell_count+=quantity

    chosen_spells=[entry for entry in selected.values() if entry["role"]!="leader"]
    weighted_mv=sum(entry["card"].mana_value*entry["quantity"] for entry in chosen_spells);spell_copies=sum(entry["quantity"] for entry in chosen_spells)
    average_mv=weighted_mv/max(1,spell_copies);land_target=max(rule.base_lands-4,min(rule.base_lands+4,rule.base_lands+round((average_mv-3)*2)))
    desired_spell_total=rule.size-land_target-(1 if leader else 0)
    while spell_count<desired_spell_total:
        choice=best_available()
        if not choice:break
        card,score,reasons=choice;roles=classify_roles(card);role=next(iter(roles));quantity=add(card,desired_spell_total-spell_count,role,score,reasons)
        if not quantity:break
        spell_count+=quantity
    while spell_count>desired_spell_total:
        removable=min((entry for entry in selected.values() if entry["role"] not in {"leader","signature spell"}),key=lambda entry:entry["score"],default=None)
        if not removable:break
        removable["quantity"]-=1;used[card_key(removable["card"]) ]-=1;spell_count-=1
        if removable["quantity"]<=0:selected.pop(removable["card"].inventory_id,None)

    pip_demand=Counter()
    for entry in selected.values():
        if entry["card"].is_land:continue
        for color in COLOR_ORDER:pip_demand[color]+=entry["card"].mana_cost.upper().count(f"{{{color}}}")*entry["quantity"]
    mana_sources=Counter();land_count=0
    while land_count<land_target:
        options=[]
        for card in lands:
            if available_capacity(card)<=0:continue
            sources=produced_colors(card);deficit=sum(max(0,pip_demand[color]-mana_sources[color]*2) for color in sources)
            score=land_score(card,normalized_colors)+deficit+(10 if len(sources)>=2 else 0)+(14 if focus and focus_matches(card,focus) else 0)
            options.append((score,len(sources),card.available,card))
        if not options:break
        score,_,_,card=max(options,key=lambda item:item[:3]);quantity=add(card,1,"land",score,["covers the deck's remaining colored mana demand"])
        if not quantity:break
        for color in produced_colors(card):mana_sources[color]+=quantity
        land_count+=quantity

    total=sum(entry["quantity"] for entry in selected.values())
    if focus and not matched_sets:warnings.append(f"No local set or collection matched “{focus}”.")
    if spell_count<desired_spell_total:warnings.append(f"Only {spell_count} of {desired_spell_total} desired nonland cards were available and legal.")
    if land_count<land_target:warnings.append(f"Only {land_count} of {land_target} recommended lands were available; scan or free additional lands before playing.")
    if total<rule.size:warnings.append(f"The collection is {rule.size-total} cards short of a complete {rule.label} deck in these colors.")

    role_counts=Counter();curve=Counter({bucket:0 for bucket in ("0–1","2","3","4","5+")});output=[]
    nonlands=[entry["card"] for entry in selected.values() if not entry["card"].is_land]
    pair_scores=[synergy_between(left,right) for index,left in enumerate(nonlands) for right in nonlands[index+1:]]
    raw_synergy=sum(pair_scores)/max(1,len(pair_scores));theme_share=sum(1 for card in nonlands if theme_hits(card,theme)>0)/max(1,len(nonlands))
    synergy_score=min(100,raw_synergy*12+theme_share*35)
    for entry in sorted(selected.values(),key=lambda item:(item["role"]!="leader",item["role"]!="land",item["card"].mana_value,item["card"].name)):
        card=entry["card"];quantity=entry["quantity"];role_counts[entry["role"]]+=quantity
        if not card.is_land:curve[curve_bucket(card.mana_value)]+=quantity
        partners=sorted(((synergy_between(card,other),other.name) for other in nonlands if card_key(card)!=card_key(other)),reverse=True)
        reasons=list(entry["reasons"])
        if partners and partners[0][0]>=3 and not any("pairs strongly" in reason for reason in reasons):reasons.append(f"pairs strongly with {partners[0][1]}")
        output.append({"inventory_id":card.inventory_id,"scryfall_id":card.scryfall_id,"name":card.name,"set_code":card.set_code,"set_name":card.set_name,"collector_number":card.collector_number,"image_url":card.image_url,"quantity":quantity,"role":entry["role"],"mana_value":card.mana_value,"score":round(entry["score"],2),"reasons":reasons})
    targets={"aggro":{"0–1":.2,"2":.35,"3":.25,"4":.12,"5+":.08},"control":{"0–1":.12,"2":.25,"3":.24,"4":.19,"5+":.2}}.get(strategy,{"0–1":.14,"2":.28,"3":.28,"4":.17,"5+":.13})
    curve_score=max(0,100-sum(abs(curve[bucket]/max(1,spell_count)-target) for bucket,target in targets.items())*50)
    role_score=sum(min(1,role_counts[role]/quota) for role,quota in quotas.items())/len(quotas)*100
    completeness=min(1,total/rule.size)*100;quality_score=.3*synergy_score+.27*role_score+.23*curve_score+.2*completeness
    color_label="/".join(COLOR_NAMES[color] for color in normalized_colors)
    explanation=[f"Built from {legal_count} legal unassigned collection entries in {color_label}.",f"The strongest supported plan is {theme.replace('-', ' ')} with a {strategy} posture.",f"Pairwise synergy scored {synergy_score:.0f}/100; overall structural quality scored {quality_score:.0f}/100.",f"Selected {land_count} lands for an average nonland mana value of {average_mv:.2f}."]
    if focus:explanation.insert(1,f"{focus_mode.title()} focus matched {len(matched_sets)} local sets for “{focus}”.")
    if leader:explanation.insert(1,f"Selected {leader.name} as the {rule.leader}.")
    if signature:explanation.insert(2,f"Paired it with {signature.name} as the signature spell.")
    structure_complete=(not rule.leader or leader is not None) and (rule.leader!="oathbreaker" or signature is not None)
    return {"name":name.strip(),"format":rule.label,"colors":list(normalized_colors),"strategy":strategy,"theme":theme,"focus":focus,"focus_mode":focus_mode,"matched_sets":matched_sets,"target_size":rule.size,"total_cards":total,"complete":total==rule.size and structure_complete,"land_count":land_count,"average_mana_value":round(average_mv,2),"synergy_score":round(synergy_score,1),"quality_score":round(quality_score,1),"mana_sources":{color:mana_sources[color] for color in COLOR_ORDER if mana_sources[color]},"curve":dict(curve),"role_counts":dict(role_counts),"warnings":warnings,"explanation":explanation,"cards":output}


def candidate_from_models(inventory,available:int,reference)->BuildCandidate:
    legalities=json.loads(reference.legalities or "{}") if reference else {}
    keywords=json.loads(reference.keywords or "[]") if reference and reference.keywords else []
    return BuildCandidate(inventory_id=inventory.id,scryfall_id=inventory.scryfall_id,name=(reference.flavor_name or reference.printed_name or reference.name) if reference else inventory.card_name,set_code=inventory.set_code,set_name=(reference.set_name if reference else inventory.set_name),collector_number=inventory.collector_number,image_url=inventory.image_url,available=int(available),type_line=(reference.type_line if reference else inventory.type_line) or "",oracle_text=(reference.oracle_text if reference else "") or "",color_identity=(reference.color_identity if reference else inventory.color_identity) or "",mana_cost=(reference.mana_cost if reference else "") or "",mana_value=float((reference.mana_value if reference else None) or 0),rarity=(reference.rarity if reference else inventory.rarity) or "",legalities=legalities,keywords=keywords,oracle_id=(reference.oracle_id if reference else inventory.oracle_id) or "")
