import discord
from discord.ext import commands
from discord import app_commands
import os
import sys
import logging
from logging.handlers import RotatingFileHandler


# ─── .env loader (без внешних зависимостей) ──────────────────────────────────────

def load_env(path: str = '.env') -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding='utf-8') as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and value and key not in os.environ:
                    os.environ[key] = value
    except Exception as e:
        print(f'Cannot read {path}: {e}')


load_env()


# ─── Logging (с ротацией, чтобы лог не рос бесконечно) ──────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(
            'fluffy.log', maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'
        ),
    ],
)
logger = logging.getLogger('FluFFy')


class FluFFyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            description='Fluffy Vanilla — Official Discord Bot',
        )
        self.db = None

    async def setup_hook(self):
        from database import Database
        self.db = Database()
        await self.db.init()

        extensions = [
            'cogs.setup',
            'cogs.tickets',
            'cogs.ticket_extras',
            'cogs.whitelist',
            'cogs.stats',
            'cogs.embed_builder',
            'cogs.voice',
            'cogs.moderation',
            'cogs.automod',
            'cogs.maintenance',
            'cogs.audit',
            'cogs.health',
            'cogs.giveaways',
        ]
        for ext in extensions:
            try:
                await self.load_extension(ext)
                logger.info(f'Loaded cog: {ext}')
            except Exception as e:
                logger.exception(f'Failed to load {ext}: {e}')

        # Постоянные view — кнопки работают и после перезапуска бота
        from cogs.tickets import TicketPanelView, TicketControlView
        from cogs.whitelist import WhitelistReviewView, WhitelistPanelView
        from cogs.voice import VoiceControlView
        self.add_view(TicketPanelView())
        self.add_view(TicketControlView())
        self.add_view(WhitelistReviewView())
        self.add_view(WhitelistPanelView())
        self.add_view(VoiceControlView())
        from cogs.ticket_extras import TicketRatingView, TicketClaimView
        self.add_view(TicketRatingView())
        self.add_view(TicketClaimView())
        from cogs.giveaways import GiveawayJoinView
        self.add_view(GiveawayJoinView())

        await self.tree.sync()
        logger.info('Slash commands synced globally.')

    async def on_ready(self):
        logger.info(f'Logged in as {self.user} (ID: {self.user.id})')
        expected = os.environ.get('CLIENT_ID')
        if expected and str(self.user.id) != str(expected).strip():
            logger.warning(
                'CLIENT_ID in .env (' + str(expected) + ') does not match the logged-in bot ('
                + str(self.user.id) + '). Probably the wrong DISCORD_TOKEN is used.'
            )
        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name='Fluffy Vanilla 🌸'
            )
        )

        # Быстрая синхронизация команд на серверах — только по флагу FAST_SYNC=1 в .env
        # (глобальный sync уже сделан в setup_hook; постоянный двойной sync — это rate limit)
        if os.environ.get('FAST_SYNC') == '1':
            for guild in self.guilds:
                try:
                    self.tree.copy_global_to(guild=guild)
                    await self.tree.sync(guild=guild)
                except Exception as e:
                    logger.error(f'Failed to sync guild {guild.id}: {e}')
            logger.info('FAST_SYNC: commands synced to all active guilds.')

    async def close(self):
        """Корректное завершение: закрываем базу, чтобы не терять данные из WAL."""
        try:
            if self.db and self.db.db:
                try:
                    await self.db.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                    await self.db.db.commit()
                except Exception:
                    pass
                await self.db.close()
                logger.info('Database connection closed.')
        except Exception as e:
            logger.error(f'Error while closing database: {e}')
        await super().close()


bot = FluFFyBot()


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):
    from utils.embeds import error_embed
    msg = None
    if isinstance(error, app_commands.MissingPermissions):
        msg = 'Недостаточно прав для выполнения этой команды.'
    elif isinstance(error, app_commands.BotMissingPermissions):
        msg = f'Боту не хватает прав: `{", ".join(error.missing_permissions)}`'
    elif isinstance(error, app_commands.CommandOnCooldown):
        msg = f'Команда на кулдауне. Попробуйте через `{error.retry_after:.1f}` сек.'
    elif isinstance(error, app_commands.NoPrivateMessage):
        msg = 'Эта команда недоступна в личных сообщениях.'
    else:
        logger.error(f'Unhandled command error: {error}', exc_info=True)
        msg = 'Произошла внутренняя ошибка. Проверьте логи.'
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=error_embed(msg), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed(msg), ephemeral=True)
    except Exception:
        pass


if __name__ == '__main__':
    token = os.environ.get('DISCORD_TOKEN')
    if not token and os.path.exists('.token'):
        # Старый способ — поддерживается, но не рекомендуется
        token = open('.token').read().strip()
        logger.warning('Токен взят из файла .token — перенеси его в .env (DISCORD_TOKEN=...)')
    if not token:
        logger.error('Токен не найден! Укажи DISCORD_TOKEN в файле .env')
        sys.exit(1)
    bot.run(token, log_handler=None)
