"""
Точка входа: myvenv\\Scripts\\python -m vk_tg_bridge

Один поток, один цикл: TG long poll (команды) -> VK long poll (сообщения в очередь) ->
отправка очереди в TG -> автосохранение. Останавливать Ctrl+C: при жёстком убийстве процесса
(кнопка Stop в VS Code) finally не выполняется и последние изменения не сохраняются.
"""
import logging
import signal
import sys
import time

from . import config, relay, vk_auth
from .dialog import Dialog
from .logging_config import setup_logging
from .storage import StateCorrupted, load_state, save_state
from .tg_client import TelegramClient
from .vk_longpoll import LongPoll

logger = logging.getLogger(__name__)

TOKEN_CHECK_INTERVAL = 60   # как часто проверять срок жизни VK-токена, с
AUTOSAVE_INTERVAL = 10      # как часто сохранять изменения, не связанные с очередью (сдвиг ts), с.
                            # Короткий — чтобы ts попал на диск, даже если бота убили жёстко (без finally)
FLUSH_RETRY_DELAY = 10      # пауза перед повторной отправкой очереди после ошибки TG, с


class Bot:
    def __init__(self, tg, group_id, state):
        self.tg = tg
        self.state = state
        self.longpoll = LongPoll(ts=state.ts)
        self.dialog = Dialog(tg, group_id, state, save=self.save, vk_connected=lambda: self.longpoll.available)
        self.offset = None          # TG offset не храним: Telegram подтверждает апдейты сам
        self.dirty = False          # есть изменения (ts), ещё не записанные на диск
        self.last_save_time = time.time()
        self.last_token_check = 0
        self.flush_retry_after = 0

    def save(self):
        if save_state(self.state, config.STATE_FILE):
            self.dirty = False
            self.last_save_time = time.time()

    def autosave(self, force=False):
        """Сохраняет сразу при force (изменилась очередь), иначе — накопленные изменения раз в AUTOSAVE_INTERVAL."""
        if force or (self.dirty and time.time() - self.last_save_time > AUTOSAVE_INTERVAL):
            self.save()

    def connect_vk(self):
        """VK-токен из браузерной сессии (если входа нет — окно для входа), затем сервер long poll."""
        if vk_auth.token_expires_soon():
            try:
                vk_auth.fetch_token()
            except vk_auth.NotLoggedIn:
                try:
                    vk_auth.login_interactive()
                except Exception as e:
                    logger.error(f'Не удалось войти в VK: {e}')
            except Exception as e:
                logger.error(f'Ошибка получения VK-токена: {e}', exc_info=True)
        # Сохранённый ts оставляем — VK отдаст события за время простоя
        try:
            if self.longpoll.refresh(keep_ts=True):
                logger.info('Long Poll сервер успешно инициализирован')
            else:
                logger.error('Не удалось получить Long Poll сервер. Проверьте VK-токен.')
        except Exception as e:
            logger.error(f'Ошибка инициализации Long Poll: {e}')

    def keep_vk_alive(self):
        """Обновляет VK-токен до истечения срока; переподключает long poll, если он отвалился."""
        if time.time() - self.last_token_check > TOKEN_CHECK_INTERVAL:
            self.last_token_check = time.time()
            if vk_auth.token_expires_soon() and vk_auth.refresh_token():
                self.longpoll.refresh(keep_ts=True)
        elif not self.longpoll.available:
            self.longpoll.refresh(keep_ts=True)

    def poll_telegram(self):
        response = self.tg.get_updates(self.offset)
        if not response:
            return
        if not response.get('ok'):
            # 409 — бот запущен ещё где-то (или стоит webhook)
            logger.error(f"getUpdates вернул ошибку {response.get('error_code')}: {response.get('description')}")
            return
        for update in response.get('result', []):
            try:
                self.dialog.handle_update(update)
            except Exception as e:
                # Иначе апдейт, роняющий обработчик, приходил бы снова и снова
                logger.error(f"Ошибка обработки апдейта {update['update_id']}: {e}", exc_info=True)
            # Подтверждаем апдейт только после обработки (и любой другой тип апдейта тоже)
            self.offset = update['update_id'] + 1

    def poll_vk(self):
        """Новые VK-сообщения — в очередь. Возвращает, сколько поставлено."""
        queued = sum(relay.enqueue(self.state, message) for message in self.longpoll.poll())
        if self.longpoll.ts != self.state.ts:
            self.state.ts = self.longpoll.ts
            self.dirty = True
        return queued

    def flush(self):
        """Отправляет очередь в TG; после временной ошибки — не раньше чем через FLUSH_RETRY_DELAY."""
        if not self.state.pending or time.time() < self.flush_retry_after:
            return False
        changed = relay.flush(self.state, self.tg)
        if self.state.pending:
            self.flush_retry_after = time.time() + FLUSH_RETRY_DELAY
        return changed

    def run(self):
        while True:
            try:
                self.keep_vk_alive()
                self.poll_telegram()
                queued = self.poll_vk()
                flushed = self.flush()
                # Изменилась очередь — сохраняем сразу (ts и очередь пишутся вместе)
                self.autosave(force=queued > 0 or flushed)
                time.sleep(0.5)  # снижаем нагрузку на CPU

            except Exception as e:
                logger.info(f'Ошибка в основном цикле: {e}', exc_info=True)
                # При ошибке Long Poll можно попробовать переполучить сервер
                if 'lp' in str(e).lower() or 'connection' in str(e).lower():
                    try:
                        self.longpoll.refresh(keep_ts=True)
                    except Exception as lp_err:
                        logger.error(f'Не удалось обновить Long Poll: {lp_err}', exc_info=True)
                        self.longpoll.available = False
                time.sleep(5)  # пауза перед повторной попыткой


def on_sigterm(signum, frame):
    # Превращаем SIGTERM в обычное исключение — состояние сохранит finally в main()
    raise SystemExit(0)


def main():
    setup_logging()
    # SIGINT (Ctrl+C) по умолчанию даёт KeyboardInterrupt; SIGTERM превращаем в SystemExit.
    # Сохранение — в finally ниже, а не в обработчике сигнала (он мог прервать save_state посреди записи)
    signal.signal(signal.SIGTERM, on_sigterm)

    token = config.read_secret(config.TG_TOKEN_FILE)
    if not token:
        logger.fatal(f'Ошибка: не найден токен Telegram. Поместите его в {config.TG_TOKEN_FILE}')
        sys.exit(1)
    group_id = config.read_secret(config.GROUP_ID_FILE)
    if not group_id:
        logger.error(f'Ошибка: не найден id вашей супергруппы. Поместите его в {config.GROUP_ID_FILE}')
        sys.exit(2)

    tg = TelegramClient(token, config.TG_PROXIES)
    tg.get_me()     # заодно проверка связи с Telegram (VPN) — сразу видно в логе, если её нет

    try:
        state = load_state(config.STATE_FILE)
    except StateCorrupted as e:
        logger.critical(f'Состояние не загружено: {e}. Проверьте файлы вручную, бот остановлен.')
        sys.exit(3)

    bot = Bot(tg, group_id, state)
    bot.dialog.sync_topics()    # пока бот не работал, топики могли удалить вручную
    bot.connect_vk()

    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info('Получен сигнал завершения')
    finally:
        bot.save()
        logger.info('Состояние сохранено, бот остановлен.')


if __name__ == '__main__':
    main()
