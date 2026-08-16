import os
import time

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import SUCCESS, WARNING, ERROR
from utils.emojis import BOT, DIAMOND, VERIFIED, HUH


class Health(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='health', description='Полная диагностика бота и конфигурации')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def health(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        settings = await self.bot.db.get_guild(guild.id)
        lines, problems = [], 0

        try:
            started = time.perf_counter()
            row = await self.bot.db.fetchone('PRAGMA quick_check')
            db_ms = (time.perf_counter() - started) * 1000
            db_ok = row and str(row[0]).lower() == 'ok'
            version = await self.bot.db.schema_version()
            lines.append(f'{"✅" if db_ok else "❌"} SQLite: {row[0] if row else "нет ответа"} · v{version} · {db_ms:.1f} мс')
            problems += 0 if db_ok else 1
        except Exception as exc:
            lines.append(f'❌ SQLite: `{type(exc).__name__}`')
            problems += 1

        db_size = os.path.getsize('fluffy.db') if os.path.exists('fluffy.db') else 0
        wal_size = os.path.getsize('fluffy.db-wal') if os.path.exists('fluffy.db-wal') else 0
        lines.append(f'{"⚠️" if wal_size > 50 * 1024 * 1024 else "✅"} Файлы БД: {db_size/1024:.0f} КБ · WAL {wal_size/1024:.0f} КБ')
        problems += 1 if wal_size > 50 * 1024 * 1024 else 0

        expected = os.environ.get('CLIENT_ID')
        token_ok = bool(os.environ.get('DISCORD_TOKEN'))
        client_ok = not expected or expected.strip() == str(self.bot.user.id)
        lines.append(f'{"✅" if token_ok and client_ok else "❌"} Секреты: токен {"есть" if token_ok else "отсутствует"}, CLIENT_ID {"совпадает" if client_ok else "не совпадает"}')
        problems += 0 if token_ok and client_ok else 1

        required = ('view_channel', 'send_messages', 'embed_links', 'manage_channels', 'manage_roles', 'manage_messages')
        missing = [name for name in required if not getattr(guild.me.guild_permissions, name, False)]
        lines.append(('✅ Права Discord: достаточно' if not missing else '❌ Не хватает прав: `' + '`, `'.join(missing) + '`'))
        problems += len(missing)

        def value(key):
            try:
                return settings[key] if settings else None
            except Exception:
                return None

        checks = {
            'Категория тикетов': value('ticket_category_id'),
            'Роль whitelist': value('wl_role_id'),
            'Категория заявок': value('wl_category_id'),
        }
        for label, entity_id in checks.items():
            if not entity_id:
                lines.append(f'➖ {label}: не настроено')
            elif guild.get_channel(int(entity_id)) or guild.get_role(int(entity_id)):
                lines.append(f'✅ {label}: найдено')
            else:
                lines.append(f'❌ {label}: объект `{entity_id}` удалён')
                problems += 1

        host, port, key = value('mc_host'), value('mc_port'), value('mc_api_key')
        if host and port and key:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f'http://{host}:{port}/stats', headers={'X-API-Key': str(key)},
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as response:
                        lines.append(f'{"✅" if response.status < 400 else "❌"} Minecraft API: HTTP {response.status}')
                        problems += 0 if response.status < 400 else 1
            except Exception as exc:
                lines.append(f'❌ Minecraft API: {type(exc).__name__}')
                problems += 1
        else:
            lines.append('➖ Minecraft API: не настроен')

        color = SUCCESS if problems == 0 else (WARNING if problems < 3 else ERROR)
        title = '✅ Система исправна' if problems == 0 else f'⚠️ Найдено проблем: {problems}'
        mood = VERIFIED if problems == 0 else HUH
        embed = discord.Embed(title=title, description=f'{BOT} {mood} Диагностика\n\n' + '\n'.join(lines), color=color)
        embed.add_field(name='Discord latency', value=f'{DIAMOND} {self.bot.latency * 1000:.0f} мс')
        embed.set_footer(text='Fluffy Vanilla • /health')
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Health(bot))
