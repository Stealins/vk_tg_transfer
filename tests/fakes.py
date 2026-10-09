"""Подделка TelegramClient для тестов: запоминает вызовы, ничего не шлёт в сеть."""
from vk_tg_bridge.tg_client import SENT


class FakeTG:
    def __init__(self):
        self.sent = []              # (chat_id, text, kwargs)
        self.send_results = []      # что вернуть на очередные send_message; пусто -> SENT
        self.topics = {}            # topic_id -> True/False/None для topic_exists
        self.next_topic_id = 100
        self.callbacks = []

    def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return self.send_results.pop(0) if self.send_results else SENT

    def answer_callback(self, callback_query_id):
        self.callbacks.append(callback_query_id)

    def create_topic(self, chat_id, name):
        if self.next_topic_id is None:
            return None
        topic_id = self.next_topic_id
        self.next_topic_id += 1
        self.topics[topic_id] = True
        return topic_id

    def topic_exists(self, chat_id, topic_id):
        return self.topics.get(topic_id, True)

    @property
    def texts(self):
        return [text for _, text, _ in self.sent]
