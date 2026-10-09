"""VK API (кроме long poll): вызов методов, параметры long poll сервера, имена отправителей с кэшем."""
import logging

import requests

from . import vk_auth

logger = logging.getLogger(__name__)

API_URL = 'https://api.vk.com/method/'
API_VERSION = '5.199'

_names = {}       # owner_id -> 'Имя Фамилия' / 'Название сообщества'
_my_id = None     # id владельца токена


class VKError(Exception):
    pass


def vk_call(method, **params):
    """Вызов метода VK API с текущим токеном. Ошибка VK — VKError, сеть — requests.RequestException."""
    params.update(access_token=vk_auth.read_token(), v=API_VERSION)
    r = requests.post(API_URL + method, data=params, timeout=10)
    data = r.json()
    if 'error' in data:
        raise VKError(f"{method}: {data['error'].get('error_code')} {data['error'].get('error_msg')}")
    return data['response']


def get_long_poll_server():
    """messages.getLongPollServer -> (server_url, key, ts); None, если VK отказал (обычно протух токен)."""
    try:
        lp = vk_call('messages.getLongPollServer')
    except VKError as e:
        logger.error(f'VK API вернул ошибку: {e}')
        return None
    server_url = lp['server']
    if not server_url.startswith(('http://', 'https://')):
        server_url = 'https://' + server_url
    return server_url, lp['key'], lp['ts']


def get_my_id():
    """id аккаунта, от имени которого работает бот (для исходящих сообщений)."""
    global _my_id
    if _my_id is None:
        _my_id = vk_call('users.get')[0]['id']
    return _my_id


def get_name(owner_id):
    """Имя пользователя (id > 0) или сообщества (id < 0). При ошибке — 'id123', без кэширования."""
    if owner_id in _names:
        return _names[owner_id]
    try:
        if owner_id > 0:
            user = vk_call('users.get', user_ids=owner_id)[0]
            name = f"{user['first_name']} {user['last_name']}"
        else:
            groups = vk_call('groups.getById', group_ids=-owner_id)
            # 5.199 возвращает {'groups': [...]}, старые версии — список
            group = (groups['groups'] if isinstance(groups, dict) else groups)[0]
            name = group['name']
    except Exception as e:
        logger.warning(f'Не удалось получить имя для {owner_id}: {e}')
        return f'id{owner_id}'
    _names[owner_id] = name
    return name


def get_sender_id(message_id):
    """Автор сообщения через messages.getById — запасной путь, если long poll не прислал 'from'."""
    items = vk_call('messages.getById', message_ids=message_id)['items']
    return items[0]['from_id'] if items else None
