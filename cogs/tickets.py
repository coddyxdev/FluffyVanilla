"""
cogs/tickets.py  —  Full ticket system for Fluffy Vanilla
  • /ticket panel     — create interactive panel in current channel
  • /ticket close     — close current ticket channel
  • /ticket reopen    — reopen a closed ticket
  • /ticket add       — add user to ticket
  • /ticket remove    — remove user from ticket
  • /ticket rename    — rename the ticket channel
  • /ticket delete    — permanently delete ticket channel
  • /ticket info      — show ticket details
"""
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
import logging

from utils.embeds import (
    PINK, PINK_SOFT, CLOSED, SUCCESS, ERROR,
    ticket_panel_embed, ticket_open_embed, ticket_closed_embed,
    ok, err, info
)
from utils.emojis import TICKET, DIAMOND, VERIFIED, BAWWW, DIZZY, MEMBER, ADMIN_PURPLE

logger = logging.getLogger('FluFFy.Tickets')


# ─── Modals ───────────────────────────────────────────────────────────────────

class TicketReasonModal(discord.ui.Modal, title='Создать тикет'):
    reason = discord.ui.TextInput(
        label='Опишите вашу проблему или вопрос',
        style=discord.TextStyle.paragraph,
        placeholder='Подробно опишите, с чем вам нужна помощь...',
        min_length=10,
        max_length=500,
    )

    def __init__(self, ticket_type: str = 'regular'):
        super().__init__()
        self.ticket_type = ticket_type
        if ticket_type == 'tech':
            self.title = 'Технический тикет'
            self.reason.label = 'Опишите техническую проблему'
            self.reason.placeholder = 'Лаги, баги, ошибки плагинов...'

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await create_ticket_channel(
            interaction,
            self.ticket_type,
            str(self.reason)
        )


class PanelEditModal(discord.ui.Modal, title='Редактировать панель'):
    p_title = discord.ui.TextInput(
        label='Заголовок панели',
        placeholder='Например: Поддержка',
        max_length=100,
    )
    description = discord.ui.TextInput(
        label='Описание',
        style=discord.TextStyle.paragraph,
        placeholder='Опишите для чего нужны тикеты...',
        max_length=1000,
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction):
        # handled externally via callback
        await interaction.response.defer()


# ─── Views ────────────────────────────────────────────────────────────────────

class TicketPanelView(discord.ui.View):
    """Persistent view for the ticket panel message."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label='Создать тикет',
        style=discord.ButtonStyle.primary,
        emoji=TICKET,
        custom_id='ticket_panel:regular',
    )
    async def create_regular(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(TicketReasonModal('regular'))

    @discord.ui.button(
        label='Тех. тикет',
        style=discord.ButtonStyle.secondary,
        emoji=DIAMOND,
        custom_id='ticket_panel:tech',
    )
    async def create_tech(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(TicketReasonModal('tech'))


class TicketPanelViewSingle(discord.ui.View):
    """Panel with only one type of ticket button."""

    def __init__(self, ticket_type: str = 'regular'):
        super().__init__(timeout=None)
        label = 'Тех. тикет' if ticket_type == 'tech' else 'Создать тикет'
        cid = f'ticket_panel_single:{ticket_type}'
        btn = discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.primary,
            custom_id=cid,
            emoji=DIAMOND if ticket_type == 'tech' else TICKET,
        )
        btn.callback = self._callback
        self._ticket_type = ticket_type
        self.add_item(btn)

    async def _callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TicketReasonModal(self._ticket_type))


class TicketControlView(discord.ui.View):
    """Persistent control buttons inside every ticket channel."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label='Закрыть тикет',
        style=discord.ButtonStyle.danger,
        emoji=BAWWW,
        custom_id='ticket_ctrl:close',
    )
    async def close_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        await _do_close_ticket(interaction)

    @discord.ui.button(
        label='Переоткрыть',
        style=discord.ButtonStyle.success,
        emoji=VERIFIED,
        custom_id='ticket_ctrl:reopen',
    )
    async def reopen_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        await _do_reopen_ticket(interaction)

    @discord.ui.button(
        label='Удалить',
        style=discord.ButtonStyle.secondary,
        emoji=DIZZY,
        custom_id='ticket_ctrl:delete',
    )
    async def delete_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        await _do_delete_ticket(interaction)


# ─── Core ticket logic ────────────────────────────────────────────────────────

async def _get_ticket_category(guild: discord.Guild, settings, ticket_type: str):
    cat_id = (
        settings['tech_ticket_category_id']
        if ticket_type == 'tech'
        else settings['ticket_category_id']
    )
    if cat_id:
        return guild.get_channel(cat_id)
    return None


async def create_ticket_channel(
    interaction: discord.Interaction,
    ticket_type: str,
    reason: str,
):
    bot = interaction.client
    guild = interaction.guild
    member = interaction.user
    db = bot.db
    settings = await db.get_guild(guild.id)

    if not settings:
        await interaction.followup.send(
            embed=err('Бот ещё не настроен. Обратитесь к администратору.'),
            ephemeral=True
        )
        return

    # Check if user already has an open ticket
    async with db.db.execute(
        '''SELECT channel_id FROM tickets
           WHERE guild_id = ? AND creator_id = ? AND status = 'open' LIMIT 1''',
        (guild.id, member.id)
    ) as cur:
        existing = await cur.fetchone()
    if existing:
        await interaction.followup.send(
            embed=err(f'У вас уже есть открытый тикет: <#{existing["channel_id"]}>'),
            ephemeral=True
        )
        return

    number = await db.next_ticket_number(guild.id)
    category = await _get_ticket_category(guild, settings, ticket_type)
    prefix = 'tech' if ticket_type == 'tech' else 'ticket'
    channel_name = f'{prefix}-{number:04d}-{member.name[:12]}'

    # Permission overwrites: private, only creator + admins
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_channels=True,
            manage_messages=True
        ),
    }
    # Give all admins/mods access
    for r in guild.roles:
        if r.permissions.manage_guild or r.permissions.administrator:
            overwrites[r] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                manage_messages=True
            )

    try:
        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            reason=f'Ticket #{number:04d} by {member}',
        )
    except discord.Forbidden:
        await interaction.followup.send(
            embed=err('Нет прав для создания канала. Проверьте настройки бота.'),
            ephemeral=True
        )
        return

    await db.create_ticket(guild.id, channel.id, member.id, ticket_type, reason, number)

    # Post ticket embed + control view
    embed = ticket_open_embed(number, ticket_type, reason, member)
    ctrl_view = TicketControlView()
    msg = await channel.send(
        content=f'{MEMBER} {member.mention} — добро пожаловать!',
        embed=embed,
        view=ctrl_view
    )
    await msg.pin()

    # Log to log channel
    log_id = settings['ticket_log_channel_id']
    if log_id:
        log_ch = guild.get_channel(log_id)
        if log_ch:
            log_embed = discord.Embed(
                title=f'Тикет #{number:04d} создан',
                description=f'**Создатель:** {member.mention}\n**Тип:** {ticket_type}\n**Причина:** {reason}',
                color=PINK,
                timestamp=datetime.now(timezone.utc),
            )
            log_embed.set_footer(text=f'Fluffy Vanilla  •  #{channel.name}')
            await log_ch.send(embed=log_embed)

    await interaction.followup.send(
        embed=ok(f'Тикет создан: {channel.mention}'),
        ephemeral=True
    )
    logger.info(f'Ticket #{number:04d} created by {member} in {guild}')


async def create_mc_ticket(
    bot: commands.Bot,
    guild: discord.Guild,
    player: str,
    reason: str,
    x: int, y: int, z: int, world: str
):
    db = bot.db
    settings = await db.get_guild(guild.id)
    if not settings: return

    number = await db.next_ticket_number(guild.id)
    category = await _get_ticket_category(guild, settings, 'regular')
    channel_name = f'help-{number:04d}-{player[:12]}'

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_channels=True,
            manage_messages=True
        ),
    }
    for r in guild.roles:
        if r.permissions.manage_guild or r.permissions.administrator:
            overwrites[r] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                manage_messages=True
            )

    try:
        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            reason=f'MC Help Ticket #{number:04d} by {player}',
        )
    except discord.Forbidden:
        return

    # find discord user if they are linked (we check whitelist_apps by nick)
    discord_id = 0
    async with db.db.execute(
        'SELECT user_id FROM whitelist_apps WHERE minecraft_nick = ? AND guild_id = ? ORDER BY id DESC LIMIT 1',
        (player, guild.id)
    ) as cur:
        row = await cur.fetchone()
        if row:
            discord_id = row['user_id']
            member = guild.get_member(discord_id)
            if member:
                await channel.set_permissions(
                    member, view_channel=True, send_messages=True, read_message_history=True
                )

    await db.create_ticket(guild.id, channel.id, discord_id, 'regular', f'MC Help: {reason}', number)

    embed = discord.Embed(
        title=f'Помощь из игры — {player}',
        description=f'**Игрок:** {player}\n**Координаты:** `X: {x}, Y: {y}, Z: {z}` ({world})\n\n**Причина:** {reason}',
        color=PINK
    )
    ctrl_view = TicketControlView()
    mention = f'<@{discord_id}>' if discord_id else player
    msg = await channel.send(
        content=f'Запрос о помощи от {mention}',
        embed=embed,
        view=ctrl_view
    )
    await msg.pin()

    log_id = settings['ticket_log_channel_id']
    if log_id:
        log_ch = guild.get_channel(log_id)
        if log_ch:
            log_embed = discord.Embed(
                title=f'MC Тикет #{number:04d} создан',
                description=f'**Игрок:** {player}\n**Причина:** {reason}',
                color=PINK,
                timestamp=datetime.now(timezone.utc),
            )
            await log_ch.send(embed=log_embed)
    logger.info(f'MC Ticket #{number:04d} created for {player}')



async def _do_close_ticket(interaction: discord.Interaction):
    """Close ticket — usable by the creator or any admin."""
    bot = interaction.client
    db = bot.db
    member = interaction.user
    channel = interaction.channel

    ticket = await db.get_ticket(channel.id)
    if not ticket:
        await interaction.response.send_message(
            embed=err('Это не тикет-канал.'), ephemeral=True
        )
        return

    if ticket['status'] == 'closed':
        await interaction.response.send_message(
            embed=err('Тикет уже закрыт.'), ephemeral=True
        )
        return

    # Must be creator or have manage_guild
    is_creator = ticket['creator_id'] == member.id
    is_admin = member.guild_permissions.manage_guild
    if not (is_creator or is_admin):
        await interaction.response.send_message(
            embed=err('Только создатель тикета или администратор может его закрыть.'),
            ephemeral=True
        )
        return

    await db.update_ticket(
        channel.id,
        status='closed',
        closed_at=datetime.now(timezone.utc).isoformat(),
        closed_by=member.id,
    )

    # Restrict creator from sending
    creator = interaction.guild.get_member(ticket['creator_id'])
    if creator:
        await channel.set_permissions(
            creator, send_messages=False, view_channel=True, read_message_history=True
        )

    await channel.edit(name=channel.name.replace('ticket-', 'closed-').replace('tech-', 'closed-tech-'))
    embed = ticket_closed_embed(ticket['ticket_number'], member)
    await interaction.response.send_message(embed=embed)
    logger.info(f'Ticket #{ticket["ticket_number"]:04d} closed by {member}')


async def _do_reopen_ticket(interaction: discord.Interaction):
    bot = interaction.client
    db = bot.db
    member = interaction.user

    if not member.guild_permissions.manage_guild:
        await interaction.response.send_message(
            embed=err('Только администратор может переоткрыть тикет.'), ephemeral=True
        )
        return

    channel = interaction.channel
    ticket = await db.get_ticket(channel.id)
    if not ticket:
        await interaction.response.send_message(embed=err('Не тикет-канал.'), ephemeral=True)
        return

    if ticket['status'] == 'open':
        await interaction.response.send_message(
            embed=err('Тикет уже открыт.'), ephemeral=True
        )
        return

    await db.update_ticket(channel.id, status='open', closed_at=None, closed_by=None)

    creator = interaction.guild.get_member(ticket['creator_id'])
    if creator:
        await channel.set_permissions(
            creator, send_messages=True, view_channel=True, read_message_history=True
        )

    old = channel.name
    new_name = old.replace('closed-tech-', 'tech-').replace('closed-', 'ticket-')
    if old != new_name:
        await channel.edit(name=new_name)

    await interaction.response.send_message(
        embed=ok(f'Тикет переоткрыт пользователем {member.mention}.')
    )


class ConfirmDeleteView(discord.ui.View):
    """Подтверждение удаления тикет-канала."""

    def __init__(self, author_id: int, channel_id: int):
        super().__init__(timeout=30)
        self.author_id = author_id
        self.channel_id = channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=err('Это подтверждение не для вас.'), ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label='Да, удалить', emoji=BAWWW, style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button):
        channel = interaction.channel
        if channel.id != self.channel_id:
            await interaction.response.send_message(
                embed=err('Канал изменился.'), ephemeral=True
            )
            return
        await interaction.response.edit_message(
            embed=discord.Embed(
                description='Канал будет удалён через 5 секунд...',
                color=ERROR,
            ),
            view=None,
        )
        try:
            await interaction.client.db.db.execute(
                'DELETE FROM tickets WHERE channel_id = ?', (channel.id,)
            )
            await interaction.client.db.db.commit()
        except Exception:
            pass
        await asyncio.sleep(5)
        try:
            await channel.delete(reason=f'Ticket deleted by {interaction.user}')
        except discord.Forbidden:
            pass

    @discord.ui.button(label='Отмена', emoji=VERIFIED, style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.edit_message(
            embed=discord.Embed(description='Удаление отменено.', color=CLOSED),
            view=None,
        )
        self.stop()


async def _do_delete_ticket(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message(
            embed=err('Только администратор может удалить тикет.'), ephemeral=True
        )
        return

    channel = interaction.channel

    try:
        ticket = await interaction.client.db.get_ticket(channel.id)
    except Exception:
        ticket = None

    if not ticket:
        await interaction.response.send_message(
            embed=err(
                'Это не тикет-канал — удалять нечего. Команда /ticket delete работает '
                'только внутри каналов, созданных ботом как тикеты.'
            ),
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        embed=discord.Embed(
            title='⚠️ Подтвердите удаление',
            description=(
                f'Канал **#{channel.name}** будет удалён навсегда вместе со всей перепиской. '
                'Восстановить сообщения будет невозможно. '
                'Если нужно просто закрыть тикет — используйте /ticket close.'
            ),
            color=ERROR,
        ),
        view=ConfirmDeleteView(interaction.user.id, channel.id),
        ephemeral=True,
    )


# ─── Cog ──────────────────────────────────────────────────────────────────────


class Tickets(commands.Cog):
    ticket_group = app_commands.Group(
        name='ticket',
        description='Управление тикетами',
        guild_only=True,
    )

    def __init__(self, bot):

        self.bot = bot

    @ticket_group.command(name='panel', description='Создать панель тикетов в этом канале')
    @app_commands.describe(
        title='Заголовок панели',
        description='Описание (необязательно)',
        type='Тип тикетов на панели',
    )
    @app_commands.choices(type=[
        app_commands.Choice(name='Оба типа', value='both'),
        app_commands.Choice(name='Только обычные', value='regular'),
        app_commands.Choice(name='Только технические', value='tech'),
    ])
    @app_commands.default_permissions(manage_guild=True)
    async def panel(
        self,
        interaction: discord.Interaction,
        title: str = 'Поддержка Fluffy Vanilla',
        description: str = (
            'Если у вас есть вопрос или проблема — создайте тикет.\n'
            'Мы ответим как можно быстрее.'
        ),
        type: str = 'both',
    ):
        embed = ticket_panel_embed(title, description)
        view = TicketPanelView() if type == 'both' else TicketPanelViewSingle(type)
        msg = await interaction.channel.send(embed=embed, view=view)

        await self.bot.db.upsert_panel(
            interaction.guild_id, interaction.channel_id,
            msg.id, title, description, type
        )
        await interaction.response.send_message(
            embed=ok('Панель тикетов создана.'), ephemeral=True
        )

    @ticket_group.command(name='close', description='Закрыть текущий тикет')
    async def close(self, interaction: discord.Interaction):
        await _do_close_ticket(interaction)

    @ticket_group.command(name='reopen', description='Переоткрыть закрытый тикет')
    @app_commands.default_permissions(manage_guild=True)
    async def reopen(self, interaction: discord.Interaction):
        await _do_reopen_ticket(interaction)

    @ticket_group.command(name='delete', description='Удалить тикет-канал навсегда')
    @app_commands.default_permissions(manage_guild=True)
    async def delete(self, interaction: discord.Interaction):
        await _do_delete_ticket(interaction)

    @ticket_group.command(name='add', description='Добавить участника в тикет')
    @app_commands.describe(member='Кого добавить')
    @app_commands.default_permissions(manage_guild=True)
    async def add(self, interaction: discord.Interaction, member: discord.Member):
        ticket = await self.bot.db.get_ticket(interaction.channel_id)
        if not ticket:
            await interaction.response.send_message(embed=err('Не тикет-канал.'), ephemeral=True)
            return
        await interaction.channel.set_permissions(
            member, view_channel=True, send_messages=True, read_message_history=True
        )
        await interaction.response.send_message(
            embed=ok(f'{member.mention} добавлен в тикет.')
        )

    @ticket_group.command(name='remove', description='Убрать участника из тикета')
    @app_commands.describe(member='Кого убрать')
    @app_commands.default_permissions(manage_guild=True)
    async def remove(self, interaction: discord.Interaction, member: discord.Member):
        ticket = await self.bot.db.get_ticket(interaction.channel_id)
        if not ticket:
            await interaction.response.send_message(embed=err('Не тикет-канал.'), ephemeral=True)
            return
        if ticket['creator_id'] == member.id:
            await interaction.response.send_message(
                embed=err('Нельзя убрать создателя тикета.'), ephemeral=True
            )
            return
        await interaction.channel.set_permissions(member, view_channel=False)
        await interaction.response.send_message(
            embed=ok(f'{member.mention} убран из тикета.')
        )

    @ticket_group.command(name='rename', description='Переименовать тикет-канал')
    @app_commands.describe(name='Новое имя канала (без пробелов)')
    @app_commands.default_permissions(manage_guild=True)
    async def rename(self, interaction: discord.Interaction, name: str):
        ticket = await self.bot.db.get_ticket(interaction.channel_id)
        if not ticket:
            await interaction.response.send_message(embed=err('Не тикет-канал.'), ephemeral=True)
            return
        new_name = name.lower().replace(' ', '-')[:100]
        await interaction.channel.edit(name=new_name)
        await interaction.response.send_message(
            embed=ok(f'Канал переименован в `{new_name}`.')
        )

    @ticket_group.command(name='info', description='Информация о текущем тикете')
    async def ticket_info(self, interaction: discord.Interaction):
        ticket = await self.bot.db.get_ticket(interaction.channel_id)
        if not ticket:
            await interaction.response.send_message(embed=err('Не тикет-канал.'), ephemeral=True)
            return
        status_icon = 'Открыт' if ticket['status'] == 'open' else 'Закрыт'
        e = discord.Embed(
            title=f'Тикет #{ticket["ticket_number"]:04d}',
            color=PINK if ticket['status'] == 'open' else CLOSED,
        )
        e.add_field(name='Статус', value=status_icon, inline=True)
        e.add_field(name='Тип', value=ticket['ticket_type'], inline=True)
        e.add_field(name='Создан', value=f'<t:{int(datetime.fromisoformat(ticket["created_at"]).timestamp())}:R>', inline=True)
        creator = interaction.guild.get_member(ticket['creator_id'])
        e.add_field(name='Создатель', value=creator.mention if creator else f'`{ticket["creator_id"]}`', inline=True)
        e.add_field(name='Причина', value=ticket['reason'] or '—', inline=False)
        e.set_footer(text='Fluffy Vanilla')
        await interaction.response.send_message(embed=e, ephemeral=True)

    @ticket_group.command(name='panel_delete', description='Удалить панель тикетов из этого канала')
    @app_commands.default_permissions(manage_guild=True)
    async def panel_delete(self, interaction: discord.Interaction):
        panels = await self.bot.db.get_panels_in_channel(interaction.channel_id)
        if not panels:
            await interaction.response.send_message(
                embed=err('В этом канале нет панелей тикетов.'), ephemeral=True
            )
            return
        deleted = 0
        for p in panels:
            try:
                msg = await interaction.channel.fetch_message(p['message_id'])
                await msg.delete()
            except Exception:
                pass
            await self.bot.db.delete_panel(p['message_id'])
            deleted += 1
        await interaction.response.send_message(
            embed=ok(f'Удалено панелей: {deleted}.'), ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(Tickets(bot))
