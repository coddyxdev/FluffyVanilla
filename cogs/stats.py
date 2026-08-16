"""
cogs/stats.py  —  Live Minecraft server stats in voice channels
Fetches JSON from FluffyVanillaStats plugin (HTTP API):
  GET http://HOST:PORT/stats  →  {"online":42,"max":100,"tps_builds":19.8,"tps_farms":18.5}
Updates voice channels every N seconds (configurable).
"""
import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiohttp
import asyncio
import logging

import json
import re
from utils.embeds import ok, err, info, PINK
from utils.emojis import DIAMOND, TROPHY, STAR, MEMBER, ARROW_BLUE
from cogs.tickets import create_mc_ticket

logger = logging.getLogger('FluFFy.Stats')

DEFAULT_INTERVAL = 300  # seconds (Discord allows ~2 channel renames per 10 min)
RETRY_AFTER_FAIL = 30   # seconds before retrying after error


class Stats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self.updater.start()
        self.events_updater.start()

    def cog_unload(self):
        self.updater.cancel()
        self.events_updater.cancel()
        if self._session and not self._session.closed:
            asyncio.create_task(self._session.close())

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8)
            )
        return self._session

    async def fetch_stats(self, host: str, port: int, api_key: str | None = None) -> dict | None:
        url = 'http' + '://' + str(host) + ':' + str(port) + '/stats'
        headers = {}
        if api_key:
            headers['X-API-Key'] = api_key
        try:
            session = await self._get_session()
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    raw = await resp.text()
                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError:
                        # Some JVM locales format decimals with a comma, which is invalid JSON.
                        normalized = re.sub(r'(?<=\d),(?=\d)', '.', raw)
                        return json.loads(normalized)
                logger.warning(f'Stats API returned {resp.status}')
                return None
        except aiohttp.ClientConnectorError:
            logger.debug(f'Cannot reach MC stats API at {host}:{port}')
            return None
        except asyncio.TimeoutError:
            logger.debug(f'Timeout fetching MC stats from {host}:{port}')
            return None
        except Exception as e:
            logger.error(f'Error fetching stats: {e}')
            return None

    @tasks.loop(seconds=DEFAULT_INTERVAL)
    async def updater(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            try:
                await self._update_guild(guild)
            except Exception as e:
                logger.error(f'Stats update failed for guild {guild.id}: {e}')

    async def _update_guild(self, guild: discord.Guild):
        db = self.bot.db
        s = await db.get_guild(guild.id)
        if not s:
            return

        # Check all three channels are set
        if not any([s['online_channel_id'], s['tps_builds_channel_id'], s['tps_farms_channel_id']]):
            return

        interval = s['stats_interval'] or DEFAULT_INTERVAL
        # Adjust task interval dynamically per the smallest configured interval
        # (simple: use the guild interval for this task — works fine for single guild)
        if self.updater.seconds != interval:
            self.updater.change_interval(seconds=interval)

        data = await self.fetch_stats(s['mc_host'], s['mc_port'], s['mc_api_key'])

        if data:
            online      = data.get('online', '?')
            max_players = data.get('max', '?')
            tps_builds  = data.get('tps_builds', data.get('tps', '?'))
            tps_farms   = data.get('tps_farms', data.get('tps', '?'))
        else:
            online = max_players = tps_builds = tps_farms = '...'

        mapping = {
            s['online_channel_id']:     f'Онлайн: {online}/{max_players}',
            s['tps_builds_channel_id']: f'TPS построек: {tps_builds}',
            s['tps_farms_channel_id']:  f'TPS ферм: {tps_farms}',
        }

        for ch_id, name in mapping.items():
            if not ch_id:
                continue
            ch = guild.get_channel(ch_id)
            if ch and ch.name != name:
                try:
                    await ch.edit(name=name, reason='MC stats update')
                except discord.Forbidden:
                    logger.warning(f'Cannot edit channel {ch_id} in {guild} — missing perms')
                except discord.RateLimited as e:
                    logger.debug(f'Rate limited editing channel, sleeping {e.retry_after}s')
                    await asyncio.sleep(e.retry_after)
                except Exception as e:
                    logger.error(f'Error editing channel {ch_id}: {e}')

    @updater.before_loop
    async def before_updater(self):
        await self.bot.wait_until_ready()
        logger.info('Stats updater started.')

    @tasks.loop(seconds=3)
    async def events_updater(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            try:
                db = self.bot.db
                s = await db.get_guild(guild.id)
                if not s or not s['mc_host']: continue
                
                url = f'http://{s["mc_host"]}:{s["mc_port"]}/events'
                headers = {}
                if s['mc_api_key']: headers['X-API-Key'] = s['mc_api_key']
                
                session = await self._get_session()
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        events = await resp.json(content_type=None)
                        if events:
                            for ev in events:
                                await self.handle_event(guild, s, ev)
            except asyncio.TimeoutError:
                pass
            except aiohttp.ClientConnectorError:
                pass  # server offline, silently skip
            except Exception as e:
                logger.debug(f'Events update failed for guild {guild.id}: {e}')

    async def handle_event(self, guild, settings, ev):
        if not ev: return
        s = dict(settings) if not isinstance(settings, dict) else settings
        ev_type = ev.get('type')
        if ev_type == 'chat':
            chat_id = s.get('mc_chat_channel_id')
            if chat_id:
                ch = guild.get_channel(chat_id)
                if ch:
                    p = ev.get('player', 'Unknown')
                    m = ev.get('message', '')
                    m = discord.utils.escape_mentions(m)
                    await ch.send(f'**{p}**: {m}')
        elif ev_type == 'help':
            p = ev.get('player', 'Unknown')
            r = ev.get('reason', 'Без причины')
            x = ev.get('x', 0)
            y = ev.get('y', 0)
            z = ev.get('z', 0)
            w = ev.get('world', 'world')
            await create_mc_ticket(self.bot, guild, p, r, x, y, z, w)

    @events_updater.before_loop
    async def before_events_updater(self):
        await self.bot.wait_until_ready()
        logger.info('Events updater started.')

    # ─── Commands ─────────────────────────────────────────────────────────────

    stats_group = app_commands.Group(
        name='stats',
        description='Управление статистикой Minecraft',
        guild_only=True,
    )

    @stats_group.command(name='refresh', description='Немедленно обновить статистику')
    @app_commands.default_permissions(manage_guild=True)
    async def refresh(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self._update_guild(interaction.guild)
        await interaction.followup.send(embed=ok('Статистика обновлена.'), ephemeral=True)

    @stats_group.command(name='check', description='Проверить подключение к серверу Minecraft')
    @app_commands.default_permissions(manage_guild=True)
    async def check(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        s = await self.bot.db.get_guild(interaction.guild_id)
        if not s:
            await interaction.followup.send(embed=err('Бот не настроен.'), ephemeral=True)
            return
        data = await self.fetch_stats(s['mc_host'], s['mc_port'], s['mc_api_key'])
        if data:
            e = discord.Embed(title='Статистика MC сервера', description=f'{DIAMOND} Данные в реальном времени', color=PINK)
            e.add_field(name='Онлайн', value=f'`{data.get("online","?")} / {data.get("max","?")}`', inline=True)
            e.add_field(name='TPS построек', value=f'`{data.get("tps_builds", data.get("tps","?"))}`', inline=True)
            e.add_field(name='TPS ферм',     value=f'`{data.get("tps_farms",  data.get("tps","?"))}`', inline=True)
            e.add_field(name='Сервер',       value=f'`{s["mc_host"]}:{s["mc_port"]}`', inline=False)
            e.set_footer(text='Fluffy Vanilla')
            await interaction.followup.send(embed=e, ephemeral=True)
        else:
            await interaction.followup.send(
                embed=err(
                    f'Не удалось подключиться к `{s["mc_host"]}:{s["mc_port"]}`.\n'
                    'Убедитесь, что плагин FluffyVanillaStats установлен и запущен.'
                ),
                ephemeral=True,
            )

    @stats_group.command(name='top-playtime', description='ТОП по часам игры')
    async def top_playtime(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        s = await self.bot.db.get_guild(interaction.guild_id)
        if not s:
            await interaction.followup.send(embed=err('Бот не настроен.'))
            return
            
        url = f'http://{s["mc_host"]}:{s["mc_port"]}/top-playtime'
        headers = {}
        if s['mc_api_key']: headers['X-API-Key'] = s['mc_api_key']
        try:
            session = await self._get_session()
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    e = discord.Embed(
                        title='ТОП-10 по времени на сервере', 
                        description=f'{TROPHY} Самые активные игроки Fluffy Vanilla:\n\n',
                        color=0xFFD700
                    )
                    desc = ""
                    
                    for i, p in enumerate(data[:10], 1):
                        hours = p['hours']
                        desc += f"{STAR} **{i}. {p['player']}** — `{hours} ч.`\n"
                    
                    e.description += desc or "Пока нет данных :("
                    e.set_thumbnail(url='https://mc-heads.net/avatar/MHF_Alex/100')
                    await interaction.followup.send(embed=e)
                else:
                    await interaction.followup.send(embed=err('Ошибка при получении данных.'))
        except Exception as e:
            await interaction.followup.send(embed=err('Не удалось подключиться к серверу.'))

    @stats_group.command(name='achievements', description='Достижения игрока')
    @app_commands.describe(nick='Никнейм игрока')
    async def achievements(self, interaction: discord.Interaction, nick: str):
        # Delegate to player_profile logic directly
        await interaction.response.defer(ephemeral=False)
        s = await self.bot.db.get_guild(interaction.guild_id)
        if not s:
            await interaction.followup.send(embed=err('Бот не настроен.'))
            return
        url = f'http://{s["mc_host"]}:{s["mc_port"]}/player?name={nick}'
        headers = {}
        if s['mc_api_key']: headers['X-API-Key'] = s['mc_api_key']
        try:
            session = await self._get_session()
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    d = await resp.json(content_type=None)
                    e = discord.Embed(title=f'Достижения игрока {d["player"]}', description=f'{TROPHY} Прогресс игрока', color=PINK)
                    e.add_field(name='Сюжетных достижений', value=f'**{d["advancements"]}** выполнено', inline=False)
                    skin_name = d.get("skin", d["player"])
                    e.set_thumbnail(url='https' + '://' + 'mc-heads.net/body/' + str(skin_name) + '/128')
                    await interaction.followup.send(embed=e)
                elif resp.status == 404:
                    await interaction.followup.send(embed=err(f'Игрок {nick} не найден.'))
                else:
                    await interaction.followup.send(embed=err('Ошибка при получении данных.'))
        except Exception:
            await interaction.followup.send(embed=err('Не удалось подключиться к серверу.'))

    @app_commands.command(name='player-profile', description='Персональная статистика игрока')
    @app_commands.describe(nick='Никнейм игрока')
    async def player_profile(self, interaction: discord.Interaction, nick: str):
        await interaction.response.defer(ephemeral=False)
        s = await self.bot.db.get_guild(interaction.guild_id)
        if not s:
            await interaction.followup.send(embed=err('Бот не настроен.'))
            return
            
        url = f'http://{s["mc_host"]}:{s["mc_port"]}/player?name={nick}'
        headers = {}
        if s['mc_api_key']: headers['X-API-Key'] = s['mc_api_key']
        try:
            session = await self._get_session()
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    d = await resp.json(content_type=None)
                    e = discord.Embed(
                        title=f'Профиль игрока {d["player"]}', 
                        description=f'{MEMBER} Подробная статистика выживания на Fluffy Vanilla',
                        color=PINK
                    )
                    
                    e.add_field(name='Время в игре', value=f'**{d["hours"]}** ч.', inline=True)
                    e.add_field(name='Убийства мобов', value=f'**{d["kills"]}**', inline=True)
                    e.add_field(name='Смерти', value=f'**{d["deaths"]}**', inline=True)
                    
                    # Highlight achievements
                    adv = d["advancements"]
                    adv_str = f"**{adv}**" if adv > 0 else "0"
                    e.add_field(name='Сюжетных достижений', value=f'{adv_str} выполнено', inline=False)
                    
                    skin_name = d.get("skin", d["player"])
                    e.set_thumbnail(url='https' + '://' + 'mc-heads.net/body/' + str(skin_name) + '/128')
                    e.set_footer(text='Fluffy Vanilla • Статистика обновляется в реальном времени')
                    
                    await interaction.followup.send(embed=e)
                elif resp.status == 404:
                    await interaction.followup.send(embed=err(f'Игрок {nick} не найден.'))
                else:
                    await interaction.followup.send(embed=err('Ошибка при получении данных.'))
        except Exception as e:
            await interaction.followup.send(embed=err('Не удалось подключиться к серверу.'))


async def setup(bot):
    cog = Stats(bot)
    await bot.add_cog(cog)

