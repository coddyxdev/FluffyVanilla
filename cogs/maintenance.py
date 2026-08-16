"""
cogs/maintenance.py  —  Обслуживание бота: бэкапы базы, чистка WAL, диагностика.

/backup now   — сделать бэкап базы прямо сейчас
/backup list  — показать последние бэкапы
/ping         — состояние бота (пинг, аптайм, база)
"""
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timezone
import os
import shutil
import logging

from utils.embeds import ok, err, info, PINK, SUCCESS
from utils.emojis import BOT, DIAMOND

logger = logging.getLogger('FluFFy.Maintenance')

DB_PATH = 'fluffy.db'
BACKUP_DIR = 'backups'
KEEP_BACKUPS = 14  # сколько последних копий хранить


class Maintenance(commands.Cog):
    backup_group = app_commands.Group(
        name='backup',
        description='Бэкапы базы данных',
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot):
        self.bot = bot
        self.started_at = datetime.now(timezone.utc)
        self._daily_backup.start()

    def cog_unload(self):
        self._daily_backup.cancel()

    # ── Бэкап ──────────────────────────────────────────────────────────

    async def _make_backup(self) -> str:
        """Сбрасывает WAL в основной файл и копирует базу. Возвращает путь копии."""
        os.makedirs(BACKUP_DIR, exist_ok=True)

        db = self.bot.db
        if db and db.db:
            for mode in ('TRUNCATE', 'PASSIVE'):
                try:
                    await db.db.execute('PRAGMA wal_checkpoint(' + mode + ')')
                    await db.db.commit()
                    break
                except Exception as e:
                    logger.debug('WAL checkpoint ' + mode + ' skipped: ' + str(e))

        stamp = datetime.now().strftime('%Y-%m-%d_%H-%M')
        dest = os.path.join(BACKUP_DIR, f'fluffy_{stamp}.db')
        shutil.copy2(DB_PATH, dest)

        # Чистим старые копии
        files = sorted(
            (f for f in os.listdir(BACKUP_DIR) if f.startswith('fluffy_') and f.endswith('.db')),
            reverse=True,
        )
        for old in files[KEEP_BACKUPS:]:
            try:
                os.remove(os.path.join(BACKUP_DIR, old))
            except OSError:
                pass

        logger.info(f'Database backup created: {dest}')
        return dest

    @tasks.loop(hours=24)
    async def _daily_backup(self):
        try:
            await self._make_backup()
        except Exception as e:
            logger.error(f'Daily backup failed: {e}')

    @_daily_backup.before_loop
    async def _before_backup(self):
        await self.bot.wait_until_ready()

    # ── Команды ──────────────────────────────────────────────────────

    @backup_group.command(name='now', description='Сделать резервную копию базы прямо сейчас')
    async def backup_now(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            path = await self._make_backup()
            size_kb = os.path.getsize(path) / 1024
            await interaction.followup.send(
                embed=ok(f'Бэкап создан: `{os.path.basename(path)}` ({size_kb:.1f} КБ)'),
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(embed=err(f'Не удалось сделать бэкап: `{e}`'), ephemeral=True)

    @backup_group.command(name='list', description='Показать последние резервные копии')
    async def backup_list(self, interaction: discord.Interaction):
        if not os.path.isdir(BACKUP_DIR):
            await interaction.response.send_message(
                embed=info('Бэкапов пока нет. Создай первый: `/backup now`'), ephemeral=True,
            )
            return
        files = sorted(
            (f for f in os.listdir(BACKUP_DIR) if f.endswith('.db')), reverse=True
        )[:10]
        if not files:
            await interaction.response.send_message(
                embed=info('Бэкапов пока нет.'), ephemeral=True,
            )
            return
        lines = []
        for f in files:
            size_kb = os.path.getsize(os.path.join(BACKUP_DIR, f)) / 1024
            lines.append(f'• `{f}` — {size_kb:.1f} КБ')
        await interaction.response.send_message(
            embed=info('\n'.join(lines), title='💾 Последние бэкапы'), ephemeral=True,
        )

    @app_commands.command(name='ping', description='Состояние бота: пинг, аптайм, база')
    async def ping(self, interaction: discord.Interaction):
        delta = datetime.now(timezone.utc) - self.started_at
        hours, rem = divmod(int(delta.total_seconds()), 3600)
        minutes = rem // 60

        db_ok = bool(self.bot.db and self.bot.db.db)
        db_size = os.path.getsize(DB_PATH) / 1024 if os.path.exists(DB_PATH) else 0

        e = discord.Embed(title='🏓 Состояние бота', description=f'{BOT} Диагностика Fluffy Vanilla', color=PINK)
        e.add_field(name='Пинг', value=f'{self.bot.latency * 1000:.0f} мс', inline=True)
        e.add_field(name='Аптайм', value=f'{hours} ч {minutes} мин', inline=True)
        e.add_field(name='Серверов', value=str(len(self.bot.guilds)), inline=True)
        e.add_field(
            name='База данных',
            value=f'{DIAMOND} ' + ('✅ подключена' if db_ok else '❌ нет соединения') + f' ({db_size:.0f} КБ)',
            inline=False,
        )
        await interaction.response.send_message(embed=e, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Maintenance(bot))
