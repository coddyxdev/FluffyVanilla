import json
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils.audit import record_audit
from utils.embeds import info, PINK
from utils.emojis import BOT, MEMBER, ARROW_WHITE


class Audit(commands.Cog):
    audit = app_commands.Group(
        name='audit', description='Журнал действий сервера', guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction, command):
        await record_audit(
            self.bot, interaction.guild_id, 'command.' + command.qualified_name,
            actor_id=interaction.user.id, target_id=interaction.channel_id,
            metadata={'command': command.qualified_name},
        )

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        await record_audit(self.bot, channel.guild.id, 'channel.create',
                           target_id=channel.id, metadata={'name': channel.name})

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        await record_audit(self.bot, channel.guild.id, 'channel.delete',
                           target_id=channel.id, metadata={'name': channel.name})

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        await record_audit(self.bot, guild.id, 'member.ban', target_id=user.id)

    @commands.Cog.listener()
    async def on_member_unban(self, guild, user):
        await record_audit(self.bot, guild.id, 'member.unban', target_id=user.id)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        before_roles = {r.id for r in before.roles}
        after_roles = {r.id for r in after.roles}
        if before_roles != after_roles:
            await record_audit(
                self.bot, after.guild.id, 'member.roles', target_id=after.id,
                metadata={
                    'added': list(after_roles - before_roles),
                    'removed': list(before_roles - after_roles),
                },
            )
        if before.timed_out_until != after.timed_out_until:
            await record_audit(
                self.bot, after.guild.id, 'member.timeout', target_id=after.id,
                metadata={'until': after.timed_out_until.isoformat() if after.timed_out_until else None},
            )

    async def _send_rows(self, interaction, rows, title):
        if not rows:
            await interaction.response.send_message(embed=info('Записей не найдено.'), ephemeral=True)
            return
        lines = []
        for row in rows:
            when = str(row['created_at'])[:19].replace('T', ' ')
            actor = f'<@{row["actor_id"]}>' if row['actor_id'] else 'система'
            target = f' → <@{row["target_id"]}>' if row['target_id'] else ''
            reason = f' — {row["reason"]}' if row['reason'] else ''
            lines.append(f'{ARROW_WHITE} `#{row["id"]}` `{when}` **{row["action"]}** · {actor}{target}{reason}')
        embed = discord.Embed(title=title, description=f'{BOT} Журнал сервера\n\n' + '\n'.join(lines)[:3900], color=PINK)
        embed.set_footer(text='Fluffy Vanilla • audit')
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @audit.command(name='recent', description='Последние события аудита')
    @app_commands.describe(limit='Количество записей: 1–25')
    async def recent(self, interaction: discord.Interaction, limit: app_commands.Range[int, 1, 25] = 15):
        rows = await self.bot.db.fetchall(
            'SELECT * FROM audit_log WHERE guild_id=? ORDER BY id DESC LIMIT ?',
            (interaction.guild_id, limit),
        )
        await self._send_rows(interaction, rows, '🧾 Последние действия')

    @audit.command(name='user', description='История действий, связанных с участником')
    async def user(self, interaction: discord.Interaction, member: discord.Member):
        rows = await self.bot.db.fetchall(
            '''SELECT * FROM audit_log WHERE guild_id=? AND (actor_id=? OR target_id=?)
               ORDER BY id DESC LIMIT 25''',
            (interaction.guild_id, member.id, member.id),
        )
        await self._send_rows(interaction, rows, f'🧾 Аудит: {member}')


async def setup(bot):
    await bot.add_cog(Audit(bot))
