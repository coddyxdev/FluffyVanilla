import discord
from datetime import datetime, timezone

from utils import emojis as em

# ─── Colour palette ──────────────────────────────────────────────────────
PINK        = 0xE91E8C   # Primary
PINK_SOFT   = 0xFF8EC8   # Soft accents
SUCCESS     = 0x57F287
ERROR       = 0xED4245
WARNING     = 0xFEE75C
INFO        = 0xF8BBD0   # Very light pink
CLOSED      = 0x747F8D   # Grey for closed tickets

FOOTER_TEXT = 'Fluffy Vanilla'


def _base(title=None, description=None, color=PINK) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color)
    e.timestamp = datetime.now(timezone.utc)
    e.set_footer(text=FOOTER_TEXT)
    return e


def ok(description: str, title: str = 'Готово') -> discord.Embed:
    return _base(f'✅  {title}', f'{em.SUCCESS} {description}', SUCCESS)


def err(description: str, title: str = 'Ошибка') -> discord.Embed:
    return _base(f'❌  {title}', f'{em.ERROR} {description}', ERROR)


def warn(description: str, title: str = 'Внимание') -> discord.Embed:
    return _base(f'⚠️  {title}', f'{em.WARNING} {description}', WARNING)


def info(description: str, title: str = None) -> discord.Embed:
    body = f'{em.INFO} {description}' if description else em.INFO
    return _base(title, body, PINK)


# ─── Specific embeds ─────────────────────────────────────────────────

def ticket_panel_embed(title: str, description: str) -> discord.Embed:
    return _base(f'🎫 {title}', f'{em.TICKET} {description}', PINK)


def ticket_open_embed(number: int, ticket_type: str, reason: str,
                      creator: discord.Member) -> discord.Embed:
    label = 'Технический тикет' if ticket_type == 'tech' else 'Тикет'
    e = _base(
        f'{label} #{number:04d}',
        f'{em.TICKET} **Причина:** {reason}',
        PINK if ticket_type == 'regular' else PINK_SOFT
    )
    e.add_field(name='Создатель', value=f'{em.MEMBER} {creator.mention}', inline=True)
    e.add_field(name='Тип', value=f'{em.TICKET} {label}', inline=True)
    e.add_field(name='Статус', value=f'{em.VERIFIED} Открыт', inline=True)
    e.set_author(name=str(creator), icon_url=creator.display_avatar.url)
    return e


def ticket_closed_embed(number: int, closed_by: discord.Member) -> discord.Embed:
    return _base(
        f'Тикет #{number:04d} закрыт',
        f'{em.TICKET} Тикет был закрыт пользователем {closed_by.mention}.',
        CLOSED
    )


def wl_info_embed() -> discord.Embed:
    return _base(
        'Добро пожаловать на Fluffy Vanilla',
        (
            f'{em.FLOWER} **Fluffy Vanilla** — уютный ванильный сервер для небольшого сообщества.\n\n'
            f'{em.ARROW_PINK} Чтобы попасть в белый список, нажми кнопку «Подать заявку» '
            'и заполни короткую анкету.\n\n'
            'Администраторы рассмотрят заявку и сообщат решение в личные сообщения.'
        ),
        PINK
    )


def wl_app_embed(applicant, nick: str, reason: str, about: str, age: str,
                 invited_by: str = None, mojang_status: str = None,
                 account_info: str = None, skin_url: str = None) -> discord.Embed:
    e = _base(
        'Заявка в белый список',
        f'{em.MEMBER} Новая заявка от {applicant.mention}',
        PINK_SOFT
    )
    e.add_field(name='Discord', value=str(applicant), inline=True)
    e.add_field(name='Ник в Minecraft', value=nick, inline=True)
    e.add_field(name='Возраст', value=age, inline=True)
    e.add_field(name='Почему хочет на сервер', value=reason[:1024], inline=False)
    e.add_field(name='О себе', value=about[:1024], inline=False)
    if invited_by:
        e.add_field(name='Пригласил(а)', value=invited_by, inline=False)
    if mojang_status:
        e.add_field(name='Проверка ника', value=mojang_status, inline=True)
    if account_info:
        e.add_field(name='Аккаунт Discord', value=account_info, inline=True)
    if skin_url:
        e.set_thumbnail(url=skin_url)
    e.set_author(name=str(applicant), icon_url=applicant.display_avatar.url)
    return e


def settings_embed(guild: discord.Guild, s) -> discord.Embed:
    """Показать все текущие настройки сервера."""
    def val(key):
        try:
            return s[key]
        except Exception:
            return None

    def ch(key):
        v = val(key)
        return f'<#{v}>' if v else '`не настроен`'

    def role(key):
        v = val(key)
        return f'<@&{v}>' if v else '`не настроена`'

    e = _base(f'⚙️ Настройки — {guild.name}', color=PINK)

    e.add_field(
        name='🎫 Тикеты',
        value=(
            f'{em.TICKET} Категория (обычные): {ch("ticket_category_id")}\n'
            f'Категория (тех): {ch("tech_ticket_category_id")}\n'
            f'Лог тикетов: {ch("ticket_log_channel_id")}\n'
            f'Всего тикетов: `{val("ticket_counter") or 0}`'
        ),
        inline=False,
    )

    e.add_field(
        name='📋 Белый список',
        value=(
            f'{em.VERIFIED} Канал с панелью: {ch("wl_info_channel_id")}\n'
            f'Категория заявок: {ch("wl_category_id")}\n'
            f'Уведомления о заявках: {ch("wl_notify_channel_id")}\n'
            f'Лог решений и архив анкет: {ch("wl_logs_channel_id")}\n'
            f'Роль белого списка: {role("wl_role_id")}'
        ),
        inline=False,
    )

    mc_host = val('mc_host')
    mc_port = val('mc_port')
    mc_key = val('mc_api_key')
    e.add_field(
        name='⛏️ Minecraft-плагин',
        value=(
            (f'{em.DIAMOND} Адрес: `{mc_host}:{mc_port}`\n' if mc_host and mc_port else f'{em.DIAMOND} Адрес: `не настроен`\n')
            + (f'API-ключ: `указан`' if mc_key else 'API-ключ: `не указан`')
        ),
        inline=False,
    )

    e.add_field(
        name='📊 Статистика и голосовые',
        value=(
            f'{em.STAR} Онлайн: {ch("online_channel_id")}\n'
            f'TPS (стройки): {ch("tps_builds_channel_id")}\n'
            f'TPS (фермы): {ch("tps_farms_channel_id")}\n'
            f'Создать комнату: {ch("voice_create_channel_id")}\n'
            f'Категория комнат: {ch("voice_category_id")}'
        ),
        inline=False,
    )

    e.add_field(
        name='📝 Логирование',
        value=f'{em.BOT} Общий лог-канал: {ch("log_channel_id")}',
        inline=False,
    )

    return e


# expose aliases
error_embed = err
success_embed = ok
