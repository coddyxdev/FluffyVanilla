"""
cogs/ticket_extras.py — доработки системы тикетов

  • Кнопка «Взять тикет» и команда /take — ответственный модератор
  • Автозакрытие тикетов без активности с предупреждением
  • Оценка поддержки в личных сообщениях после закрытия
  • Самоочистка записей об удалённых каналах
"""
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.embeds import ok, err, warn as warn_embed, info
from utils.emojis import STAR, ADMIN_PURPLE, HEART, TICKET

logger = logging.getLogger('FluFFy.TicketExtras')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS ticket_extra_config (
    guild_id       INTEGER PRIMARY KEY,
    autoclose      INTEGER NOT NULL DEFAULT 1,
    warn_hours     INTEGER NOT NULL DEFAULT 48,
    close_hours    INTEGER NOT NULL DEFAULT 72,
    rating_enabled INTEGER NOT NULL DEFAULT 1,
    selfheal       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS ticket_extra_state (
    channel_id      INTEGER PRIMARY KEY,
    guild_id        INTEGER,
    warned_at       TEXT,
    claim_posted    INTEGER NOT NULL DEFAULT 0,
    rating_prompted INTEGER NOT NULL DEFAULT 0,
    rating_msg_id   INTEGER,
    user_id         INTEGER,
    staff_id        INTEGER,
    ticket_number   INTEGER
);

CREATE TABLE IF NOT EXISTS ticket_ratings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id      INTEGER NOT NULL,
    channel_id    INTEGER,
    ticket_number INTEGER,
    user_id       INTEGER NOT NULL,
    staff_id      INTEGER,
    score         INTEGER NOT NULL,
    comment       TEXT,
    created_at    TEXT NOT NULL
);
'''


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace(' ', 'T'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# ─── VIEWS ──────────────────────────────────────────────────

class RatingCommentModal(discord.ui.Modal, title='Оценка поддержки'):
    comment = discord.ui.TextInput(
        label='Комментарий (необязательно)',
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
        placeholder='Что понравилось или что стоит улучшить?',
    )

    def __init__(self, cog, score: int, state):
        super().__init__()
        self.cog = cog
        self.score = score
        self.state = state

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.save_rating(interaction, self.score, self.state, str(self.comment) or None)


class RatingButton(discord.ui.Button):
    def __init__(self, score: int):
        super().__init__(
            label=str(score),
            emoji=STAR,
            style=discord.ButtonStyle.success if score >= 4 else discord.ButtonStyle.secondary,
            custom_id=f'tkrate:{score}',
        )
        self.score = score

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog('TicketExtras')
        if cog is None:
            await interaction.response.send_message('Модуль оценок недоступен.', ephemeral=True)
            return
        await cog.handle_rating(interaction, self.score)


class TicketRatingView(discord.ui.View):
    """Кнопки оценки в личных сообщениях — живут после перезапуска бота."""

    def __init__(self):
        super().__init__(timeout=None)
        for score in (1, 2, 3, 4, 5):
            self.add_item(RatingButton(score))


class TicketClaimView(discord.ui.View):
    """Кнопка «Взять тикет» внутри канала тикета."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label='Взять тикет',
        emoji=ADMIN_PURPLE,
        style=discord.ButtonStyle.primary,
        custom_id='tkclaim:take',
    )
    async def take(self, interaction: discord.Interaction, _: discord.ui.Button):
        cog = interaction.client.get_cog('TicketExtras')
        if cog is None:
            await interaction.response.send_message('Модуль недоступен.', ephemeral=True)
            return
        await cog.claim(interaction)


# ─── COG ──────────────────────────────────────────────────

class TicketExtras(commands.Cog):
    """Назначение ответственных, автозакрытие и оценки поддержки."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await self.bot.db.db.executescript(SCHEMA)
        await self.bot.db.db.commit()
        self._worker.start()
        logger.info('Ticket extras tables ready.')

    async def cog_unload(self):
        self._worker.cancel()

    # ─── база ──────────────────────────────────────────────

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
        await self.bot.db.db.execute(query, params)
        await self.bot.db.db.commit()

    async def _cfg(self, guild_id: int):
        row = await self._fetchone('SELECT * FROM ticket_extra_config WHERE guild_id = ?', (guild_id,))
        if row is None:
            await self._exec('INSERT INTO ticket_extra_config (guild_id) VALUES (?)', (guild_id,))
            row = await self._fetchone('SELECT * FROM ticket_extra_config WHERE guild_id = ?', (guild_id,))
        return row

    async def _state(self, channel_id: int, guild_id: int = None):
        row = await self._fetchone('SELECT * FROM ticket_extra_state WHERE channel_id = ?', (channel_id,))
        if row is None:
            await self._exec(
                'INSERT INTO ticket_extra_state (channel_id, guild_id) VALUES (?, ?)',
                (channel_id, guild_id),
            )
            row = await self._fetchone('SELECT * FROM ticket_extra_state WHERE channel_id = ?', (channel_id,))
        return row

    async def _log(self, guild: discord.Guild, embed: discord.Embed):
        try:
            settings = await self.bot.db.get_guild(guild.id)
        except Exception:
            settings = None
        cid = None
        if settings is not None:
            try:
                cid = settings['ticket_log_channel_id'] or settings['log_channel_id']
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
            logger.error(f'Ticket log failed: {e}')

    def _is_staff(self, member: discord.Member) -> bool:
        p = member.guild_permissions
        return p.administrator or p.manage_guild or p.manage_messages or p.moderate_members

    # ─── ВЗЯТЬ ТИКЕТ ──────────────────────────────────────

    async def claim(self, interaction: discord.Interaction):
        channel = interaction.channel
        ticket = await self._fetchone('SELECT * FROM tickets WHERE channel_id = ?', (channel.id,))
        if ticket is None:
            await interaction.response.send_message(
                embed=err('Этот канал не является тикетом.'), ephemeral=True
            )
            return
        if not self._is_staff(interaction.user):
            await interaction.response.send_message(
                embed=err('Брать тикеты может только команда сервера.'), ephemeral=True
            )
            return

        current = ticket['assigned_to']
        if current and int(current) != interaction.user.id:
            await interaction.response.send_message(
                embed=warn_embed(
                    f'Тикет уже взял <@{current}>.\n'
                    'Если нужно перехватить — используйте `/take перехватить: Да`.'
                ),
                ephemeral=True,
            )
            return
        if current and int(current) == interaction.user.id:
            await interaction.response.send_message(
                embed=info('Вы уже отвечаете за этот тикет.'), ephemeral=True
            )
            return

        await self._assign(interaction.guild, channel, ticket, interaction.user)
        await interaction.response.send_message(
            embed=ok('Вы взяли тикет. Остальные видят, что им занимаетесь вы.'),
            ephemeral=True,
        )

    async def _assign(self, guild, channel, ticket, staff: discord.Member):
        await self._exec('UPDATE tickets SET assigned_to = ? WHERE channel_id = ?',
                         (staff.id, channel.id))
        await self._exec('UPDATE ticket_extra_state SET staff_id = ? WHERE channel_id = ?',
                         (staff.id, channel.id))

        number = ticket['ticket_number'] or 0
        e = info(
            f'За тикетом закреплён {staff.mention}.\n'
            'Ответ будет от этого человека — остальные не будут дублировать работу.',
            '🙋 Тикет взят в работу',
        )
        e.set_author(name=str(staff), icon_url=staff.display_avatar.url)
        try:
            await channel.send(embed=e)
        except Exception:
            pass

        try:
            topic = f'Тикет #{number:04d} • отвечает {staff.display_name}'
            await channel.edit(topic=topic[:1024])
        except Exception:
            pass

        await self._log(
            guild,
            info(
                f'**Тикет:** {channel.mention} (#{number:04d})\n'
                f'**Ответственный:** {staff.mention}',
                '🙋 Тикет взят в работу',
            ),
        )

    @app_commands.command(name='take', description='Взять текущий тикет в работу')
    @app_commands.describe(перехватить='Забрать тикет у другого модератора')
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def take_cmd(self, interaction: discord.Interaction, перехватить: bool = False):
        ticket = await self._fetchone(
            'SELECT * FROM tickets WHERE channel_id = ?', (interaction.channel_id,)
        )
        if ticket is None:
            await interaction.response.send_message(
                embed=err('Эту команду нужно вызывать внутри канала тикета.'), ephemeral=True
            )
            return
        if not self._is_staff(interaction.user):
            await interaction.response.send_message(
                embed=err('Команда доступна только команде сервера.'), ephemeral=True
            )
            return

        current = ticket['assigned_to']
        if current and int(current) != interaction.user.id and not перехватить:
            await interaction.response.send_message(
                embed=warn_embed(
                    f'Тикет уже ведёт <@{current}>.\n'
                    'Чтобы забрать его себе, вызовите команду с параметром `перехватить: Да`.'
                ),
                ephemeral=True,
            )
            return

        await self._assign(interaction.guild, interaction.channel, ticket, interaction.user)
        await interaction.response.send_message(
            embed=ok('Тикет закреплён за вами.'), ephemeral=True
        )

    # ─── ОЦЕНКИ ───────────────────────────────────────────

    async def handle_rating(self, interaction: discord.Interaction, score: int):
        state = await self._fetchone(
            'SELECT * FROM ticket_extra_state WHERE rating_msg_id = ?', (interaction.message.id,)
        )
        if state is None:
            await interaction.response.send_message(
                'Не могу найти тикет для этой оценки — возможно, он удалён.', ephemeral=True
            )
            return
        existing = await self._fetchone(
            'SELECT id FROM ticket_ratings WHERE channel_id = ? AND user_id = ?',
            (state['channel_id'], interaction.user.id),
        )
        if existing is not None:
            await interaction.response.send_message('Вы уже оценили этот тикет. Спасибо!', ephemeral=True)
            return
        await interaction.response.send_modal(RatingCommentModal(self, score, state))

    async def save_rating(self, interaction, score, state, comment):
        await self._exec(
            'INSERT INTO ticket_ratings (guild_id, channel_id, ticket_number, user_id, '
            'staff_id, score, comment, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (
                state['guild_id'], state['channel_id'], state['ticket_number'],
                interaction.user.id, state['staff_id'], score, comment, _now(),
            ),
        )
        stars = ' '.join([STAR] * score)
        await interaction.response.send_message(
            embed=ok(
                f'Спасибо за оценку! {stars}\n'
                'Она помогает нам становиться лучше 💗',
                'Оценка принята',
            ),
            ephemeral=True,
        )
        try:
            await interaction.message.edit(view=None)
        except Exception:
            pass

        guild = self.bot.get_guild(int(state['guild_id'])) if state['guild_id'] else None
        if guild is None:
            return
        number = state['ticket_number'] or 0
        e = info(
            f'**Оценка:** {stars} ({score}/5)\n'
            f'**Тикет:** #{number:04d}\n'
            f'**Игрок:** {interaction.user.mention}\n'
            + (f'**Отвечал:** <@{state["staff_id"]}>\n' if state['staff_id'] else '')
            + (f'**Комментарий:** {comment}' if comment else ''),
            '⭐ Оценка поддержки',
        )
        await self._log(guild, e)

    @app_commands.command(name='ratings', description='Статистика оценок поддержки')
    @app_commands.describe(staff='Показать только по одному модератору')
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def ratings(self, interaction: discord.Interaction, staff: discord.Member = None):
        await interaction.response.defer(ephemeral=True)

        if staff is not None:
            row = await self._fetchone(
                'SELECT COUNT(*) AS c, AVG(score) AS avg FROM ticket_ratings '
                'WHERE guild_id = ? AND staff_id = ?',
                (interaction.guild_id, staff.id),
            )
            count = int(row['c'] or 0)
            if not count:
                await interaction.followup.send(
                    embed=info(f'У {staff.mention} пока нет оценок.'), ephemeral=True
                )
                return
            avg = float(row['avg'] or 0)
            recent = await self._fetchall(
                'SELECT score, comment FROM ticket_ratings WHERE guild_id = ? AND staff_id = ? '
                'ORDER BY id DESC LIMIT 5',
                (interaction.guild_id, staff.id),
            )
            lines = []
            for r in recent:
                lines.append(' '.join([STAR] * int(r['score'])) + (f' — {r["comment"]}' if r['comment'] else ''))
            e = info(
                f'**Средний балл:** {avg:.2f} из 5\n'
                f'**Всего оценок:** {count}\n\n'
                '**Последние отзывы:**\n' + '\n'.join(lines),
                f'⭐ Оценки — {staff.display_name}',
            )
            e.set_thumbnail(url=staff.display_avatar.url)
            await interaction.followup.send(embed=e, ephemeral=True)
            return

        total = await self._fetchone(
            'SELECT COUNT(*) AS c, AVG(score) AS avg FROM ticket_ratings WHERE guild_id = ?',
            (interaction.guild_id,),
        )
        count = int(total['c'] or 0)
        if not count:
            await interaction.followup.send(
                embed=info('Оценок пока нет. Они появятся после закрытия первых тикетов.'),
                ephemeral=True,
            )
            return

        top = await self._fetchall(
            'SELECT staff_id, COUNT(*) AS c, AVG(score) AS avg FROM ticket_ratings '
            'WHERE guild_id = ? AND staff_id IS NOT NULL GROUP BY staff_id ORDER BY avg DESC LIMIT 10',
            (interaction.guild_id,),
        )
        dist = await self._fetchall(
            'SELECT score, COUNT(*) AS c FROM ticket_ratings WHERE guild_id = ? '
            'GROUP BY score ORDER BY score DESC',
            (interaction.guild_id,),
        )

        e = info('', '⭐ Оценки поддержки')
        e.add_field(
            name='Общее',
            value=f'Средний балл: **{float(total["avg"] or 0):.2f}** из 5\nВсего оценок: **{count}**',
            inline=False,
        )
        if dist:
            e.add_field(
                name='Распределение',
                value='\n'.join(' '.join([STAR] * int(d['score'])) + f' — {int(d["c"])}' for d in dist),
                inline=False,
            )
        if top:
            e.add_field(
                name='По модераторам',
                value='\n'.join(
                    f'<@{t["staff_id"]}> — **{float(t["avg"]):.2f}** ({int(t["c"])})' for t in top
                ),
                inline=False,
            )
        await interaction.followup.send(embed=e, ephemeral=True)

    # ─── НАСТРОЙКИ ───────────────────────────────────────

    @app_commands.command(name='ticketsettings', description='Автозакрытие, оценки и самоочистка тикетов')
    @app_commands.describe(
        автозакрытие='Закрывать тикеты без активности',
        часов_до_предупреждения='Через сколько часов тишины напомнить',
        часов_до_закрытия='Через сколько часов тишины закрыть',
        оценки='Спрашивать оценку после закрытия',
        самоочистка='Удалять записи о тикетах с удалёнными каналами',
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def ticketsettings(
        self,
        interaction: discord.Interaction,
        автозакрытие: bool = None,
        часов_до_предупреждения: int = None,
        часов_до_закрытия: int = None,
        оценки: bool = None,
        самоочистка: bool = None,
    ):
        await self._cfg(interaction.guild_id)
        changes = []

        if автозакрытие is not None:
            await self._exec('UPDATE ticket_extra_config SET autoclose = ? WHERE guild_id = ?',
                             (1 if автозакрытие else 0, interaction.guild_id))
            changes.append('Автозакрытие → ' + ('вкл ✅' if автозакрытие else 'выкл ⬜'))
        if часов_до_предупреждения is not None:
            v = max(1, min(720, часов_до_предупреждения))
            await self._exec('UPDATE ticket_extra_config SET warn_hours = ? WHERE guild_id = ?',
                             (v, interaction.guild_id))
            changes.append(f'Напоминание → через **{v}** ч.')
        if часов_до_закрытия is not None:
            v = max(2, min(1440, часов_до_закрытия))
            await self._exec('UPDATE ticket_extra_config SET close_hours = ? WHERE guild_id = ?',
                             (v, interaction.guild_id))
            changes.append(f'Закрытие → через **{v}** ч.')
        if оценки is not None:
            await self._exec('UPDATE ticket_extra_config SET rating_enabled = ? WHERE guild_id = ?',
                             (1 if оценки else 0, interaction.guild_id))
            changes.append('Оценки → ' + ('вкл ✅' if оценки else 'выкл ⬜'))
        if самоочистка is not None:
            await self._exec('UPDATE ticket_extra_config SET selfheal = ? WHERE guild_id = ?',
                             (1 if самоочистка else 0, interaction.guild_id))
            changes.append('Самоочистка → ' + ('вкл ✅' if самоочистка else 'выкл ⬜'))

        cfg = await self._cfg(interaction.guild_id)
        e = info(
            ('**Изменено:**\n' + '\n'.join('• ' + c for c in changes) + '\n\n' if changes else '')
            + '**Текущие настройки:**\n'
            + f'• Автозакрытие: {"вкл ✅" if cfg["autoclose"] else "выкл ⬜"}\n'
            + f'• Напоминание после **{cfg["warn_hours"]}** ч. тишины\n'
            + f'• Закрытие после **{cfg["close_hours"]}** ч. тишины\n'
            + f'• Оценки: {"вкл ✅" if cfg["rating_enabled"] else "выкл ⬜"}\n'
            + f'• Самоочистка: {"вкл ✅" if cfg["selfheal"] else "выкл ⬜"}',
            '🎟️ Настройки тикетов',
        )
        await interaction.response.send_message(embed=e, ephemeral=True)

    # ─── ФОНОВАЯ ЗАДАЧА ───────────────────────────────────

    @tasks.loop(minutes=10)
    async def _worker(self):
        try:
            await self._process_open()
            await self._process_closed()
        except Exception as e:
            logger.error(f'Ticket worker error: {e}', exc_info=True)

    @_worker.before_loop
    async def _before_worker(self):
        await self.bot.wait_until_ready()

    async def _process_open(self):
        rows = await self._fetchall("SELECT * FROM tickets WHERE status = 'open'")
        for t in rows:
            guild = self.bot.get_guild(int(t['guild_id']))
            if guild is None:
                continue
            cfg = await self._cfg(guild.id)
            channel = guild.get_channel(int(t['channel_id']))

            # самоочистка: канал удалён, а запись висит
            if channel is None:
                if cfg['selfheal']:
                    await self._exec('DELETE FROM tickets WHERE channel_id = ?', (t['channel_id'],))
                    await self._exec('DELETE FROM ticket_extra_state WHERE channel_id = ?',
                                     (t['channel_id'],))
                    logger.info(f'Self-heal: removed ticket row for deleted channel {t["channel_id"]}')
                continue

            state = await self._state(int(t['channel_id']), guild.id)

            # кнопка «Взять тикет» — один раз на тикет
            if not state['claim_posted']:
                await self._exec(
                    'UPDATE ticket_extra_state SET claim_posted = 1, user_id = ?, '
                    'ticket_number = ? WHERE channel_id = ?',
                    (t['creator_id'], t['ticket_number'], t['channel_id']),
                )
                if not t['assigned_to']:
                    try:
                        await channel.send(
                            embed=info(
                                'Кто из команды берёт этот тикет в работу?\n'
                                'Нажмите кнопку ниже, чтобы остальные не дублировали ответ.',
                                '🙋 Свободный тикет',
                            ),
                            view=TicketClaimView(),
                        )
                    except Exception:
                        pass

            if not cfg['autoclose']:
                continue

            last = await self._last_activity(channel)
            if last is None:
                continue
            idle = datetime.now(timezone.utc) - last
            warn_hours = int(cfg['warn_hours'])
            close_hours = int(cfg['close_hours'])

            if idle >= timedelta(hours=close_hours):
                await self._auto_close(guild, channel, t)
            elif idle >= timedelta(hours=warn_hours) and not state['warned_at']:
                left = close_hours - warn_hours
                try:
                    await channel.send(
                        content=f'<@{t["creator_id"]}>',
                        embed=warn_embed(
                            f'В тикете тишина уже **{warn_hours} ч.**\n\n'
                            f'Если вопрос решён — ничего делать не нужно, тикет закроется '
                            f'автоматически через **{left} ч.**\n'
                            'Если вопрос ещё актуален — просто напишите сюда любое сообщение.',
                            '⏰ Тикет скоро закроется',
                        ),
                    )
                except Exception:
                    pass
                await self._exec(
                    'UPDATE ticket_extra_state SET warned_at = ? WHERE channel_id = ?',
                    (_now(), t['channel_id']),
                )

    async def _last_activity(self, channel):
        try:
            async for message in channel.history(limit=1):
                return message.created_at
        except Exception:
            return None
        return channel.created_at

    async def _auto_close(self, guild, channel, ticket):
        number = ticket['ticket_number'] or 0
        try:
            member = guild.get_member(int(ticket['creator_id']))
            if member is not None:
                await channel.set_permissions(member, send_messages=False, read_messages=True)
        except Exception:
            pass

        await self._exec(
            "UPDATE tickets SET status = 'closed', closed_at = ?, closed_by = ? WHERE channel_id = ?",
            (_now(), self.bot.user.id, channel.id),
        )
        try:
            await channel.send(
                embed=info(
                    f'Тикет **#{number:04d}** закрыт автоматически из-за отсутствия активности.\n\n'
                    'Если вопрос остался — откройте новый тикет, мы всегда на связи 💗',
                    '🔒 Тикет закрыт',
                )
            )
        except Exception:
            pass

        await self._log(
            guild,
            info(
                f'**Тикет:** #{number:04d} ({channel.mention})\n'
                f'**Создатель:** <@{ticket["creator_id"]}>\n'
                '**Причина:** нет активности',
                '🔒 Автозакрытие тикета',
            ),
        )

    async def _process_closed(self):
        rows = await self._fetchall(
            "SELECT * FROM tickets WHERE status = 'closed' ORDER BY id DESC LIMIT 100"
        )
        for t in rows:
            guild = self.bot.get_guild(int(t['guild_id']))
            if guild is None:
                continue
            cfg = await self._cfg(guild.id)
            if not cfg['rating_enabled']:
                continue

            state = await self._state(int(t['channel_id']), guild.id)
            if state['rating_prompted']:
                continue

            member = guild.get_member(int(t['creator_id']))
            if member is None:
                await self._exec(
                    'UPDATE ticket_extra_state SET rating_prompted = 1 WHERE channel_id = ?',
                    (t['channel_id'],),
                )
                continue

            number = t['ticket_number'] or 0
            e = info(
                f'Ваш тикет **#{number:04d}** на сервере **{guild.name}** закрыт.\n\n'
                'Оцените, пожалуйста, работу поддержки от 1 до 5 звёзд.\n'
                'Это займёт пару секунд и очень нам поможет 💗',
                '⭐ Как вам поддержка?',
            )
            msg = None
            try:
                msg = await member.send(embed=e, view=TicketRatingView())
            except Exception:
                pass

            await self._exec(
                'UPDATE ticket_extra_state SET rating_prompted = 1, rating_msg_id = ?, '
                'user_id = ?, staff_id = ?, ticket_number = ? WHERE channel_id = ?',
                (
                    msg.id if msg else None,
                    t['creator_id'],
                    t['assigned_to'] or t['closed_by'],
                    t['ticket_number'],
                    t['channel_id'],
                ),
            )


async def setup(bot):
    await bot.add_cog(TicketExtras(bot))
