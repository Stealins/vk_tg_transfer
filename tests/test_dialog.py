import unittest

from tests.fakes import FakeTG
from vk_tg_bridge.dialog import CANCEL_MENU, MAIN_MENU, PEER_ID_HINT, Dialog, Step
from vk_tg_bridge.storage import BotState, Chat

GROUP = '-1001234'


def msg(text, chat_id=GROUP, topic=False, is_bot=False):
    m = {'chat': {'id': int(chat_id)}, 'text': text, 'from': {'is_bot': is_bot}}
    if topic:
        m['is_topic_message'] = True
        m['message_thread_id'] = 5
    return m


class DialogTest(unittest.TestCase):
    def setUp(self):
        self.tg = FakeTG()
        self.state = BotState()
        self.saves = 0
        self.vk_ok = True
        self.dialog = Dialog(self.tg, GROUP, self.state, save=self.count_save, vk_connected=lambda: self.vk_ok)

    def count_save(self):
        self.saves += 1

    def say(self, text, **kwargs):
        self.dialog.handle_update({'update_id': 1, 'message': msg(text, **kwargs)})

    def press(self, data, chat_id=GROUP):
        self.dialog.handle_update({'update_id': 1, 'callback_query': {
            'id': 'cb', 'data': data, 'message': {'chat': {'id': int(chat_id)}}}})

    def add_chat(self, name='Друзья', peer='2000000005'):
        self.press('add_chat')
        self.say(name)
        self.say(peer)

    def test_full_add_flow(self):
        self.say('/start')
        self.assertEqual(self.tg.sent[-1][2], {'reply_markup': MAIN_MENU})
        self.add_chat()
        chat = self.state.chats[2000000005]
        self.assertEqual((chat.name, chat.tg_chat_id, chat.tg_topic_id), ('Друзья', GROUP, 100))
        self.assertEqual(self.saves, 1)
        self.assertIn('📌 Топик для чата «Друзья» успешно создан!', self.tg.texts)
        welcome = [s for s in self.tg.sent if s[1].startswith('📌')][0]
        self.assertEqual(welcome[2], {'thread_id': 100})
        self.assertEqual(self.tg.texts[-1], 'Чат «Друзья» добавлен.')
        self.assertEqual(self.dialog.step, Step.BASE)
        self.assertEqual(self.tg.callbacks, ['cb'])

    def test_start_warns_when_vk_is_down(self):
        self.vk_ok = False
        self.say('/start')
        self.assertIn('VK сейчас не подключён', self.tg.texts[-1])

    def test_ignores_topics_private_chats_and_bots(self):
        self.press('add_chat')
        sent = len(self.tg.sent)
        self.say('Имя', topic=True)
        self.say('Имя', chat_id='42')
        self.say('Имя', is_bot=True)
        self.press('cancel', chat_id='42')
        self.assertEqual(len(self.tg.sent), sent)
        self.assertEqual(self.dialog.step, Step.WAITING_CHAT_NAME)

    def test_plain_text_outside_dialog_is_ignored(self):
        self.say('просто болтаем')
        self.assertEqual(self.tg.sent, [])

    def test_start_with_bot_name_resets_dialog(self):
        self.press('add_chat')
        self.say('Друзья')
        self.say('/start@mkno_bot')
        self.assertEqual(self.tg.texts[-2:][0], 'Добавление чата отменено.')
        self.assertEqual(self.dialog.step, Step.BASE)
        self.assertEqual(self.state.chats, {})

    def test_commands_are_never_input(self):
        self.press('add_chat')
        self.say('/foo')
        self.assertEqual(self.tg.texts[-1], 'Неизвестная команда. Доступны /start и /cancel.')
        self.assertEqual(self.dialog.step, Step.WAITING_CHAT_NAME)

    def test_cancel(self):
        self.say('/cancel')
        self.assertEqual(self.tg.texts[-1], 'Нечего отменять.')
        self.press('add_chat')
        self.press('cancel')
        self.assertEqual(self.tg.texts[-1], 'Добавление чата отменено.')
        self.assertEqual(self.dialog.step, Step.BASE)

    def test_message_without_text(self):
        self.press('add_chat')
        self.dialog.handle_message({'chat': {'id': int(GROUP)}, 'from': {}, 'photo': []})
        self.assertEqual(self.tg.sent[-1][2], {'reply_markup': CANCEL_MENU})
        self.assertTrue(self.tg.texts[-1].startswith('Нужен текст'))

    def test_long_name_rejected(self):
        self.press('add_chat')
        self.say('я' * 129)
        self.assertEqual(self.dialog.step, Step.WAITING_CHAT_NAME)
        self.say('я' * 128)
        self.assertEqual(self.tg.texts[-1], PEER_ID_HINT)

    def test_bad_peer_id_rejected(self):
        self.press('add_chat')
        self.say('Друзья')
        for bad in ('abc', '0'):
            self.say(bad)
            self.assertTrue(self.tg.texts[-1].startswith('peer_id должен быть ненулевым'))
        self.assertEqual(self.dialog.step, Step.WAITING_CHAT_PEER)

    def test_duplicate_peer_id_rejected(self):
        self.add_chat()
        self.add_chat(name='Ещё раз')
        self.assertTrue(self.tg.texts[-1].startswith('Этот VK-чат уже привязан к топику «Друзья»'))
        self.assertEqual(self.dialog.step, Step.WAITING_CHAT_PEER)

    def test_duplicate_allowed_if_old_topic_deleted(self):
        self.add_chat()
        self.tg.topics[100] = False
        self.add_chat(name='Новый')
        self.assertEqual(self.state.chats[2000000005].name, 'Новый')
        self.assertEqual(self.state.chats[2000000005].tg_topic_id, 101)

    def test_topic_creation_failure(self):
        self.tg.next_topic_id = None
        self.add_chat()
        self.assertEqual(self.state.chats, {})
        self.assertTrue(self.tg.texts[-1].startswith('Не удалось создать топик'))
        self.assertEqual(self.dialog.step, Step.BASE)


class SyncTopicsTest(unittest.TestCase):
    def test_removes_deleted_topics_and_reports(self):
        tg = FakeTG()
        tg.topics = {1: True, 2: False, 3: None}    # None — проверить не удалось, не трогаем
        state = BotState(chats={p: Chat(peer_id=p, name=f'c{p}', tg_chat_id=GROUP, tg_topic_id=p) for p in (1, 2, 3)})
        saves = []
        Dialog(tg, GROUP, state, save=lambda: saves.append(1), vk_connected=lambda: True).sync_topics()
        self.assertEqual(sorted(state.chats), [1, 3])
        self.assertEqual(saves, [1])
        self.assertIn('«c2» (peer_id 2)', tg.texts[-1])

    def test_nothing_removed_nothing_sent(self):
        tg = FakeTG()
        state = BotState(chats={1: Chat(peer_id=1, name='c', tg_chat_id=GROUP, tg_topic_id=1)})
        Dialog(tg, GROUP, state, save=lambda: self.fail('лишнее сохранение'), vk_connected=lambda: True).sync_topics()
        self.assertEqual(tg.sent, [])


if __name__ == '__main__':
    unittest.main()
