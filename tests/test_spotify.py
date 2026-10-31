import pytest
import requests
from playlist_maker.spotify import SpotifyClient, SpotifyError, AuthorizationError, UncertainWrite
from test_core import raw, track


class Response:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code = status
        self.data = data if data is not None else {}
        self.headers = headers or {}
        self.content = b'json'
    def json(self): return self.data


class Session:
    def __init__(self, responses, refresh=None):
        self.responses = iter(responses)
        self.calls = []
        self.refresh = refresh
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = next(self.responses)
        if isinstance(result, Exception): raise result
        return result
    def post(self, url, **kwargs):
        return self.refresh


def client(responses, **kwargs):
    session = Session(responses, kwargs.pop('refresh', None))
    api = SpotifyClient('client', {'access_token': 'test', 'refresh_token': 'test-refresh',
                                  'expires_at': 99999999999}, session=session, **kwargs)
    return api, session


def test_search_only_uses_spotify_ids_and_development_limit():
    api, session = client([Response(data={'tracks': {'items': [raw(), raw(is_playable=False), raw(id='fake')]}})])
    assert len(api.search('Artist Title')) == 1
    assert session.calls[0][2]['params']['limit'] == 10


def test_429_bounded_retry_and_retry_after():
    sleeps = []
    api, _ = client([Response(429, headers={'Retry-After': '2'}), Response(data={'id': 'me'})], sleep=sleeps.append)
    assert api.me()['id'] == 'me'
    assert sleeps == [2]
    api, _ = client([Response(429, headers={'Retry-After': '120'})])
    with pytest.raises(SpotifyError, match='120'):
        api.me()


def test_401_refresh_then_retry():
    api, session = client([Response(401), Response(data={'id': 'me'})],
                           refresh=Response(data={'access_token': 'new', 'expires_in': 3600}))
    assert api.me()['id'] == 'me'
    assert session.calls[-1][2]['headers']['Authorization'] == 'Bearer new'
    assert api.token['refresh_token'] == 'test-refresh'


def test_refresh_rejection():
    api, _ = client([Response(401)], refresh=Response(400))
    with pytest.raises(AuthorizationError): api.me()


def test_403_explains_development_mode():
    api, _ = client([Response(403)])
    with pytest.raises(SpotifyError, match='Development Mode'): api.me()


@pytest.mark.parametrize('failure', [requests.Timeout(), Response(503)])
def test_uncertain_create_never_retries(failure):
    api, session = client([failure])
    with pytest.raises(UncertainWrite): api.create_private('Name', '')
    assert len(session.calls) == 1


def test_private_creation_and_replace_order_modern_endpoints():
    identifier = 'p' * 22
    tracks = [track(2), track(1)]
    api, session = client([Response(data={'id': identifier, 'public': False}), Response(data={'snapshot_id': 's'}),
        Response(data={'public': False, 'items': {'items': [{'item': {'id': t.id}} for t in tracks], 'next': None}})])
    assert api.create_private('Name', '') == identifier
    api.replace_items(identifier, tracks)
    assert api.verify_playlist(identifier, tracks).endswith(identifier)
    assert session.calls[0][1].endswith('/me/playlists')
    assert session.calls[0][2]['json']['public'] is False
    assert session.calls[1][0] == 'PUT'
    assert session.calls[1][1].endswith('/items')
    assert session.calls[1][2]['json']['uris'] == [t.uri for t in tracks]


def test_verification_requires_private_and_exact_order():
    api, _ = client([Response(data={'public': True, 'items': {'items': []}})])
    with pytest.raises(SpotifyError): api.verify_playlist('p'*22, [track()])


def test_creation_not_confirmed_private_stops():
    api, _ = client([Response(data={'id': 'p'*22, 'public': True})])
    with pytest.raises(UncertainWrite): api.create_private('Name', '')


def test_preflight_detects_tracks_no_longer_available():
    api, _ = client([Response(data=raw(is_playable=False))])
    with pytest.raises(SpotifyError, match='недоступен'):
        api.validate_tracks([track()])


def test_preflight_checks_duplicate_ids_only_once():
    t = track()
    api, session = client([Response(data=raw(id=t.id, uri=t.uri))])
    api.validate_tracks([t, t])
    assert len(session.calls) == 1
