import unittest
from unittest import mock

from tests.fakes import FakeTG
from vk_tg_bridge import relay
from vk_tg_bridge.storage import BotState, Chat
from vk_tg_bridge.tg_client import DROP, RETRY
from vk_tg_bridge.vk_longpoll import VkMessage

PEER = 2000000005


def message(text='привет', peer_id=PEER):
    return VkMessage(message_id=1, flags=0, peer_id=peer_id, date=1_700_000_000, text=text, extras=[{'from': '77'}])


def bound_state():
    return BotState(chats={PEER: Chat(peer_id=PEER, name='Друзья', tg_chat_id='-100', tg_topic_id=7)})


@mock.patch('vk_tg_bridge.vk_client.get_name', lambda owner_id: f'Имя<{owner_id}>')
class FormatMessageTest(unittest.TestCase):
    def test_header_and_escaping(self):
        text = relay.format_message({'peer_id': PEER, 'from_id': 77, 'date': 1_700_000_000, 'text': 'a < b & c'})
        header, body = text.split('\n', 1)
        self.assertTrue(header.startswith('<b>Имя&lt;77&gt;</b> · '))
        self.assertEqual(body, 'a &lt; b &amp; c')

    def test_unknown_sender(self):
        text = relay.format_message({'peer_id': PEER, 'from_id': None, 'date': None, 'text': 'x'})
        self.assertEqual(text, '<b>Неизвестный отправитель</b>\nx')

    def test_old_queue_item_is_plain_text(self):
        self.assertEqual(relay.format_message({'peer_id': PEER, 'text': '<i>'}), '&lt;i&gt;')

    def test_long_text_is_truncated(self):
        text = relay.format_message({'peer_id': PEER, 'text': 'я' * 5000})
        self.assertEqual(len(text), relay.TG_TEXT_LIMIT + 1)
        self.assertTrue(text.endswith('…'))


class EnqueueTest(unittest.TestCase):
    def test_bound_chat_is_queued(self):
        state = bound_state()
        self.assertTrue(relay.enqueue(state, message()))
        self.assertEqual(state.pending, [{'peer_id': PEER, 'from_id': 77, 'date': 1_700_000_000, 'text': 'привет'}])

    def test_unbound_chat_is_skipped_without_vk_calls(self):
        state = bound_state()
        msg = message(peer_id=123)
        with mock.patch.object(VkMessage, 'sender_id', side_effect=AssertionError('лишний запрос к VK')):
            self.assertFalse(relay.enqueue(state, msg))
        self.assertEqual(state.pending, [])

    def test_empty_text_is_skipped(self):
        state = bound_state()
        self.assertFalse(relay.enqueue(state, message(text='')))

    def test_overflow_drops_oldest(self):
        state = bound_state()
        state.pending = [{'peer_id': PEER, 'text': str(i)} for i in range(relay.MAX_PENDING)]
        relay.enqueue(state, message(text='new'))
        self.assertEqual(len(state.pending), relay.MAX_PENDING)
        self.assertEqual(state.pending[0]['text'], '1')
        self.assertEqual(state.pending[-1]['text'], 'new')


class FlushTest(unittest.TestCase):
    def setUp(self):
        self.state = bound_state()
        self.state.pending = [{'peer_id': PEER, 'text': str(i)} for i in range(3)]
        self.tg = FakeTG()

    def test_all_sent_in_order_to_topic(self):
        self.assertTrue(relay.flush(self.state, self.tg))
        self.assertEqual(self.tg.texts, ['0', '1', '2'])
        self.assertEqual(self.tg.sent[0][0], '-100')
        self.assertEqual(self.tg.sent[0][2], {'thread_id': 7, 'parse_mode': 'HTML'})
        self.assertEqual(self.state.pending, [])

    def test_retry_stops_and_keeps_message(self):
        self.tg.send_results = ['sent', RETRY]
        self.assertTrue(relay.flush(self.state, self.tg))
        self.assertEqual([i['text'] for i in self.state.pending], ['1', '2'])

    def test_retry_on_first_means_no_change(self):
        self.tg.send_results = [RETRY]
        self.assertFalse(relay.flush(self.state, self.tg))
        self.assertEqual(len(self.state.pending), 3)

    def test_drop_skips_message(self):
        self.tg.send_results = [DROP]
        relay.flush(self.state, self.tg)
        self.assertEqual(self.state.pending, [])
        self.assertEqual(len(self.tg.sent), 3)

    def test_unbound_item_is_dropped_without_sending(self):
        self.state.pending.insert(0, {'peer_id': 999, 'text': 'lost'})
        relay.flush(self.state, self.tg)
        self.assertNotIn('lost', self.tg.texts)


if __name__ == '__main__':
    unittest.main()
