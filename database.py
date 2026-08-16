import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

import aiosqlite

logger = logging.getLogger('FluFFy.DB')
DB_PATH = 'fluffy.db'


class Database:
    """SQLite storage with ordered migrations and validated dynamic updates."""

    GUILD_FIELDS = {
        'ticket_category_id', 'tech_ticket_category_id', 'ticket_log_channel_id',
        'ticket_counter', 'wl_code_channel_id', 'wl_role_id', 'wl_notify_channel_id',
        'wl_info_channel_id', 'wl_logs_channel_id', 'wl_category_id', 'mc_api_host',
        'mc_api_port', 'mc_api_key', 'mc_host', 'mc_port', 'online_channel_id',
        'tps_builds_channel_id', 'tps_farms_channel_id', 'stats_interval',
        'mc_chat_channel_id', 'voice_create_channel_id', 'voice_category_id',
        'log_channel_id',
    }
    TICKET_FIELDS = {'status', 'reason', 'assigned_to', 'closed_at', 'closed_by'}
    APP_FIELDS = {
        'ticket_channel_id', 'minecraft_nick', 'join_reason',
        'about_me', 'age', 'invited_by', 'status', 'reviewed_by', 'reviewed_at',
    }

    def __init__(self, path: str = DB_PATH):
        self.path = path
        self.db: Optional[aiosqlite.Connection] = None
        self._write_lock = asyncio.Lock()

    async def init(self):
        self.db = await aiosqlite.connect(self.path, timeout=15)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute('PRAGMA journal_mode=WAL')
        await self.db.execute('PRAGMA foreign_keys=ON')
        await self.db.execute('PRAGMA busy_timeout=15000')
        await self._run_migrations()
        logger.info('Database initialised; schema version %s.', await self.schema_version())

    async def _run_migrations(self):
        await self.db.execute('''CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY, name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''')
        await self.db.commit()
        migrations = [
            (1, 'base_schema', self._migration_base),
            (2, 'legacy_columns', self._migration_columns),
            (3, 'audit_links_suggestions', self._migration_features),
            (4, 'indexes', self._migration_indexes),
            (5, 'remove_links_suggestions', self._migration_remove_features),
            (6, 'giveaways', self._migration_giveaways),
        ]
        for version, name, callback in migrations:
            row = await self.fetchone('SELECT 1 FROM schema_migrations WHERE version = ?', (version,))
            if row:
                continue
            logger.info('Applying migration %s: %s', version, name)
            try:
                await self.db.execute('BEGIN IMMEDIATE')
                await callback()
                await self.db.execute(
                    'INSERT INTO schema_migrations(version, name) VALUES (?, ?)', (version, name)
                )
                await self.db.commit()
            except Exception:
                await self.db.rollback()
                logger.exception('Migration %s failed', version)
                raise

    async def _exec_script(self, script: str):
        for statement in script.split(';'):
            statement = statement.strip()
            if statement:
                await self.db.execute(statement)

    async def _migration_base(self):
        await self._exec_script('''
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id INTEGER PRIMARY KEY,
            ticket_category_id INTEGER, tech_ticket_category_id INTEGER,
            ticket_log_channel_id INTEGER, ticket_counter INTEGER DEFAULT 0,
            wl_code_channel_id INTEGER, wl_role_id INTEGER, wl_notify_channel_id INTEGER,
            wl_info_channel_id INTEGER, wl_logs_channel_id INTEGER, wl_category_id INTEGER,
            mc_api_host TEXT, mc_api_port INTEGER, mc_api_key TEXT, mc_host TEXT, mc_port INTEGER,
            online_channel_id INTEGER, tps_builds_channel_id INTEGER, tps_farms_channel_id INTEGER,
            stats_interval INTEGER DEFAULT 60, mc_chat_channel_id INTEGER,
            voice_create_channel_id INTEGER, voice_category_id INTEGER, log_channel_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
            channel_id INTEGER UNIQUE NOT NULL, creator_id INTEGER NOT NULL,
            ticket_type TEXT DEFAULT 'regular', status TEXT DEFAULT 'open', reason TEXT,
            ticket_number INTEGER, assigned_to INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            closed_at TEXT, closed_by INTEGER
        );
        CREATE TABLE IF NOT EXISTS ticket_panels (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL, message_id INTEGER UNIQUE NOT NULL,
            title TEXT DEFAULT 'Поддержка', description TEXT,
            ticket_type TEXT DEFAULT 'both', created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS whitelist_apps (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL, ticket_channel_id INTEGER, minecraft_nick TEXT,
            join_reason TEXT, about_me TEXT, age TEXT, invited_by TEXT,
            status TEXT DEFAULT 'pending', reviewed_by INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP, reviewed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS whitelist_codes (
            code TEXT PRIMARY KEY, user_id INTEGER NOT NULL, guild_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL
        );
        ''')

    async def _columns(self, table: str) -> set[str]:
        rows = await self.fetchall(f'PRAGMA table_info("{table}")')
        return {str(r['name']) for r in rows}

    async def _add_column(self, table: str, definition: str):
        name = definition.split()[0].strip('"')
        if name not in await self._columns(table):
            await self.db.execute(f'ALTER TABLE "{table}" ADD COLUMN {definition}')

    async def _migration_columns(self):
        guild_columns = [
            'wl_logs_channel_id INTEGER', 'wl_category_id INTEGER', 'mc_api_host TEXT',
            'mc_api_port INTEGER', 'mc_api_key TEXT', 'mc_host TEXT', 'mc_port INTEGER',
            'online_channel_id INTEGER', 'tps_builds_channel_id INTEGER',
            'tps_farms_channel_id INTEGER', 'stats_interval INTEGER DEFAULT 60',
            'mc_chat_channel_id INTEGER', 'voice_create_channel_id INTEGER',
            'voice_category_id INTEGER', 'suggestions_channel_id INTEGER',
            'suggestions_log_channel_id INTEGER',
        ]
        for definition in guild_columns:
            await self._add_column('guild_settings', definition)
        for definition in ('invited_by TEXT', 'minecraft_uuid TEXT'):
            await self._add_column('whitelist_apps', definition)

    async def _migration_features(self):
        await self._exec_script('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
            actor_id INTEGER, target_id INTEGER, action TEXT NOT NULL,
            reason TEXT, metadata TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS minecraft_links (
            guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, minecraft_uuid TEXT NOT NULL,
            minecraft_nick TEXT NOT NULL, verified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            verified_by INTEGER, PRIMARY KEY(guild_id, user_id),
            UNIQUE(guild_id, minecraft_uuid)
        );
        CREATE TABLE IF NOT EXISTS minecraft_link_codes (
            code_hash TEXT PRIMARY KEY, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            minecraft_uuid TEXT NOT NULL, minecraft_nick TEXT NOT NULL,
            expires_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, message_id INTEGER UNIQUE,
            title TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
            staff_id INTEGER, staff_reason TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS suggestion_votes (
            suggestion_id INTEGER NOT NULL REFERENCES suggestions(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL, vote INTEGER NOT NULL CHECK(vote IN (-1, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(suggestion_id, user_id)
        );
        ''')

    async def _migration_remove_features(self):
        await self.db.execute('DROP INDEX IF EXISTS idx_suggestions_guild')
        await self.db.execute('DROP INDEX IF EXISTS idx_link_codes_expiry')
        for table in ('suggestion_votes', 'suggestions', 'minecraft_link_codes', 'minecraft_links'):
            await self.db.execute(f'DROP TABLE IF EXISTS \"{table}\"')
        guild_columns = await self._columns('guild_settings')
        for column in ('suggestions_channel_id', 'suggestions_log_channel_id'):
            if column in guild_columns:
                await self.db.execute(f'ALTER TABLE guild_settings DROP COLUMN \"{column}\"')
        if 'minecraft_uuid' in await self._columns('whitelist_apps'):
            await self.db.execute('ALTER TABLE whitelist_apps DROP COLUMN minecraft_uuid')

    async def _migration_giveaways(self):
        await self._exec_script('''
        CREATE TABLE IF NOT EXISTS giveaways (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL, message_id INTEGER UNIQUE, host_id INTEGER NOT NULL,
            prize TEXT NOT NULL, description TEXT, winner_count INTEGER NOT NULL DEFAULT 1,
            required_role_id INTEGER, status TEXT NOT NULL DEFAULT 'open',
            end_at TEXT NOT NULL, winner_ids TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ended_at TEXT
        );
        CREATE TABLE IF NOT EXISTS giveaway_entries (
            giveaway_id INTEGER NOT NULL REFERENCES giveaways(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL, joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(giveaway_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_giveaways_due ON giveaways(status, end_at);
        CREATE INDEX IF NOT EXISTS idx_giveaway_entries ON giveaway_entries(giveaway_id);
        ''')

    async def _migration_indexes(self):
        await self._exec_script('''
        CREATE INDEX IF NOT EXISTS idx_audit_guild_time ON audit_log(guild_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_log(guild_id, target_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_wl_user ON whitelist_apps(guild_id, user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_suggestions_guild ON suggestions(guild_id, status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_link_codes_expiry ON minecraft_link_codes(expires_at);
        ''')

    async def schema_version(self) -> int:
        row = await self.fetchone('SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations')
        return int(row['version']) if row else 0

    async def fetchone(self, query: str, params=()):
        async with self.db.execute(query, params) as cur:
            return await cur.fetchone()

    async def fetchall(self, query: str, params=()):
        async with self.db.execute(query, params) as cur:
            return await cur.fetchall()

    async def execute(self, query: str, params=(), *, commit=True):
        async with self._write_lock:
            cur = await self.db.execute(query, params)
            lastrowid, rowcount = cur.lastrowid, cur.rowcount
            await cur.close()
            if commit:
                await self.db.commit()
            return lastrowid, rowcount

    @asynccontextmanager
    async def transaction(self):
        async with self._write_lock:
            await self.db.execute('BEGIN IMMEDIATE')
            try:
                yield self.db
                await self.db.commit()
            except Exception:
                await self.db.rollback()
                raise

    @staticmethod
    def _validated_updates(kwargs, allowed):
        unknown = set(kwargs) - allowed
        if unknown:
            raise ValueError('Unsupported database fields: ' + ', '.join(sorted(unknown)))
        return list(kwargs.items())

    async def get_guild(self, guild_id: int):
        return await self.fetchone('SELECT * FROM guild_settings WHERE guild_id = ?', (guild_id,))

    async def ensure_guild(self, guild_id: int):
        await self.execute('INSERT OR IGNORE INTO guild_settings(guild_id) VALUES (?)', (guild_id,))

    async def set(self, guild_id: int, **kwargs):
        items = self._validated_updates(kwargs, self.GUILD_FIELDS)
        if not items:
            return
        async with self.transaction() as db:
            await db.execute('INSERT OR IGNORE INTO guild_settings(guild_id) VALUES (?)', (guild_id,))
            sql = ', '.join(f'"{key}" = ?' for key, _ in items)
            await db.execute(f'UPDATE guild_settings SET {sql} WHERE guild_id = ?',
                             tuple(value for _, value in items) + (guild_id,))

    async def next_ticket_number(self, guild_id: int) -> int:
        async with self.transaction() as db:
            await db.execute('INSERT OR IGNORE INTO guild_settings(guild_id) VALUES (?)', (guild_id,))
            await db.execute('UPDATE guild_settings SET ticket_counter = ticket_counter + 1 WHERE guild_id = ?', (guild_id,))
            cur = await db.execute('SELECT ticket_counter FROM guild_settings WHERE guild_id = ?', (guild_id,))
            row = await cur.fetchone()
            await cur.close()
            return int(row['ticket_counter'])

    async def create_ticket(self, guild_id, channel_id, creator_id, ticket_type, reason, number):
        row_id, _ = await self.execute(
            'INSERT INTO tickets(guild_id, channel_id, creator_id, ticket_type, reason, ticket_number) VALUES (?, ?, ?, ?, ?, ?)',
            (guild_id, channel_id, creator_id, ticket_type, reason, number))
        return row_id

    async def get_ticket(self, channel_id: int):
        return await self.fetchone('SELECT * FROM tickets WHERE channel_id = ?', (channel_id,))

    async def update_ticket(self, channel_id: int, **kwargs):
        items = self._validated_updates(kwargs, self.TICKET_FIELDS)
        if items:
            sql = ', '.join(f'"{key}" = ?' for key, _ in items)
            await self.execute(f'UPDATE tickets SET {sql} WHERE channel_id = ?',
                               tuple(v for _, v in items) + (channel_id,))

    async def upsert_panel(self, guild_id, channel_id, message_id, title, description, ticket_type):
        await self.execute('''INSERT OR REPLACE INTO ticket_panels
            (guild_id, channel_id, message_id, title, description, ticket_type)
            VALUES (?, ?, ?, ?, ?, ?)''',
            (guild_id, channel_id, message_id, title, description, ticket_type))

    async def get_panel(self, message_id: int):
        return await self.fetchone('SELECT * FROM ticket_panels WHERE message_id = ?', (message_id,))

    async def delete_panel(self, message_id: int):
        await self.execute('DELETE FROM ticket_panels WHERE message_id = ?', (message_id,))

    async def get_panels_in_channel(self, channel_id: int):
        return await self.fetchall('SELECT * FROM ticket_panels WHERE channel_id = ?', (channel_id,))

    async def create_wl_app(self, guild_id, user_id, channel_id, nick, reason, about, age, invited_by=None):
        row_id, _ = await self.execute('''INSERT INTO whitelist_apps
            (guild_id, user_id, ticket_channel_id, minecraft_nick, join_reason, about_me, age, invited_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (guild_id, user_id, channel_id, nick, reason, about, age, invited_by))
        return row_id

    async def get_pending_app(self, guild_id: int, user_id: int):
        return await self.fetchone("SELECT * FROM whitelist_apps WHERE guild_id=? AND user_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1", (guild_id, user_id))

    async def get_app_by_channel(self, channel_id: int):
        return await self.fetchone('SELECT * FROM whitelist_apps WHERE ticket_channel_id = ?', (channel_id,))

    async def update_app(self, app_id: int, **kwargs):
        items = self._validated_updates(kwargs, self.APP_FIELDS)
        if items:
            sql = ', '.join(f'"{key}" = ?' for key, _ in items)
            await self.execute(f'UPDATE whitelist_apps SET {sql} WHERE id = ?', tuple(v for _, v in items) + (app_id,))

    async def save_code(self, code: str, user_id: int, guild_id: int, expires_at: str):
        async with self.transaction() as db:
            await db.execute('DELETE FROM whitelist_codes WHERE user_id=? AND guild_id=?', (user_id, guild_id))
            await db.execute('INSERT INTO whitelist_codes(code,user_id,guild_id,expires_at) VALUES(?,?,?,?)', (code, user_id, guild_id, expires_at))

    async def get_code(self, code: str, guild_id: int):
        return await self.fetchone("SELECT * FROM whitelist_codes WHERE code=? AND guild_id=? AND datetime(expires_at)>datetime('now')", (code, guild_id))

    async def delete_code(self, code: str):
        await self.execute('DELETE FROM whitelist_codes WHERE code = ?', (code,))

    async def delete_expired_codes(self):
        _, count = await self.execute("DELETE FROM whitelist_codes WHERE datetime(expires_at)<=datetime('now')")
        return count

    async def get_active_code_for_user(self, user_id: int, guild_id: int):
        return await self.fetchone("SELECT * FROM whitelist_codes WHERE user_id=? AND guild_id=? AND datetime(expires_at)>datetime('now') LIMIT 1", (user_id, guild_id))

    async def get_approved_app(self, guild_id: int, user_id: int):
        return await self.fetchone("SELECT * FROM whitelist_apps WHERE guild_id=? AND user_id=? AND status='approved' ORDER BY created_at DESC LIMIT 1", (guild_id, user_id))

    async def get_latest_app(self, guild_id: int, user_id: int):
        return await self.fetchone('SELECT * FROM whitelist_apps WHERE guild_id=? AND user_id=? ORDER BY created_at DESC LIMIT 1', (guild_id, user_id))

    async def close(self):
        if self.db:
            await self.db.close()
            self.db = None
