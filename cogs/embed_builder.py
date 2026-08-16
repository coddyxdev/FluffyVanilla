import discord
from discord import app_commands
from discord.ext import commands
import logging

from utils.embeds import PINK, SUCCESS, ERROR

logger = logging.getLogger('FluFFy.EmbedBuilder')

class EmbedColorModal(discord.ui.Modal, title='Изменить цвет Embed'):
    color = discord.ui.TextInput(
        label='HEX Код (например: #FF55FF или FF55FF)',
        style=discord.TextStyle.short,
        placeholder='FF55FF',
        max_length=7,
        required=True
    )

    def __init__(self, view):
        super().__init__()
        self.embed_view = view

    async def on_submit(self, interaction: discord.Interaction):
        color_str = self.color.value.strip().replace('#', '')
        try:
            color_int = int(color_str, 16)
            self.embed_view.current_embed.color = discord.Color(color_int)
            await self.embed_view.update_preview(interaction)
        except ValueError:
            await interaction.response.send_message("❌ Неверный HEX код!", ephemeral=True)


class EmbedTextModal(discord.ui.Modal, title='Текст сообщения'):
    embed_title = discord.ui.TextInput(
        label='Заголовок',
        style=discord.TextStyle.short,
        placeholder='(необязательно)',
        required=False,
        max_length=256
    )
    embed_desc = discord.ui.TextInput(
        label='Текст (Описание)',
        style=discord.TextStyle.paragraph,
        placeholder='Введите текст вашего сообщения здесь...',
        required=True,
        max_length=4000
    )

    def __init__(self, view):
        super().__init__()
        self.embed_view = view
        self.embed_title.default = self.embed_view.current_embed.title
        self.embed_desc.default = self.embed_view.current_embed.description

    async def on_submit(self, interaction: discord.Interaction):
        self.embed_view.current_embed.title = self.embed_title.value
        self.embed_view.current_embed.description = self.embed_desc.value
        await self.embed_view.update_preview(interaction)


class EmbedImageModal(discord.ui.Modal, title='Изображения'):
    image_url = discord.ui.TextInput(
        label='URL картинки снизу',
        style=discord.TextStyle.short,
        placeholder='https://...',
        required=False
    )
    thumbnail_url = discord.ui.TextInput(
        label='URL миниатюры (справа)',
        style=discord.TextStyle.short,
        placeholder='https://...',
        required=False
    )

    def __init__(self, view):
        super().__init__()
        self.embed_view = view
        if self.embed_view.current_embed.image:
            self.image_url.default = self.embed_view.current_embed.image.url
        if self.embed_view.current_embed.thumbnail:
            self.thumbnail_url.default = self.embed_view.current_embed.thumbnail.url

    async def on_submit(self, interaction: discord.Interaction):
        if self.image_url.value:
            self.embed_view.current_embed.set_image(url=self.image_url.value)
        else:
            self.embed_view.current_embed.set_image(url=None)
            
        if self.thumbnail_url.value:
            self.embed_view.current_embed.set_thumbnail(url=self.thumbnail_url.value)
        else:
            self.embed_view.current_embed.set_thumbnail(url=None)
            
        await self.embed_view.update_preview(interaction)


class EmbedBuilderView(discord.ui.View):
    def __init__(self, author: discord.Member):
        super().__init__(timeout=900)
        self.author = author
        self.current_embed = discord.Embed(
            title='Заголовок',
            description='Настрой этот текст нажав на кнопки ниже!',
            color=PINK
        )
        self.target_channel: discord.TextChannel = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.author:
            await interaction.response.send_message("❌ Это меню вызвал другой пользователь.", ephemeral=True)
            return False
        return True

    async def update_preview(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=self.current_embed, view=self)

    @discord.ui.button(label='✏️ Текст', style=discord.ButtonStyle.secondary, row=0)
    async def edit_text(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EmbedTextModal(self))

    @discord.ui.button(label='🎨 Цвет', style=discord.ButtonStyle.secondary, row=0)
    async def edit_color(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EmbedColorModal(self))

    @discord.ui.button(label='🖼 Картинки', style=discord.ButtonStyle.secondary, row=0)
    async def edit_img(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EmbedImageModal(self))

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text, discord.ChannelType.news], placeholder='Выберите канал для отправки...', row=1)
    async def select_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        self.target_channel = select.values[0]
        await interaction.response.send_message(f"✅ Выбран канал: {self.target_channel.mention}", ephemeral=True)

    @discord.ui.button(label='📤 ОТПРАВИТЬ', style=discord.ButtonStyle.success, row=2)
    async def send_embed(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.target_channel:
            await interaction.response.send_message("❌ Сначала выбери канал в выпадающем меню выше!", ephemeral=True)
            return

        real_channel = interaction.guild.get_channel(self.target_channel.id)
        if not real_channel:
            await interaction.response.send_message("❌ Ошибка: канал не найден на сервере.", ephemeral=True)
            return

        try:
            await real_channel.send(embed=self.current_embed)
            await interaction.response.send_message(f"✅ Сообщение успешно отправлено в {real_channel.mention}!", ephemeral=True)
            self.stop()
        except discord.Forbidden:
            await interaction.response.send_message(f"❌ У бота нет прав писать в канал {real_channel.mention}!", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ Ошибка отправки (Возможно, битая ссылка на картинку?):\n`{e}`", ephemeral=True)


class EmbedBuilderCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='embed', description='Создать и отправить кастомное красивое Embed-сообщение')
    @app_commands.default_permissions(manage_guild=True)
    async def embed_create(self, interaction: discord.Interaction):
        view = EmbedBuilderView(interaction.user)
        # Отправляем превью только создателю сообщения
        await interaction.response.send_message(
            content='**Превью вашего сообщения:**\n*Используйте кнопки ниже для настройки и выбора канала для отправки.*',
            embed=view.current_embed,
            view=view,
            ephemeral=True
        )

async def setup(bot):
    await bot.add_cog(EmbedBuilderCog(bot))
