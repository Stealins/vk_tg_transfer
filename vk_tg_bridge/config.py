"""
Пути к файлам и сетевые настройки. Пути считаются от корня проекта, а не от текущей папки,
поэтому бота можно запускать откуда угодно. Секреты лежат в personal_data/ (в .gitignore).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / 'personal_data'

TG_TOKEN_FILE = DATA_DIR / 'token_tg.txt'         # токен Telegram-бота
GROUP_ID_FILE = DATA_DIR / 'group_id.txt'         # id супергруппы-форума
VK_TOKEN_FILE = DATA_DIR / 'token.txt'            # VK access_token (пишет vk_auth)
VK_TOKEN_META_FILE = DATA_DIR / 'token_meta.json' # срок жизни VK-токена
BROWSER_PROFILE_DIR = DATA_DIR / 'browser_profile'  # профиль Edge с сессией VK
STATE_FILE = DATA_DIR / 'state.json'              # чаты, ts VK, очередь в TG
LOG_FILE = ROOT / 'bot.log'

# Telegram доступен только через VPN — SOCKS5-прокси (socks5h: DNS тоже через прокси)
TG_PROXY = 'socks5h://127.0.0.1:10808'
TG_PROXIES = {'http': TG_PROXY, 'https': TG_PROXY}

# Команда для первого входа в VK — подсказка в логах и в сообщениях бота
VK_LOGIN_COMMAND = r'myvenv\Scripts\python -m vk_tg_bridge.vk_auth'


def read_secret(path):
    """Содержимое файла без пробелов по краям; пустая строка, если файла нет."""
    try:
        return Path(path).read_text(encoding='utf-8').strip()
    except FileNotFoundError:
        return ''
