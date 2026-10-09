import json
import os
import tempfile
import unittest

from vk_tg_bridge.storage import BotState, Chat, StateCorrupted, load_state, save_state


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'state.json')

    def tearDown(self):
        self.tmp.cleanup()

    def make_state(self):
        chat = Chat(peer_id=2000000005, name='Друзья', tg_chat_id='-100123', tg_topic_id=7)
        return BotState(chats={chat.peer_id: chat}, ts=42,
                        pending=[{'peer_id': 2000000005, 'from_id': 1, 'date': 1, 'text': 'hi'}])

    def test_roundtrip(self):
        state = self.make_state()
        self.assertTrue(save_state(state, self.path))
        loaded = load_state(self.path)
        self.assertEqual(loaded.ts, 42)
        self.assertEqual(loaded.pending, state.pending)
        self.assertEqual(loaded.chats, state.chats)
        self.assertIsInstance(next(iter(loaded.chats)), int)    # ключи из JSON -> int

    def test_second_save_keeps_backup(self):
        state = self.make_state()
        save_state(state, self.path)
        state.ts = 43
        save_state(state, self.path)
        self.assertEqual(load_state(self.path + '.bak').ts, 42)
        self.assertFalse(os.path.exists(self.path + '.tmp'))

    def test_missing_file_gives_empty_state(self):
        self.assertEqual(load_state(self.path), BotState())

    def test_corrupt_file_falls_back_to_bak(self):
        state = self.make_state()
        save_state(state, self.path)
        save_state(state, self.path)
        with open(self.path, 'w') as f:
            f.write('{broken')
        loaded = load_state(self.path)
        self.assertEqual(loaded.ts, 42)
        self.assertTrue(os.path.exists(self.path + '.corrupt'))

    def test_both_corrupt_raises(self):
        for p in (self.path, self.path + '.bak'):
            with open(p, 'w') as f:
                f.write('{broken')
        with self.assertRaises(StateCorrupted):
            load_state(self.path)

    def test_old_format_is_readable(self):
        # ранние state.json: лишние поля key/server_url, у чата нет части полей, нет pending
        data = {'chats': {'5': {'peer_id': '5', 'name': 'x', 'tg_chat_id': '-1', 'tg_topic_id': 3}},
                'ts': 10, 'key': 'k', 'server_url': 'u'}
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        loaded = load_state(self.path)
        self.assertEqual(loaded.chats[5].tg_topic_id, 3)
        self.assertTrue(loaded.chats[5].is_active)
        self.assertEqual(loaded.pending, [])

    def test_chat_dict_field_order(self):
        # формат state.json не меняется
        self.assertEqual(list(Chat(peer_id=1, name='x').to_dict()),
                         ['peer_id', 'name', 'tg_chat_id', 'tg_topic_id', 'created_at', 'last_activity',
                          'is_active', 'settings'])


if __name__ == '__main__':
    unittest.main()
