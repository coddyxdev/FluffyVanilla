"""
cogs/setup.py  —  Все команды настройки бота Fluffy Vanilla

/setup view                 — показать все текущие настройки
/setup check                — самопроверка: каналы, роли, права, связь с MC-сервером
/setup log                  — канал логов модерации
/setup ticket ...           — настройки тикетов
/setup whitelist ...        — настройки белого списка
/setup stats_plugin         — подключение к плагину Minecraft
/setup stats_channels       — голосовые каналы со статистикой
"""
import discord
from discord import app_commands
from discord.ext import commands
import logging

from utils.embeds import ok, err, settings_embed, PINK, SUCCESS, ERROR, WARNING
from utils.emojis import BOT, VERIFIED, HUH

logger = logging.getLogger('FluFFy.Setup')


class Setup(commands.Cog):
    setup_group = app_commands.Group(
        name='setup',
        description='Настройка бота',
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    ticket_setup = app_commands.Group(
        name='ticket', description='Настройки тикетов', parent=setup_group
    )
    wl_setup = app_commands.Group(
        name='whitelist', description='Настройки белого списка', parent=setup_group
    )

    def __init__(self, bot):
        self.bot = bot

    # ── /setup view ─────────────────────────────────────────────────────

    @setup_group.command(name='view', description='Показать текущие настройки бота')
    async def view(self, interaction: discord.Interaction):
        s = await self.bot.db.get_guild(interaction.guild_id)
        if not s:
            await self.bot.db.ensure_guild(interaction.guild_id)
            s = await self.bot.db.get_guild(interaction.guild_id)
        await interaction.response.send_message(
            embed=settings_embed(interaction.guild, s), ephemeral=True
        )

    # ── /setup check ───────────────────────────────────────────────────

    @setup_group.command(name='check',
                         description='Проверить, всё ли настроено правильно и хватает ли прав')
    async def check(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        s = await self.bot.db.get_guild(guild.id)
        if not s:
            await self.bot.db.ensure_guild(guild.id)
            s = await self.bot.db.get_guild(guild.id)

        def val(key):
            try:
                return s[key]
            except Exception:
                return None

        lines = []
        problems = 0

        def check_channel(key, label, required=True):
            nonlocal problems
            cid = val(key)
            if not cid:
                if required:
                    problems += 1
                    lines.append(f'❌ {label}: не настроен')
                else:
                    lines.append(f'➖ {label}: не настроен (необязательно)')
                return
            channel = guild.get_channel(cid)
            if not channel:
                problems += 1
                lines.append(f'❌ {label}: канал удалён или недоступен (ID `{cid}`)')
                return
            perms = channel.permissions_for(guild.me)
            if isinstance(channel, discord.TextChannel) and not (perms.view_channel and perms.send_messages):
                problems += 1
                lines.append(f'⚠️ {label}: {channel.mention} — бот не может туда писать')
                return
            lines.append(f'✅ {label}: {channel.mention}')

        check_channel('wl_info_channel_id', 'Канал с панелью заявок')
        check_channel('wl_category_id', 'Категория заявок')
        check_channel('wl_notify_channel_id', 'Канал уведомлений', required=False)
        check_channel('wl_logs_channel_id', 'Архив/лог заявок', required=False)
        check_channel('ticket_category_id', 'Категория тикетов', required=False)

        # Роль белого списка
        role_id = val('wl_role_id')
        if not role_id:
            problems += 1
            lines.append('❌ Роль белого списка: не настроена')
        else:
            role = guild.get_role(role_id)
            if not role:
                problems += 1
                lines.append(f'❌ Роль белого списка: удалена (ID `{role_id}`)')
            elif role >= guild.me.top_role:
                problems += 1
                lines.append(
                    f'❌ Роль {role.mention} выше роли бота — бот не сможет её выдавать. '
                    'Подними роль бота выше в настройках сервера.'
                )
            else:
                lines.append(f'✅ Роль белого списка: {role.mention}')

        # Глобальные права бота
        me_perms = guild.me.guild_permissions
        needed = {
            'manage_channels': 'Управлять каналами (создание тикетов)',
            'manage_roles': 'Управлять ролями (выдача белого списка)',
            'manage_messages': 'Управлять сообщениями',
        }
        for perm, label in needed.items():
            if getattr(me_perms, perm, False):
                lines.append(f'✅ Право: {label}')
            else:
                problems += 1
                lines.append(f'❌ Нет права: {label}')

        # Плагин Minecraft
        mc_host, mc_port, mc_key = val('mc_host'), val('mc_port'), val('mc_api_key')
        if not (mc_host and mc_port and mc_key):
            lines.append('➖ Minecraft-плагин: не настроен (`/setup stats_plugin`)')
        else:
            import aiohttp
            url = 'http://' + str(mc_host) + ':' + str(mc_port) + '/stats'
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers={'X-API-Key': mc_key}, timeout=5) as resp:
                        if resp.status < 400:
                            lines.append(f'✅ Minecraft-плагин: отвечает (`{mc_host}:{mc_port}`)')
                        else:
                            problems += 1
                            lines.append(f'⚠️ Minecraft-плагин: ответ `{resp.status}` — проверь API-ключ')
            except Exception:
                problems += 1
                lines.append(f'❌ Minecraft-плагин: недоступен (`{mc_host}:{mc_port}`)')

        color = SUCCESS if problems == 0 else (WARNING if problems < 3 else ERROR)
        title = '✅ Всё настроено правильно' if problems == 0 else f'Найдено проблем: {problems}'
        mood = VERIFIED if problems == 0 else HUH
        e = discord.Embed(title=title, description=f'{BOT} {mood} Проверка конфигурации\n\n' + '\n'.join(lines), color=color)
        e.set_footer(text='Fluffy Vanilla  •  /setup check')
        await interaction.followup.send(embed=e, ephemeral=True)

    # ── /setup log ────────────────────────────────────────────────────

    @setup_group.command(name='log', description='Установить канал для логов модерации')
    @app_commands.describe(channel='Текстовый канал для логов')
    async def log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await self.bot.db.set(interaction.guild_id, log_channel_id=channel.id)
        await interaction.response.send_message(
            embed=ok(f'Лог-канал установлен: {channel.mention}'), ephemeral=True
        )

    # ─── Тикеты ───────────────────────────────────────────────────

    @ticket_setup.command(name='category', description='Категория для обычных тикетов')
    @app_commands.describe(category='Категория каналов')
    async def t_category(self, interaction: discord.Interaction, category: discord.CategoryChannel):
        await self.bot.db.set(interaction.guild_id, ticket_category_id=category.id)
        await interaction.response.send_message(
            embed=ok(f'Категория обычных тикетов: **{category.name}**'), ephemeral=True
        )

    @ticket_setup.command(name='tech_category', description='Категория для технических тикетов')
    @app_commands.describe(category='Категория каналов')
    async def t_tech_category(self, interaction: discord.Interaction, category: discord.CategoryChannel):
        await self.bot.db.set(interaction.guild_id, tech_ticket_category_id=category.id)
        await interaction.response.send_message(
            embed=ok(f'Категория тех. тикетов: **{category.name}**'), ephemeral=True
        )

    @ticket_setup.command(name='log_channel', description='Канал для логов тикетов')
    @app_commands.describe(channel='Текстовый канал')
    async def t_log(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await self.bot.db.set(interaction.guild_id, ticket_log_channel_id=channel.id)
        await interaction.response.send_message(
            embed=ok(f'Лог тикетов: {channel.mention}'), ephemeral=True
        )

    # ─── Белый список ─────────────────────────────────────────────

    @wl_setup.command(name='role', description='Роль, которая выдаётся после одобрения заявки')
    @app_commands.describe(role='Роль белого списка')
    async def wl_role(self, interaction: discord.Interaction, role: discord.Role):
        await self.bot.db.set(interaction.guild_id, wl_role_id=role.id)
        note = ''
        if role >= interaction.guild.me.top_role:
            note = ('\n\n⚠️ Эта роль выше роли бота — бот не сможет её выдавать. '
                    'Перетащи роль бота выше в настройках сервера.')
        await interaction.response.send_message(
            embed=ok(f'Роль белого списка: {role.mention}{note}'), ephemeral=True
        )

    @wl_setup.command(name='app_category', description='Категория, в которой создаются каналы заявок')
    @app_commands.describe(category='Категория каналов')
    async def wl_app_cat(self, interaction: discord.Interaction, category: discord.CategoryChannel):
        await self.bot.db.set(interaction.guild_id, wl_category_id=category.id)
        await interaction.response.send_message(
            embed=ok(f'Категория для заявок: **{category.name}**'), ephemeral=True
        )

    @wl_setup.command(name='notify_channel', description='Канал уведомлений о новых заявках (для админов)')
    @app_commands.describe(channel='Текстовый канал')
    async def wl_notify(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await self.bot.db.set(interaction.guild_id, wl_notify_channel_id=channel.id)
        await interaction.response.send_message(
            embed=ok(f'Канал уведомлений: {channel.mention}'), ephemeral=True
        )

    @wl_setup.command(name='logs_channel', description='Канал-архив: туда сохраняются анкеты и решения')
    @app_commands.describe(channel='Текстовый канал')
    async def wl_logs(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await self.bot.db.set(interaction.guild_id, wl_logs_channel_id=channel.id)
        await interaction.response.send_message(
            embed=ok(f'Архив заявок: {channel.mention}'), ephemeral=True
        )

    @wl_setup.command(name='info_channel', description='Канал, где висит кнопка подачи заявки')
    @app_commands.describe(channel='Текстовый канал')
    async def wl_info(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await self.bot.db.set(interaction.guild_id, wl_info_channel_id=channel.id)
        await interaction.response.send_message(
            embed=ok(
                f'Канал белого списка: {channel.mention}\n'
                'Теперь отправь туда кнопку: `/whitelist panel`'
            ),
            ephemeral=True,
        )

    # ─── Minecraft ───────────────────────────────────────────────────

    @setup_group.command(name='stats_plugin',
                         description='Подключение к плагину Minecraft (статистика и выдача вайтлиста)')
    @app_commands.describe(host='IP адрес сервера', port='Порт плагина (например 31295)',
                           key='API-ключ из config.yml плагина')
    async def srv_stats(self, interaction: discord.Interaction, host: str, port: int, key: str):
        await self.bot.db.set(interaction.guild_id, mc_host=host, mc_port=port, mc_api_key=key)
        await interaction.response.send_message(
            embed=ok(
                f'Подключение к плагину сохранено: `{host}:{port}`\n'
                'Проверь связь командой `/setup check`'
            ),
            ephemeral=True,
        )

    @setup_group.command(name='stats_channels', description='Голосовые каналы для статистики сервера')
    @app_commands.describe(online='Канал с онлайном', builds='Канал с TPS строек', farms='Канал с TPS ферм')
    async def stats_channels(self, interaction: discord.Interaction, online: discord.VoiceChannel,
                             builds: discord.VoiceChannel, farms: discord.VoiceChannel):
        await self.bot.db.set(
            interaction.guild_id,
            online_channel_id=online.id,
            tps_builds_channel_id=builds.id,
            tps_farms_channel_id=farms.id,
        )
        await interaction.response.send_message(
            embed=ok('Каналы статистики установлены! Бот обновит их в течение минуты.'), ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(Setup(bot))
