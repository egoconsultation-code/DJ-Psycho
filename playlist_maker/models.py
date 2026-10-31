from dataclasses import dataclass
import hashlib
import json
import re


@dataclass(frozen=True)
class Track:
    id: str
    name: str
    artists: tuple[str, ...]
    duration_ms: int
    uri: str

    @property
    def url(self):
        return f"https://open.spotify.com/track/{self.id}"


def parse_track(item):
    """Accept only Spotify search results with usable IDs and durations."""
    if not isinstance(item, dict) or item.get('type') != 'track':
        return None
    identifier = item.get('id', '')
    duration = item.get('duration_ms')
    if not re.fullmatch(r'[A-Za-z0-9]{22}', identifier):
        return None
    if item.get('is_local') or item.get('is_playable') is False or item.get('restrictions'):
        return None
    if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
        return None
    artists = tuple(a['name'] for a in item.get('artists', []) if a.get('name'))
    if not artists or not item.get('name') or item.get('uri') != f'spotify:track:{identifier}':
        return None
    return Track(identifier, item['name'], artists, duration, item['uri'])


def duration_label(ms):
    seconds = ms // 1000
    return f'{seconds // 60}:{seconds % 60:02d}'


def recording_key(track):
    # Different album editions of the same recording should not repeat either.
    clean = lambda value: re.sub(r'[^\w]', '', value.casefold())
    title = re.sub(r'\s*[-(]\s*(?:\d{4}\s*)?remaster.*$', '', track.name, flags=re.I)
    return clean(title), tuple(clean(a) for a in track.artists)


def unique_tracks(tracks, allow_duplicates=False):
    result, ids, recordings = [], set(), set()
    for track in tracks:
        key = recording_key(track)
        if allow_duplicates or (track.id not in ids and key not in recordings):
            result.append(track)
            ids.add(track.id)
            recordings.add(key)
    return result


def fit_duration(tracks, target_ms, allow_duplicates=False):
    """Knapsack over seconds: maximize occupied time, never exceed the budget."""
    candidates = unique_tracks(tracks, allow_duplicates)
    # Round tracks UP and budget DOWN so rounding cannot exceed the real limit.
    budget = target_ms // 1000
    states = {0: ()}
    for index, track in enumerate(candidates):
        cost = (track.duration_ms + 999) // 1000
        for total, chosen in list(states.items()):
            new = total + cost
            if new <= budget and new not in states:
                states[new] = (*chosen, index)
    return [candidates[i] for i in states[max(states)]]


def playlist_fingerprint(name, description, tracks):
    return hashlib.sha256(json.dumps([name, description, [t.id for t in tracks]],
                                     ensure_ascii=False).encode()).hexdigest()
