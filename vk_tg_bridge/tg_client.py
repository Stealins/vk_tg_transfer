"""Telegram Bot API: все HTTP-вызовы к Telegram — только здесь (через SOCKS5-прокси)."""
import logging

import requests

logger = logging.getLogger(__name__)

# Результат отправки сообщения:
SENT = 'sent'     # доставлено
RETRY = 'retry'   # временная ошибка (сеть/VPN, 429, 5xx) — повторить позже
DROP = 'drop'     # постоянная ошибка (400/403: пустой текст, нет топика...) — повтор не поможет


class TelegramClient:
    def __init__(self, token, proxies):
        self.api_url = f'https://api.telegram.org/bot{token}'
        self.proxies = proxies

    def call(self, method, payload=None, timeout=10):
        """
        Вызов метода Bot API, ответ TG как есть ({'ok': ..., ...}). Поля со значением None не отправляются.
        Сеть — requests.RequestException, ответ не JSON — ValueError.
        """
        payload = {k: v for k, v in (payload or {}).items() if v is not None}
        r = requests.post(f'{self.api_url}/{method}', json=payload, proxies=self.proxies, timeout=timeout)
        return r.json()

    def get_me(self):
        """@username бота или None. Ошибка здесь почти всегда значит, что нет связи с TG (VPN)."""
        try:
            r = self.call('getMe', timeout=15)
        except (requests.exceptions.RequestException, ValueError) as e:
            logger.error(f'Нет связи с Telegram (VPN/прокси {self.proxies.get("https")}?): {e}')
            return None
        if not r.get('ok'):
            logger.error(f"Telegram отклонил токен бота: {r.get('description')}")
            return None
        username = r['result']['username']
        logger.info(f'Telegram доступен, бот @{username}')
        return username

    def get_updates(self, offset):
        """Long poll апдейтов (TG держит запрос до 25 с). None — при сетевой ошибке."""
        try:
            return self.call('getUpdates', {'timeout': 25, 'offset': offset}, timeout=35)
        except (requests.exceptions.RequestException, ValueError) as e:
            logger.error(f'Get updates error: {e}', exc_info=True)
            return None

    def send_message(self, chat_id, text, thread_id=None, reply_markup=None, parse_mode=None):
        """
        sendMessage. thread_id — топик супергруппы; parse_mode='HTML' требует экранированного text.
        Возвращает SENT / RETRY / DROP.
        """
        payload = {
            'chat_id': chat_id,
            'text': text,
            'message_thread_id': thread_id,
            'reply_markup': reply_markup,
            'parse_mode': parse_mode,
        }
        try:
            result = self.call('sendMessage', payload)
        except requests.exceptions.RequestException as e:
            logger.warning(f'Ошибка сети при отправке в TG: {e}')
            return RETRY
        except ValueError:
            logger.warning('TG вернул не JSON')
            return RETRY

        if result.get('ok'):
            return SENT
        code = result.get('error_code') or 500
        logger.warning(f"TG отклонил сообщение: {code} {result.get('description')}")
        if code == 429 or code >= 500:
            return RETRY
        return DROP

    def answer_callback(self, callback_query_id):
        """Подтверждает нажатие кнопки — иначе в клиенте TG на ней висят «часики»."""
        try:
            self.call('answerCallbackQuery', {'callback_query_id': callback_query_id})
        except (requests.exceptions.RequestException, ValueError) as e:
            logger.warning(f'answerCallbackQuery не удался: {e}')

    def create_topic(self, chat_id, name):
        """Создаёт топик в супергруппе-форуме. Возвращает его message_thread_id или None."""
        try:
            result = self.call('createForumTopic', {'chat_id': chat_id, 'name': name})
        except (requests.exceptions.RequestException, ValueError) as e:
            logger.error(f'Create topic error: {e}', exc_info=True)
            return None
        if not result.get('ok'):
            logger.info(f"❌ Ошибка создания топика: {result.get('description', 'Неизвестная ошибка')}")
            return None
        topic_id = result['result']['message_thread_id']
        logger.info(f'✅ Топик создан! ID: {topic_id} для чата {name}')
        return topic_id

    def topic_exists(self, chat_id, topic_id):
        """
        Есть ли ещё топик в супергруппе. TG не сообщает боту об удалении топика, а метода «получить топик» нет,
        поэтому проверяем через reopenForumTopic: открытый топик -> TOPIC_NOT_MODIFIED, удалённый -> TOPIC_ID_INVALID,
        закрытый он откроет (ok) — тогда закрываем обратно. Возвращает True/False, None — если проверить не удалось.
        sendChatAction и пустой editForumTopic для проверки не годятся — отвечают ok и на удалённый топик.
        """
        payload = {'chat_id': chat_id, 'message_thread_id': topic_id}
        try:
            r = self.call('reopenForumTopic', payload)
            if r.get('ok'):
                self.call('closeForumTopic', payload)
                return True
        except (requests.exceptions.RequestException, ValueError) as e:
            logger.warning(f'Не удалось проверить топик {topic_id}: {e}')
            return None
        description = r.get('description', '')
        if 'TOPIC_NOT_MODIFIED' in description:
            return True
        if 'TOPIC_ID_INVALID' in description:
            return False
        logger.warning(f'Не удалось проверить топик {topic_id}: {description}')
        return None
