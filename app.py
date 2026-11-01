import os
from pathlib import Path
import streamlit as st
from dotenv import load_dotenv
from playlist_maker.auth import AuthBroker
from playlist_maker.models import duration_label, fit_duration, unique_tracks, playlist_fingerprint, recording_key
from playlist_maker.planner import demo_plan, ai_plan, PlanningError
from playlist_maker.spotify import SpotifyClient, SpotifyError, UncertainWrite

load_dotenv(Path(__file__).with_name('.env'))
st.set_page_config(page_title='Spotify AI Playlist Maker', page_icon='🎧', layout='wide')
st.markdown('''<style>
.stApp {background:linear-gradient(145deg,#0c1411,#12141e);}
.block-container {max-width:1080px;padding-top:2.5rem;}
h1 {letter-spacing:-1.7px;} [data-testid="stMetric"] {border:1px solid #284237;border-radius:14px;padding:14px;}
</style>''', unsafe_allow_html=True)


@st.cache_resource
def auth_service(client_id, redirect):
    broker = AuthBroker(client_id, redirect)
    broker.server = broker.serve()
    return broker


def invalidate():
    st.session_state.pop('confirmed', None)
    st.session_state.pop('replacement', None)
    st.session_state.pop('replace_index', None)


def client():
    return SpotifyClient(os.getenv('SPOTIFY_CLIENT_ID', ''), st.session_state.token)


def edit(index, action):
    tracks = st.session_state.tracks
    if action == 'delete':
        tracks.pop(index)
    elif action == 'up' and index > 0:
        tracks[index - 1], tracks[index] = tracks[index], tracks[index - 1]
    elif action == 'down' and index < len(tracks) - 1:
        tracks[index + 1], tracks[index] = tracks[index], tracks[index + 1]
    invalidate()


st.session_state.setdefault('tracks', [])
st.session_state.setdefault('queries', [])
st.session_state.setdefault('allow_duplicates', False)
st.session_state.setdefault('target_ms', 30 * 60000)
st.title('🎧 Spotify AI Playlist Maker')
st.markdown('### Ваше настроение. Ваша музыка.')
st.caption('Опишите идею на русском, English или français. Проверьте подборку — и только потом сохраните её в Spotify.')

with st.sidebar:
    st.header('Подключения')
    st.caption('Локальная версия · ничего не публикуется')
    connected = 'token' in st.session_state
    if connected:
        st.success('Spotify подключён')
        if st.button('Выйти из Spotify'):
            for key in ['token', 'auth_pending', 'tracks', 'replacement', 'confirmed', 'write', 'finished', 'account']:
                st.session_state.pop(key, None)
            st.rerun()
    else:
        st.info('Spotify пока не подключён')
        cid = os.getenv('SPOTIFY_CLIENT_ID', '').strip()
        if cid:
            try:
                broker = auth_service(cid, os.getenv('SPOTIFY_REDIRECT_URI', 'http://127.0.0.1:8888/callback'))
                if st.button('Подготовить вход Spotify'):
                    state, claim, link = broker.begin()
                    st.session_state.auth_pending = (state, claim, link)
                if 'auth_pending' in st.session_state:
                    state, claim, link = st.session_state.auth_pending
                    st.link_button('Войти в Spotify ↗', link)
                    st.caption('Откроется новая вкладка. После входа вернитесь сюда.')
                    if st.button('Проверить вход'):
                        token = broker.claim(state, claim)
                        if token:
                            st.session_state.token = token
                            st.session_state.pop('auth_pending', None)
                            st.rerun()
                        else:
                            st.info('Завершите вход в открывшейся вкладке.')
            except (ValueError, OSError) as exc:
                st.error(str(exc))
        else:
            st.caption('Добавьте SPOTIFY_CLIENT_ID в .env. Пошаговая инструкция — в README.md.')
    demo = st.toggle('Демо-подбор без OpenAI', value=not bool(os.getenv('OPENAI_API_KEY')))
    st.caption('Демо предлагает небольшой фиксированный набор запросов. Для свободного понимания жанра, эпохи и настроения нужен OpenAI.')
    st.caption('В OpenAI отправляются только ваше описание и целевая длительность. Данные Spotify туда не передаются.')

locked = 'write' in st.session_state or 'finished' in st.session_state
if locked:
    st.info('Подборка зафиксирована на время сохранения. Для другой подборки начните новый черновик ниже.')

with st.form('preferences'):
    prompt = st.text_area('Какой плейлист вы хотите?',
        placeholder='Например: спокойный французский джаз для вечера, 1960–1980 годы', max_chars=2000)
    left, right = st.columns(2)
    minutes = left.number_input('Длительность, минут', min_value=5, max_value=180, value=30, step=5)
    duplicates = right.checkbox('Разрешить повторяющиеся композиции', value=False)
    submitted = st.form_submit_button('✨ Подобрать музыку', disabled=locked)

if submitted:
    if not prompt.strip():
        st.warning('Опишите музыку, которую хотите услышать.')
    else:
        try:
            with st.spinner('Готовлю поисковые запросы…'):
                queries = demo_plan(prompt) if demo else ai_plan(prompt, minutes,
                    os.getenv('OPENAI_API_KEY', ''), os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'))
            st.session_state.queries = queries
            st.session_state.target_ms = minutes * 60000
            st.session_state.allow_duplicates = duplicates
            st.session_state.tracks = []
            st.session_state.missing = []
            invalidate()
        except PlanningError as exc:
            st.error(str(exc))

if st.session_state.queries:
    with st.expander('Поисковые запросы · ещё не треки Spotify', expanded=not connected):
        for query in st.session_state.queries:
            st.write('• ' + query)
        if not connected:
            st.info('Интерфейс работает без ключей. Названия выше — предложения для поиска. '
                    'ID и длительность появятся только после проверки через Spotify.')
    if st.button('🔎 Найти реальные треки в Spotify', disabled=not connected or locked):
        try:
            found, missing = [], []
            with st.spinner('Проверяю композиции в Spotify…'):
                api = client()
                # /me gives the current user's identity; search uses the token's country.
                profile = api.me()
                st.session_state.account = profile.get('display_name') or profile.get('id', 'Spotify')
                for query in st.session_state.queries:
                    results = api.search(query)
                    used = unique_tracks(found)
                    eligible = [t for t in results if st.session_state.allow_duplicates or all(
                        t.id != old.id and recording_key(t) != recording_key(old) for old in used)]
                    if eligible:
                        found.append(eligible[0])
                    else:
                        missing.append(query)
            st.session_state.tracks = fit_duration(found, st.session_state.target_ms,
                                                    st.session_state.allow_duplicates)
            st.session_state.missing = missing
            invalidate()
            if not st.session_state.tracks:
                st.warning('Нет доступных треков в заданном времени. Измените описание или длительность.')
        except SpotifyError as exc:
            st.error(str(exc))

tracks = st.session_state.tracks
if tracks:
    st.divider()
    st.subheader('Ваша подборка')
    total = sum(t.duration_ms for t in tracks)
    a, b, c = st.columns(3)
    a.metric('Композиции', len(tracks))
    b.metric('Общая длительность', duration_label(total))
    c.metric('Цель', duration_label(st.session_state.target_ms))
    if total < st.session_state.target_ms * .85:
        st.warning('Подборка заметно короче цели. Можно добавить треки вручную или изменить запрос. '
                   'В демо доступно только 6 поисковых предложений.')
    st.caption('Длительность не превышает цель; небольшой остаток неизбежен из-за длины целых композиций. '
               'Результаты поиска проверяйте на соответствие настроению и эпохе: алгоритм может ошибаться.')
    for i, track in enumerate(tracks):
        columns = st.columns([7, 1, 1, 1, 1])
        with columns[0]:
            st.write(f'{i+1}. {track.name} — {", ".join(track.artists)}')
            st.caption(duration_label(track.duration_ms))
            st.link_button('Прослушать в Spotify', track.url)
        columns[1].button('↑', key=f'up_{i}', disabled=locked or i == 0,
                           on_click=edit, args=(i, 'up'))
        columns[2].button('↓', key=f'down_{i}', disabled=locked or i == len(tracks)-1,
                           on_click=edit, args=(i, 'down'))
        if columns[3].button('↻', key=f'replace_{i}', disabled=locked, help='Заменить трек'):
            st.session_state.replace_index = i
            st.session_state.pop('replacement', None)
        columns[4].button('✕', key=f'delete_{i}', disabled=locked, help='Удалить трек',
                          on_click=edit, args=(i, 'delete'))

if connected and not locked:
    with st.expander('Добавить или заменить композицию', expanded='replace_index' in st.session_state):
        index = st.session_state.get('replace_index')
        st.caption(f'Замена позиции {index+1}' if index is not None and index < len(tracks) else 'Добавление в конец подборки')
        query = st.text_input('Название композиции и исполнитель', max_chars=250)
        if st.button('Искать композицию', disabled=not query.strip()):
            try:
                st.session_state.replacement = client().search(query.strip())
            except SpotifyError as exc:
                st.error(str(exc))
        options = st.session_state.get('replacement', [])
        if 'replacement' in st.session_state and not options:
            st.info('Доступных композиций не найдено.')
        if options:
            selected = st.selectbox('Выберите результат', range(len(options)), format_func=lambda j:
                f'{options[j].name} — {", ".join(options[j].artists)} · {duration_label(options[j].duration_ms)}')
            if st.button('Применить композицию'):
                proposed = list(tracks)
                if index is not None and index < len(proposed):
                    proposed[index] = options[selected]
                else:
                    proposed.append(options[selected])
                if len(unique_tracks(proposed, st.session_state.allow_duplicates)) != len(proposed):
                    st.warning('Эта композиция уже есть в подборке.')
                elif len(proposed) > 100:
                    st.warning('Лимит этого приложения — 100 композиций.')
                elif sum(t.duration_ms for t in proposed) > st.session_state.target_ms:
                    st.warning('Эта композиция превысит выбранную длительность. Удалите другой трек.')
                else:
                    st.session_state.tracks = proposed
                    st.session_state.pop('replace_index', None)
                    invalidate()
                    st.rerun()
        if index is not None and st.button('Отменить замену'):
            st.session_state.pop('replace_index', None)
            st.session_state.pop('replacement', None)
            st.rerun()

if st.session_state.get('missing'):
    with st.expander('Не найдены или недоступны'):
        st.write('\n'.join(st.session_state.missing))

if tracks and connected and not locked:
    st.divider()
    st.subheader('Сохранить приватный плейлист')
    st.caption('Сейчас это только черновик. Spotify изменится после подтверждения ниже.')
    name = st.text_input('Название плейлиста', value='Мой AI-плейлист', max_chars=100)
    description = st.text_input('Описание в Spotify', value='Подборка Spotify AI Playlist Maker', max_chars=250)
    fingerprint = playlist_fingerprint(name.strip(), description, tracks)
    if st.session_state.get('preview_fingerprint') != fingerprint:
        st.session_state.pop('confirmed', None)
        st.session_state.preview_fingerprint = fingerprint
    confirmed = st.checkbox('Подтверждаю создание приватного плейлиста с этим списком и порядком треков', key='confirmed')
    if st.button('Создать приватный плейлист', type='primary', disabled=not confirmed or not name.strip()):
        # Lock before any request; reruns/double clicks cannot create a second playlist.
        st.session_state.write = {'phase': 'creating', 'tracks': list(tracks),
                                  'name': name.strip(), 'description': description}
        try:
            with st.spinner('Создаю приватный плейлист…'):
                api = client()
                api.validate_tracks(tracks)
                identifier = api.create_private(name.strip(), description)
                st.session_state.write.update(id=identifier, phase='items')
        except UncertainWrite as exc:
            st.session_state.write['phase'] = 'uncertain'
            st.error(str(exc))
        except SpotifyError as exc:
            st.session_state.pop('write', None)
            st.error(str(exc))
        if 'write' in st.session_state:
            st.rerun()

if 'write' in st.session_state:
    job = st.session_state.write
    if job['phase'] == 'creating':
        # A rerun interrupted creation; outcome is unknown and must never auto-retry.
        job['phase'] = 'uncertain'
    if job['phase'] == 'items':
        job['phase'] = 'needs_retry'
        try:
            with st.spinner('Добавляю треки и проверяю результат…'):
                api = client()
                api.replace_items(job['id'], job['tracks'])
                job['phase'] = 'verify'
                url = api.verify_playlist(job['id'], job['tracks'])
                st.session_state.finished = url
                st.session_state.pop('write', None)
                st.rerun()
        except SpotifyError as exc:
            st.error(str(exc))
    if 'write' in st.session_state and job['phase'] in ('needs_retry', 'verify'):
        st.warning('Приватный плейлист уже создан. Можно безопасно повторить заполнение того же плейлиста; '
                   'новый создан не будет. Повтор заменит его содержимое утверждённым списком.')
        st.link_button('Проверить плейлист в Spotify', f'https://open.spotify.com/playlist/{job["id"]}')
        if st.button('Повторить заполнение и проверку'):
            job['phase'] = 'items'
            st.rerun()
    elif job['phase'] == 'uncertain':
        st.warning('Результат создания неизвестен. Проверьте Spotify вручную перед новым черновиком: '
                   'плейлист мог быть создан. Автоматическое повторное создание запрещено.')

if 'finished' in st.session_state:
    st.success('Приватный плейлист создан. Список и порядок подтверждены ответом Spotify.')
    st.link_button('Открыть готовый плейлист в Spotify ↗', st.session_state.finished)

if 'write' in st.session_state or 'finished' in st.session_state:
    checked = st.checkbox('Я проверил результат в Spotify и хочу начать новый черновик')
    if st.button('Новый черновик', disabled=not checked):
        for key in ['write', 'finished', 'confirmed', 'replacement', 'replace_index', 'preview_fingerprint']:
            st.session_state.pop(key, None)
        st.session_state.tracks = []
        st.session_state.queries = []
        st.rerun()

st.divider()
st.caption('Неофициальный учебный инструмент. Воспроизведение музыки — в Spotify. '
           'Секреты не вводите в описание плейлиста. Инструкция по подключению: README.md.')
