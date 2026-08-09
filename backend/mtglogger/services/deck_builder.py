import json
from collections import Counter
from dataclasses import dataclass


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


def _select_quantity(card:BuildCandidate,rule:FormatRule,remaining:int)->int:
    restricted=rule.legality and card.legalities.get(rule.legality)=="restricted"
    maximum=1 if (rule.singleton and not card.is_basic) or restricted else (card.available if card.is_basic else min(4,card.available))
    return max(0,min(maximum,remaining))


def build_deck(candidates:list[BuildCandidate],format_name:str,colors:list[str],strategy:str="balanced",name:str="Auto-built deck")->dict:
    rule=normalize_format(format_name);normalized_colors=normalize_colors(colors)
    strategy=strategy.strip().casefold() or "balanced"
    if strategy not in {"balanced","aggro","midrange","control",*THEMES}:
        raise ValueError(f"Unsupported strategy: {strategy}")
    legal=[card for card in candidates if card.available>0 and legal_for(card,rule,normalized_colors)]
    spells=[card for card in legal if not card.is_land];lands=[card for card in legal if card.is_land]
    theme=choose_theme(spells,strategy)
    leader=choose_leader(spells,rule,normalized_colors,theme)
    selected:dict[str,dict]={}
    warnings:list[str]=[]
    if rule.leader and not leader:warnings.append(f"No legal {rule.leader} matching the selected colors is unassigned.")
    if leader:
        selected[leader.inventory_id]={"card":leader,"quantity":1,"role":"leader","score":leader_score(leader,normalized_colors,theme),"reasons":[f"best available {rule.leader} for the selected colors",f"supports the {theme} plan"]}
    signature=None
    if rule.leader=="oathbreaker":
        eligible_signatures=[card for card in spells if card is not leader and ("Instant" in card.type_line or "Sorcery" in card.type_line)]
        if eligible_signatures:
            signature=max(eligible_signatures,key=lambda card:card_score(card,strategy,theme)[0])
            score,reasons=card_score(signature,strategy,theme)
            selected[signature.inventory_id]={"card":signature,"quantity":1,"role":"signature spell","score":score,"reasons":["best available signature spell",*reasons]}
        else:warnings.append("No legal instant or sorcery is available for the Oathbreaker's signature spell.")
    scored=[]
    for card in spells:
        if card.inventory_id in selected:continue
        score,reasons=card_score(card,strategy,theme)
        scored.append((score,card,reasons,classify_roles(card)))
    scored.sort(key=lambda item:(item[0],-item[1].mana_value,item[1].name),reverse=True)
    provisional_land_target=rule.base_lands
    spell_target=rule.size-provisional_land_target-(1 if leader else 0)
    quotas=(
        {"ramp":10,"draw":10,"interaction":10,"board wipe":3,"protection":4}
        if rule.size==100 else {"ramp":4,"draw":5,"interaction":6,"board wipe":2,"protection":2}
    )
    spell_count=1 if signature else 0
    for role,quota in quotas.items():
        role_count=0
        for score,card,reasons,roles in scored:
            if spell_count>=spell_target or role_count>=quota:break
            if role not in roles or card.inventory_id in selected:continue
            quantity=_select_quantity(card,rule,min(quota-role_count,spell_target-spell_count))
            if not quantity:continue
            selected[card.inventory_id]={"card":card,"quantity":quantity,"role":role,"score":score,"reasons":reasons}
            role_count+=quantity;spell_count+=quantity
    for score,card,reasons,roles in scored:
        if spell_count>=spell_target:break
        if card.inventory_id in selected:continue
        quantity=_select_quantity(card,rule,spell_target-spell_count)
        if not quantity:continue
        primary=next(iter(sorted(roles,key=lambda role:{"threat":0,"interaction":1,"draw":2,"ramp":3,"protection":4,"board wipe":5,"utility":6}.get(role,9))))
        selected[card.inventory_id]={"card":card,"quantity":quantity,"role":primary,"score":score,"reasons":reasons}
        spell_count+=quantity
    chosen_spells=[entry for entry in selected.values() if entry["role"]!="leader"]
    weighted_mv=sum(entry["card"].mana_value*entry["quantity"] for entry in chosen_spells)
    spell_copies=sum(entry["quantity"] for entry in chosen_spells)
    average_mv=weighted_mv/max(1,spell_copies)
    land_adjust=round((average_mv-3)*2)
    land_target=max(rule.base_lands-4,min(rule.base_lands+4,rule.base_lands+land_adjust))
    desired_spell_total=rule.size-land_target-(1 if leader else 0)
    if spell_count<desired_spell_total:
        for score,card,reasons,roles in scored:
            if spell_count>=desired_spell_total:break
            if card.inventory_id in selected:continue
            quantity=_select_quantity(card,rule,desired_spell_total-spell_count)
            if not quantity:continue
            primary=next(iter(sorted(roles,key=lambda role:{"threat":0,"interaction":1,"draw":2,"ramp":3,"protection":4,"board wipe":5,"utility":6}.get(role,9))))
            selected[card.inventory_id]={"card":card,"quantity":quantity,"role":primary,"score":score,"reasons":reasons}
            spell_count+=quantity
    while spell_count>desired_spell_total:
        removable=min((entry for entry in selected.values() if entry["role"]!="leader"),key=lambda entry:entry["score"],default=None)
        if not removable:break
        removable["quantity"]-=1;spell_count-=1
        if removable["quantity"]<=0:selected.pop(removable["card"].inventory_id,None)
    land_count=0
    for card in sorted(lands,key=lambda item:(land_score(item,normalized_colors),item.available),reverse=True):
        if land_count>=land_target:break
        quantity=_select_quantity(card,rule,land_target-land_count)
        if not quantity:continue
        selected[card.inventory_id]={"card":card,"quantity":quantity,"role":"land","score":land_score(card,normalized_colors),"reasons":["supports the selected colors and mana requirements"]}
        land_count+=quantity
    total=sum(entry["quantity"] for entry in selected.values())
    if spell_count<desired_spell_total:warnings.append(f"Only {spell_count} of {desired_spell_total} desired nonland cards were available and legal.")
    if land_count<land_target:warnings.append(f"Only {land_count} of {land_target} recommended lands were available; scan or free additional lands before playing.")
    if total<rule.size:warnings.append(f"The collection is {rule.size-total} cards short of a complete {rule.label} deck in these colors.")
    role_counts=Counter()
    output=[]
    for entry in sorted(selected.values(),key=lambda item:(item["role"]!="leader",item["role"]!="land",item["card"].mana_value,item["card"].name)):
        card=entry["card"];quantity=entry["quantity"];role_counts[entry["role"]]+=quantity
        output.append({"inventory_id":card.inventory_id,"scryfall_id":card.scryfall_id,"name":card.name,"set_code":card.set_code,"collector_number":card.collector_number,"image_url":card.image_url,"quantity":quantity,"role":entry["role"],"mana_value":card.mana_value,"score":round(entry["score"],2),"reasons":entry["reasons"]})
    color_label="/".join(COLOR_NAMES[color] for color in normalized_colors)
    explanation=[f"Built from {len(legal)} legal unassigned collection entries in {color_label}.",f"The strongest supported plan is {theme.replace('-', ' ')} with a {strategy} posture.",f"Selected {land_count} lands for an average nonland mana value of {average_mv:.2f}."]
    if leader:explanation.insert(1,f"Selected {leader.name} as the {rule.leader}.")
    if signature:explanation.insert(2,f"Paired it with {signature.name} as the signature spell.")
    structure_complete=(not rule.leader or leader is not None) and (rule.leader!="oathbreaker" or signature is not None)
    return {"name":name.strip(),"format":rule.label,"colors":list(normalized_colors),"strategy":strategy,"theme":theme,"target_size":rule.size,"total_cards":total,"complete":total==rule.size and structure_complete,"land_count":land_count,"average_mana_value":round(average_mv,2),"role_counts":dict(role_counts),"warnings":warnings,"explanation":explanation,"cards":output}


def candidate_from_models(inventory,available:int,reference)->BuildCandidate:
    legalities=json.loads(reference.legalities or "{}") if reference else {}
    keywords=json.loads(reference.keywords or "[]") if reference and reference.keywords else []
    return BuildCandidate(inventory_id=inventory.id,scryfall_id=inventory.scryfall_id,name=(reference.flavor_name or reference.printed_name or reference.name) if reference else inventory.card_name,set_code=inventory.set_code,collector_number=inventory.collector_number,image_url=inventory.image_url,available=int(available),type_line=(reference.type_line if reference else inventory.type_line) or "",oracle_text=(reference.oracle_text if reference else "") or "",color_identity=(reference.color_identity if reference else inventory.color_identity) or "",mana_cost=(reference.mana_cost if reference else "") or "",mana_value=float((reference.mana_value if reference else None) or 0),rarity=(reference.rarity if reference else inventory.rarity) or "",legalities=legalities,keywords=keywords)
