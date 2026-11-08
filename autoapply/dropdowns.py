"""Choose observed autocomplete options using explicit applicant preferences."""
import re
from .jobs import normalize


def dropdown_key(label):
    from .concepts import canonical_key
    key = getattr(label, 'semantic_key', None) or canonical_key(label)
    return key if key in {'education.school', 'contact.city'} else None


def query_for(label, profile):
    key = dropdown_key(label)
    if not key:
        return None
    section, name = key.split('.')
    return profile.get(section, {}).get(name)


def choose_option(label, options, profile):
    key = dropdown_key(label)
    if not key:
        return None
    value = query_for(label, profile)
    if not value:
        return None
    if key == 'education.school':
        preferences = profile.get('dropdown_preferences', {}).get(key, [value])
        for preferred in preferences:
            # Preserve preference ordering even when punctuation normalization merges aliases.
            exact = [o for o in options if o.strip().casefold() == preferred.strip().casefold()]
            if len(exact) == 1:
                return exact[0]
        for preferred in preferences:
            matches = [o for o in options if normalize(o) == normalize(preferred)]
            if len(matches) == 1:
                return matches[0]
        return None
    state = normalize(profile.get('contact', {}).get('state', ''))
    # Explicit state aliases avoid mistaking similarly named cities for the residence.
    aliases = {state}
    pairs = 'AL:Alabama|AK:Alaska|AZ:Arizona|AR:Arkansas|CA:California|CO:Colorado|CT:Connecticut|DE:Delaware|FL:Florida|GA:Georgia|HI:Hawaii|ID:Idaho|IL:Illinois|IN:Indiana|IA:Iowa|KS:Kansas|KY:Kentucky|LA:Louisiana|ME:Maine|MD:Maryland|MA:Massachusetts|MI:Michigan|MN:Minnesota|MS:Mississippi|MO:Missouri|MT:Montana|NE:Nebraska|NV:Nevada|NH:New Hampshire|NJ:New Jersey|NM:New Mexico|NY:New York|NC:North Carolina|ND:North Dakota|OH:Ohio|OK:Oklahoma|OR:Oregon|PA:Pennsylvania|RI:Rhode Island|SC:South Carolina|SD:South Dakota|TN:Tennessee|TX:Texas|UT:Utah|VT:Vermont|VA:Virginia|WA:Washington|WV:West Virginia|WI:Wisconsin|WY:Wyoming|DC:District of Columbia'
    for pair in pairs.split('|'):
        short, full = pair.split(':')
        if state in {normalize(short), normalize(full)}:
            aliases.update({normalize(short), normalize(full)})
    matches = []
    for option in options:
        parts = [normalize(p) for p in re.split(r'[,;]', option)]
        if (parts[0] == normalize(value) and len(parts) > 1 and parts[1] in aliases
                and (len(parts) == 2 or parts[2] in {'us', 'usa', 'united states', 'united states of america'})):
            matches.append(option)
    return matches[0] if len(matches) == 1 else None


ORDERED_LOCATIONS_SETTING = 'known_answer_signature:ordered_location_preferences'


def location_ordinal(label):
    text = normalize(label)
    match = re.match(r'^(?:what is |please (?:select|provide) )?(?:your )?(1st|2nd|3rd|first|second|third|top|primary) location preference\b', text)
    return {'1st': 1, 'first': 1, 'top': 1, 'primary': 1, '2nd': 2, 'second': 2, '3rd': 3, 'third': 3}.get(match[1]) if match else None


def us_state(value):
    from .profile_rules import STATES
    return next((short for short, full in STATES.items()
                 if isinstance(value, str) and normalize(value) in {short.lower(), normalize(full)}), None)


def location_identity(value):
    """Strict U.S. city/state identity with optional nongeographic office annotation."""
    if not isinstance(value, str):
        return None
    # Strip only a known office annotation, never geographic parentheticals.
    text = re.sub(r'\s*\((?:HQ|headquarters)\)\s*$', '', value, flags=re.I)
    if re.search(r'[()]', text):
        return None
    parts = [normalize(part) for part in text.split(',')]
    if len(parts) not in {2, 3} or len(parts) == 3 and parts[2] not in {
            'us', 'u s', 'usa', 'u s a', 'united states', 'united states of america'}:
        return None
    state = us_state(parts[1])
    city = 'new york' if parts[0] == 'new york city' else parts[0]
    return (city, state) if city and state else None


def ordered_locations(profile, saved=None):
    base = profile.get('locations', {}).get('ordered_preferences') or []
    learned = saved.get('value', []) if isinstance(saved, dict) and saved.get('source') == 'USER_PROVIDED' else []
    result, seen = [], set()
    for value in list(learned) + list(base):
        identity = location_identity(value)
        if identity and identity not in seen:
            result.append(value)
            seen.add(identity)
    return result


def ranked_location(label, options, preferences, preferred_states=None):
    rank = location_ordinal(label)
    if not rank:
        return None
    offered = {}
    for option in options:
        identity = location_identity(option)
        if identity:
            offered.setdefault(identity, []).append(option)
    ordered = list(dict.fromkeys(identity for value in preferences if (identity := location_identity(value))))
    states = list(dict.fromkeys(state for value in preferred_states or [] if (state := us_state(value))))
    tiers, used = [], set()
    for state in states:
        for identity in ordered:
            if identity[1] == state and identity in offered:
                tiers.append(offered[identity])
                used.add(identity)
        # State preference authorizes other cities there. Unranked cities tied
        # within one state are ambiguous; menu order cannot resolve that tie.
        others = [option for identity, labels in offered.items()
                  if identity[1] == state and identity not in used for option in labels]
        if others:
            tiers.append(others)
        used.update(identity for identity in offered if identity[1] == state)
    tiers.extend(offered[identity] for identity in ordered if identity in offered and identity not in used)
    for choices in tiers:
        if len(choices) != 1:
            return None
        if rank == 1:
            return choices[0]
        rank -= 1
    return None
