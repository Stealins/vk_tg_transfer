"""
Состояние бота и его хранение в state.json: привязки VK-чатов к топикам, ts VK long poll,
очередь неотправленных в TG сообщений.
"""
import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


def _now():
    return datetime.now().isoformat()


def _default_settings():
    return {
        'notify_about_new_messages': True,
        'notify_about_edits': False,
        'custom_keywords': []
    }


@dataclass
class Chat:
    """Привязка VK-чата (peer_id) к топику Telegram-супергруппы."""
    peer_id: int
    name: str
    tg_chat_id: int | str | None = None     # id супергруппы
    tg_topic_id: int | None = None          # message_thread_id топика
    created_at: str = field(default_factory=_now)
    last_activity: str = field(default_factory=_now)
    is_active: bool = True
    settings: dict = field(default_factory=_default_settings)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        chat = cls(peer_id=int(data['peer_id']), name=data.get('name', ''))
        for key in ('tg_chat_id', 'tg_topic_id', 'created_at', 'last_activity', 'is_active', 'settings'):
            if key in data:
                setattr(chat, key, data[key])
        return chat

    def update_activity(self):
        self.last_activity = _now()


@dataclass
class BotState:
    """Всё, что переживает перезапуск бота."""
    chats: dict[int, Chat] = field(default_factory=dict)
    ts: int | None = None       # позиция VK long poll: при старте VK отдаст события за время простоя
    pending: list[dict] = field(default_factory=list)   # очередь в TG: {'peer_id', 'from_id', 'date', 'text'}


class StateCorrupted(Exception):
    """Не читается ни state.json, ни его .bak."""


def save_state(state, path):
    """
    Пишет во временный файл и атомарно подменяет им state.json — прерывание записи не портит файл.
    Предыдущая версия остаётся в state.json.bak. TG offset не храним: Telegram помнит его сам.
    Возвращает True при успехе.
    """
    data = {
        'chats': {str(peer_id): chat.to_dict() for peer_id, chat in state.chats.items()},
        'ts': state.ts,
        'pending': state.pending,
        'saved_at': _now()
    }
    path = str(path)
    tmp_file = path + '.tmp'
    try:
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(path):
            shutil.copy2(path, path + '.bak')
        os.replace(tmp_file, path)
        logger.debug(f'Состояние сохранено в {path}')
        return True
    except OSError as e:
        logger.error(f'Ошибка сохранения состояния: {e}', exc_info=True)
        return False


def _read_state_file(path):
    """Читает и разбирает файл состояния. Бросает исключение, если файл битый."""
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    # ключи в JSON — строки
    chats = {int(peer_id): Chat.from_dict(chat) for peer_id, chat in data.get('chats', {}).items()}
    return BotState(chats=chats, ts=data.get('ts'), pending=list(data.get('pending', [])))


def load_state(path):
    """
    Загружает состояние из state.json, при ошибке — из state.json.bak. Битый файл переименовывается в *.corrupt.
    Нет ни одного файла — пустое состояние. Не читается ни один — StateCorrupted
    (бот должен остановиться, иначе автосохранение затрёт чаты пустым списком).
    """
    path = str(path)
    candidates = [p for p in (path, path + '.bak') if os.path.exists(p)]
    if not candidates:
        logger.info('Файл состояния не найден, начинаем с чистого листа.')
        return BotState()

    for candidate in candidates:
        try:
            state = _read_state_file(candidate)
        except Exception as e:
            logger.error(f'Не удалось прочитать {candidate}: {e}')
            if candidate == path:
                os.replace(path, path + '.corrupt')
                logger.error(f'Битый файл сохранён как {path}.corrupt')
            continue
        logger.info(f'Состояние загружено из {candidate}, чатов: {len(state.chats)}, в очереди: {len(state.pending)}')
        return state

    raise StateCorrupted(f'{path} и {path}.bak повреждены')
