"""
Получение VK access_token из веб-версии VK через браузер (Playwright + установленный Edge).

Веб-клиент vk.ru/im сам получает токен (login.vk.ru/?act=web_token) и шлёт его в каждом
запросе к api.vk.ru. Мы открываем страницу в браузере с постоянным профилем и перехватываем
токен из сетевого трафика.

- Первый вход: login_interactive() — откроется окно браузера, нужно войти в VK вручную.
  Сессия (cookies) сохраняется в personal_data/browser_profile.
  Из командной строки: myvenv\\Scripts\\python -m vk_tg_bridge.vk_auth
- Дальше: fetch_token() — headless, без участия пользователя; refresh_token() — то же для бота,
  с паузой между неудачными попытками.
"""
import json
import time
import logging
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright

from . import config

logger = logging.getLogger(__name__)

IM_URL = 'https://vk.ru/im'

# Если VK не сообщил срок жизни токена — обновляем через это время
DEFAULT_TTL = 30 * 60
# Обновляем заранее, за столько секунд до истечения
REFRESH_MARGIN = 5 * 60
# Пауза после неудачной попытки обновить токен (не запускать браузер на каждой итерации цикла)
RETRY_DELAY = 5 * 60

_retry_after = 0


class NotLoggedIn(Exception):
    """В профиле браузера нет активной сессии VK — нужен login_interactive()."""


def _find_key(obj, name):
    """Рекурсивно ищет ключ name в распарсенном JSON."""
    if isinstance(obj, dict):
        if name in obj:
            return obj[name]
        obj = list(obj.values())
    if isinstance(obj, list):
        for item in obj:
            found = _find_key(item, name)
            if found is not None:
                return found
    return None


def _is_user_token(token):
    # Без входа VK выдаёт анонимный токен "anonym.…" — он для бота бесполезен
    return isinstance(token, str) and bool(token) and not token.startswith('anonym')


def _token_from_request(request):
    """Достаёт access_token из запроса к (web.)api.vk.* (query или тело формы)."""
    host = urlparse(request.url).hostname or ''
    if 'api.vk.' not in host:
        return None
    params = parse_qs(urlparse(request.url).query)
    try:
        if request.post_data:
            params.update(parse_qs(request.post_data))
    except Exception:
        pass  # бинарное тело запроса
    token = params.get('access_token', [None])[0]
    return token if _is_user_token(token) else None


def _grab_token(headless, timeout):
    """Открывает vk.ru/im и ждёт, пока в трафике появится токен. Возвращает (token, expires_at)."""
    captured = {'token': None, 'expires': None}

    def on_response(response):
        # Основной источник: ответ act=web_token содержит и токен, и срок жизни
        if 'act=web_token' not in response.url:
            return
        try:
            data = response.json()
        except Exception:
            return
        token = _find_key(data, 'access_token')
        if _is_user_token(token):
            captured['token'] = token
            captured['expires'] = _find_key(data, 'expires')

    def on_request(request):
        # Запасной источник: токен из обычного вызова API
        if captured['token'] is None:
            token = _token_from_request(request)
            if token:
                captured['token'] = token

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(config.BROWSER_PROFILE_DIR), channel='msedge', headless=headless,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.on('response', on_response)
            page.on('request', on_request)
            page.goto(IM_URL, wait_until='domcontentloaded')

            started = time.time()
            deadline = started + timeout
            first_seen = None
            while time.time() < deadline:
                # Без сессии VK уводит с /im на главную/страницу входа. В headless ждать нечего
                if headless and not captured['token'] and time.time() - started > 8 \
                        and '/im' not in urlparse(page.url).path:
                    break
                if captured['token']:
                    first_seen = first_seen or time.time()
                    # Срок жизни известен, либо web_token так и не пришёл за 5 с после первого токена
                    if captured['expires'] or time.time() - first_seen > 5:
                        break
                page.wait_for_timeout(500)
        finally:
            context.close()

    if not captured['token']:
        return None, None

    expires = captured['expires']
    # expires бывает unix-временем или числом секунд — приводим к unix-времени
    if isinstance(expires, (int, float)) and expires > 0:
        expires_at = expires if expires > 10**9 else time.time() + expires
    else:
        expires_at = time.time() + DEFAULT_TTL
    return captured['token'], expires_at


def _save(token, expires_at):
    with open(config.VK_TOKEN_FILE, 'w', encoding='utf-8') as f:
        f.write(token)
    with open(config.VK_TOKEN_META_FILE, 'w', encoding='utf-8') as f:
        json.dump({'expires_at': expires_at, 'obtained_at': time.time()}, f)
    logger.info(f"VK-токен обновлён, действует до {time.strftime('%H:%M:%S', time.localtime(expires_at))}")


def read_token():
    """Текущий VK-токен из файла (читается каждый раз — браузер его обновляет)."""
    with open(config.VK_TOKEN_FILE, 'r', encoding='utf-8') as f:
        return f.read().strip()


def fetch_token(timeout=40):
    """Headless-получение токена по сохранённой сессии. Бросает NotLoggedIn, если сессии нет."""
    token, expires_at = _grab_token(headless=True, timeout=timeout)
    if not token:
        raise NotLoggedIn('Не удалось получить VK-токен: войдите в VK через login_interactive()')
    _save(token, expires_at)
    return token


def login_interactive(timeout=300):
    """Открывает окно браузера для ручного входа в VK и ждёт появления токена."""
    print('Откроется окно браузера. Войдите в VK, дождитесь загрузки сообщений — окно закроется само.')
    token, expires_at = _grab_token(headless=False, timeout=timeout)
    if not token:
        raise NotLoggedIn('Вход в VK не выполнен за отведённое время')
    _save(token, expires_at)
    return token


def token_expires_soon():
    """True, если токена нет или он истекает в ближайшие REFRESH_MARGIN секунд."""
    try:
        with open(config.VK_TOKEN_META_FILE, 'r', encoding='utf-8') as f:
            expires_at = json.load(f)['expires_at']
    except (FileNotFoundError, KeyError, ValueError):
        return True
    return time.time() > expires_at - REFRESH_MARGIN


def refresh_token(force=False):
    """
    Для бота: обновляет токен, если он истекает (или force). Ошибки не бросает — пишет в лог.
    После неудачи браузер не запускается повторно RETRY_DELAY секунд. Возвращает True, если токен годен.
    """
    global _retry_after
    if not force and not token_expires_soon():
        return True
    if time.time() < _retry_after:
        return False
    try:
        fetch_token()
        return True
    except NotLoggedIn as e:
        logger.error(f'{e}. Запустите: {config.VK_LOGIN_COMMAND}')
    except Exception as e:
        logger.error(f'Ошибка обновления VK-токена: {e}', exc_info=True)
    _retry_after = time.time() + RETRY_DELAY
    return False


if __name__ == '__main__':
    # Первичная настройка: myvenv\Scripts\python -m vk_tg_bridge.vk_auth
    from .logging_config import setup_logging
    setup_logging()
    login_interactive()
    print('Токен сохранён в', config.VK_TOKEN_FILE)
