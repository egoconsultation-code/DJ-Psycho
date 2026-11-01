from pathlib import Path
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / 'app.py')


def test_demo_interface_without_keys(monkeypatch):
    monkeypatch.delenv('SPOTIFY_CLIENT_ID', raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    app = AppTest.from_file(APP).run(timeout=20)
    assert not app.exception
    assert any('Spotify AI Playlist Maker' in title.value for title in app.title)
    app.text_area[0].set_value('Спокойный джаз для вечера')
    app.button[0].click().run()
    assert not app.exception
    assert len(app.session_state['queries']) == 6
    assert app.session_state['tracks'] == []
    search = next(b for b in app.button if b.label == '🔎 Найти реальные треки в Spotify')
    assert search.disabled


def test_blank_prompt_does_not_generate(monkeypatch):
    monkeypatch.delenv('SPOTIFY_CLIENT_ID', raising=False)
    app = AppTest.from_file(APP).run(timeout=20)
    app.button[0].click().run()
    assert not app.exception
    assert app.warning
    assert app.session_state['queries'] == []


def prepared_app(monkeypatch):
    from test_core import track
    monkeypatch.delenv('SPOTIFY_CLIENT_ID', raising=False)
    app = AppTest.from_file(APP).run(timeout=20)
    app.session_state['token'] = {'access_token': 'test-only'}
    app.session_state['tracks'] = [track(1), track(2), track(3)]
    return app.run()


def test_editor_and_confirmation_gate(monkeypatch):
    app = prepared_app(monkeypatch)
    create = next(b for b in app.button if b.label == 'Создать приватный плейлист')
    assert create.disabled
    assert not app.session_state['confirmed']
    app.button(key='down_0').click().run()
    assert [t.id for t in app.session_state['tracks']][0].endswith('2')
    app.button(key='delete_0').click().run()
    assert len(app.session_state['tracks']) == 2
    assert not app.exception


def test_confirmed_write_is_once_and_verified(monkeypatch):
    import playlist_maker.spotify as spotify
    calls = []
    class FakeClient:
        def __init__(self, *args): pass
        def validate_tracks(self, tracks): calls.append('preflight')
        def create_private(self, name, description):
            calls.append('create')
            return 'p'*22
        def replace_items(self, identifier, tracks): calls.append('items')
        def verify_playlist(self, identifier, tracks):
            calls.append('verify')
            return 'https://open.spotify.com/playlist/' + identifier
    monkeypatch.setattr(spotify, 'SpotifyClient', FakeClient)
    app = prepared_app(monkeypatch)
    app.checkbox(key='confirmed').check().run()
    next(b for b in app.button if b.label == 'Создать приватный плейлист').click().run()
    assert not app.exception
    assert calls == ['preflight', 'create', 'items', 'verify']
    assert 'finished' in app.session_state
    app.run()
    assert calls.count('create') == 1


def test_uncertain_creation_cannot_auto_retry(monkeypatch):
    import playlist_maker.spotify as spotify
    from playlist_maker.spotify import UncertainWrite
    calls = []
    class FakeClient:
        def __init__(self, *args): pass
        def validate_tracks(self, tracks): pass
        def create_private(self, *args):
            calls.append('create')
            raise UncertainWrite('Unknown outcome')
    monkeypatch.setattr(spotify, 'SpotifyClient', FakeClient)
    app = prepared_app(monkeypatch)
    app.checkbox(key='confirmed').check().run()
    next(b for b in app.button if b.label == 'Создать приватный плейлист').click().run()
    assert not app.exception
    assert app.session_state['write']['phase'] == 'uncertain'
    app.run()
    assert calls == ['create']
    assert not any(b.label == 'Создать приватный плейлист' for b in app.button)


def assert_no_internal_output(app):
    assert not app.exception
    assert not any('DeltaGenerator' in element.value for element in app.code)
    assert not any('DeltaGenerator' in element.value for element in app.markdown)
    assert not any('DeltaGenerator' in str(element.proto) for element in app.get('help_info'))


def test_sidebar_status_never_renders_delta_generator():
    app = AppTest.from_file(APP).run(timeout=20)
    assert any(info.value == 'Spotify пока не подключён' for info in app.sidebar.info)
    assert_no_internal_output(app)
    app.session_state['token'] = {'access_token': 'test-only'}
    app.run()
    assert any(message.value == 'Spotify подключён' for message in app.sidebar.success)
    assert_no_internal_output(app)


def test_sidebar_pkce_login_buttons(monkeypatch):
    from playlist_maker.auth import AuthBroker

    class LocalTestBroker(AuthBroker):
        def serve(self):
            return None  # UI test does not start a network service.

        def claim(self, state, claim):
            return {'access_token': 'test-only'}

    monkeypatch.setattr('playlist_maker.auth.AuthBroker', LocalTestBroker)
    monkeypatch.setenv('SPOTIFY_CLIENT_ID', 'sidebar-ui-test-client')
    app = AppTest.from_file(APP).run(timeout=20)
    prepare = next(b for b in app.sidebar.button if b.label == 'Подготовить вход Spotify')
    assert not prepare.disabled
    prepare.click().run()
    assert_no_internal_output(app)
    links = app.sidebar.get('link_button')
    login = next(link for link in links if link.proto.label == 'Войти в Spotify ↗')
    assert login.proto.url.startswith('http://127.0.0.1:8888/login?request=')
    next(b for b in app.sidebar.button if b.label == 'Проверить вход').click().run()
    assert_no_internal_output(app)
    assert any(message.value == 'Spotify подключён' for message in app.sidebar.success)
