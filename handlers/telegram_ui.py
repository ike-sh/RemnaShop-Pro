"""Edit callback messages according to their Telegram message type."""

import logging

from telegram.error import BadRequest


logger = logging.getLogger(__name__)
CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096
CAPTION_MEDIA = ('photo', 'document', 'video', 'animation', 'audio', 'voice')
EDIT_LIMIT_ERRORS = (
    'there is no text in the message to edit',
    'message caption is too long',
    'message is too long',
    "message can't be edited",
    'message to edit not found',
    'message identifier is not specified',
)


def utf16_length(value: str) -> int:
    return len(value.encode('utf-16-le')) // 2


def fit_telegram_text(value: str, limit: int = TEXT_LIMIT) -> str:
    result = []
    units = 0
    for char in value:
        cost = 2 if ord(char) > 0xFFFF else 1
        if units + cost > limit - 1:
            return ''.join(result) + '…'
        result.append(char)
        units += cost
    return ''.join(result)


def callback_message_kind(message) -> str:
    if message is None:
        return 'inaccessible'
    if getattr(message, 'text', None) is not None:
        return 'text'
    for name in CAPTION_MEDIA:
        if getattr(message, name, None):
            return name
    return 'other'


def _is_edit_limit(error: BadRequest) -> bool:
    return any(part in str(error).lower() for part in EDIT_LIMIT_ERRORS)


def _is_parse_error(error: BadRequest) -> bool:
    return "can't parse entities" in str(error).lower()


async def edit_callback_message(query, bot, chat_id: int, content: str,
                                reply_markup=None, parse_mode=None) -> str:
    """Return text, caption, or fallback; never mistake a media caption for text."""
    kind = callback_message_kind(getattr(query, 'message', None))
    method = None
    if kind == 'text' and utf16_length(content) <= TEXT_LIMIT:
        method = query.edit_message_text
    elif kind in CAPTION_MEDIA and utf16_length(content) <= CAPTION_LIMIT:
        method = query.edit_message_caption

    if method is not None:
        kwargs = {'text' if kind == 'text' else 'caption': content,
                  'reply_markup': reply_markup, 'parse_mode': parse_mode}
        try:
            await method(**kwargs)
            return 'text' if kind == 'text' else 'caption'
        except BadRequest as exc:
            if 'message is not modified' in str(exc).lower():
                return 'text' if kind == 'text' else 'caption'
            if parse_mode is not None and _is_parse_error(exc):
                try:
                    await method(**{**kwargs, 'parse_mode': None})
                    return 'text' if kind == 'text' else 'caption'
                except BadRequest as plain_error:
                    if not _is_edit_limit(plain_error):
                        raise
                    exc = plain_error
            if not _is_edit_limit(exc):
                raise
            logger.warning('Callback edit limitation: kind=%s error=%s', kind, type(exc).__name__)

    keyboard_cleared = False
    try:
        await query.edit_message_reply_markup(reply_markup=None)
        keyboard_cleared = True
    except BadRequest as exc:
        logger.warning('Could not clear callback keyboard: kind=%s error=%s', kind, type(exc).__name__)
        try:
            await query.delete_message()
            keyboard_cleared = True
        except BadRequest as delete_error:
            logger.warning('Could not delete callback message: kind=%s error=%s',
                           kind, type(delete_error).__name__)

    body = content if keyboard_cleared else '⚠️ 原卡片按钮无法移除，请以当前订单状态为准。\n' + content
    body = fit_telegram_text(body)
    try:
        await bot.send_message(chat_id=chat_id, text=body, reply_markup=reply_markup,
                               parse_mode=parse_mode)
    except BadRequest as exc:
        if parse_mode is None or not _is_parse_error(exc):
            raise
        await bot.send_message(chat_id=chat_id, text=body, reply_markup=reply_markup)
    return 'fallback'
