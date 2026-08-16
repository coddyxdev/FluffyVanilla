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


patch('cogs/health.py', [
    ("from utils.embeds import SUCCESS, WARNING, ERROR\n",
     "from utils.embeds import SUCCESS, WARNING, ERROR\nfrom utils.emojis import BOT, DIAMOND, VERIFIED, HUH\n"),
    ("        embed = discord.Embed(title=title, description='\\n'.join(lines), color=color)\n",
     "        mood = VERIFIED if problems == 0 else HUH\n        embed = discord.Embed(title=title, description=f'{BOT} {mood} Диагностика\\n\\n' + '\\n'.join(lines), color=color)\n"),
    ("        embed.add_field(name='Discord latency', value=f'{self.bot.latency * 1000:.0f} мс')\n",
     "        embed.add_field(name='Discord latency', value=f'{DIAMOND} {self.bot.latency * 1000:.0f} мс')\n"),
])

patch('cogs/audit.py', [
    ("from utils.embeds import info, PINK\n", "from utils.embeds import info, PINK\nfrom utils.emojis import BOT, MEMBER, ARROW_WHITE\n"),
    ("            lines.append(f'`#{row[\"id\"]}` `{when}` **{row[\"action\"]}** · {actor}{target}{reason}')\n",
     "            lines.append(f'{ARROW_WHITE} `#{row[\"id\"]}` `{when}` **{row[\"action\"]}** · {actor}{target}{reason}')\n"),
    ("        embed = discord.Embed(title=title, description='\\n'.join(lines)[:4000], color=PINK)\n",
     "        embed = discord.Embed(title=title, description=f'{BOT} Журнал сервера\\n\\n' + '\\n'.join(lines)[:3900], color=PINK)\n"),
])

patch('cogs/stats.py', [
    ("from utils.embeds import ok, err, info, PINK\n",
     "from utils.embeds import ok, err, info, PINK\nfrom utils.emojis import DIAMOND, TROPHY, STAR, MEMBER, ARROW_BLUE\n"),
    ("            e = discord.Embed(title='Статистика MC сервера', color=PINK)\n",
     "            e = discord.Embed(title='Статистика MC сервера', description=f'{DIAMOND} Данные в реальном времени', color=PINK)\n"),
    ("                        description='Самые активные игроки Fluffy Vanilla:\\n\\n',\n",
     "                        description=f'{TROPHY} Самые активные игроки Fluffy Vanilla:\\n\\n',\n"),
    ("                        desc += f\"**{i}. {p['player']}** — `{hours} ч.`\\n\"\n",
     "                        desc += f\"{STAR} **{i}. {p['player']}** — `{hours} ч.`\\n\"\n"),
    ("                    e = discord.Embed(title=f'Достижения игрока {d[\"player\"]}', color=PINK)\n",
     "                    e = discord.Embed(title=f'Достижения игрока {d[\"player\"]}', description=f'{TROPHY} Прогресс игрока', color=PINK)\n"),
    ("                        description=f'Подробная статистика выживания на Fluffy Vanilla',\n",
     "                        description=f'{MEMBER} Подробная статистика выживания на Fluffy Vanilla',\n"),
])

patch('cogs/setup.py', [
    ("from utils.embeds import ok, err, settings_embed, PINK, SUCCESS, ERROR, WARNING\n",
     "from utils.embeds import ok, err, settings_embed, PINK, SUCCESS, ERROR, WARNING\nfrom utils.emojis import BOT, VERIFIED, HUH\n"),
    ("        e = discord.Embed(title=title, description='\\n'.join(lines), color=color)\n",
     "        mood = VERIFIED if problems == 0 else HUH\n        e = discord.Embed(title=title, description=f'{BOT} {mood} Проверка конфигурации\\n\\n' + '\\n'.join(lines), color=color)\n"),
])

print('MORE_CUSTOM_EMOJI_PATCHES_COMPLETE')
