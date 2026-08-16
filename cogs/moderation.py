"""
cogs/moderation.py — система модерации Fluffy Vanilla

  /warn выдать        — выдать предупреждение (с автонаказанием)
  /warn список        — активные предупреждения игрока
  /warn снять        — снять одно предупреждение по номеру
  /warn очистить     — снять все предупреждения игрока
  /mute, /unmute      — временный мут через тайм-аут Discord
  /ban, /unban        — бан, в том числе временный с автоснятием
  /kick               — кик с уведомлением в личку
  /history            — вся история наказаний игрока
  /modconfig          — настройка порогов и лог-канала
"""
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.embeds import ok, err, warn as warn_embed, info, PINK, ERROR, WARNING, SUCCESS, CLOSED
from utils.emojis import ADMIN_PINK, MEMBER, NAUGHTY, VERIFIED

logger = logging.getLogger('FluFFy.Moderation')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS warns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    reason      TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    removed_by  INTEGER,
    removed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_warns_user ON warns (guild_id, user_id, active);

CREATE TABLE IF NOT EXISTS punishments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    type        TEXT NOT NULL,
    reason      TEXT,
    created_at  TEXT NOT NULL,
    expires_at  TEXT,
    active      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_punish_active ON punishments (active, expires_at);

CREATE TABLE IF NOT EXISTS mod_config (
    guild_id        INTEGER PRIMARY KEY,
    log_channel_id  INTEGER,
    warn_mute_at    INTEGER NOT NULL DEFAULT 3,
    warn_ban_at     INTEGER NOT NULL DEFAULT 5,
    mute_minutes    INTEGER NOT NULL DEFAULT 60,
    ban_days        INTEGER NOT NULL DEFAULT 7
);
'''

MAX_TIMEOUT_DAYS = 28  # ограничение Discord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt_dt(value) -> str:
    if not value:
        return '—'
    try:
        dt = datetime.fromisoformat(str(value))
        return f'<t:{int(dt.timestamp())}:f>'
    except Exception:
        return str(value)


def _fmt_left(value) -> str:
    if not value:
        return 'навсегда'
    try:
        dt = datetime.fromisoformat(str(value))
        return f'<t:{int(dt.timestamp())}:R>'
    except Exception:
        return str(value)


class Moderation(commands.Cog):
    """Предупреждения, муты, баны и история наказаний."""

    warn_group = app_commands.Group(
        name='warn',
        description='Предупреждения игроков',
        guild_only=True,
        default_permissions=discord.Permissions(moderate_members=True),
    )

    def __init__(self, bot):
        self.bot = bot

    # ─── служебное ───────────────────────────────────────────

    async def cog_load(self):
        await self.bot.db.db.executescript(SCHEMA)
        await self.bot.db.db.commit()
        self._expiry_worker.start()
        logger.info('Moderation tables ready.')

    async def cog_unload(self):
        self._expiry_worker.cancel()

    async def _fetchone(self, query, params=()):
        cur = await self.bot.db.db.execute(query, params)
        row = await cur.fetchone()
        await cur.close()
        return row

    async def _fetchall(self, query, params=()):
        cur = await self.bot.db.db.execute(query, params)
        rows = await cur.fetchall()
        await cur.close()
        return rows

    async def _exec(self, query, params=()):
        cur = await self.bot.db.db.execute(query, params)
        await self.bot.db.db.commit()
        last_id = cur.lastrowid
        await cur.close()
        return last_id

    async def _config(self, guild_id: int):
        row = await self._fetchone('SELECT * FROM mod_config WHERE guild_id = ?', (guild_id,))
        if row is None:
            await self._exec('INSERT INTO mod_config (guild_id) VALUES (?)', (guild_id,))
            row = await self._fetchone('SELECT * FROM mod_config WHERE guild_id = ?', (guild_id,))
        return row

    async def _log_channel(self, guild: discord.Guild):
        cfg = await self._config(guild.id)
        cid = cfg['log_channel_id'] if cfg else None
        if not cid:
            try:
                settings = await self.bot.db.get_guild(guild.id)
                cid = settings['log_channel_id'] if settings else None
            except Exception:
                cid = None
        return guild.get_channel(int(cid)) if cid else None

    async def _log(self, guild: discord.Guild, embed: discord.Embed):
        channel = await self._log_channel(guild)
        if channel is None:
            return
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            logger.warning(f'No permission to write moderation log in #{channel}')
        except Exception as e:
            logger.error(f'Moderation log failed: {e}')

    async def _dm(self, user: discord.abc.User, embed: discord.Embed) -> bool:
        try:
            await user.send(embed=embed)
            return True
        except Exception:
            return False

    def _can_act(self, actor: discord.Member, target: discord.Member) -> str:
        """Возвращает текст ошибки или пустую строку, если действие разрешено."""
        if target.id == actor.id:
            return 'Нельзя применить это к самому себе.'
        if target.bot:
            return 'К ботам наказания не применяются.'
        if target.id == target.guild.owner_id:
            return 'Нельзя наказать владельца сервера.'
        if actor.id != actor.guild.owner_id and target.top_role >= actor.top_role:
            return 'У этого участника роль выше или равна вашей.'
        me = target.guild.me
        if target.top_role >= me.top_role:
            return 'Роль участника выше роли бота — я не могу его наказать.'
        return ''

    # ─── автоснятие временных наказаний ──────────────────────────

    @tasks.loop(minutes=1)
    async def _expiry_worker(self):
        try:
            now = _now()
            rows = await self._fetchall(
                "SELECT * FROM punishments WHERE active = 1 AND type = 'ban' "
                'AND expires_at IS NOT NULL AND expires_at <= ?',
                (now,),
            )
            for row in rows:
                guild = self.bot.get_guild(int(row['guild_id']))
                if guild is None:
                    continue
                try:
                    await guild.unban(
                        discord.Object(id=int(row['user_id'])),
                        reason='Срок временного бана истёк',
                    )
                    logger.info(f'Auto-unbanned {row["user_id"]} in {guild.id}')
                except discord.NotFound:
                    pass
                except Exception as e:
                    logger.error(f'Auto-unban failed: {e}')
                await self._exec('UPDATE punishments SET active = 0 WHERE id = ?', (row['id'],))
                e = info(
                    f'<@{row["user_id"]}> — срок бана истёк, бан снят автоматически.',
                    '🔓 Авторазбан',
                )
                await self._log(guild, e)
        except Exception as e:
            logger.error(f'Expiry worker error: {e}')

    @_expiry_worker.before_loop
    async def _before_expiry(self):
        await self.bot.wait_until_ready()

    # ─── ПРЕДУПРЕЖДЕНИЯ ──────────────────────────────────

    @warn_group.command(name='выдать', description='Выдать предупреждение участнику')
    @app_commands.describe(member='Кому выдать', reason='Причина предупреждения')
    async def warn_add(self, interaction: discord.Interaction, member: discord.Member, reason: str):
        problem = self._can_act(interaction.user, member)
        if problem:
            await interaction.response.send_message(embed=err(problem), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        await self._exec(
            'INSERT INTO warns (guild_id, user_id, moderator_id, reason, created_at) '
            'VALUES (?, ?, ?, ?, ?)',
            (interaction.guild_id, member.id, interaction.user.id, reason[:500], _now()),
        )
        row = await self._fetchone(
            'SELECT COUNT(*) AS c FROM warns WHERE guild_id = ? AND user_id = ? AND active = 1',
            (interaction.guild_id, member.id),
        )
        count = int(row['c'])
        cfg = await self._config(interaction.guild_id)
        mute_at = int(cfg['warn_mute_at'])
        ban_at = int(cfg['warn_ban_at'])

        # уведомление игроку
        dm = warn_embed(
            f'Вы получили предупреждение на сервере **{0}**.\n\n'
            '**Причина:** {1}\n'
            '**Всего предупреждений:** {2} из {3}'.format(
                interaction.guild.name, reason, count, ban_at
            ),
            '⚠️ Предупреждение',
        )
        delivered = await self._dm(member, dm)

        # автонаказание
        action_note = ''
        if count >= ban_at:
            days = int(cfg['ban_days'])
            done = await self._apply_ban(interaction.guild, member, interaction.user,
                                         f'Накоплено {count} предупреждений', days)
            action_note = f'\n\n🔨 Автоматически выдан бан на {days} дн.' if done else ''
        elif count >= mute_at:
            minutes = int(cfg['mute_minutes'])
            done = await self._apply_mute(interaction.guild, member, interaction.user,
                                          f'Накоплено {count} предупреждений', minutes)
            action_note = f'\n\n🔇 Автоматически выдан мут на {minutes} мин.' if done else ''

        await interaction.followup.send(
            embed=ok(
                f'{member.mention} получил предупреждение.\n'
                f'**Причина:** {reason}\n'
                f'**Всего активных:** {count}'
                + ('' if delivered else '\n\n⚠️ Личные сообщения закрыты — уведомление не дошло.')
                + action_note
            ),
            ephemeral=True,
        )

        log = warn_embed(
            f'**Участник:** {member.mention} (`{member.id}`)\n'
            f'**Модератор:** {interaction.user.mention}\n'
            f'**Причина:** {reason}\n'
            f'**Активных предупреждений:** {count}' + action_note,
            '⚠️ Выдано предупреждение',
        )
        log.set_thumbnail(url=member.display_avatar.url)
        await self._log(interaction.guild, log)

    @warn_group.command(name='список', description='Показать предупреждения участника')
    @app_commands.describe(member='Чьи предупреждения показать')
    async def warn_list(self, interaction: discord.Interaction, member: discord.Member):
        rows = await self._fetchall(
            'SELECT * FROM warns WHERE guild_id = ? AND user_id = ? AND active = 1 '
            'ORDER BY id DESC LIMIT 25',
            (interaction.guild_id, member.id),
        )
        if not rows:
            await interaction.response.send_message(
                embed=info(f'У {member.mention} нет активных предупреждений.', '✨ Чисто'),
                ephemeral=True,
            )
            return

        lines = []
        for r in rows:
            lines.append(
                f'**#{r["id"]}** — {r["reason"]}\n'
                f'    от <@{r["moderator_id"]}> • {_fmt_dt(r["created_at"])}'
            )
        e = warn_embed('\n\n'.join(lines), f'Предупреждения — {member.display_name}')
        e.set_thumbnail(url=member.display_avatar.url)
        e.set_footer(text=f'Всего активных: {len(rows)} • Fluffy Vanilla')
        await interaction.response.send_message(embed=e, ephemeral=True)

    @warn_group.command(name='снять', description='Снять одно предупреждение по номеру')
    @app_commands.describe(warn_id='Номер предупреждения из списка')
    async def warn_remove(self, interaction: discord.Interaction, warn_id: int):
        row = await self._fetchone(
            'SELECT * FROM warns WHERE id = ? AND guild_id = ?', (warn_id, interaction.guild_id)
        )
        if row is None:
            await interaction.response.send_message(
                embed=err(f'Предупреждение **#{warn_id}** не найдено.'), ephemeral=True
            )
            return
        if not row['active']:
            await interaction.response.send_message(
                embed=err(f'Предупреждение **#{warn_id}** уже снято.'), ephemeral=True
            )
            return

        await self._exec(
            'UPDATE warns SET active = 0, removed_by = ?, removed_at = ? WHERE id = ?',
            (interaction.user.id, _now(), warn_id),
        )
        await interaction.response.send_message(
            embed=ok(f'Предупреждение **#{warn_id}** снято с <@{row["user_id"]}>.'),
            ephemeral=True,
        )
        await self._log(
            interaction.guild,
            info(
                f'**Участник:** <@{row["user_id"]}>\n'
                f'**Модератор:** {interaction.user.mention}\n'
                f'**Предупреждение:** #{warn_id} — {row["reason"]}',
                '♻️ Предупреждение снято',
            ),
        )

    @warn_group.command(name='очистить', description='Снять все предупреждения участника')
    @app_commands.describe(member='У кого снять все предупреждения')
    @app_commands.default_permissions(manage_guild=True)
    async def warn_clear(self, interaction: discord.Interaction, member: discord.Member):
        row = await self._fetchone(
            'SELECT COUNT(*) AS c FROM warns WHERE guild_id = ? AND user_id = ? AND active = 1',
            (interaction.guild_id, member.id),
        )
        count = int(row['c'])
        if not count:
            await interaction.response.send_message(
                embed=info(f'У {member.mention} и так нет предупреждений.'), ephemeral=True
            )
            return
        await self._exec(
            'UPDATE warns SET active = 0, removed_by = ?, removed_at = ? '
            'WHERE guild_id = ? AND user_id = ? AND active = 1',
            (interaction.user.id, _now(), interaction.guild_id, member.id),
        )
        await interaction.response.send_message(
            embed=ok(f'Снято предупреждений: **{count}** у {member.mention}.'), ephemeral=True
        )
        await self._log(
            interaction.guild,
            info(
                f'**Участник:** {member.mention}\n'
                f'**Модератор:** {interaction.user.mention}\n'
                f'**Снято:** {count}',
                '♻️ Все предупреждения сняты',
            ),
        )

    # ─── МУТ ──────────────────────────────────────────────

    async def _apply_mute(self, guild, member, moderator, reason, minutes) -> bool:
        until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        try:
            await member.timeout(until, reason=f'{reason} ({moderator})')
        except Exception as e:
            logger.error(f'Mute failed: {e}')
            return False
        await self._exec(
            'INSERT INTO punishments (guild_id, user_id, moderator_id, type, reason, '
            'created_at, expires_at, active) VALUES (?, ?, ?, ?, ?, ?, ?, 1)',
            (guild.id, member.id, moderator.id, 'mute', reason, _now(), until.isoformat()),
        )
        await self._dm(
            member,
            warn_embed(
                f'Вы получили мут на сервере **{guild.name}**.\n\n'
                f'**Причина:** {reason}\n'
                f'**Длительность:** {minutes} мин.\n'
                f'**Истекает:** <t:{int(until.timestamp())}:R>',
                '🔇 Мут',
            ),
        )
        return True

    @app_commands.command(name='mute', description='Выдать участнику временный мут')
    @app_commands.describe(
        member='Кого замутить',
        minutes='На сколько минут (максимум 40320 = 28 дней)',
        reason='Причина',
    )
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def mute(self, interaction: discord.Interaction, member: discord.Member,
                   minutes: int, reason: str = 'Причина не указана'):
        problem = self._can_act(interaction.user, member)
        if problem:
            await interaction.response.send_message(embed=err(problem), ephemeral=True)
            return
        if minutes < 1 or minutes > MAX_TIMEOUT_DAYS * 24 * 60:
            await interaction.response.send_message(
                embed=err(f'Длительность — от 1 минуты до {MAX_TIMEOUT_DAYS * 24 * 60} минут ({MAX_TIMEOUT_DAYS} дней).'),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        done = await self._apply_mute(interaction.guild, member, interaction.user, reason, minutes)
        if not done:
            await interaction.followup.send(
                embed=err('Не удалось выдать мут. Проверьте права бота и иерархию ролей.'),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embed=ok(f'{member.mention} замучен на **{minutes}** мин.\n**Причина:** {reason}'),
            ephemeral=True,
        )
        log = warn_embed(
            f'**Участник:** {member.mention} (`{member.id}`)\n'
            f'**Модератор:** {interaction.user.mention}\n'
            f'**Длительность:** {minutes} мин.\n'
            f'**Причина:** {reason}',
            '🔇 Выдан мут',
        )
        log.set_thumbnail(url=member.display_avatar.url)
        await self._log(interaction.guild, log)

    @app_commands.command(name='unmute', description='Снять мут с участника')
    @app_commands.describe(member='С кого снять мут')
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def unmute(self, interaction: discord.Interaction, member: discord.Member):
        try:
            await member.timeout(None, reason=f'Мут снят ({interaction.user})')
        except Exception as e:
            await interaction.response.send_message(
                embed=err(f'Не удалось снять мут: `{e}`'), ephemeral=True
            )
            return
        await self._exec(
            "UPDATE punishments SET active = 0 WHERE guild_id = ? AND user_id = ? "
            "AND type = 'mute' AND active = 1",
            (interaction.guild_id, member.id),
        )
        await interaction.response.send_message(
            embed=ok(f'С {member.mention} снят мут.'), ephemeral=True
        )
        await self._log(
            interaction.guild,
            info(
                f'**Участник:** {member.mention}\n'
                f'**Модератор:** {interaction.user.mention}',
                '🔊 Мут снят',
            ),
        )

    # ─── БАН ──────────────────────────────────────────────

    async def _apply_ban(self, guild, member, moderator, reason, days) -> bool:
        expires = None
        if days and days > 0:
            expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

        await self._dm(
            member,
            warn_embed(
                f'Вы забанены на сервере **{guild.name}**.\n\n'
                f'**Причина:** {reason}\n'
                + (f'**Срок:** {days} дн.\n' if days else '**Срок:** навсегда\n')
                + ('**Авторазбан:** ' + _fmt_left(expires) if expires else ''),
                '🔨 Бан',
            ),
        )
        try:
            await guild.ban(member, reason=f'{reason} ({moderator})', delete_message_days=0)
        except Exception as e:
            logger.error(f'Ban failed: {e}')
            return False
        await self._exec(
            'INSERT INTO punishments (guild_id, user_id, moderator_id, type, reason, '
            'created_at, expires_at, active) VALUES (?, ?, ?, ?, ?, ?, ?, 1)',
            (guild.id, member.id, moderator.id, 'ban', reason, _now(), expires),
        )
        return True

    @app_commands.command(name='ban', description='Забанить участника (можно временно)')
    @app_commands.describe(
        member='Кого забанить',
        days='На сколько дней (0 или пусто — навсегда)',
        reason='Причина',
        clean_days='Удалить сообщения за N дней (0-7)',
    )
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    async def ban(self, interaction: discord.Interaction, member: discord.Member,
                  days: int = 0, reason: str = 'Причина не указана', clean_days: int = 0):
        problem = self._can_act(interaction.user, member)
        if problem:
            await interaction.response.send_message(embed=err(problem), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        expires = None
        if days > 0:
            expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

        await self._dm(
            member,
            warn_embed(
                f'Вы забанены на сервере **{interaction.guild.name}**.\n\n'
                f'**Причина:** {reason}\n'
                + (f'**Срок:** {days} дн. • авторазбан {_fmt_left(expires)}'
                   if days > 0 else '**Срок:** навсегда'),
                '🔨 Бан',
            ),
        )
        try:
            await interaction.guild.ban(
                member,
                reason=f'{reason} ({interaction.user})',
                delete_message_days=max(0, min(7, clean_days)),
            )
        except Exception as e:
            await interaction.followup.send(embed=err(f'Не удалось забанить: `{e}`'), ephemeral=True)
            return

        await self._exec(
            'INSERT INTO punishments (guild_id, user_id, moderator_id, type, reason, '
            'created_at, expires_at, active) VALUES (?, ?, ?, ?, ?, ?, ?, 1)',
            (interaction.guild_id, member.id, interaction.user.id, 'ban', reason, _now(), expires),
        )
        await interaction.followup.send(
            embed=ok(
                f'{member.mention} забанен.\n**Причина:** {reason}\n'
                + (f'**Срок:** {days} дн. (авторазбан {_fmt_left(expires)})'
                   if days > 0 else '**Срок:** навсегда')
            ),
            ephemeral=True,
        )
        log = warn_embed(
            f'**Участник:** {member.mention} (`{member.id}`)\n'
            f'**Модератор:** {interaction.user.mention}\n'
            f'**Причина:** {reason}\n'
            + (f'**Срок:** {days} дн. • авторазбан {_fmt_left(expires)}'
               if days > 0 else '**Срок:** навсегда'),
            '🔨 Выдан бан',
        )
        log.set_thumbnail(url=member.display_avatar.url)
        await self._log(interaction.guild, log)

    @app_commands.command(name='unban', description='Разбанить пользователя по ID')
    @app_commands.describe(user_id='ID пользователя', reason='Причина разбана')
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    async def unban(self, interaction: discord.Interaction, user_id: str,
                    reason: str = 'Разбан модератором'):
        if not user_id.isdigit():
            await interaction.response.send_message(
                embed=err('ID должен состоять только из цифр.'), ephemeral=True
            )
            return
        uid = int(user_id)
        try:
            await interaction.guild.unban(discord.Object(id=uid), reason=f'{reason} ({interaction.user})')
        except discord.NotFound:
            await interaction.response.send_message(
                embed=err('Этот пользователь не забанен.'), ephemeral=True
            )
            return
        except Exception as e:
            await interaction.response.send_message(
                embed=err(f'Не удалось разбанить: `{e}`'), ephemeral=True
            )
            return

        await self._exec(
            "UPDATE punishments SET active = 0 WHERE guild_id = ? AND user_id = ? "
            "AND type = 'ban' AND active = 1",
            (interaction.guild_id, uid),
        )
        await interaction.response.send_message(
            embed=ok(f'Пользователь <@{uid}> разбанен.'), ephemeral=True
        )
        await self._log(
            interaction.guild,
            info(
                f'**Пользователь:** <@{uid}> (`{uid}`)\n'
                f'**Модератор:** {interaction.user.mention}\n'
                f'**Причина:** {reason}',
                '🔓 Разбан',
            ),
        )

    @app_commands.command(name='kick', description='Выгнать участника с сервера')
    @app_commands.describe(member='Кого выгнать', reason='Причина')
    @app_commands.default_permissions(kick_members=True)
    @app_commands.guild_only()
    async def kick(self, interaction: discord.Interaction, member: discord.Member,
                   reason: str = 'Причина не указана'):
        problem = self._can_act(interaction.user, member)
        if problem:
            await interaction.response.send_message(embed=err(problem), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await self._dm(
            member,
            warn_embed(
                f'Вас выгнали с сервера **{interaction.guild.name}**.\n\n'
                f'**Причина:** {reason}\n\n'
                'Вы можете вернуться по новому приглашению.',
                '👢 Кик',
            ),
        )
        try:
            await member.kick(reason=f'{reason} ({interaction.user})')
        except Exception as e:
            await interaction.followup.send(embed=err(f'Не удалось выгнать: `{e}`'), ephemeral=True)
            return

        await self._exec(
            'INSERT INTO punishments (guild_id, user_id, moderator_id, type, reason, '
            'created_at, expires_at, active) VALUES (?, ?, ?, ?, ?, ?, NULL, 0)',
            (interaction.guild_id, member.id, interaction.user.id, 'kick', reason, _now()),
        )
        await interaction.followup.send(
            embed=ok(f'{member.mention} выгнан с сервера.\n**Причина:** {reason}'),
            ephemeral=True,
        )
        await self._log(
            interaction.guild,
            warn_embed(
                f'**Участник:** {member.mention} (`{member.id}`)\n'
                f'**Модератор:** {interaction.user.mention}\n'
                f'**Причина:** {reason}',
                '👢 Участник выгнан',
            ),
        )

    # ─── ИСТОРИЯ И НАСТРОЙКИ ──────────────────────────────

    @app_commands.command(name='history', description='История наказаний участника')
    @app_commands.describe(member='Чью историю показать')
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def history(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)

        warns_total = await self._fetchone(
            'SELECT COUNT(*) AS c FROM warns WHERE guild_id = ? AND user_id = ?',
            (interaction.guild_id, member.id),
        )
        warns_active = await self._fetchone(
            'SELECT COUNT(*) AS c FROM warns WHERE guild_id = ? AND user_id = ? AND active = 1',
            (interaction.guild_id, member.id),
        )
        punishments = await self._fetchall(
            'SELECT * FROM punishments WHERE guild_id = ? AND user_id = ? '
            'ORDER BY id DESC LIMIT 15',
            (interaction.guild_id, member.id),
        )

        e = info(f'{MEMBER} История участника', f'📑 История — {member.display_name}')
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(
            name='Предупреждения',
            value=f'Активных: **{int(warns_active["c"])}** • всего: **{int(warns_total["c"])}**',
            inline=False,
        )
        e.add_field(
            name='На сервере с',
            value=f'<t:{int(member.joined_at.timestamp())}:D>' if member.joined_at else '—',
            inline=True,
        )
        e.add_field(
            name='Аккаунт создан',
            value=f'<t:{int(member.created_at.timestamp())}:D>',
            inline=True,
        )

        names = {'mute': '🔇 Мут', 'ban': '🔨 Бан', 'kick': '👢 Кик'}
        if punishments:
            lines = []
            for r in punishments:
                status = 'активно' if r['active'] else 'снято'
                lines.append(
                    f'{names.get(r["type"], r["type"])} • {status}\n'
                    f'    {r["reason"] or "—"}\n'
                    f'    <@{r["moderator_id"]}> • {_fmt_dt(r["created_at"])}'
                )
            e.add_field(name='Наказания', value='\n\n'.join(lines)[:1024], inline=False)
        else:
            e.add_field(name='Наказания', value='Нет — чистая история ✨', inline=False)

        await interaction.followup.send(embed=e, ephemeral=True)

    @app_commands.command(name='modconfig', description='Настройка системы модерации')
    @app_commands.describe(
        log_channel='Канал для логов модерации',
        warn_mute_at='При скольких варнах выдавать мут',
        warn_ban_at='При скольких варнах выдавать бан',
        mute_minutes='Длительность автомута в минутах',
        ban_days='Длительность автобана в днях',
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def modconfig(
        self,
        interaction: discord.Interaction,
        log_channel: discord.TextChannel = None,
        warn_mute_at: int = None,
        warn_ban_at: int = None,
        mute_minutes: int = None,
        ban_days: int = None,
    ):
        cfg = await self._config(interaction.guild_id)
        updates = []
        if log_channel is not None:
            await self._exec('UPDATE mod_config SET log_channel_id = ? WHERE guild_id = ?',
                             (log_channel.id, interaction.guild_id))
            updates.append(f'Лог-канал → {log_channel.mention}')
        if warn_mute_at is not None:
            await self._exec('UPDATE mod_config SET warn_mute_at = ? WHERE guild_id = ?',
                             (max(1, warn_mute_at), interaction.guild_id))
            updates.append(f'Мут при варнах → {max(1, warn_mute_at)}')
        if warn_ban_at is not None:
            await self._exec('UPDATE mod_config SET warn_ban_at = ? WHERE guild_id = ?',
                             (max(1, warn_ban_at), interaction.guild_id))
            updates.append(f'Бан при варнах → {max(1, warn_ban_at)}')
        if mute_minutes is not None:
            await self._exec('UPDATE mod_config SET mute_minutes = ? WHERE guild_id = ?',
                             (max(1, mute_minutes), interaction.guild_id))
            updates.append(f'Длительность автомута → {max(1, mute_minutes)} мин.')
        if ban_days is not None:
            await self._exec('UPDATE mod_config SET ban_days = ? WHERE guild_id = ?',
                             (max(1, ban_days), interaction.guild_id))
            updates.append(f'Длительность автобана → {max(1, ban_days)} дн.')

        cfg = await self._config(interaction.guild_id)
        log_id = cfg['log_channel_id']
        e = info(
            ('**Изменено:**\n' + '\n'.join('• ' + u for u in updates) + '\n\n' if updates else '')
            + '**Текущие настройки:**\n'
            + f'• Лог-канал: ' + (f'<#{log_id}>' if log_id else '`общий лог бота`') + '\n'
            + f'• Мут при **{cfg["warn_mute_at"]}** варнах на **{cfg["mute_minutes"]}** мин.\n'
            + f'• Бан при **{cfg["warn_ban_at"]}** варнах на **{cfg["ban_days"]}** дн.',
            f'⚙️ Модерация {ADMIN_PINK}',
        )
        await interaction.response.send_message(embed=e, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Moderation(bot))
