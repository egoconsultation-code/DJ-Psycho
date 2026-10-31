import time
import requests
from .models import parse_track

API = 'https://api.spotify.com/v1'
TOKEN_URL = 'https://accounts.spotify.com/api/token'


class SpotifyError(Exception):
    pass


class AuthorizationError(SpotifyError):
    pass


class UncertainWrite(SpotifyError):
    """A write may have reached Spotify: never repeat automatically."""


class SpotifyClient:
    def __init__(self, client_id, token, session=None, sleep=time.sleep, clock=time.time):
        self.client_id, self.token = client_id, token
        self.http = session or requests.Session()
        self.sleep, self.clock = sleep, clock

    def _refresh(self):
        refresh = self.token.get('refresh_token')
        if not refresh:
            raise AuthorizationError('Войдите в Spotify заново.')
        try:
            r = self.http.post(TOKEN_URL, data={'grant_type': 'refresh_token',
                'refresh_token': refresh, 'client_id': self.client_id}, timeout=20)
        except requests.RequestException as exc:
            raise AuthorizationError('Не удалось обновить вход Spotify. Повторите позже.') from exc
        if r.status_code != 200:
            raise AuthorizationError('Сессия Spotify истекла или отозвана. Войдите заново.')
        try:
            data = r.json()
            access = data['access_token']
            expires = int(data.get('expires_in', 3600))
        except (ValueError, KeyError, TypeError) as exc:
            raise AuthorizationError('Некорректный ответ авторизации Spotify.') from exc
        self.token.update(data)
        self.token['access_token'] = access
        self.token['expires_at'] = self.clock() + expires

    def request(self, method, path, **kwargs):
        if self.token.get('expires_at', 0) <= self.clock() + 30:
            self._refresh()
        refreshed = False
        for attempt in range(3):
            try:
                r = self.http.request(method, API + path,
                    headers={'Authorization': 'Bearer ' + self.token['access_token']},
                    timeout=25, **kwargs)
            except requests.RequestException as exc:
                if method != 'GET':
                    raise UncertainWrite('Связь прервалась во время записи. Проверьте Spotify: '
                                         'операция могла выполниться. Автоповтор отключён.') from exc
                raise SpotifyError('Не удалось связаться со Spotify. Проверьте сеть.') from exc
            if r.status_code == 401:
                if refreshed:
                    raise AuthorizationError('Spotify отклонил вход. Авторизуйтесь заново.')
                self._refresh()
                refreshed = True
                continue
            if r.status_code == 429:
                try:
                    delay = max(1, int(r.headers.get('Retry-After', '5')))
                except (ValueError, TypeError):
                    delay = 5
                if delay > 10 or attempt == 2:
                    raise SpotifyError(f'Лимит Spotify API. Повторите не раньше чем через {delay} сек.')
                self.sleep(delay)
                continue
            if r.status_code == 403:
                raise SpotifyError('Spotify запретил операцию (403). Проверьте Premium владельца '
                    'приложения, список разрешённых пользователей Development Mode и OAuth scopes.')
            if r.status_code >= 500 and method != 'GET':
                raise UncertainWrite('Spotify вернул ошибку при записи. Проверьте аккаунт перед '
                                     'повтором: операция могла выполниться.')
            if not 200 <= r.status_code < 300:
                raise SpotifyError(f'Ошибка Spotify API ({r.status_code}). '
                                   'Проверьте доступность трека и настройки приложения.')
            if not r.content:
                return {}
            try:
                return r.json()
            except ValueError as exc:
                if method != 'GET':
                    raise UncertainWrite('Некорректный ответ записи. Проверьте результат в Spotify.') from exc
                raise SpotifyError('Spotify вернул некорректный JSON.') from exc
        raise AuthorizationError('Не удалось завершить авторизацию. Войдите заново.')

    def me(self):
        return self.request('GET', '/me')

    def search(self, query):
        # Development Mode 2026: use at most 10 results; no Recommendations/Audio Features.
        data = self.request('GET', '/search', params={'q': query, 'type': 'track', 'limit': 10})
        return [track for item in data.get('tracks', {}).get('items', [])
                if (track := parse_track(item)) is not None]

    def validate_tracks(self, tracks):
        for identifier in dict.fromkeys(t.id for t in tracks):
            data = self.request('GET', f'/tracks/{identifier}')
            checked = parse_track(data)
            if checked is None or checked.id != identifier:
                raise SpotifyError('Один из треков больше недоступен. Вернитесь к подборке '
                                   'и замените его. Плейлист не создан.')

    def create_private(self, name, description):
        # Current /me endpoint; never silently fall back to a second creation request.
        data = self.request('POST', '/me/playlists', json={
            'name': name, 'description': description, 'public': False})
        identifier = data.get('id', '')
        import re
        if not re.fullmatch(r'[A-Za-z0-9]{22}', identifier) or data.get('public') is not False:
            raise UncertainWrite('Spotify не подтвердил приватный плейлист. Проверьте аккаунт; '
                                 'добавление треков остановлено.')
        return identifier

    def replace_items(self, identifier, tracks):
        # PUT replaces the entire draft (<=100 tracks), so retries cannot append duplicates.
        self.request('PUT', f'/playlists/{identifier}/items',
                     json={'uris': [t.uri for t in tracks]})

    def verify_playlist(self, identifier, tracks):
        data = self.request('GET', f'/playlists/{identifier}')
        container = data.get('items', data.get('tracks', {}))
        rows = container.get('items', [])
        actual = [row.get('item', row.get('track')) for row in rows]
        actual_ids = [item.get('id') if isinstance(item, dict) else None for item in actual]
        if (data.get('public') is not False or actual_ids != [t.id for t in tracks] or container.get('next')
                or any(not isinstance(item, dict) or item.get('is_playable') is False
                       or item.get('restrictions') for item in actual)):
            raise SpotifyError('Spotify не подтвердил приватность или полный порядок треков. '
                               'Проверьте плейлист вручную.')
        return f'https://open.spotify.com/playlist/{identifier}'
