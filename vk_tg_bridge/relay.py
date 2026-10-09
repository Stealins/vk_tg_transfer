"""Очередь VK -> TG: постановка новых сообщений, оформление и отправка в топики по порядку."""
import html
import logging
from datetime import datetime

from . import vk_client
from .tg_client import DROP, RETRY

logger = logging.getLogger(__name__)

MAX_PENDING = 1000          # предел очереди; при переполнении выбрасываются самые старые сообщения
TG_TEXT_LIMIT = 3900        # лимит TG — 4096 символов; запас под заголовок


def enqueue(state, message):
    """Ставит VkMessage в очередь state.pending, если чат привязан к топику. Возвращает True, если добавлено."""
    if message.peer_id not in state.chats:
        logger.info(f'Нет привязки для peer_id {message.peer_id}')
        return False
    if not message.text:
        logger.info(f'Сообщение без текста из peer_id {message.peer_id} (вложения?) — пропущено')
        return False
    state.pending.append({'peer_id': message.peer_id, 'from_id': message.sender_id(),
                          'date': message.date, 'text': message.text})
    if len(state.pending) > MAX_PENDING:
        dropped = state.pending.pop(0)
        logger.error(f"Очередь переполнена ({MAX_PENDING}), выброшено старое сообщение из peer_id {dropped['peer_id']}")
    return True


def format_message(item):
    """
    Текст для TG (parse_mode=HTML):
        <b>Имя Фамилия</b> · 07.10.2026 14:05
        текст сообщения
    """
    text = item['text']
    if len(text) > TG_TEXT_LIMIT:
        text = text[:TG_TEXT_LIMIT] + '…'
    if 'from_id' not in item:           # элемент очереди старого формата — только текст
        return html.escape(text, quote=False)
    from_id = item['from_id']
    name = vk_client.get_name(from_id) if from_id else 'Неизвестный отправитель'
    header = f'<b>{html.escape(name, quote=False)}</b>'
    if item.get('date'):
        header += ' · ' + datetime.fromtimestamp(item['date']).strftime('%d.%m.%Y %H:%M')
    return f'{header}\n{html.escape(text, quote=False)}'


def flush(state, tg):
    """
    Отправляет очередь в TG по порядку. На временной ошибке (нет сети/VPN, 429, 5xx) останавливается —
    сообщение остаётся в очереди и уйдёт позже. Возвращает True, если очередь изменилась.
    """
    changed = False
    while state.pending:
        item = state.pending[0]
        chat = state.chats.get(item['peer_id'])
        if chat is None:
            logger.warning(f"Привязка для peer_id {item['peer_id']} удалена — сообщение выброшено из очереди")
        else:
            result = tg.send_message(chat.tg_chat_id, format_message(item),
                                     thread_id=chat.tg_topic_id, parse_mode='HTML')
            if result == RETRY:
                logger.warning(f'Отправка в TG не удалась, в очереди {len(state.pending)} сообщ., повтор позже')
                break
            if result == DROP:
                logger.error(f"TG не принимает сообщение из peer_id {item['peer_id']} — выброшено из очереди")
            else:
                chat.update_activity()
        state.pending.pop(0)
        changed = True
    return changed
