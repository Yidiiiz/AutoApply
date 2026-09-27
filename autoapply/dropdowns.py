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
