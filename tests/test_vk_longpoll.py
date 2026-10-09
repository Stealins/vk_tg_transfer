import unittest
from unittest import mock

from vk_tg_bridge import vk_longpoll
from vk_tg_bridge.vk_longpoll import LongPoll, VkMessage, vk_text


class VkTextTest(unittest.TestCase):
    def test_br_and_entities(self):
        self.assertEqual(vk_text(' a<br>b &amp; &lt;c&gt; &quot;d&quot; '), 'a\nb & <c> "d"')


class SenderTest(unittest.TestCase):
    def msg(self, flags=0, peer_id=2000000005, extras=()):
        return VkMessage(message_id=9, flags=flags, peer_id=peer_id, date=0, text='x', extras=list(extras))

    @mock.patch('vk_tg_bridge.vk_client.get_my_id', return_value=1)
    def test_outgoing_is_token_owner(self, _):
        self.assertEqual(self.msg(flags=vk_longpoll.VK_OUTBOX | 1).sender_id(), 1)

    def test_private_chat_author_is_peer(self):
        self.assertEqual(self.msg(peer_id=55).sender_id(), 55)
        self.assertEqual(self.msg(peer_id=-200).sender_id(), -200)   # сообщество

    def test_group_chat_from_field(self):
        self.assertEqual(self.msg(extras=[{'title': 't'}, {'from': '77'}]).sender_id(), 77)

    @mock.patch('vk_tg_bridge.vk_client.get_sender_id', return_value=88)
    def test_group_chat_fallback_to_get_by_id(self, get_sender_id):
        self.assertEqual(self.msg(extras=[{}]).sender_id(), 88)
        get_sender_id.assert_called_once_with(9)

    @mock.patch('vk_tg_bridge.vk_client.get_sender_id', side_effect=RuntimeError('нет сети'))
    def test_error_gives_none(self, _):
        self.assertIsNone(self.msg().sender_id())

    def test_from_event(self):
        msg = VkMessage.from_event([4, 9, 2, 2000000005, 1700000000, 'a<br>b', {'from': '7'}, {}, 'extra'])
        self.assertEqual((msg.peer_id, msg.text, msg.extras), (2000000005, 'a\nb', [{'from': '7'}, {}]))


def response(data):
    r = mock.Mock()
    r.json.return_value = data
    return r


class PollTest(unittest.TestCase):
    def setUp(self):
        self.lp = LongPoll(ts=100)
        self.lp.server_url, self.lp.key, self.lp.available = 'https://lp', 'k', True

    def poll(self, data):
        with mock.patch('requests.get', return_value=response(data)):
            return self.lp.poll()

    def test_new_messages_only(self):
        messages = self.poll({'ts': 101, 'updates': [[4, 1, 0, 5, 0, 'hi'], [7, 5, 1], [4, 2, 0, 6, 0, 'yo']]})
        self.assertEqual([m.text for m in messages], ['hi', 'yo'])
        self.assertEqual(self.lp.ts, 101)

    def test_failed_1_takes_new_ts(self):
        self.assertEqual(self.poll({'failed': 1, 'ts': 500}), [])
        self.assertEqual(self.lp.ts, 500)

    @mock.patch('vk_tg_bridge.vk_client.get_long_poll_server', return_value=('https://new', 'k2', 900))
    def test_failed_2_keeps_ts(self, _):
        self.poll({'failed': 2})
        self.assertEqual((self.lp.key, self.lp.ts), ('k2', 100))

    @mock.patch('vk_tg_bridge.vk_client.get_long_poll_server', return_value=('https://new', 'k2', 900))
    def test_failed_3_takes_new_ts(self, _):
        self.poll({'failed': 3})
        self.assertEqual((self.lp.key, self.lp.ts), ('k2', 900))

    def test_network_error_keeps_ts(self):
        import requests
        with mock.patch('requests.get', side_effect=requests.exceptions.ConnectionError('VPN')):
            self.assertEqual(self.lp.poll(), [])
        self.assertEqual(self.lp.ts, 100)

    def test_unavailable_does_not_poll(self):
        self.lp.available = False
        with mock.patch('requests.get', side_effect=AssertionError('не должно быть запроса')):
            self.assertEqual(self.lp.poll(), [])


class RefreshTest(unittest.TestCase):
    @mock.patch('vk_tg_bridge.vk_auth.refresh_token', return_value=True)
    @mock.patch('vk_tg_bridge.vk_client.get_long_poll_server', side_effect=[None, ('https://s', 'k', 5)])
    def test_refreshes_token_when_vk_refuses(self, _, refresh_token):
        lp = LongPoll()
        self.assertTrue(lp.refresh())
        refresh_token.assert_called_once_with(force=True)
        self.assertEqual((lp.ts, lp.available), (5, True))

    @mock.patch('vk_tg_bridge.vk_auth.refresh_token', return_value=False)
    @mock.patch('vk_tg_bridge.vk_client.get_long_poll_server', return_value=None)
    def test_unavailable_when_token_fails(self, *_):
        lp = LongPoll(ts=3)
        self.assertFalse(lp.refresh(keep_ts=True))
        self.assertEqual((lp.ts, lp.available), (3, False))


if __name__ == '__main__':
    unittest.main()
