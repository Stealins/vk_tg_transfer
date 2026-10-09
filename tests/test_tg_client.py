import unittest
from unittest import mock

import requests

from vk_tg_bridge.tg_client import DROP, RETRY, SENT, TelegramClient


def http_response(data):
    r = mock.Mock()
    r.json.return_value = data
    return r


class SendMessageTest(unittest.TestCase):
    def setUp(self):
        self.tg = TelegramClient('123:abc', {'https': 'socks5h://proxy'})

    def send(self, result=None, error=None, **kwargs):
        side = {'side_effect': error} if error else {'return_value': http_response(result)}
        with mock.patch('requests.post', **side) as post:
            return self.tg.send_message('-100', 'hi', **kwargs), post

    def test_ok(self):
        result, post = self.send({'ok': True}, thread_id=7, parse_mode='HTML')
        self.assertEqual(result, SENT)
        self.assertEqual(post.call_args.kwargs['json'],
                         {'chat_id': '-100', 'text': 'hi', 'message_thread_id': 7, 'parse_mode': 'HTML'})
        self.assertEqual(post.call_args.kwargs['proxies'], {'https': 'socks5h://proxy'})

    def test_none_fields_not_sent(self):
        _, post = self.send({'ok': True})
        self.assertEqual(post.call_args.kwargs['json'], {'chat_id': '-100', 'text': 'hi'})

    def test_temporary_errors_retry(self):
        for code in (429, 500, 502):
            self.assertEqual(self.send({'ok': False, 'error_code': code})[0], RETRY)
        self.assertEqual(self.send(error=requests.exceptions.ConnectionError('VPN'))[0], RETRY)

    def test_permanent_errors_drop(self):
        for code in (400, 403):
            self.assertEqual(self.send({'ok': False, 'error_code': code})[0], DROP)


class TopicExistsTest(unittest.TestCase):
    def check(self, *responses):
        tg = TelegramClient('123:abc', {})
        with mock.patch('requests.post', side_effect=[http_response(r) for r in responses]) as post:
            return tg.topic_exists('-100', 5), [c.args[0].rsplit('/', 1)[1] for c in post.call_args_list]

    def test_open_topic(self):
        self.assertEqual(self.check({'ok': False, 'description': 'Bad Request: TOPIC_NOT_MODIFIED'}),
                         (True, ['reopenForumTopic']))

    def test_deleted_topic(self):
        self.assertEqual(self.check({'ok': False, 'description': 'Bad Request: TOPIC_ID_INVALID'})[0], False)

    def test_closed_topic_is_closed_back(self):
        self.assertEqual(self.check({'ok': True}, {'ok': True}), (True, ['reopenForumTopic', 'closeForumTopic']))

    def test_unknown_answer(self):
        self.assertIsNone(self.check({'ok': False, 'description': 'Forbidden'})[0])


if __name__ == '__main__':
    unittest.main()
