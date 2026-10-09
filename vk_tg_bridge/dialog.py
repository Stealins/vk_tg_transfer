"""
Диалог с ботом в ветке General супергруппы и управление привязками VK-чатов к топикам.

Всё взаимодействие — только в General: остальные топики — это VK-чаты, и текст из них
не должен становиться вводом диалога. Бот однопользовательский — состояние диалога одно.
"""
import logging
from enum import Enum, auto

from . import config
from .storage import Chat

logger = logging.getLogger(__name__)

TOPIC_NAME_LIMIT = 128      # максимальная длина названия топика в TG

PEER_ID_HINT = (
    'Введите peer_id VK-чата:\n'
    '• личная переписка — ID пользователя (положительное число);\n'
    '• беседа — 2000000000 + id беседы (беседа 5 → 2000000005);\n'
    '• сообщество — минус id сообщества (отрицательное число).'
)

MAIN_MENU = {'inline_keyboard': [[{'text': '➕ Добавить VK-чат', 'callback_data': 'add_chat'}]]}
CANCEL_MENU = {'inline_keyboard': [[{'text': '✖ Отмена', 'callback_data': 'cancel'}]]}


class Step(Enum):
    BASE = auto()               # вне диалога
    WAITING_CHAT_NAME = auto()  # ждём название топика
    WAITING_CHAT_PEER = auto()  # ждём peer_id VK-чата


class Dialog:
    def __init__(self, tg, group_id, state, save, vk_connected):
        """
        tg — TelegramClient; group_id — id супергруппы; state — BotState (меняются state.chats);
        save() — сохранить состояние на диск; vk_connected() — работает ли VK long poll.
        """
        self.tg = tg
        self.group_id = group_id
        self.state = state
        self.save = save
        self.vk_connected = vk_connected
        self.step = Step.BASE
        self.draft_name = None      # название топика, пока идёт добавление чата

    # --- входящие апдейты ---

    def handle_update(self, update):
        if 'callback_query' in update:
            self.handle_callback(update['callback_query'])
        elif 'message' in update:
            self.handle_message(update['message'])

    def is_general(self, message):
        """Сообщение в ветке General нашей супергруппы (у сообщений General нет is_topic_message)."""
        return str(message['chat']['id']) == str(self.group_id) and not message.get('is_topic_message')

    def handle_callback(self, callback_query):
        self.tg.answer_callback(callback_query['id'])
        message = callback_query.get('message')
        if not message or not self.is_general(message):
            return
        chat_id = message['chat']['id']
        data = callback_query.get('data')
        if data == 'add_chat':
            self.start_add_chat(chat_id)
        elif data == 'cancel' and self.reset():
            self.tg.send_message(chat_id, 'Добавление чата отменено.', reply_markup=MAIN_MENU)

    def handle_message(self, message):
        """Сообщения из других топиков (включая служебные о создании топиков), лички и других чатов игнорируются."""
        if message.get('from', {}).get('is_bot') or not self.is_general(message):
            return
        chat_id = message['chat']['id']
        text = (message.get('text') or '').strip()

        logger.info(f"TG: {'команда ' + text.split()[0] if text.startswith('/') else 'сообщение'} "
                    f"в General, состояние {self.step.name}")

        if text.startswith('/'):
            self.handle_command(chat_id, text)
            return

        if self.step == Step.BASE:   # вне диалога обычные сообщения в General не трогаем
            return

        if not text:   # фото, стикер, голосовое...
            self.tg.send_message(chat_id, 'Нужен текст. Введите ещё раз или нажмите «Отмена».',
                                 reply_markup=CANCEL_MENU)
            return

        if self.step == Step.WAITING_CHAT_NAME:
            self.receive_name(chat_id, text)
        elif self.step == Step.WAITING_CHAT_PEER:
            self.receive_peer_id(chat_id, text)

    def handle_command(self, chat_id, text):
        """Команды обрабатываются в любом состоянии и никогда не считаются вводом."""
        command = text.split()[0].split('@')[0].lower()   # '/start@my_bot args' -> '/start'
        if command == '/start':
            if self.reset():
                self.tg.send_message(chat_id, 'Добавление чата отменено.')
            self.send_start(chat_id)
        elif command == '/cancel':
            text = 'Добавление чата отменено.' if self.reset() else 'Нечего отменять.'
            self.tg.send_message(chat_id, text, reply_markup=MAIN_MENU)
        else:
            self.tg.send_message(chat_id, 'Неизвестная команда. Доступны /start и /cancel.')

    # --- шаги диалога ---

    def reset(self):
        """Сбрасывает незаконченный диалог. Возвращает True, если диалог был."""
        was_active = self.step != Step.BASE
        self.step = Step.BASE
        self.draft_name = None
        return was_active

    def send_start(self, chat_id):
        text = 'Привет! Это бот для пересылки сообщений из VK в Telegram.'
        if not self.vk_connected():
            text += f'\n\n⚠️ VK сейчас не подключён. Войдите в VK: {config.VK_LOGIN_COMMAND}'
        self.tg.send_message(chat_id, text, reply_markup=MAIN_MENU)

    def start_add_chat(self, chat_id):
        self.step = Step.WAITING_CHAT_NAME
        self.draft_name = None
        self.tg.send_message(chat_id, 'Введите название топика для этого чата:', reply_markup=CANCEL_MENU)

    def receive_name(self, chat_id, text):
        if len(text) > TOPIC_NAME_LIMIT:
            self.tg.send_message(chat_id, f'Название длиннее {TOPIC_NAME_LIMIT} символов. Введите покороче:',
                                 reply_markup=CANCEL_MENU)
            return
        self.draft_name = text
        self.step = Step.WAITING_CHAT_PEER
        self.tg.send_message(chat_id, PEER_ID_HINT, reply_markup=CANCEL_MENU)

    def receive_peer_id(self, chat_id, text):
        try:
            peer_id = int(text)
        except ValueError:
            peer_id = 0
        if peer_id == 0:
            self.tg.send_message(chat_id, 'peer_id должен быть ненулевым числом. Введите ещё раз:',
                                 reply_markup=CANCEL_MENU)
            return
        chats = self.state.chats
        if peer_id in chats and self.topic_exists(chats[peer_id]) is False:
            # топик удалили, пока бот работал — старая привязка больше не нужна
            logger.warning(f'Топик «{chats[peer_id].name}» удалён из группы — привязка peer_id {peer_id} заменяется')
            del chats[peer_id]
        if peer_id in chats:
            self.tg.send_message(chat_id, f'Этот VK-чат уже привязан к топику «{chats[peer_id].name}». '
                                          'Введите другой peer_id или нажмите «Отмена».', reply_markup=CANCEL_MENU)
            return

        name = self.draft_name
        topic_id = self.tg.create_topic(self.group_id, name)
        if topic_id is not None:
            self.tg.send_message(self.group_id, f'📌 Топик для чата «{name}» успешно создан!', thread_id=topic_id)
            chats[peer_id] = Chat(peer_id=peer_id, name=name, tg_chat_id=self.group_id, tg_topic_id=topic_id)
            self.save()
            self.tg.send_message(chat_id, f'Чат «{name}» добавлен.', reply_markup=MAIN_MENU)
        else:
            self.tg.send_message(chat_id, 'Не удалось создать топик. Проверьте права бота и что группа является форумом.',
                                 reply_markup=MAIN_MENU)
        self.reset()

    # --- привязки ---

    def topic_exists(self, chat):
        return self.tg.topic_exists(chat.tg_chat_id, chat.tg_topic_id)

    def sync_topics(self):
        """
        При старте снимает привязки к топикам, удалённым из супергруппы, пока бот не работал
        (иначе бот считает их живыми и не даёт добавить тот же peer_id заново).
        """
        chats = self.state.chats
        removed = [chat for chat in chats.values() if self.topic_exists(chat) is False]
        for chat in removed:
            del chats[chat.peer_id]
            logger.warning(f'Топик «{chat.name}» ({chat.tg_topic_id}) удалён из группы — '
                           f'привязка peer_id {chat.peer_id} снята')
        if removed:
            self.save()
            names = '\n'.join(f'• «{c.name}» (peer_id {c.peer_id})' for c in removed)
            self.tg.send_message(self.group_id, f'Эти топики удалены из группы, их привязки сняты:\n{names}\n'
                                                'Чтобы снова пересылать эти чаты, добавьте их заново.')
