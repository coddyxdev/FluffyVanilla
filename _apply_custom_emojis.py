from pathlib import Path

ROOT = Path(__file__).parent


def patch(rel, replacements):
    path = ROOT / rel
    text = path.read_text(encoding='utf-8')
    for old, new in replacements:
        if old not in text:
            raise RuntimeError(f'Anchor missing in {rel}: {old[:120]!r}')
        text = text.replace(old, new)
    path.write_text(text, encoding='utf-8')
    print('PATCHED', rel)


patch('utils/embeds.py', [
    ("from datetime import datetime, timezone\n", "from datetime import datetime, timezone\n\nfrom utils import emojis as em\n"),
    ("def ok(description: str, title: str = 'Готово') -> discord.Embed:\n    return _base(f'✅  {title}', description, SUCCESS)\n",
     "def ok(description: str, title: str = 'Готово') -> discord.Embed:\n    return _base(f'✅  {title}', f'{em.SUCCESS} {description}', SUCCESS)\n"),
    ("def err(description: str, title: str = 'Ошибка') -> discord.Embed:\n    return _base(f'❌  {title}', description, ERROR)\n",
     "def err(description: str, title: str = 'Ошибка') -> discord.Embed:\n    return _base(f'❌  {title}', f'{em.ERROR} {description}', ERROR)\n"),
    ("def warn(description: str, title: str = 'Внимание') -> discord.Embed:\n    return _base(f'⚠️  {title}', description, WARNING)\n",
     "def warn(description: str, title: str = 'Внимание') -> discord.Embed:\n    return _base(f'⚠️  {title}', f'{em.WARNING} {description}', WARNING)\n"),
    ("def info(description: str, title: str = None) -> discord.Embed:\n    return _base(title, description, PINK)\n",
     "def info(description: str, title: str = None) -> discord.Embed:\n    body = f'{em.INFO} {description}' if description else em.INFO\n    return _base(title, body, PINK)\n"),
    ("def ticket_panel_embed(title: str, description: str) -> discord.Embed:\n    return _base(title, description, PINK)\n",
     "def ticket_panel_embed(title: str, description: str) -> discord.Embed:\n    return _base(f'🎫 {title}', f'{em.TICKET} {description}', PINK)\n"),
    ("        f'**Причина:** {reason}',\n", "        f'{em.TICKET} **Причина:** {reason}',\n"),
    ("    e.add_field(name='Создатель', value=creator.mention, inline=True)\n    e.add_field(name='Тип', value=label, inline=True)\n    e.add_field(name='Статус', value='Открыт', inline=True)\n",
     "    e.add_field(name='Создатель', value=f'{em.MEMBER} {creator.mention}', inline=True)\n    e.add_field(name='Тип', value=f'{em.TICKET} {label}', inline=True)\n    e.add_field(name='Статус', value=f'{em.VERIFIED} Открыт', inline=True)\n"),
    ("        f'Тикет был закрыт пользователем {closed_by.mention}.',\n",
     "        f'{em.TICKET} Тикет был закрыт пользователем {closed_by.mention}.',\n"),
    ("            'Fluffy Vanilla — уютный ванильный сервер для небольшого сообщества.\\n\\n'\n",
     "            f'{em.FLOWER} **Fluffy Vanilla** — уютный ванильный сервер для небольшого сообщества.\\n\\n'\n"),
    ("            'Чтобы попасть в белый список, нажми кнопку «Подать заявку» '\n",
     "            f'{em.ARROW_PINK} Чтобы попасть в белый список, нажми кнопку «Подать заявку» '\n"),
    ("        f'Новая заявка от {applicant.mention}',\n", "        f'{em.MEMBER} Новая заявка от {applicant.mention}',\n"),
    ("            f'Категория (обычные): {ch(\"ticket_category_id\")}\\n'\n",
     "            f'{em.TICKET} Категория (обычные): {ch(\"ticket_category_id\")}\\n'\n"),
    ("            f'Канал с панелью: {ch(\"wl_info_channel_id\")}\\n'\n",
     "            f'{em.VERIFIED} Канал с панелью: {ch(\"wl_info_channel_id\")}\\n'\n"),
    ("            (f'Адрес: `{mc_host}:{mc_port}`\\n' if mc_host and mc_port else 'Адрес: `не настроен`\\n')\n",
     "            (f'{em.DIAMOND} Адрес: `{mc_host}:{mc_port}`\\n' if mc_host and mc_port else f'{em.DIAMOND} Адрес: `не настроен`\\n')\n"),
    ("            f'Онлайн: {ch(\"online_channel_id\")}\\n'\n",
     "            f'{em.STAR} Онлайн: {ch(\"online_channel_id\")}\\n'\n"),
    ("        value=f'Общий лог-канал: {ch(\"log_channel_id\")}',\n",
     "        value=f'{em.BOT} Общий лог-канал: {ch(\"log_channel_id\")}',\n"),
])

patch('cogs/giveaways.py', [
    ("from utils.embeds import ok, err, info, PINK, SUCCESS, ERROR, WARNING\n",
     "from utils.embeds import ok, err, info, PINK, SUCCESS, ERROR, WARNING\nfrom utils.emojis import EVENT, TROPHY, MEMBER, VERIFIED, EXCITED, ARROW_PINK, BAWWW\n"),
    ("        label='Участвовать', emoji='🎉', style=discord.ButtonStyle.success,\n",
     "        label='Участвовать', emoji=EVENT, style=discord.ButtonStyle.success,\n"),
    ("            description=description or 'Нажми кнопку ниже, чтобы принять участие!',\n",
     "            description=f'{ARROW_PINK} ' + (description or 'Нажми кнопку ниже, чтобы принять участие!'),\n"),
    ("        embed.add_field(name='Победителей', value=str(winners), inline=True)\n        embed.add_field(name='Участников', value=str(entries), inline=True)\n",
     "        embed.add_field(name='Победителей', value=f'{TROPHY} {winners}', inline=True)\n        embed.add_field(name='Участников', value=f'{MEMBER} {entries}', inline=True)\n"),
    ("        embed.add_field(name='Требуемая роль', value=requirement, inline=True)\n",
     "        embed.add_field(name='Требуемая роль', value=f'{VERIFIED} {requirement}', inline=True)\n"),
    ("            result = mentions if winners else 'Подходящих участников нет.'\n",
     "            result = f'{TROPHY} {mentions}' if winners else f'{BAWWW} Подходящих участников нет.'\n"),
    ("                        f'🎊 Поздравляем {mentions}! Вы выиграли **{giveaway[\"prize\"]}**.\\n'\n",
     "                        f'{EXCITED} {TROPHY} Поздравляем {mentions}! Вы выиграли **{giveaway[\"prize\"]}**.\\n'\n"),
    ("            await channel.send(f'🔄 Новый выбор для **{row[\"prize\"]}**: {mentions}')\n",
     "            await channel.send(f'{TROPHY} 🔄 Новый выбор для **{row[\"prize\"]}**: {mentions}')\n"),
])

patch('cogs/tickets.py', [
    ("    ok, err, info\n)\n", "    ok, err, info\n)\nfrom utils.emojis import TICKET, DIAMOND, VERIFIED, BAWWW, DIZZY, MEMBER, ADMIN_PURPLE\n"),
    ("        emoji='🎫',\n", "        emoji=TICKET,\n"),
    ("        emoji='🔧',\n", "        emoji=DIAMOND,\n"),
    ("            custom_id=cid,\n", "            custom_id=cid,\n            emoji=DIAMOND if ticket_type == 'tech' else TICKET,\n"),
    ("        style=discord.ButtonStyle.danger,\n        custom_id='ticket_ctrl:close',\n",
     "        style=discord.ButtonStyle.danger,\n        emoji=BAWWW,\n        custom_id='ticket_ctrl:close',\n"),
    ("        style=discord.ButtonStyle.success,\n        custom_id='ticket_ctrl:reopen',\n",
     "        style=discord.ButtonStyle.success,\n        emoji=VERIFIED,\n        custom_id='ticket_ctrl:reopen',\n"),
    ("        style=discord.ButtonStyle.secondary,\n        custom_id='ticket_ctrl:delete',\n",
     "        style=discord.ButtonStyle.secondary,\n        emoji=DIZZY,\n        custom_id='ticket_ctrl:delete',\n"),
    ("        content=f'{member.mention} — добро пожаловать!',\n",
     "        content=f'{MEMBER} {member.mention} — добро пожаловать!',\n"),
    ("    @discord.ui.button(label='Да, удалить', style=discord.ButtonStyle.danger)\n",
     "    @discord.ui.button(label='Да, удалить', emoji=BAWWW, style=discord.ButtonStyle.danger)\n"),
    ("    @discord.ui.button(label='Отмена', style=discord.ButtonStyle.secondary)\n",
     "    @discord.ui.button(label='Отмена', emoji=VERIFIED, style=discord.ButtonStyle.secondary)\n"),
])

patch('cogs/ticket_extras.py', [
    ("from utils.embeds import ok, err, warn as warn_embed, info\n",
     "from utils.embeds import ok, err, warn as warn_embed, info\nfrom utils.emojis import STAR, ADMIN_PURPLE, HEART, TICKET\n"),
    ("            emoji='⭐',\n", "            emoji=STAR,\n"),
    ("        emoji='🙋',\n", "        emoji=ADMIN_PURPLE,\n"),
    ("        stars = '⭐' * score\n", "        stars = ' '.join([STAR] * score)\n"),
    ("                lines.append('⭐' * int(r['score']) + (f' — {r[\"comment\"]}' if r['comment'] else ''))\n",
     "                lines.append(' '.join([STAR] * int(r['score'])) + (f' — {r[\"comment\"]}' if r['comment'] else ''))\n"),
    ("                value='\\n'.join('⭐' * int(d['score']) + f' — {int(d[\"c\"])}' for d in dist),\n",
     "                value='\\n'.join(' '.join([STAR] * int(d['score'])) + f' — {int(d[\"c\"])}' for d in dist),\n"),
])

patch('cogs/whitelist.py', [
    ("from utils.audit import record_audit\n", "from utils.audit import record_audit\nfrom utils.emojis import MEMBER, VERIFIED, BAWWW, FLOWER, ADMIN_PINK, ARROW_PINK\n"),
    ("        title='📝 Белый список Fluffy Vanilla',\n", "        title='📝 Белый список Fluffy Vanilla',\n"),
    ("            '**Fluffy Vanilla** — уютный ванильный сервер для тёплого сообщества 🌿\\n\\n'\n",
     "            f'{FLOWER} **Fluffy Vanilla** — уютный ванильный сервер для тёплого сообщества 🌿\\n\\n'\n"),
    ("            'Чтобы получить доступ к серверу, оставь заявку — это займёт меньше минуты:\\n\\n'\n",
     "            f'{ARROW_PINK} Чтобы получить доступ к серверу, оставь заявку — это займёт меньше минуты:\\n\\n'\n"),
    ("        label='Подать заявку', emoji='📝',\n", "        label='Подать заявку', emoji=MEMBER,\n"),
    ("    @discord.ui.button(label='Одобрить', emoji='✅',\n", "    @discord.ui.button(label='Одобрить', emoji=VERIFIED,\n"),
    ("    @discord.ui.button(label='Отклонить', emoji='❌',\n", "    @discord.ui.button(label='Отклонить', emoji=BAWWW,\n"),
    ("            title='✅ Заявка одобрена!',\n", "            title='✅ Заявка одобрена!',\n"),
    ("                f'Привет, {member.mention}! Твоя заявка на **Fluffy Vanilla** одобрена 💚\\n\\n'\n",
     "                f'{VERIFIED} Привет, {member.mention}! Твоя заявка на **Fluffy Vanilla** одобрена 💚\\n\\n'\n"),
])

patch('cogs/moderation.py', [
    ("from utils.embeds import ok, err, warn as warn_embed, info, PINK, ERROR, WARNING, SUCCESS, CLOSED\n",
     "from utils.embeds import ok, err, warn as warn_embed, info, PINK, ERROR, WARNING, SUCCESS, CLOSED\nfrom utils.emojis import ADMIN_PINK, MEMBER, NAUGHTY, VERIFIED\n"),
    ("        e = info('', f'📑 История — {member.display_name}')\n",
     "        e = info(f'{MEMBER} История участника', f'📑 История — {member.display_name}')\n"),
    ("            '⚙️ Модерация',\n", "            f'⚙️ Модерация {ADMIN_PINK}',\n"),
])

patch('cogs/maintenance.py', [
    ("from utils.embeds import ok, err, info, PINK, SUCCESS\n",
     "from utils.embeds import ok, err, info, PINK, SUCCESS\nfrom utils.emojis import BOT, DIAMOND\n"),
    ("        e = discord.Embed(title='🏓 Состояние бота', color=PINK)\n",
     "        e = discord.Embed(title='🏓 Состояние бота', description=f'{BOT} Диагностика Fluffy Vanilla', color=PINK)\n"),
    ("            value=('✅ подключена' if db_ok else '❌ нет соединения') + f' ({db_size:.0f} КБ)',\n",
     "            value=f'{DIAMOND} ' + ('✅ подключена' if db_ok else '❌ нет соединения') + f' ({db_size:.0f} КБ)',\n"),
])

print('CUSTOM_EMOJI_PATCH_COMPLETE')
