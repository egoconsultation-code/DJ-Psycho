import hashlib
import base64
from urllib.parse import urlparse, parse_qs
import pytest
from playlist_maker.models import Track, parse_track, unique_tracks, fit_duration, playlist_fingerprint
from playlist_maker.auth import pkce_pair, authorization_url, AuthBroker, SCOPE
from playlist_maker.planner import demo_plan


def track(i=1, ms=180000, name=None):
    identifier = str(i).zfill(22)
    return Track(identifier, name or f'Song {i}', ('Artist',), ms, f'spotify:track:{identifier}')


def raw(**overrides):
    item = {'type': 'track', 'id': 'a' * 22, 'name': 'Real search result',
            'artists': [{'name': 'Artist'}], 'duration_ms': 180001, 'uri': 'spotify:track:' + 'a' * 22}
    return item | overrides


@pytest.mark.parametrize('changes', [dict(is_local=True), dict(is_playable=False),
    dict(restrictions={'reason': 'market'}), dict(id='invented'), dict(duration_ms=0),
    dict(duration_ms=True), dict(uri='spotify:track:wrong'), dict(artists=[]), dict(type='episode')])
def test_unavailable_or_invalid_tracks_rejected(changes):
    assert parse_track(raw(**changes)) is None


def test_parse_real_spotify_shape():
    assert parse_track(raw()).duration_ms == 180001


def test_dedup_ids_and_recordings():
    original = track(1, name='Song')
    remaster = track(2, name='Song - 2011 Remaster')
    assert unique_tracks([original, original, remaster]) == [original]
    assert len(unique_tracks([original, original], True)) == 2


def test_duration_picks_best_combination_and_keeps_order():
    candidates = [track(1, 240000), track(2, 180000), track(3, 180000)]
    assert fit_duration(candidates, 360000) == candidates[1:]
    assert sum(t.duration_ms for t in fit_duration(candidates, 500000)) <= 500000


def test_fractional_seconds_never_exceed_target():
    candidates = [track(1, 180001), track(2, 179999)]
    assert sum(t.duration_ms for t in fit_duration(candidates, 359999)) <= 359999
    assert fit_duration([track(1, 500000)], 100000) == []


def test_fingerprint_changes_on_edit():
    a, b = track(), track(2)
    assert playlist_fingerprint('N', '', [a, b]) != playlist_fingerprint('N', '', [b, a])
    assert playlist_fingerprint('N', '', [a]) != playlist_fingerprint('New', '', [a])


def test_pkce_s256_and_scopes():
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    assert challenge == expected
    query = parse_qs(urlparse(authorization_url('id', 'http://127.0.0.1:8888/callback', 'state', challenge)).query)
    assert query['state'] == ['state']
    assert query['code_challenge_method'] == ['S256']
    assert 'playlist-modify-public' not in query['scope'][0]
    assert 'playlist-modify-private' in query['scope'][0]


def test_oauth_cookie_state_and_expiry():
    clock = [0]
    broker = AuthBroker('client', 'http://127.0.0.1:8888/callback', clock=lambda: clock[0])
    state, claim, _ = broker.begin()
    _, cookie = broker.start(state)
    with pytest.raises(ValueError):
        broker.complete(state, 'bad-cookie', code='code')
    with pytest.raises(ValueError):
        broker.claim(state, 'wrong-claim')
    assert broker.claim(state, claim) is None
    clock[0] = 601
    with pytest.raises(ValueError):
        broker.complete(state, cookie, code='code')


def test_oauth_success_one_time_and_refresh_retained(monkeypatch):
    broker = AuthBroker('client', 'http://127.0.0.1:8888/callback')
    state, claim, _ = broker.begin()
    _, cookie = broker.start(state)
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'access_token': 'test-only', 'refresh_token': 'refresh-test',
                               'scope': SCOPE, 'expires_in': 3600}
    seen = {}
    def post(url, **kwargs):
        seen.update(kwargs['data'])
        return Response()
    monkeypatch.setattr('playlist_maker.auth.requests.post', post)
    broker.complete(state, cookie, code='test-code')
    assert seen['code_verifier']
    assert 'client_secret' not in seen
    assert broker.claim(state, claim)['refresh_token'] == 'refresh-test'
    with pytest.raises(ValueError):
        broker.claim(state, claim)


def test_denied_login_and_replay():
    broker = AuthBroker('client', 'http://127.0.0.1:8888/callback')
    state, claim, _ = broker.begin()
    _, cookie = broker.start(state)
    broker.complete(state, cookie, error='access_denied')
    with pytest.raises(ValueError):
        broker.complete(state, cookie, code='replayed')
    with pytest.raises(ValueError, match='отменён'):
        broker.claim(state, claim)


@pytest.mark.parametrize('prompt', ['джаз для вечера', 'French songs', 'musique électronique'])
def test_demo_has_queries_not_fake_ids(prompt):
    queries = demo_plan(prompt)
    assert len(queries) == 6
    assert all('spotify:' not in q for q in queries)
