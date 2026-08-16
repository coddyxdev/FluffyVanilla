import asyncio
import re
import secrets
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.audit import record_audit
from utils.embeds import ok, err, info, PINK, SUCCESS, ERROR, WARNING
from utils.emojis import EVENT, TROPHY, MEMBER, VERIFIED, EXCITED, ARROW_PINK, BAWWW

DURATION_RE = re.compile(r'(\d+)\s*([smhdw])', re.IGNORECASE)
MAX_DURATION = timedelta(days=30)
MIN_DURATION = timedelta(seconds=10)


def parse_duration(value: str) -> timedelta:
    """Parse combinations such as 30m, 2h, 1d12h or 1w."""
    compact = re.sub(r'\s+', '', value.lower())
    matches = list(DURATION_RE.finditer(compact))
    if not matches or ''.join(m.group(0) for m in matches) != compact:
        raise ValueError('Используй формат `30m`, `2h`, `1d12h` или `1w`.')
    factors = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}
    seconds = sum(int(m.group(1)) * factors[m.group(2)] for m in matches)
    duration = timedelta(seconds=seconds)
    if duration < MIN_DURATION:
        raise ValueError('Минимальная длительность — 10 секунд.')
    if duration > MAX_DURATION:
        raise ValueError('Максимальная длительность — 30 дней.')
    return duration


def discord_time(value: str) -> str:
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    stamp = int(dt.timestamp())
    return f'<t:{stamp}:F> (<t:{stamp}:R>)'


class GiveawayJoinView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label='Участвовать', emoji=EVENT, style=discord.ButtonStyle.success,
        custom_id='giveaway:toggle',
    )
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        bot = interaction.client
        giveaway = await bot.db.fetchone(
            'SELECT * FROM giveaways WHERE message_id=?', (interaction.message.id,)
        )
        if not giveaway or giveaway['status'] != 'open':
            await interaction.response.send_message(
                embed=err('Этот розыгрыш уже завершён.'), ephemeral=True,
            )
            return
        end_at = datetime.fromisoformat(str(giveaway['end_at']).replace('Z', '+00:00'))
        if end_at.tzinfo is None:
            end_at = end_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= end_at:
            await interaction.response.send_message(
                embed=err('Время участия уже закончилось.'), ephemeral=True,
            )
            return
        if interaction.user.bot:
            await interaction.response.send_message(embed=err('Боты не участвуют.'), ephemeral=True)
            return
        if int(giveaway['host_id']) == interaction.user.id:
            await interaction.response.send_message(
                embed=err('Организатор не может участвовать в своём розыгрыше.'), ephemeral=True,
            )
            return
        role_id = giveaway['required_role_id']
        if role_id and int(role_id) not in {role.id for role in interaction.user.roles}:
            role = interaction.guild.get_role(int(role_id))
            label = role.mention if role else f'роль `{role_id}`'
            await interaction.response.send_message(
                embed=err(f'Для участия требуется {label}.'), ephemeral=True,
            )
            return

        async with bot.db.transaction() as db:
            cursor = await db.execute(
                'SELECT 1 FROM giveaway_entries WHERE giveaway_id=? AND user_id=?',
                (giveaway['id'], interaction.user.id),
            )
            exists = await cursor.fetchone()
            await cursor.close()
            if exists:
                await db.execute(
                    'DELETE FROM giveaway_entries WHERE giveaway_id=? AND user_id=?',
                    (giveaway['id'], interaction.user.id),
                )
                joined = False
            else:
                await db.execute(
                    'INSERT INTO giveaway_entries(giveaway_id,user_id) VALUES(?,?)',
                    (giveaway['id'], interaction.user.id),
                )
                joined = True

        count = await bot.db.fetchone(
            'SELECT COUNT(*) AS total FROM giveaway_entries WHERE giveaway_id=?',
            (giveaway['id'],),
        )
        if interaction.message.embeds:
            embed = interaction.message.embeds[0]
            embed.set_field_at(1, name='Участников', value=str(count['total']), inline=True)
            try:
                await interaction.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass
        text = 'Ты участвуешь в розыгрыше!' if joined else 'Ты вышел из розыгрыша.'
        await interaction.response.send_message(embed=ok(text), ephemeral=True)


class Giveaways(commands.Cog):
    giveaway = app_commands.Group(
        name='giveaway', description='Управление розыгрышами', guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot):
        self.bot = bot
        self._ending = set()
        self._worker.start()

    async def cog_load(self):
        # Если процесс остановился во время выбора, вернём розыгрыш в очередь.
        await self.bot.db.execute("UPDATE giveaways SET status='open' WHERE status='drawing'")

    def cog_unload(self):
        self._worker.cancel()

    def _embed(self, giveaway_id: int, prize: str, description: str, host_id: int,
               end_at: str, winners: int, required_role_id=None, entries=0):
        embed = discord.Embed(
            title='🎉 Розыгрыш: ' + prize,
            description=f'{ARROW_PINK} ' + (description or 'Нажми кнопку ниже, чтобы принять участие!'),
            color=PINK,
        )
        embed.add_field(name='Победителей', value=f'{TROPHY} {winners}', inline=True)
        embed.add_field(name='Участников', value=f'{MEMBER} {entries}', inline=True)
        embed.add_field(name='Завершение', value=discord_time(end_at), inline=False)
        requirement = f'<@&{required_role_id}>' if required_role_id else 'Нет'
        embed.add_field(name='Требуемая роль', value=f'{VERIFIED} {requirement}', inline=True)
        embed.add_field(name='Организатор', value=f'<@{host_id}>', inline=True)
        embed.set_footer(text=f'Fluffy Vanilla • Giveaway #{giveaway_id}')
        return embed

    async def _eligible(self, giveaway):
        guild = self.bot.get_guild(int(giveaway['guild_id']))
        if not guild:
            return []
        rows = await self.bot.db.fetchall(
            'SELECT user_id FROM giveaway_entries WHERE giveaway_id=?', (giveaway['id'],)
        )
        required_role = int(giveaway['required_role_id']) if giveaway['required_role_id'] else None
        eligible = []
        for row in rows:
            user_id = int(row['user_id'])
            if user_id == int(giveaway['host_id']):
                continue
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    continue
            if member.bot:
                continue
            if required_role and required_role not in {role.id for role in member.roles}:
                continue
            eligible.append(member)
        return eligible

    async def _finish(self, giveaway_id: int, *, forced_by=None):
        if giveaway_id in self._ending:
            return None
        self._ending.add(giveaway_id)
        try:
            async with self.bot.db.transaction() as db:
                cursor = await db.execute('SELECT * FROM giveaways WHERE id=?', (giveaway_id,))
                giveaway = await cursor.fetchone()
                await cursor.close()
                if not giveaway or giveaway['status'] != 'open':
                    return None
                await db.execute(
                    "UPDATE giveaways SET status='drawing' WHERE id=? AND status='open'",
                    (giveaway_id,),
                )

            eligible = await self._eligible(giveaway)
            count = min(int(giveaway['winner_count']), len(eligible))
            winners = secrets.SystemRandom().sample(eligible, count) if count else []
            winner_ids = ','.join(str(member.id) for member in winners)
            now = datetime.now(timezone.utc).isoformat()
            await self.bot.db.execute(
                "UPDATE giveaways SET status='ended',ended_at=?,winner_ids=? WHERE id=?",
                (now, winner_ids, giveaway_id),
            )

            guild = self.bot.get_guild(int(giveaway['guild_id']))
            channel = guild.get_channel(int(giveaway['channel_id'])) if guild else None
            message = None
            if channel and giveaway['message_id']:
                try:
                    message = await channel.fetch_message(int(giveaway['message_id']))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            mentions = ', '.join(member.mention for member in winners)
            result = f'{TROPHY} {mentions}' if winners else f'{BAWWW} Подходящих участников нет.'
            if message and message.embeds:
                embed = message.embeds[0]
                embed.color = SUCCESS if winners else WARNING
                embed.title = '🏁 Завершено: ' + str(giveaway['prize'])
                embed.add_field(name='Победители', value=result[:1024], inline=False)
                await message.edit(embed=embed, view=None)
            if channel:
                if winners:
                    await channel.send(
                        f'{EXCITED} {TROPHY} Поздравляем {mentions}! Вы выиграли **{giveaway["prize"]}**.\n'
                        f'Розыгрыш: {message.jump_url if message else f"#{giveaway_id}"}'
                    )
                else:
                    await channel.send(f'Розыгрыш **{giveaway["prize"]}** завершён без победителей.')
            await record_audit(
                self.bot, int(giveaway['guild_id']), 'giveaway.ended',
                actor_id=forced_by, target_id=giveaway['message_id'],
                metadata={'id': giveaway_id, 'winners': [m.id for m in winners]},
            )
            return winners
        finally:
            self._ending.discard(giveaway_id)

    @tasks.loop(seconds=15)
    async def _worker(self):
        rows = await self.bot.db.fetchall(
            "SELECT id FROM giveaways WHERE status='open' AND datetime(end_at)<=datetime('now') LIMIT 25"
        )
        for row in rows:
            try:
                await self._finish(int(row['id']))
            except Exception:
                import logging
                logging.getLogger('FluFFy.Giveaway').exception('Cannot finish giveaway %s', row['id'])

    @_worker.before_loop
    async def _before_worker(self):
        await self.bot.wait_until_ready()

    @giveaway.command(name='create', description='Создать новый розыгрыш')
    @app_commands.describe(
        prize='Что разыгрывается', duration='Например: 30m, 2h, 1d12h или 1w',
        winners='Количество победителей', channel='Канал проведения',
        required_role='Роль, обязательная для участия', description='Дополнительные условия',
    )
    async def create(self, interaction: discord.Interaction,
                     prize: app_commands.Range[str, 2, 150], duration: str,
                     winners: app_commands.Range[int, 1, 20] = 1,
                     channel: discord.TextChannel = None,
                     required_role: discord.Role = None,
                     description: app_commands.Range[str, 0, 1000] = None):
        try:
            delta = parse_duration(duration)
        except ValueError as exc:
            await interaction.response.send_message(embed=err(str(exc)), ephemeral=True)
            return
        target = channel or interaction.channel
        perms = target.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            await interaction.response.send_message(
                embed=err(f'Боту не хватает прав для отправки розыгрыша в {target.mention}.'),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        end_at = (datetime.now(timezone.utc) + delta).isoformat()
        giveaway_id, _ = await self.bot.db.execute(
            '''INSERT INTO giveaways
               (guild_id,channel_id,host_id,prize,description,winner_count,required_role_id,end_at)
               VALUES(?,?,?,?,?,?,?,?)''',
            (interaction.guild_id, target.id, interaction.user.id, prize.strip(),
             description, winners, required_role.id if required_role else None, end_at),
        )
        embed = self._embed(giveaway_id, prize.strip(), description, interaction.user.id,
                            end_at, winners, required_role.id if required_role else None)
        try:
            message = await target.send(embed=embed, view=GiveawayJoinView())
        except Exception as exc:
            await self.bot.db.execute('DELETE FROM giveaways WHERE id=?', (giveaway_id,))
            await interaction.followup.send(embed=err(f'Не удалось отправить розыгрыш: `{exc}`'), ephemeral=True)
            return
        await self.bot.db.execute(
            'UPDATE giveaways SET message_id=? WHERE id=?', (message.id, giveaway_id)
        )
        await interaction.followup.send(
            embed=ok(f'Розыгрыш #{giveaway_id} создан в {target.mention}.'), ephemeral=True,
        )
        await record_audit(
            self.bot, interaction.guild_id, 'giveaway.created', actor_id=interaction.user.id,
            target_id=message.id, metadata={'id': giveaway_id, 'prize': prize.strip()},
        )

    @giveaway.command(name='end', description='Завершить розыгрыш досрочно')
    async def end(self, interaction: discord.Interaction, giveaway_id: int):
        await interaction.response.defer(ephemeral=True)
        winners = await self._finish(giveaway_id, forced_by=interaction.user.id)
        if winners is None:
            await interaction.followup.send(embed=err('Активный розыгрыш с таким ID не найден.'), ephemeral=True)
        else:
            await interaction.followup.send(embed=ok('Розыгрыш завершён.'), ephemeral=True)

    @giveaway.command(name='cancel', description='Отменить розыгрыш без победителей')
    async def cancel(self, interaction: discord.Interaction, giveaway_id: int,
                     reason: str = 'Без указания причины'):
        row = await self.bot.db.fetchone(
            "SELECT * FROM giveaways WHERE guild_id=? AND id=? AND status='open'",
            (interaction.guild_id, giveaway_id),
        )
        if not row:
            await interaction.response.send_message(embed=err('Активный розыгрыш не найден.'), ephemeral=True)
            return
        _, changed = await self.bot.db.execute(
            "UPDATE giveaways SET status='cancelled',ended_at=? WHERE id=? AND status='open'",
            (datetime.now(timezone.utc).isoformat(), giveaway_id),
        )
        if not changed:
            await interaction.response.send_message(embed=err('Розыгрыш уже обработан.'), ephemeral=True)
            return
        channel = interaction.guild.get_channel(int(row['channel_id']))
        if channel and row['message_id']:
            try:
                message = await channel.fetch_message(int(row['message_id']))
                if message.embeds:
                    embed = message.embeds[0]
                    embed.title = '❌ Розыгрыш отменён: ' + str(row['prize'])
                    embed.color = ERROR
                    embed.add_field(name='Причина', value=reason[:1024], inline=False)
                    await message.edit(embed=embed, view=None)
            except Exception:
                pass
        await interaction.response.send_message(embed=ok('Розыгрыш отменён.'), ephemeral=True)
        await record_audit(self.bot, interaction.guild_id, 'giveaway.cancelled',
                           actor_id=interaction.user.id, target_id=row['message_id'], reason=reason,
                           metadata={'id': giveaway_id})

    @giveaway.command(name='reroll', description='Повторно выбрать победителей завершённого розыгрыша')
    async def reroll(self, interaction: discord.Interaction, giveaway_id: int,
                     winners: app_commands.Range[int, 1, 20] = 1):
        await interaction.response.defer(ephemeral=True)
        row = await self.bot.db.fetchone(
            "SELECT * FROM giveaways WHERE guild_id=? AND id=? AND status='ended'",
            (interaction.guild_id, giveaway_id),
        )
        if not row:
            await interaction.followup.send(embed=err('Завершённый розыгрыш не найден.'), ephemeral=True)
            return
        eligible = await self._eligible(row)
        old = {int(x) for x in str(row['winner_ids'] or '').split(',') if x}
        pool = [member for member in eligible if member.id not in old]
        count = min(winners, len(pool))
        selected = secrets.SystemRandom().sample(pool, count) if count else []
        if not selected:
            await interaction.followup.send(embed=err('Нет новых подходящих участников для перевыбора.'), ephemeral=True)
            return
        all_winners = sorted(old | {member.id for member in selected})
        await self.bot.db.execute(
            'UPDATE giveaways SET winner_ids=? WHERE id=?',
            (','.join(str(user_id) for user_id in all_winners), giveaway_id),
        )
        channel = interaction.guild.get_channel(int(row['channel_id']))
        mentions = ', '.join(member.mention for member in selected)
        if channel:
            await channel.send(f'{TROPHY} 🔄 Новый выбор для **{row["prize"]}**: {mentions}')
        await interaction.followup.send(embed=ok(f'Новые победители: {mentions}'), ephemeral=True)
        await record_audit(self.bot, interaction.guild_id, 'giveaway.reroll',
                           actor_id=interaction.user.id, target_id=row['message_id'],
                           metadata={'id': giveaway_id, 'winners': [m.id for m in selected]})

    @giveaway.command(name='list', description='Показать последние розыгрыши')
    async def list_giveaways(self, interaction: discord.Interaction):
        rows = await self.bot.db.fetchall(
            'SELECT id,prize,status,end_at FROM giveaways WHERE guild_id=? ORDER BY id DESC LIMIT 15',
            (interaction.guild_id,),
        )
        if not rows:
            await interaction.response.send_message(embed=info('Розыгрышей пока нет.'), ephemeral=True)
            return
        labels = {'open': '🟢 идёт', 'drawing': '🟡 выбор', 'ended': '🏁 завершён', 'cancelled': '❌ отменён'}
        lines = [f'`#{row["id"]}` **{row["prize"]}** — {labels.get(row["status"], row["status"])}' for row in rows]
        await interaction.response.send_message(
            embed=info('\n'.join(lines), title='🎉 Последние розыгрыши'), ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(Giveaways(bot))
