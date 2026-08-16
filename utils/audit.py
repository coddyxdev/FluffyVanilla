import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger('FluFFy.Audit')


async def record_audit(bot, guild_id: int, action: str, *, actor_id=None,
                       target_id=None, reason=None, metadata=None):
    """Best-effort structured audit record. Audit failures never break user actions."""
    try:
        payload = json.dumps(metadata or {}, ensure_ascii=False, separators=(',', ':'))
        await bot.db.execute(
            '''INSERT INTO audit_log
               (guild_id, actor_id, target_id, action, reason, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (guild_id, actor_id, target_id, action, reason, payload,
             datetime.now(timezone.utc).isoformat()),
        )
    except Exception:
        logger.exception('Cannot write audit event %s', action)
