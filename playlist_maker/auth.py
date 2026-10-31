"""Local PKCE callback server. Tokens remain in memory, never in URLs or files."""
import base64
import hashlib
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
import threading
import time
from urllib.parse import urlencode, urlparse, parse_qs
import requests
from .spotify import TOKEN_URL

SCOPE = 'playlist-modify-private playlist-read-private'


def pkce_pair():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return verifier, challenge


def authorization_url(client_id, redirect_uri, state, challenge):
    return 'https://accounts.spotify.com/authorize?' + urlencode({
        'client_id': client_id, 'response_type': 'code', 'redirect_uri': redirect_uri,
        'code_challenge_method': 'S256', 'code_challenge': challenge,
        'state': state, 'scope': SCOPE})


class AuthBroker:
    def __init__(self, client_id, redirect_uri, clock=time.time):
        parsed = urlparse(redirect_uri)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.path != '/callback' \
                or parsed.port != 8888 or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('Локальная версия использует http://127.0.0.1:8888/callback.')
        self.client_id, self.redirect_uri, self.clock = client_id, redirect_uri, clock
        self.pending = {}
        self.lock = threading.Lock()

    def begin(self):
        verifier, challenge = pkce_pair()
        state, claim = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.lock:
            self.pending = {k: v for k, v in self.pending.items() if v['expires'] > self.clock()}
            self.pending[state] = {'verifier': verifier, 'challenge': challenge,
                'claim': claim, 'expires': self.clock() + 600, 'started': False}
        return state, claim, f'http://127.0.0.1:8888/login?request={state}'

    def start(self, state):
        with self.lock:
            item = self.pending.get(state)
            if not item or item['expires'] <= self.clock() or item['started']:
                raise ValueError('Ссылка входа истекла или уже использована. Начните вход заново.')
            item['started'] = True
            cookie = secrets.token_urlsafe(32)
            item['cookie'] = cookie
            return authorization_url(self.client_id, self.redirect_uri, state, item['challenge']), cookie

    def complete(self, state, cookie, code=None, error=None):
        with self.lock:
            item = self.pending.get(state)
            if not item or item['expires'] <= self.clock() or not cookie or \
                    not hmac.compare_digest(item.get('cookie', ''), cookie) or item.get('used'):
                raise ValueError('Проверка безопасности OAuth не пройдена. Начните вход заново.')
            item['used'] = True
            verifier = item.pop('verifier')
        if error or not code:
            item['error'] = 'Вход отменён или Spotify не вернул код. Начните заново.'
            return
        try:
            response = requests.post(TOKEN_URL, data={'grant_type': 'authorization_code',
                'code': code, 'redirect_uri': self.redirect_uri, 'client_id': self.client_id,
                'code_verifier': verifier}, timeout=20)
            response.raise_for_status()
            token = response.json()
            if not token.get('access_token'):
                raise ValueError('No access token')
            granted = set(token.get('scope', '').split())
            if not set(SCOPE.split()).issubset(granted):
                raise ValueError('Missing required scopes')
            token['expires_at'] = self.clock() + int(token.get('expires_in', 3600))
            item['token'] = token
        except (requests.RequestException, ValueError, TypeError):
            item['error'] = 'Не удалось завершить вход. Проверьте Client ID, Redirect URI и доступ аккаунта.'

    def claim(self, state, claim):
        with self.lock:
            item = self.pending.get(state)
            if not item or item['expires'] <= self.clock() or not hmac.compare_digest(item['claim'], claim):
                raise ValueError('Время входа истекло. Начните заново.')
            if item.get('error'):
                del self.pending[state]
                raise ValueError(item['error'])
            if 'token' not in item:
                return None
            del self.pending[state]
            return item['token']

    def serve(self):
        broker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Never log OAuth codes, states, or cookies.

            def send_page(self, text, status=200):
                body = ('<!doctype html><html lang="ru"><meta charset="utf-8">'
                        '<title>Spotify — вход</title><body><p>' + text + '</p></body></html>').encode()
                self.send_response(status)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'")
                self.send_header('Set-Cookie', 'spotify_pkce=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0')
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                # Only loopback Host is accepted, avoiding DNS rebinding.
                if self.headers.get('Host') != '127.0.0.1:8888':
                    self.send_page('Неверный адрес.', 400)
                    return
                url = urlparse(self.path)
                params = parse_qs(url.query)
                try:
                    if url.path == '/login':
                        location, cookie = broker.start(params.get('request', [''])[0])
                        self.send_response(302)
                        self.send_header('Location', location)
                        self.send_header('Cache-Control', 'no-store')
                        self.send_header('Referrer-Policy', 'no-referrer')
                        self.send_header('Set-Cookie', f'spotify_pkce={cookie}; HttpOnly; SameSite=Lax; Path=/; Max-Age=600')
                        self.end_headers()
                    elif url.path == '/callback':
                        cookies = SimpleCookie(self.headers.get('Cookie', ''))
                        cookie = cookies.get('spotify_pkce')
                        broker.complete(params.get('state', [''])[0], cookie.value if cookie else '',
                            params.get('code', [None])[0], params.get('error', [None])[0])
                        self.send_page('Вернитесь в исходную вкладку приложения и нажмите «Проверить вход». '
                                       'Эту вкладку можно закрыть.')
                    else:
                        self.send_page('Страница не найдена.', 404)
                except (ValueError, KeyError):
                    self.send_page('Ссылка недействительна или истекла. Начните вход заново в приложении.', 400)

        server = ThreadingHTTPServer(('127.0.0.1', 8888), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server
