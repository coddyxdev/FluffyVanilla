"""
cogs/automod.py — автомодерация чата и защита от рейдов

  /automod настройки   — показать текущее состояние
  /automod фильтр      — включить или выключить конкретный фильтр
  /automod параметры  — пороги капса, спама, упоминаний
  /automod исключение — роли и каналы вне проверки
  /antiraid настройки  — состояние антирейда
  /antiraid настроить  — пороги и реакция на волну заходов
  /antiraid блокировка — ручной режим осады
"""
import logging
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import ok, err, warn as warn_embed, info, PINK, ERROR, WARNING

logger = logging.getLogger('FluFFy.AutoMod')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS automod_config (
    guild_id        INTEGER PRIMARY KEY,
    enabled         INTEGER NOT NULL DEFAULT 1,
    filter_invites  INTEGER NOT NULL DEFAULT 1,
    filter_links    INTEGER NOT NULL DEFAULT 0,
    filter_caps     INTEGER NOT NULL DEFAULT 1,
    filter_spam     INTEGER NOT NULL DEFAULT 1,
    filter_mentions INTEGER NOT NULL DEFAULT 1,
    caps_percent    INTEGER NOT NULL DEFAULT 70,
    caps_minlen     INTEGER NOT NULL DEFAULT 10,
    spam_messages   INTEGER NOT NULL DEFAULT 5,
    spam_seconds    INTEGER NOT NULL DEFAULT 5,
    mention_limit   INTEGER NOT NULL DEFAULT 5,
    strikes_to_mute INTEGER NOT NULL DEFAULT 3,
    mute_minutes    INTEGER NOT NULL DEFAULT 10,
    log_channel_id  INTEGER,
    exempt_roles    TEXT NOT NULL DEFAULT '',
    exempt_channels TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS raid_config (
    guild_id         INTEGER PRIMARY KEY,
    enabled          INTEGER NOT NULL DEFAULT 1,
    join_count       INTEGER NOT NULL DEFAULT 5,
    join_seconds     INTEGER NOT NULL DEFAULT 60,
    min_account_days INTEGER NOT NULL DEFAULT 7,
    action           TEXT NOT NULL DEFAULT 'alert',
    alert_channel_id INTEGER,
    alert_role_id    INTEGER,
    lockdown         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS automod_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    rule       TEXT NOT NULL,
    content    TEXT,
    created_at TEXT NOT NULL
);
'''

INVITE_RE = re.compile(r'(discord\.(gg|io|me|li)|discordapp\.com/invite|discord\.com/invite)/\S+', re.I)
LINK_RE = re.compile(r'https?://\S+|www\.\S+', re.I)

RULE_NAMES = {
    'invite': 'Приглашение на другой сервер',
    'link': 'Внешняя ссылка',
    'caps': 'Сообщение капсом',
    'spam': 'Флуд сообщениями',
    'mentions': 'Массовые упоминания',
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ids(raw) -> set:
    if not raw:
        return set()
    out = set()
    for part in str(raw).split(','):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


class AutoMod(commands.Cog):
    """Фильтрация чата и защита от массовых заходов."""

    automod_group = app_commands.Group(
        name='automod',
        description='Автомодерация чата',
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )
    raid_group = app_commands.Group(
        name='antiraid',
        description='Защита от рейдов',
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot):
        self.bot = bot
        self._recent = defaultdict(lambda: deque(maxlen=15))   # (guild, user) -> времена сообщений
        self._strikes = defaultdict(lambda: deque(maxlen=10))  # (guild, user) -> времена нарушений
        self._joins = defaultdict(lambda: deque(maxlen=50))    # guild -> времена заходов
        self._raid_until = {}                                   # guild -> timestamp

    # ─── служебное ───────────────────────────────────────────

    async def cog_load(self):
        await self.bot.db.db.executescript(SCHEMA)
        await self.bot.db.db.commit()
        logger.info('AutoMod tables ready.')

    async def _fetchone(self, query, params=()):
        cur = await self.bot.db.db.execute(query, params)
        row = await cur.fetchone()
        await cur.close()
        return row

    async def _exec(self, query, params=()):
        await self.bot.db.db.execute(query, params)
        await self.bot.db.db.commit()

    async def _cfg(self, guild_id: int):
        row = await self._fetchone('SELECT * FROM automod_config WHERE guild_id = ?', (guild_id,))
        if row is None:
            await self._exec('INSERT INTO automod_config (guild_id) VALUES (?)', (guild_id,))
            row = await self._fetchone('SELECT * FROM automod_config WHERE guild_id = ?', (guild_id,))
        return row

    async def _raid_cfg(self, guild_id: int):
        row = await self._fetchone('SELECT * FROM raid_config WHERE guild_id = ?', (guild_id,))
        if row is None:
            await self._exec('INSERT INTO raid_config (guild_id) VALUES (?)', (guild_id,))
            row = await self._fetchone('SELECT * FROM raid_config WHERE guild_id = ?', (guild_id,))
        return row

    async def _log(self, guild: discord.Guild, embed: discord.Embed):
        cfg = await self._cfg(guild.id)
        cid = cfg['log_channel_id']
        if not cid:
            try:
                s = await self.bot.db.get_guild(guild.id)
                cid = s['log_channel_id'] if s else None
            except Exception:
                cid = None
        if not cid:
            return
        ch = guild.get_channel(int(cid))
        if ch is None:
            return
        try:
            await ch.send(embed=embed)
        except Exception as e:
            logger.error(f'AutoMod log failed: {e}')

    # ─── ФИЛЬТРАЦИЯ СООБЩЕНИЙ ───────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return

        cfg = await self._cfg(message.guild.id)
        if not cfg['enabled']:
            return

        # исключения
        if message.channel.id in _ids(cfg['exempt_channels']):
            return
        exempt_roles = _ids(cfg['exempt_roles'])
        if exempt_roles and any(r.id in exempt_roles for r in message.author.roles):
            return
        perms = message.author.guild_permissions
        if perms.administrator or perms.manage_messages:
            return

        rule = self._check(message, cfg)
        if rule is None:
            return

        try:
            await message.delete()
        except Exception:
            pass

        await self._exec(
            'INSERT INTO automod_log (guild_id, user_id, rule, content, created_at) '
            'VALUES (?, ?, ?, ?, ?)',
            (message.guild.id, message.author.id, rule, message.content[:400], _now()),
        )

        # короткое предупреждение в канале, самоудаляется
        try:
            await message.channel.send(
                f'{message.author.mention}, сообщение удалено: **{RULE_NAMES.get(rule, rule)}**.',
                delete_after=6,
            )
        except Exception:
            pass

        # накопление нарушений → автомут
        key = (message.guild.id, message.author.id)
        now = time.time()
        strikes = self._strikes[key]
        strikes.append(now)
        recent_strikes = [t for t in strikes if now - t <= 600]
        muted_note = ''
        if len(recent_strikes) >= int(cfg['strikes_to_mute']):
            minutes = int(cfg['mute_minutes'])
            try:
                await message.author.timeout(
                    datetime.now(timezone.utc) + timedelta(minutes=minutes),
                    reason=f'Автомодерация: {len(recent_strikes)} нарушений подряд',
                )
                muted_note = f'\n\n🔇 Выдан автоматический мут на {minutes} мин.'
                strikes.clear()
            except Exception as e:
                logger.error(f'AutoMod mute failed: {e}')

        e = warn_embed(
            f'**Участник:** {message.author.mention} (`{message.author.id}`)\n'
            f'**Канал:** {message.channel.mention}\n'
            f'**Правило:** {RULE_NAMES.get(rule, rule)}\n'
            f'**Нарушений за 10 мин:** {len(recent_strikes)}\n'
            f'**Текст:** {discord.utils.escape_markdown(message.content[:300]) or "—"}'
            + muted_note,
            '🧹 Автомодерация',
        )
        await self._log(message.guild, e)

    def _check(self, message: discord.Message, cfg):
        content = message.content or ''

        if cfg['filter_invites'] and INVITE_RE.search(content):
            return 'invite'

        if cfg['filter_links'] and LINK_RE.search(content):
            return 'link'

        if cfg['filter_mentions']:
            total = len(message.mentions) + len(message.role_mentions)
            if total >= int(cfg['mention_limit']):
                return 'mentions'

        if cfg['filter_caps'] and len(content) >= int(cfg['caps_minlen']):
            letters = [c for c in content if c.isalpha()]
            if letters:
                upper = sum(1 for c in letters if c.isupper())
                if upper * 100 // len(letters) >= int(cfg['caps_percent']):
                    return 'caps'

        if cfg['filter_spam']:
            key = (message.guild.id, message.author.id)
            now = time.time()
            self._recent[key].append(now)
            window = int(cfg['spam_seconds'])
            recent = [t for t in self._recent[key] if now - t <= window]
            if len(recent) >= int(cfg['spam_messages']):
                self._recent[key].clear()
                return 'spam'

        return None

    # ─── АНТИРЕЙД ─────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        cfg = await self._raid_cfg(guild.id)
        if not cfg['enabled']:
            return

        now = time.time()
        joins = self._joins[guild.id]
        joins.append(now)
        window = int(cfg['join_seconds'])
        recent = [t for t in joins if now - t <= window]

        raid_active = self._raid_until.get(guild.id, 0) > now
        if len(recent) >= int(cfg['join_count']) and not raid_active:
            self._raid_until[guild.id] = now + 600  # режим осады на 10 минут
            raid_active = True
            await self._raid_alert(guild, cfg, len(recent), window)

        if not raid_active:
            return

        # реакция на подозрительные аккаунты во время рейда
        age_days = (datetime.now(timezone.utc) - member.created_at).days
        if age_days >= int(cfg['min_account_days']):
            return

        action = str(cfg['action'])
        done = ''
        try:
            if action == 'kick':
                await member.kick(reason=f'Антирейд: аккаунту {age_days} дн.')
                done = 'выгнан с сервера'
            elif action == 'ban':
                await member.ban(reason=f'Антирейд: аккаунту {age_days} дн.', delete_message_days=1)
                done = 'забанен'
            elif action == 'mute':
                await member.timeout(
                    datetime.now(timezone.utc) + timedelta(hours=1),
                    reason='Антирейд: проверка нового аккаунта',
                )
                done = 'замучен на 1 час до проверки'
            else:
                done = 'помечен, действий не применено'
        except Exception as e:
            done = f'ошибка действия: {e}'

        await self._raid_log(
            guild, cfg,
            warn_embed(
                f'**Участник:** {member.mention} (`{member.id}`)\n'
                f'**Аккаунту:** {age_days} дн. (порог {cfg["min_account_days"]})\n'
                f'**Действие:** {done}',
                '🛡️ Антирейд — новый аккаунт',
            ),
        )

    async def _raid_alert(self, guild, cfg, count, window):
        e = warn_embed(
            f'За **{window} сек.** на сервер зашло **{count}** участников.\n\n'
            f'Режим осады включён на **10 минут**. Новые аккаунты моложе '
            f'**{cfg["min_account_days"]} дн.** обрабатываются по правилу `{cfg["action"]}`.\n\n'
            'Проверьте список участников и при необходимости включите '
            '`/antiraid блокировка`.',
            '🚨 Подозрение на рейд',
        )
        await self._raid_log(guild, cfg, e, ping=True)

    async def _raid_log(self, guild, cfg, embed, ping=False):
        cid = cfg['alert_channel_id']
        ch = guild.get_channel(int(cid)) if cid else None
        if ch is None:
            await self._log(guild, embed)
            return
        content = None
        if ping and cfg['alert_role_id']:
            content = f'<@&{cfg["alert_role_id"]}>'
        try:
            await ch.send(content=content, embed=embed)
        except Exception as e:
            logger.error(f'Raid alert failed: {e}')

    # ─── КОМАНДЫ: AUTOMOD ────────────────────────────────────

    @automod_group.command(name='настройки', description='Показать текущие настройки автомодерации')
    async def am_view(self, interaction: discord.Interaction):
        cfg = await self._cfg(interaction.guild_id)

        def mark(v):
            return '✅ вкл' if v else '⬜ выкл'

        roles = _ids(cfg['exempt_roles'])
        chans = _ids(cfg['exempt_channels'])
        log_id = cfg['log_channel_id']

        e = info('', '🧹 Автомодерация')
        e.add_field(
            name='Состояние',
            value=('Работает ✅' if cfg['enabled'] else 'Отключена ⬜'),
            inline=False,
        )
        e.add_field(
            name='Фильтры',
            value=(
                f'Приглашения: {mark(cfg["filter_invites"])}\n'
                f'Ссылки: {mark(cfg["filter_links"])}\n'
                f'Капс: {mark(cfg["filter_caps"])} (от {cfg["caps_percent"]}% и {cfg["caps_minlen"]} симв.)\n'
                f'Флуд: {mark(cfg["filter_spam"])} ({cfg["spam_messages"]} сообщ. за {cfg["spam_seconds"]} сек.)\n'
                f'Упоминания: {mark(cfg["filter_mentions"])} (от {cfg["mention_limit"]} за раз)'
            ),
            inline=False,
        )
        e.add_field(
            name='Наказание',
            value=f'{cfg["strikes_to_mute"]} нарушения за 10 мин → мут на {cfg["mute_minutes"]} мин.',
            inline=False,
        )
        e.add_field(
            name='Исключения',
            value=(
                ('Роли: ' + ', '.join(f'<@&{r}>' for r in roles) if roles else 'Роли: `нет`') + '\n'
                + ('Каналы: ' + ', '.join(f'<#{c}>' for c in chans) if chans else 'Каналы: `нет`')
                + '\nАдмины и модераторы не проверяются всегда.'
            ),
            inline=False,
        )
        e.add_field(
            name='Лог',
            value=(f'<#{log_id}>' if log_id else '`общий лог бота`'),
            inline=False,
        )
        await interaction.response.send_message(embed=e, ephemeral=True)

    @automod_group.command(name='фильтр', description='Включить или выключить фильтр')
    @app_commands.describe(фильтр='Какой фильтр меняем', включить='Включить или выключить')
    @app_commands.choices(фильтр=[
        app_commands.Choice(name='Вся автомодерация', value='enabled'),
        app_commands.Choice(name='Приглашения на другие серверы', value='filter_invites'),
        app_commands.Choice(name='Внешние ссылки', value='filter_links'),
        app_commands.Choice(name='Капс', value='filter_caps'),
        app_commands.Choice(name='Флуд', value='filter_spam'),
        app_commands.Choice(name='Массовые упоминания', value='filter_mentions'),
    ])
    async def am_toggle(self, interaction: discord.Interaction,
                        фильтр: app_commands.Choice[str], включить: bool):
        await self._cfg(interaction.guild_id)
        await self._exec(
            f'UPDATE automod_config SET {фильтр.value} = ? WHERE guild_id = ?',
            (1 if включить else 0, interaction.guild_id),
        )
        await interaction.response.send_message(
            embed=ok(f'**{фильтр.name}** — {"включён ✅" if включить else "выключен ⬜"}.'),
            ephemeral=True,
        )

    @automod_group.command(name='параметры', description='Пороги срабатывания и лог-канал')
    @app_commands.describe(
        caps_percent='Процент заглавных букв для срабатывания (по умолчанию 70)',
        spam_messages='Сколько сообщений считать флудом',
        spam_seconds='За сколько секунд',
        mention_limit='Сколько упоминаний в одном сообщении запрещено',
        strikes_to_mute='Сколько нарушений до автомута',
        mute_minutes='Длительность автомута в минутах',
        log_channel='Куда писать лог автомодерации',
    )
    async def am_params(
        self,
        interaction: discord.Interaction,
        caps_percent: int = None,
        spam_messages: int = None,
        spam_seconds: int = None,
        mention_limit: int = None,
        strikes_to_mute: int = None,
        mute_minutes: int = None,
        log_channel: discord.TextChannel = None,
    ):
        await self._cfg(interaction.guild_id)
        changes = []
        pairs = [
            ('caps_percent', caps_percent, 10, 100, 'Порог капса'),
            ('spam_messages', spam_messages, 2, 30, 'Сообщений для флуда'),
            ('spam_seconds', spam_seconds, 1, 120, 'Окно флуда (сек)'),
            ('mention_limit', mention_limit, 2, 50, 'Лимит упоминаний'),
            ('strikes_to_mute', strikes_to_mute, 1, 20, 'Нарушений до мута'),
            ('mute_minutes', mute_minutes, 1, 40320, 'Длительность мута (мин)'),
        ]
        for column, value, low, high, label in pairs:
            if value is None:
                continue
            value = max(low, min(high, value))
            await self._exec(
                f'UPDATE automod_config SET {column} = ? WHERE guild_id = ?',
                (value, interaction.guild_id),
            )
            changes.append(f'{label} → **{value}**')

        if log_channel is not None:
            await self._exec('UPDATE automod_config SET log_channel_id = ? WHERE guild_id = ?',
                             (log_channel.id, interaction.guild_id))
            changes.append(f'Лог-канал → {log_channel.mention}')

        if not changes:
            await interaction.response.send_message(
                embed=info('Ничего не изменено — укажите хотя бы один параметр.\n'
                           'Текущие значения — `/automod настройки`.'),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=ok('\n'.join('• ' + c for c in changes)), ephemeral=True
        )

    @automod_group.command(name='исключение', description='Добавить или убрать роль или канал из исключений')
    @app_commands.describe(
        действие='Добавить или убрать',
        role='Роль, которую не проверять',
        channel='Канал, который не проверять',
    )
    @app_commands.choices(действие=[
        app_commands.Choice(name='Добавить', value='add'),
        app_commands.Choice(name='Убрать', value='remove'),
    ])
    async def am_exempt(self, interaction: discord.Interaction,
                        действие: app_commands.Choice[str],
                        role: discord.Role = None, channel: discord.TextChannel = None):
        if role is None and channel is None:
            await interaction.response.send_message(
                embed=err('Укажите роль или канал.'), ephemeral=True
            )
            return

        cfg = await self._cfg(interaction.guild_id)
        notes = []

        if role is not None:
            roles = _ids(cfg['exempt_roles'])
            if действие.value == 'add':
                roles.add(role.id)
                notes.append(f'Роль {role.mention} больше не проверяется.')
            else:
                roles.discard(role.id)
                notes.append(f'Роль {role.mention} снова проверяется.')
            await self._exec('UPDATE automod_config SET exempt_roles = ? WHERE guild_id = ?',
                             (','.join(str(x) for x in sorted(roles)), interaction.guild_id))

        if channel is not None:
            chans = _ids(cfg['exempt_channels'])
            if действие.value == 'add':
                chans.add(channel.id)
                notes.append(f'Канал {channel.mention} больше не проверяется.')
            else:
                chans.discard(channel.id)
                notes.append(f'Канал {channel.mention} снова проверяется.')
            await self._exec('UPDATE automod_config SET exempt_channels = ? WHERE guild_id = ?',
                             (','.join(str(x) for x in sorted(chans)), interaction.guild_id))

        await interaction.response.send_message(embed=ok('\n'.join(notes)), ephemeral=True)

    # ─── КОМАНДЫ: ANTIRAID ───────────────────────────────────

    @raid_group.command(name='настройки', description='Показать настройки антирейда')
    async def raid_view(self, interaction: discord.Interaction):
        cfg = await self._raid_cfg(interaction.guild_id)
        actions = {
            'alert': 'только уведомить',
            'mute': 'выдать мут на час',
            'kick': 'выгнать',
            'ban': 'забанить',
        }
        alert_id = cfg['alert_channel_id']
        role_id = cfg['alert_role_id']
        active = self._raid_until.get(interaction.guild_id, 0) > time.time()

        e = info('', '🛡️ Антирейд')
        e.add_field(
            name='Состояние',
            value=('Работает ✅' if cfg['enabled'] else 'Отключён ⬜')
            + ('\n⚠️ **Сейчас активен режим осады**' if active else '')
            + ('\n🔒 **Блокировка включена вручную**' if cfg['lockdown'] else ''),
            inline=False,
        )
        e.add_field(
            name='Порог срабатывания',
            value=f'**{cfg["join_count"]}** заходов за **{cfg["join_seconds"]}** сек.',
            inline=False,
        )
        e.add_field(
            name='Новые аккаунты',
            value=f'Моложе **{cfg["min_account_days"]}** дн. → {actions.get(str(cfg["action"]), cfg["action"])}',
            inline=False,
        )
        e.add_field(
            name='Куда сообщать',
            value=(f'{f"<#{alert_id}>" if alert_id else "`общий лог бота`"}\n'
                   f'Пинг: {f"<@&{role_id}>" if role_id else "`нет`"}'),
            inline=False,
        )
        await interaction.response.send_message(embed=e, ephemeral=True)

    @raid_group.command(name='настроить', description='Изменить пороги и реакцию антирейда')
    @app_commands.describe(
        включён='Включить или выключить защиту',
        join_count='Сколько заходов считать рейдом',
        join_seconds='За сколько секунд',
        min_account_days='Аккаунты моложе скольких дней считать подозрительными',
        действие='Что делать с ними во время рейда',
        alert_channel='Куда присылать тревогу',
        alert_role='Кого пинговать при тревоге',
    )
    @app_commands.choices(действие=[
        app_commands.Choice(name='Только уведомить', value='alert'),
        app_commands.Choice(name='Выдать мут на час', value='mute'),
        app_commands.Choice(name='Выгнать', value='kick'),
        app_commands.Choice(name='Забанить', value='ban'),
    ])
    async def raid_setup(
        self,
        interaction: discord.Interaction,
        включён: bool = None,
        join_count: int = None,
        join_seconds: int = None,
        min_account_days: int = None,
        действие: app_commands.Choice[str] = None,
        alert_channel: discord.TextChannel = None,
        alert_role: discord.Role = None,
    ):
        await self._raid_cfg(interaction.guild_id)
        changes = []

        if включён is not None:
            await self._exec('UPDATE raid_config SET enabled = ? WHERE guild_id = ?',
                             (1 if включён else 0, interaction.guild_id))
            changes.append('Защита → ' + ('включена ✅' if включён else 'выключена ⬜'))
        if join_count is not None:
            v = max(2, min(50, join_count))
            await self._exec('UPDATE raid_config SET join_count = ? WHERE guild_id = ?',
                             (v, interaction.guild_id))
            changes.append(f'Порог заходов → **{v}**')
        if join_seconds is not None:
            v = max(10, min(600, join_seconds))
            await self._exec('UPDATE raid_config SET join_seconds = ? WHERE guild_id = ?',
                             (v, interaction.guild_id))
            changes.append(f'Окно → **{v}** сек.')
        if min_account_days is not None:
            v = max(0, min(365, min_account_days))
            await self._exec('UPDATE raid_config SET min_account_days = ? WHERE guild_id = ?',
                             (v, interaction.guild_id))
            changes.append(f'Молодой аккаунт → меньше **{v}** дн.')
        if действие is not None:
            await self._exec('UPDATE raid_config SET action = ? WHERE guild_id = ?',
                             (действие.value, interaction.guild_id))
            changes.append(f'Действие → **{действие.name}**')
        if alert_channel is not None:
            await self._exec('UPDATE raid_config SET alert_channel_id = ? WHERE guild_id = ?',
                             (alert_channel.id, interaction.guild_id))
            changes.append(f'Канал тревоги → {alert_channel.mention}')
        if alert_role is not None:
            await self._exec('UPDATE raid_config SET alert_role_id = ? WHERE guild_id = ?',
                             (alert_role.id, interaction.guild_id))
            changes.append(f'Пинг → {alert_role.mention}')

        if not changes:
            await interaction.response.send_message(
                embed=info('Ничего не изменено. Текущие значения — `/antiraid настройки`.'),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=ok('\n'.join('• ' + c for c in changes)), ephemeral=True
        )

    @raid_group.command(name='блокировка', description='Ручной режим осады — жёсткая проверка новичков')
    @app_commands.describe(включить='Включить или снять блокировку')
    async def raid_lockdown(self, interaction: discord.Interaction, включить: bool):
        await self._raid_cfg(interaction.guild_id)
        await self._exec('UPDATE raid_config SET lockdown = ? WHERE guild_id = ?',
                         (1 if включить else 0, interaction.guild_id))
        if включить:
            self._raid_until[interaction.guild_id] = time.time() + 3600
            text = ('Режим осады включён на **1 час**.\n'
                    'Новые аккаунты обрабатываются по выбранному правилу сразу при заходе.')
        else:
            self._raid_until.pop(interaction.guild_id, None)
            text = 'Блокировка снята, сервер работает в обычном режиме.'

        await interaction.response.send_message(embed=ok(text), ephemeral=True)
        cfg = await self._raid_cfg(interaction.guild_id)
        await self._raid_log(
            interaction.guild, cfg,
            warn_embed(f'{text}\n\n**Модератор:** {interaction.user.mention}',
                       '🔒 Блокировка сервера'),
        )


async def setup(bot):
    await bot.add_cog(AutoMod(bot))
