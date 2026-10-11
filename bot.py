from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, Router
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramNetworkError,
    TelegramUnauthorizedError,
)
from aiogram.filters import Command, CommandObject
from aiogram.types import BotCommand, Message
from aiogram.utils.token import TokenValidationError


# Состояние приложения хранится только в памяти.
HISTORY_SIZE = 100
MAX_LOGS_REQUEST = 20
MAX_SHORT_SECONDS = 24 * 60 * 60
MAX_DAYS = 7

UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
UNIT_FORMS = {
    "s": ("секунду", "секунды", "секунд"),
    "m": ("минуту", "минуты", "минут"),
    "h": ("час", "часа", "часов"),
    "d": ("день", "дня", "дней"),
}

HELP_TEXT = (
    "Доступные команды:\n\n"
    "/start — показать приветствие и справку.\n"
    "Пример: /start\n\n"
    "/help — показать все команды.\n"
    "Пример: /help\n\n"
    "/timer <число><единица> — установить таймер.\n"
    "s — секунды, m — минуты, h — часы, d — дни.\n"
    "Примеры: /timer 30s, /timer 10m, /timer 2h, /timer 2d.\n"
    "Пробелы игнорируются: /timer 10 m.\n"
    "Для s/m/h максимум 24 часа; для d максимум 7 дней.\n"
    "Новый таймер заменяет предыдущий.\n\n"
    "/cancel — отменить свой активный таймер.\n"
    "Пример: /cancel\n\n"
    "/logs n — показать последние n своих команд.\n"
    f"Пример: /logs 5. Допустимо от 1 до {MAX_LOGS_REQUEST}.\n"
    "История включает саму команду /logs.\n\n"
    "Все таймеры и история теряются при перезапуске."
)

TIMER_HINT = (
    "Укажите положительное целое число и единицу s, m, h или d.\n"
    "Примеры: /timer 30s, /timer 10m, /timer 2h, /timer 2d.\n"
    "Можно использовать пробелы: /timer 10 m.\n"
    "Для s/m/h максимум 24 часа; для d максимум 7 дней."
)

logger = logging.getLogger("timer_bot")
router = Router()


@dataclass(frozen=True, slots=True)
class Duration:
    amount: int
    unit: str
    seconds: int

    @property
    def normalized(self) -> str:
        return f"{self.amount}{self.unit}"

    @property
    def description(self) -> str:
        one, few, many = UNIT_FORMS[self.unit]
        if 11 <= self.amount % 100 <= 14:
            word = many
        elif self.amount % 10 == 1:
            word = one
        elif self.amount % 10 in (2, 3, 4):
            word = few
        else:
            word = many
        return f"{self.amount} {word}"


@dataclass(slots=True)
class CommandLog:
    timestamp: datetime
    command: str
    state: str = "обработка"
    result: str = "выполняется"
    error: str | None = None


@dataclass(slots=True)
class TimerEntry:
    task: asyncio.Task[None]
    end_time: datetime
    duration: Duration
    command_log: CommandLog


timers: dict[int, TimerEntry] = {}
histories: dict[int, deque[CommandLog]] = {}
user_locks: dict[int, asyncio.Lock] = {}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Собственные сообщения не содержат токенов, текстов пользователей
    # или содержимого исключений. Служебные логи библиотеки отключены,
    # чтобы случайно не вывести чувствительные сведения из исключений.
    logging.getLogger("aiogram").disabled = True
    for name in (
        "aiogram.dispatcher",
        "aiogram.event",
        "aiogram.middlewares",
        "aiogram.webhook",
    ):
        logging.getLogger(name).disabled = True


def load_environment() -> None:
    """Минимальный загрузчик BOT_TOKEN из .env рядом с bot.py."""
    path = Path(__file__).resolve().with_name(".env")
    if not path.exists():
        return

    # Это файл конфигурации, а не постоянное хранилище состояния.
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()

        key, separator, value = line.partition("=")
        if not separator or key.strip() != "BOT_TOKEN":
            continue

        value = value.strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError("Незакрытая кавычка у BOT_TOKEN в .env")
            value = value[1:-1]

        # Переменная, заданная в системе, имеет приоритет.
        os.environ.setdefault("BOT_TOKEN", value)


def parse_duration(argument: str | None) -> Duration:
    compact = re.sub(r"\s+", "", argument or "").lower()
    match = re.fullmatch(r"([0-9]{1,9})([smhd])", compact)
    if match is None:
        raise ValueError(TIMER_HINT)

    amount = int(match.group(1))
    unit = match.group(2)

    if amount <= 0:
        raise ValueError("Длительность должна быть больше нуля.\n" + TIMER_HINT)

    seconds = amount * UNIT_SECONDS[unit]
    if unit == "d":
        if amount > MAX_DAYS:
            raise ValueError(
                f"Для единицы d максимум {MAX_DAYS} дней. "
                "Пример: /timer 2d."
            )
    elif seconds > MAX_SHORT_SECONDS:
        raise ValueError(
            "Для единиц s/m/h максимум 24 часа: "
            "86400s, 1440m или 24h.\n"
            "Для более долгого таймера используйте d: /timer 2d."
        )

    return Duration(amount=amount, unit=unit, seconds=seconds)


def parse_log_count(argument: str | None) -> int:
    value = (argument or "").strip()
    if not re.fullmatch(r"[0-9]{1,3}", value):
        raise ValueError(
            f"Используйте /logs n, где n от 1 до {MAX_LOGS_REQUEST}. "
            "Пример: /logs 5."
        )

    count = int(value)
    if not 1 <= count <= MAX_LOGS_REQUEST:
        raise ValueError(
            f"Количество записей должно быть от 1 до {MAX_LOGS_REQUEST}."
        )
    return count


def current_state(user_id: int) -> str:
    entry = timers.get(user_id)
    if entry is None:
        return "активного таймера нет"
    end = entry.end_time.strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"активен {entry.duration.normalized}, окончание {end}"


async def safe_send(
    bot: Bot,
    chat_id: int,
    user_id: int,
    text: str,
) -> str | None:
    """Отправить текст; вернуть только безопасное имя ошибки."""
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception as exc:
        # CancelledError в Python 3.11 не является Exception:
        # отмена задачи здесь не перехватывается.
        error = type(exc).__name__
        logger.error(
            "user_id=%s event=send_failed error=%s", user_id, error
        )
        return error
    return None


async def reply(
    bot: Bot,
    message: Message,
    user_id: int,
    record: CommandLog,
    text: str,
) -> None:
    error = await safe_send(bot, message.chat.id, user_id, text)
    if error:
        record.error = error
        record.result += "; ответ не доставлен"


async def run_timer(
    bot: Bot,
    user_id: int,
    chat_id: int,
    seconds: int,
    record: CommandLog,
) -> None:
    """Ожидание таймера без блокировки обработчиков команд."""
    own_task = asyncio.current_task()
    try:
        await asyncio.sleep(seconds)

        # После начала уведомления задача остаётся доступной для отмены.
        record.state = "отправка уведомления"
        error = await safe_send(bot, chat_id, user_id, "Время вышло!")

        if error:
            record.error = error
            record.state = "завершён"
            record.result = "Время истекло; уведомление не доставлено"
        else:
            record.state = "завершён"
            record.result = "Время истекло; уведомление доставлено"

        logger.info(
            "user_id=%s event=timer_finished notification=%s",
            user_id,
            "failed" if error else "sent",
        )
    finally:
        # Старый таймер никогда не удаляет запись нового таймера.
        entry = timers.get(user_id)
        if entry is not None and entry.task is own_task:
            timers.pop(user_id, None)
        # CancelledError намеренно не перехватывается и не подавляется.


async def cancel_timer(user_id: int, reason: str) -> bool:
    entry = timers.get(user_id)
    if entry is None:
        return False

    entry.task.cancel()

    # Ожидаем завершения отменённой дочерней задачи. gather получает
    # её исключение отмены, но сама задача остаётся cancelled().
    # Отмена текущего обработчика продолжает распространяться.
    await asyncio.gather(entry.task, return_exceptions=True)

    if timers.get(user_id) is entry:
        timers.pop(user_id, None)

    entry.command_log.state = "отменён"
    entry.command_log.result = reason
    logger.info("user_id=%s event=timer_cancelled", user_id)
    return True


def build_log_messages(user_id: int, count: int) -> list[str]:
    entries = list(histories[user_id])[-count:]
    header = (
        f"Последние команды: {len(entries)}.\n"
        f"Текущее состояние: {current_state(user_id)}.\n"
        "Время указано в UTC; состояние записи относится к её выполнению."
    )

    # Разбиваем длинный журнал, не превышая лимит Telegram.
    messages: list[str] = []
    chunk = header
    for record in entries:
        block = (
            f"\n\n{record.timestamp:%Y-%m-%d %H:%M:%S} UTC\n"
            f"Команда: {record.command}\n"
            f"Состояние: {record.state}\n"
            f"Результат: {record.result}\n"
            f"Ошибка: {record.error or 'нет'}"
        )
        if len(chunk) + len(block) > 3500:
            messages.append(chunk)
            chunk = "Продолжение журнала:"
        chunk += block

    messages.append(chunk)
    return messages


async def process_message(
    message: Message,
    bot: Bot,
    command: CommandObject | None,
) -> None:
    if message.from_user is None or message.from_user.is_bot:
        return

    user_id = message.from_user.id
    lock = user_locks.setdefault(user_id, asyncio.Lock())

    # Команды одного пользователя выполняются последовательно.
    # Другие пользователи при этом не блокируются.
    async with lock:
        first_contact = user_id not in histories
        history = histories.setdefault(user_id, deque(maxlen=HISTORY_SIZE))
        name = command.command.lower() if command else None

        if first_contact and name not in {"start", "help"}:
            await safe_send(
                bot,
                message.chat.id,
                user_id,
                "Здравствуйте! Я бот для таймеров.\n\n" + HELP_TEXT,
            )

        if command is None:
            # Произвольные сообщения и аргументы не сохраняются.
            if not (message.text or "").lstrip().startswith("/"):
                return
            record = CommandLog(
                timestamp=utc_now(),
                command="/[неизвестная команда]",
                state=current_state(user_id),
                result="Отклонена",
                error="UnknownCommand",
            )
            history.append(record)
            await reply(
                bot, message, user_id, record,
                "Неизвестная команда. Используйте /help.",
            )
            return

        # Сохраняются только известное имя команды и проверенные аргументы.
        record = CommandLog(timestamp=utc_now(), command=f"/{name}")
        history.append(record)

        try:
            if name in {"start", "help"}:
                record.state = current_state(user_id)
                record.result = "Справка подготовлена"
                text = HELP_TEXT
                if name == "start":
                    text = "Здравствуйте! Я бот для таймеров.\n\n" + text
                await reply(bot, message, user_id, record, text)

            elif name == "timer":
                record.command = "/timer [непроверенный аргумент]"
                duration = parse_duration(command.args)
                record.command = f"/timer {duration.normalized}"

                # Сначала полностью отменяем прежнюю задачу.
                replaced = await cancel_timer(
                    user_id, "Заменён новым таймером"
                )

                task = asyncio.create_task(
                    run_timer(
                        bot, user_id, message.chat.id,
                        duration.seconds, record,
                    ),
                    name=f"timer:{user_id}",
                )
                timers[user_id] = TimerEntry(
                    task=task,
                    end_time=utc_now() + timedelta(seconds=duration.seconds),
                    duration=duration,
                    command_log=record,
                )

                record.state = current_state(user_id)
                record.result = (
                    "Установлен; предыдущий таймер отменён"
                    if replaced else "Установлен"
                )
                logger.info(
                    "user_id=%s event=timer_started duration=%s",
                    user_id, duration.normalized,
                )
                await reply(
                    bot, message, user_id, record,
                    f"Таймер на {duration.description} установлен",
                )

            elif name == "cancel":
                cancelled = await cancel_timer(
                    user_id, "Отменён командой /cancel"
                )
                record.state = current_state(user_id)
                record.result = (
                    "Таймер отменён"
                    if cancelled else "Активный таймер не найден"
                )
                await reply(
                    bot, message, user_id, record, record.result
                )

            elif name == "logs":
                record.command = "/logs [непроверенный аргумент]"
                count = parse_log_count(command.args)
                record.command = f"/logs {count}"
                record.state = current_state(user_id)
                record.result = "Журнал подготовлен"

                for text in build_log_messages(user_id, count):
                    await reply(
                        bot, message, user_id, record, text
                    )

        except ValueError as exc:
            # Это только наши сообщения валидации, без исходного ввода.
            record.command = f"/{name} [неверный аргумент]"
            record.state = current_state(user_id)
            record.result = "Команда отклонена"
            record.error = "ValidationError"
            await reply(
                bot, message, user_id, record, str(exc)
            )
        except Exception as exc:
            record.state = current_state(user_id)
            record.result = "Ошибка выполнения"
            record.error = type(exc).__name__
            logger.error(
                "user_id=%s event=command_failed command=%s error=%s",
                user_id, name, type(exc).__name__,
            )
            await reply(
                bot, message, user_id, record,
                "Не удалось выполнить команду. Попробуйте ещё раз.",
            )


@router.message(Command("start", "help", "timer", "cancel", "logs"))
async def handle_command(
    message: Message,
    bot: Bot,
    command: CommandObject,
) -> None:
    await process_message(message, bot, command)


@router.message()
async def handle_other_message(message: Message, bot: Bot) -> None:
    # Первая обычная реплика тоже вызывает показ справки.
    await process_message(message, bot, None)


async def shutdown_timers() -> None:
    entries = list(timers.items())
    for user_id, entry in entries:
        entry.command_log.state = "отменён"
        entry.command_log.result = "Бот остановлен"
        entry.task.cancel()
        logger.info(
            "user_id=%s event=timer_cancelled reason=shutdown",
            user_id,
        )

    if entries:
        await asyncio.gather(
            *(entry.task for _, entry in entries),
            return_exceptions=True,
        )
    timers.clear()


async def main() -> int:
    configure_logging()

    try:
        load_environment()
    except (OSError, UnicodeError, ValueError) as exc:
        logger.error(
            "Не удалось прочитать .env. Проверьте доступ и формат "
            "BOT_TOKEN=значение. Тип ошибки: %s",
            type(exc).__name__,
        )
        return 1

    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        logger.error(
            "BOT_TOKEN не задан. Создайте .env рядом с bot.py "
            "и добавьте BOT_TOKEN=ваш_токен."
        )
        return 1

    try:
        bot = Bot(token=token)
    except TokenValidationError:
        logger.error(
            "Некорректный формат BOT_TOKEN. Проверьте токен от BotFather."
        )
        return 1

    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    try:
        # Проверяем действительность токена до начала polling.
        await bot.get_me()

        # Удаляем старый webhook и пропускаем накопленные команды:
        # они не должны создавать устаревшие таймеры после перезапуска.
        await bot.delete_webhook(drop_pending_updates=True)

        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Начало работы"),
                BotCommand(command="help", description="Команды и примеры"),
                BotCommand(command="timer", description="Установить таймер"),
                BotCommand(command="cancel", description="Отменить таймер"),
                BotCommand(command="logs", description="Свои последние команды"),
            ]
        )

        logger.info("Бот запущен; ожидание сообщений")
        await dispatcher.start_polling(
            bot,
            allowed_updates=["message"],
            close_bot_session=False,
        )
        return 0

    except TelegramUnauthorizedError:
        logger.error(
            "Telegram отклонил BOT_TOKEN. Проверьте токен или "
            "получите новый у BotFather."
        )
        return 1
    except TelegramNetworkError:
        logger.error(
            "Не удалось подключиться к Telegram. "
            "Проверьте интернет и повторите запуск."
        )
        return 1
    except TelegramAPIError as exc:
        logger.error(
            "Ошибка Telegram API при запуске: %s. "
            "Проверьте настройки и отсутствие второго экземпляра бота.",
            type(exc).__name__,
        )
        return 1
    except Exception as exc:
        # Не печатаем содержимое исключения: оно может содержать секреты.
        logger.error(
            "Не удалось запустить или продолжить работу бота. "
            "Тип ошибки: %s",
            type(exc).__name__,
        )
        return 1
    finally:
        try:
            await shutdown_timers()
        finally:
            await bot.session.close()
            logger.info("Бот остановлен")


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
    except KeyboardInterrupt:
        exit_code = 0
    sys.exit(exit_code)
    