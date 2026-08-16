"""
cogs/voice.py — Temporary Voice Channel System for Fluffy Vanilla

Flow:
  1. Admin runs /voice setup → picks a "create" voice channel + category
  2. User joins that channel → bot creates a private VC, moves user, posts panel
  3. Panel auto-updates after every action (rename, limit, lock, etc.)
  4. When everyone leaves → channel is deleted
"""
import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import logging
from datetime import datetime, timezone

from utils.embeds import PINK, SUCCESS, ERROR, ok, err, info

logger = logging.getLogger('FluFFy.Voice')

# ── State ────────────────────────────────────────────────────────────────────
_owners: dict[int, int] = {}           # channel_id → owner_id
_bans: dict[int, set[int]] = {}       # channel_id → {banned_user_ids}
_panels: dict[int, discord.Message] = {}  # channel_id → panel message


def _is_owner(ch_id: int, uid: int) -> bool:
    return _owners.get(ch_id) == uid


# ── Panel embed builder ─────────────────────────────────────────────────────

def _build_panel(vc: discord.VoiceChannel, owner: discord.Member) -> discord.Embed:
    ow = vc.overwrites_for(vc.guild.default_role)
    locked = ow.connect is False
    hidden = ow.view_channel is False

    status_parts = []
    if locked:
        status_parts.append('Закрыт')
    else:
        status_parts.append('Открыт')
    if hidden:
        status_parts.append('Скрыт')

    members_str = ', '.join(m.display_name for m in vc.members) or '—'
    limit_str = str(vc.user_limit) if vc.user_limit else '∞'
    banned = _bans.get(vc.id, set())

    e = discord.Embed(
        title=f'🎙️ {vc.name}',
        description='Управляй каналом с помощью кнопок ниже.',
        color=PINK,
    )
    e.add_field(name='👑 Владелец', value=owner.mention, inline=True)
    e.add_field(name='👥 Участники', value=f'{len(vc.members)}/{limit_str}', inline=True)
    e.add_field(name='📌 Статус', value=' · '.join(status_parts), inline=True)
    e.add_field(name='🎧 В канале', value=members_str, inline=False)
    if banned:
        e.add_field(name='🚫 Забанены', value=f'{len(banned)} чел.', inline=True)
    e.set_thumbnail(url=owner.display_avatar.url)
    e.set_footer(text='Fluffy Vanilla Voice')
    e.timestamp = datetime.now(timezone.utc)
    return e


async def _refresh_panel(vc: discord.VoiceChannel):
    """Edit the stored panel message with fresh data."""
    msg = _panels.get(vc.id)
    if not msg:
        return
    owner_id = _owners.get(vc.id)
    if not owner_id:
        return
    owner = vc.guild.get_member(owner_id)
    if not owner:
        return
    try:
        embed = _build_panel(vc, owner)
        await msg.edit(embed=embed)
    except (discord.NotFound, discord.Forbidden):
        _panels.pop(vc.id, None)
    except Exception as e:
        logger.debug(f'Panel refresh failed: {e}')


# ── Modals ───────────────────────────────────────────────────────────────────

class RenameModal(discord.ui.Modal, title='Переименовать канал'):
    new_name = discord.ui.TextInput(
        label='Новое название',
        placeholder='Например: Моя комната',
        max_length=100,
    )

    async def on_submit(self, interaction: discord.Interaction):
        vc = interaction.user.voice and interaction.user.voice.channel
        if not vc or not _is_owner(vc.id, interaction.user.id):
            await interaction.response.send_message(embed=err('Нет прав.'), ephemeral=True)
            return
        new_name = str(self.new_name)
        # Respond first to avoid interaction timeout
        await interaction.response.send_message(
            embed=ok(f'Канал переименован в **{new_name}**'), ephemeral=True
        )
        await vc.edit(name=new_name)
        await _refresh_panel(vc)


class LimitModal(discord.ui.Modal, title='Лимит участников'):
    limit_input = discord.ui.TextInput(
        label='Лимит (0 = без лимита)',
        placeholder='Например: 5',
        max_length=3,
    )

    async def on_submit(self, interaction: discord.Interaction):
        vc = interaction.user.voice and interaction.user.voice.channel
        if not vc or not _is_owner(vc.id, interaction.user.id):
            await interaction.response.send_message(embed=err('Нет прав.'), ephemeral=True)
            return
        try:
            lim = int(str(self.limit_input))
            if not 0 <= lim <= 99:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(embed=err('Введи число от 0 до 99.'), ephemeral=True)
            return
        txt = f'Лимит установлен: **{lim}** чел.' if lim else 'Лимит снят.'
        # Respond first to avoid interaction timeout
        await interaction.response.send_message(embed=ok(txt), ephemeral=True)
        await vc.edit(user_limit=lim)
        await _refresh_panel(vc)


# ── User Selects ─────────────────────────────────────────────────────────────

class KickSelect(discord.ui.UserSelect):
    placeholder = 'Выбери участника для кика'
    min_values = 1
    max_values = 1

    async def callback(self, interaction: discord.Interaction):
        vc = interaction.user.voice and interaction.user.voice.channel
        if not vc or not _is_owner(vc.id, interaction.user.id):
            await interaction.response.send_message(embed=err('Нет прав.'), ephemeral=True)
            return
        target: discord.Member = self.values[0]
        if target.id == interaction.user.id:
            await interaction.response.send_message(embed=err('Нельзя кикнуть себя.'), ephemeral=True)
            return
        if target.voice and target.voice.channel == vc:
            try:
                await target.move_to(None)
                await interaction.response.send_message(
                    embed=ok(f'{target.display_name} выгнан из канала.'), ephemeral=True
                )
                await _refresh_panel(vc)
            except discord.Forbidden:
                await interaction.response.send_message(embed=err('Не хватает прав.'), ephemeral=True)
        else:
            await interaction.response.send_message(embed=err('Этот участник не в канале.'), ephemeral=True)


class BanSelect(discord.ui.UserSelect):
    placeholder = 'Забанить участника в канале'
    min_values = 1
    max_values = 1

    async def callback(self, interaction: discord.Interaction):
        vc = interaction.user.voice and interaction.user.voice.channel
        if not vc or not _is_owner(vc.id, interaction.user.id):
            await interaction.response.send_message(embed=err('Нет прав.'), ephemeral=True)
            return
        target: discord.Member = self.values[0]
        if target.id == interaction.user.id:
            await interaction.response.send_message(embed=err('Нельзя забанить себя.'), ephemeral=True)
            return
        _bans.setdefault(vc.id, set()).add(target.id)
        await vc.set_permissions(target, connect=False, view_channel=False)
        if target.voice and target.voice.channel == vc:
            try:
                await target.move_to(None)
            except Exception:
                pass
        await interaction.response.send_message(
            embed=ok(f'{target.display_name} заблокирован.'), ephemeral=True
        )
        await _refresh_panel(vc)


class UnbanSelect(discord.ui.UserSelect):
    placeholder = 'Разбанить участника'
    min_values = 1
    max_values = 1

    async def callback(self, interaction: discord.Interaction):
        vc = interaction.user.voice and interaction.user.voice.channel
        if not vc or not _is_owner(vc.id, interaction.user.id):
            await interaction.response.send_message(embed=err('Нет прав.'), ephemeral=True)
            return
        target: discord.Member = self.values[0]
        _bans.get(vc.id, set()).discard(target.id)
        await vc.set_permissions(target, overwrite=None)
        await interaction.response.send_message(
            embed=ok(f'{target.display_name} разблокирован.'), ephemeral=True
        )
        await _refresh_panel(vc)


class TransferSelect(discord.ui.UserSelect):
    placeholder = 'Передать управление'
    min_values = 1
    max_values = 1

    async def callback(self, interaction: discord.Interaction):
        vc = interaction.user.voice and interaction.user.voice.channel
        if not vc or not _is_owner(vc.id, interaction.user.id):
            await interaction.response.send_message(embed=err('Нет прав.'), ephemeral=True)
            return
        target: discord.Member = self.values[0]
        if target.id == interaction.user.id:
            await interaction.response.send_message(embed=err('Это уже твой канал.'), ephemeral=True)
            return
        if not (target.voice and target.voice.channel == vc):
            await interaction.response.send_message(embed=err('Участник не в канале.'), ephemeral=True)
            return
        # Swap permissions
        _owners[vc.id] = target.id
        await vc.set_permissions(interaction.user, overwrite=None)
        await vc.set_permissions(
            target, manage_channels=True, move_members=True,
            connect=True, view_channel=True, mute_members=True,
        )
        await interaction.response.send_message(
            embed=ok(f'Управление передано {target.display_name}.'), ephemeral=True
        )
        await _refresh_panel(vc)


# ── Helper views for selects ─────────────────────────────────────────────────

class _SelectView(discord.ui.View):
    """Wrapper that holds a single UserSelect."""
    def __init__(self, select_cls):
        super().__init__(timeout=30)
        self.add_item(select_cls())


# ── Main Panel View (persistent) ─────────────────────────────────────────────

class VoiceControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # ── Row 0: Rename · Limit · Lock ─────────────────────────────────────

    @discord.ui.button(label='Переименовать', emoji='✏️',
                       style=discord.ButtonStyle.secondary, row=0, custom_id='vc:rename')
    async def btn_rename(self, interaction: discord.Interaction, _btn):
        vc = interaction.user.voice and interaction.user.voice.channel
        if not vc or not _is_owner(vc.id, interaction.user.id):
            await interaction.response.send_message(embed=err('Нет прав.'), ephemeral=True)
            return
        await interaction.response.send_modal(RenameModal())

    @discord.ui.button(label='Лимит', emoji='👥',
                       style=discord.ButtonStyle.secondary, row=0, custom_id='vc:limit')
    async def btn_limit(self, interaction: discord.Interaction, _btn):
        vc = interaction.user.voice and interaction.user.voice.channel
        if not vc or not _is_owner(vc.id, interaction.user.id):
            await interaction.response.send_message(embed=err('Нет прав.'), ephemeral=True)
            return
        await interaction.response.send_modal(LimitModal())

    @discord.ui.button(label='Закрыть', emoji='🔒',
                       style=discord.ButtonStyle.danger, row=0, custom_id='vc:lock')
    async def btn_lock(self, interaction: discord.Interaction, _btn):
        vc = interaction.user.voice and interaction.user.voice.channel
        if not vc or not _is_owner(vc.id, interaction.user.id):
            await interaction.response.send_message(embed=err('Нет прав.'), ephemeral=True)
            return

        default_ow = vc.overwrites_for(interaction.guild.default_role)
        if default_ow.connect is False:
            await vc.set_permissions(interaction.guild.default_role, connect=None)
            await interaction.response.send_message(embed=ok('Канал **открыт** для входа.'), ephemeral=True)
        else:
            await vc.set_permissions(interaction.guild.default_role, connect=False)
            await interaction.response.send_message(embed=ok('Канал **закрыт** от входа.'), ephemeral=True)
        await _refresh_panel(vc)

    # ── Row 1: Hide · Kick · Ban ─────────────────────────────────────────

    @discord.ui.button(label='Невидимка', emoji='👁️',
                       style=discord.ButtonStyle.secondary, row=1, custom_id='vc:hide')
    async def btn_hide(self, interaction: discord.Interaction, _btn):
        vc = interaction.user.voice and interaction.user.voice.channel
        if not vc or not _is_owner(vc.id, interaction.user.id):
            await interaction.response.send_message(embed=err('Нет прав.'), ephemeral=True)
            return

        default_ow = vc.overwrites_for(interaction.guild.default_role)
        if default_ow.view_channel is False:
            await vc.set_permissions(interaction.guild.default_role, view_channel=None)
            await interaction.response.send_message(embed=ok('Канал теперь **виден** всем.'), ephemeral=True)
        else:
            await vc.set_permissions(interaction.guild.default_role, view_channel=False)
            await interaction.response.send_message(embed=ok('Канал **скрыт** от всех.'), ephemeral=True)
        await _refresh_panel(vc)

    @discord.ui.button(label='Кикнуть', emoji='👢',
                       style=discord.ButtonStyle.danger, row=1, custom_id='vc:kick')
    async def btn_kick(self, interaction: discord.Interaction, _btn):
        vc = interaction.user.voice and interaction.user.voice.channel
        if not vc or not _is_owner(vc.id, interaction.user.id):
            await interaction.response.send_message(embed=err('Нет прав.'), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=info('Выбери участника для кика:'),
            view=_SelectView(KickSelect), ephemeral=True,
        )

    @discord.ui.button(label='Забанить', emoji='🚫',
                       style=discord.ButtonStyle.danger, row=1, custom_id='vc:ban')
    async def btn_ban(self, interaction: discord.Interaction, _btn):
        vc = interaction.user.voice and interaction.user.voice.channel
        if not vc or not _is_owner(vc.id, interaction.user.id):
            await interaction.response.send_message(embed=err('Нет прав.'), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=info('Выбери участника для блокировки:'),
            view=_SelectView(BanSelect), ephemeral=True,
        )

    # ── Row 2: Unban · Transfer · Delete ─────────────────────────────────

    @discord.ui.button(label='Разбанить', emoji='✅',
                       style=discord.ButtonStyle.success, row=2, custom_id='vc:unban')
    async def btn_unban(self, interaction: discord.Interaction, _btn):
        vc = interaction.user.voice and interaction.user.voice.channel
        if not vc or not _is_owner(vc.id, interaction.user.id):
            await interaction.response.send_message(embed=err('Нет прав.'), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=info('Выбери участника для разблокировки:'),
            view=_SelectView(UnbanSelect), ephemeral=True,
        )

    @discord.ui.button(label='Передать', emoji='👑',
                       style=discord.ButtonStyle.primary, row=2, custom_id='vc:transfer')
    async def btn_transfer(self, interaction: discord.Interaction, _btn):
        vc = interaction.user.voice and interaction.user.voice.channel
        if not vc or not _is_owner(vc.id, interaction.user.id):
            await interaction.response.send_message(embed=err('Нет прав.'), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=info('Выбери участника для передачи управления:'),
            view=_SelectView(TransferSelect), ephemeral=True,
        )

    @discord.ui.button(label='Удалить', emoji='🗑️',
                       style=discord.ButtonStyle.danger, row=2, custom_id='vc:delete')
    async def btn_delete(self, interaction: discord.Interaction, _btn):
        vc = interaction.user.voice and interaction.user.voice.channel
        if not vc or not _is_owner(vc.id, interaction.user.id):
            await interaction.response.send_message(embed=err('Нет прав.'), ephemeral=True)
            return
        await interaction.response.send_message(embed=ok('Канал удаляется...'), ephemeral=True)
        # Move everyone out first
        for m in vc.members:
            try:
                await m.move_to(None)
            except Exception:
                pass
        try:
            await vc.delete(reason='Owner deleted temp voice channel')
        except Exception:
            pass
        _owners.pop(vc.id, None)
        _bans.pop(vc.id, None)
        _panels.pop(vc.id, None)


# ── Cog ──────────────────────────────────────────────────────────────────────

class Voice(commands.Cog):
    voice_group = app_commands.Group(
        name='voice',
        description='Управление временными голосовыми каналами',
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._create_channels: dict[int, int] = {}  # guild_id → vc_id
        self._categories: dict[int, int] = {}        # guild_id → cat_id

    @commands.Cog.listener()
    async def on_ready(self):
        await self._load_settings()

    async def _load_settings(self):
        db = self.bot.db
        if not db or not db.db:
            return
        try:
            async with db.db.execute(
                'SELECT guild_id, voice_create_channel_id, voice_category_id FROM guild_settings'
            ) as cur:
                rows = await cur.fetchall()
                for row in rows:
                    if row['voice_create_channel_id']:
                        self._create_channels[row['guild_id']] = row['voice_create_channel_id']
                    if row['voice_category_id']:
                        self._categories[row['guild_id']] = row['voice_category_id']
        except Exception as e:
            logger.error(f'Failed to load voice settings: {e}')

    # ── Voice state listener ─────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        guild = member.guild

        # ── Joined a channel ─────────────────────────────────────────────
        if after.channel:
            create_ch_id = self._create_channels.get(guild.id)
            if create_ch_id and after.channel.id == create_ch_id:
                await self._create_temp(member, guild)
            elif after.channel.id in _bans and member.id in _bans[after.channel.id]:
                try:
                    await member.move_to(None)
                except Exception:
                    pass
            elif after.channel.id in _owners:
                # Someone joined a temp channel — refresh panel
                await _refresh_panel(after.channel)

        # ── Left a channel ───────────────────────────────────────────────
        if before.channel and before.channel.id in _owners:
            ch = before.channel
            if len(ch.members) == 0:
                await self._delete_temp(ch)
            else:
                await _refresh_panel(ch)

    async def _create_temp(self, member: discord.Member, guild: discord.Guild):
        cat_id = self._categories.get(guild.id)
        category = guild.get_channel(cat_id) if cat_id else None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True, connect=True,
            ),
            member: discord.PermissionOverwrite(
                manage_channels=True, move_members=True, mute_members=True,
                connect=True, view_channel=True, send_messages=True,
            ),
            guild.me: discord.PermissionOverwrite(
                manage_channels=True, move_members=True,
                connect=True, view_channel=True, send_messages=True,
            ),
        }

        try:
            vc = await guild.create_voice_channel(
                name=f'🎙️ {member.display_name}',
                category=category,
                overwrites=overwrites,
                reason=f'Temp voice for {member}',
            )
        except discord.Forbidden:
            logger.warning(f'Cannot create temp voice for {member} in {guild}')
            return

        _owners[vc.id] = member.id
        _bans[vc.id] = set()

        try:
            await member.move_to(vc)
        except Exception:
            await vc.delete()
            _owners.pop(vc.id, None)
            _bans.pop(vc.id, None)
            return

        # Post panel in the VC's built-in text chat
        try:
            embed = _build_panel(vc, member)
            view = VoiceControlView()
            msg = await vc.send(
                content=f'{member.mention}, **твой голосовой канал создан!**',
                embed=embed,
                view=view,
            )
            _panels[vc.id] = msg
        except Exception as e:
            logger.warning(f'Cannot send voice panel: {e}')

        logger.info(f'Temp voice "{vc.name}" created for {member}')

    async def _delete_temp(self, vc: discord.VoiceChannel):
        ch_id = vc.id
        try:
            await vc.delete(reason='Temp voice: empty')
        except Exception:
            pass
        _owners.pop(ch_id, None)
        _bans.pop(ch_id, None)
        _panels.pop(ch_id, None)
        logger.info(f'Temp voice {ch_id} deleted (empty)')

    # ── Slash commands ───────────────────────────────────────────────────

    @voice_group.command(name='setup', description='Настроить систему временных голосовых каналов')
    @app_commands.describe(
        create_channel='Голосовой канал-триггер (при заходе создаётся новый)',
        category='Категория для временных каналов',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def setup(
        self, interaction: discord.Interaction,
        create_channel: discord.VoiceChannel,
        category: discord.CategoryChannel,
    ):
        await interaction.response.defer(ephemeral=True)
        db = self.bot.db
        await db.db.execute(
            'UPDATE guild_settings SET voice_create_channel_id = ?, voice_category_id = ? WHERE guild_id = ?',
            (create_channel.id, category.id, interaction.guild_id),
        )
        await db.db.commit()
        self._create_channels[interaction.guild_id] = create_channel.id
        self._categories[interaction.guild_id] = category.id

        embed = discord.Embed(
            title='✅ Голосовые каналы настроены',
            description=(
                f'**Канал-триггер:** {create_channel.mention}\n'
                f'**Категория:** {category.mention}\n\n'
                'Зайди в канал-триггер — бот создаст тебе персональный войс!'
            ),
            color=SUCCESS,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @voice_group.command(name='panel', description='Открыть панель управления каналом')
    async def panel_cmd(self, interaction: discord.Interaction):
        vc = interaction.user.voice and interaction.user.voice.channel
        if not vc or not _is_owner(vc.id, interaction.user.id):
            await interaction.response.send_message(embed=err('Ты не владелец канала.'), ephemeral=True)
            return
        embed = _build_panel(vc, interaction.user)
        view = VoiceControlView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @voice_group.command(name='claim', description='Забрать брошенный канал (если владелец вышел)')
    async def claim(self, interaction: discord.Interaction):
        vc = interaction.user.voice and interaction.user.voice.channel
        if not vc:
            await interaction.response.send_message(embed=err('Ты не в голосовом канале.'), ephemeral=True)
            return
        if vc.id not in _owners:
            await interaction.response.send_message(embed=err('Это не временный канал.'), ephemeral=True)
            return
        owner_id = _owners[vc.id]
        if any(m.id == owner_id for m in vc.members):
            await interaction.response.send_message(embed=err('Владелец ещё в канале.'), ephemeral=True)
            return
        _owners[vc.id] = interaction.user.id
        await vc.set_permissions(
            interaction.user,
            manage_channels=True, move_members=True, mute_members=True,
            connect=True, view_channel=True, send_messages=True,
        )
        await interaction.response.send_message(
            embed=ok(f'Теперь ты владелец **{vc.name}**!'), ephemeral=True
        )
        await _refresh_panel(vc)

    @voice_group.command(name='info', description='Инфо о временном голосовом канале')
    async def info_cmd(self, interaction: discord.Interaction):
        vc = interaction.user.voice and interaction.user.voice.channel
        if not vc or vc.id not in _owners:
            await interaction.response.send_message(embed=err('Ты не в временном канале.'), ephemeral=True)
            return
        owner = interaction.guild.get_member(_owners[vc.id])
        embed = _build_panel(vc, owner or interaction.user)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Voice(bot))
