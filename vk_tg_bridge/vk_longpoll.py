"""VK user long poll (mode=2, version=3): получение новых сообщений и разбор событий."""
import html
import logging
from dataclasses import dataclass

import requests

from . import vk_auth, vk_client

logger = logging.getLogger(__name__)

EVENT_NEW_MESSAGE = 4
VK_OUTBOX = 2                   # флаг сообщения: исходящее (написано владельцем токена)
VK_CHAT_PEER_BASE = 2000000000  # peer_id бесед начинаются с этого числа


def vk_text(raw):
    """Текст из long poll приходит HTML-экранированным, переносы строк — <br>."""
    return html.unescape(raw.replace('<br>', '\n')).strip()


@dataclass
class VkMessage:
    """Новое сообщение — событие long poll [4, message_id, flags, peer_id, timestamp, text, extra, ...]."""
    message_id: int
    flags: int
    peer_id: int
    date: int
    text: str           # уже раскодирован (vk_text)
    extras: list        # update[6:] — в беседах там лежит автор ('from')

    @classmethod
    def from_event(cls, update):
        return cls(message_id=update[1], flags=update[2], peer_id=int(update[3]), date=update[4],
                   text=vk_text(update[5]), extras=list(update[6:8]))

    def sender_id(self):
        """id автора: исходящее -> владелец токена; личка/сообщество -> peer_id; беседа -> 'from' или getById."""
        try:
            if self.flags & VK_OUTBOX:
                return vk_client.get_my_id()
            if self.peer_id < VK_CHAT_PEER_BASE:
                return self.peer_id         # личная переписка или сообщество: автор — собеседник
            for extra in self.extras:       # в беседе long poll кладёт автора в поле 'from'
                if isinstance(extra, dict) and 'from' in extra:
                    return int(extra['from'])
            return vk_client.get_sender_id(self.message_id)
        except Exception as e:
            logger.warning(f'Не удалось определить автора сообщения {self.message_id}: {e}')
            return None


class LongPoll:
    def __init__(self, ts=None):
        self.server_url = None
        self.key = None
        self.ts = ts                # сохранённый ts — VK отдаст события за время простоя
        self.available = False

    def refresh(self, keep_ts=False):
        """Переполучает сервер long poll; при отказе VK (протухший токен) обновляет токен и пробует снова."""
        lp = vk_client.get_long_poll_server()
        if not lp and vk_auth.refresh_token(force=True):
            lp = vk_client.get_long_poll_server()
        if not lp:
            self.available = False
            return False
        self.server_url, self.key, new_ts = lp
        if not keep_ts or self.ts is None:
            self.ts = new_ts
        self.available = True
        logger.info('Long Poll сервер обновлён')
        return True

    def poll(self):
        """Один запрос long poll (до 25 с). Сдвигает self.ts, возвращает новые сообщения (list[VkMessage])."""
        if not (self.available and self.server_url and self.key and self.ts):
            return []
        try:
            r = requests.get(url=self.server_url, params={'key': self.key,
                                                          'v': '5.199',
                                                          'ts': self.ts,
                                                          'wait': 25,
                                                          'mode': 2,
                                                          'version': 3},
                             timeout=35)   # без таймаута оборванное соединение вешало бота навсегда
            response_data = r.json()
        except requests.exceptions.RequestException as e:
            logger.error(f'Await message error: {e}', exc_info=True)
            return []
        logger.info('Получен ответ от ВК')

        # failed: 1 — устарел ts; 2 — истёк key; 3 — потеряна информация, нужны key и ts
        failed = response_data.get('failed')
        if failed == 1:
            # ts слишком старый (например, бот долго стоял) — события до нового ts VK уже не отдаст
            logger.warning(f'VK Long Poll failed=1: история событий потеряна, ts {self.ts} -> {response_data["ts"]}')
            self.ts = response_data['ts']
            return []
        if failed in (2, 3):
            logger.info(f'VK Long Poll failed={failed}, переполучаем сервер')
            self.refresh(keep_ts=failed == 2)
            return []

        messages = []
        if response_data.get('updates'):
            logger.info('Получен апдейт от ВК!')
            messages = [VkMessage.from_event(u) for u in response_data['updates'] if u[0] == EVENT_NEW_MESSAGE]
        self.ts = response_data['ts']
        return messages
