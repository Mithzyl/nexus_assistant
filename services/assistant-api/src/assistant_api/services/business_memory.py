"""Use Assistant-owned facts as bounded business data, never as identity or permissions."""
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from assistant_api.core.errors import ConflictError
from assistant_api.nexus_client.errors import NexusProtocolError

TYPE_CATEGORY = {'place': 'place', 'commute_preference': 'travel_policy',
                 'time_preference': 'routine', 'experience': 'procedure'}


MODES = {'walk': 'walking', 'bicycle': 'bicycling', 'transit': 'transit', 'drive': 'driving',
         'walking': 'walking', 'bicycling': 'bicycling', 'driving': 'driving', 'electrobike': 'electrobike'}


def business_projection(item):
    """Preserve open labels and legacy display hints without making them retrieval restrictions."""
    typed = item.get('typed_data') or {}
    app_data = item.get('app_data') or {}
    if not isinstance(app_data, dict):
        raise NexusProtocolError('记忆业务扩展结构无效。')
    fields = app_data.get('fields') or {}
    if not isinstance(typed, dict) or not isinstance(fields, dict):
        raise NexusProtocolError('记忆业务字段结构无效。')
    category = fields.get('business_category')
    if not isinstance(category, str) or not category.strip():
        kind = typed.get('memory_type')
        category = TYPE_CATEGORY.get(kind) if isinstance(kind, str) else None
        if category is None and any(k in fields for k in ('arrival_local_time', 'transport_mode', 'commute')):
            category = 'travel_policy'
    tags = fields.get('tags', [])
    tags = list(dict.fromkeys(tag for tag in tags if isinstance(tag, str) and tag.strip()))[:20] if isinstance(tags, list) else []
    result = {'category': category, 'tags': tags}
    mode = typed.get('travel_mode') or fields.get('transport_mode')
    business_value = fields.get('business_value')
    if isinstance(business_value, dict):
        mode = mode or business_value.get('transport_mode')
        result['value'] = business_value
    if isinstance(mode, str) and mode in MODES:
        result['transport_mode'] = MODES[mode]
    if isinstance(fields.get('role'), str):
        result['role'] = fields['role'][:100]
    if isinstance(fields.get('applicability'), dict):
        result['applicability'] = fields['applicability']
    elif 'applicability' in fields:
        # Invalid scope metadata must never widen an entry to all purposes.
        result['applicability'] = {'purposes': []}
    return result


def applicable(projection, purpose, timezone, matter_id=None, *, at=None):
    """Honor restrictions at the current or supplied business time; invalid scope never widens use."""
    rules = projection.get('applicability') or {}
    if rules.get('matter_id') is not None and rules['matter_id'] != matter_id:
        return False
    purposes = rules.get('purposes')
    if purposes is not None and (not isinstance(purposes, list) or purpose not in purposes):
        return False
    now = at if at is not None else datetime.now(UTC)
    for key, lower in (('effective_from', True), ('effective_until', False)):
        value = rules.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            return False
        try:
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                from datetime import date
                current, bound = now.astimezone(ZoneInfo(timezone)).date(), date.fromisoformat(value)
            else:
                current, bound = now, datetime.fromisoformat(value.replace('Z', '+00:00'))
                if bound.tzinfo is None:
                    return False
            if (lower and current < bound) or (not lower and current > bound):
                return False
        except (ValueError, TypeError):
            return False
    return True


async def validate_sources(client, user_id, sources):
    """Check owned entries by exact ID/current history, independent of their list offset."""
    if not sources:
        return
    settings = await client.application_memory_settings(user_id)
    if settings.get('enabled') is not True:
        raise ConflictError('business_memory_disabled', '记忆已关闭或不可确认，请重新处理当前安排。')
    for entry_id, version in {source['entry_id']: source['version'] for source in sources}.items():
        history = await client.list_application_memory(user_id, history_entry_id=entry_id, limit=1)
        items = history.get('items')
        if (history.get('entry_id') != entry_id or type(history.get('current_version')) is not int
                or not isinstance(items, list) or not items or not isinstance(items[0], dict)
                or items[0].get('version') != history['current_version']):
            raise ConflictError('business_memory_unconfirmed', '所用记忆的当前版本未能确认，未执行依赖它的新操作。')
        if history['current_version'] != version or items[0].get('status') != 'active':
            raise ConflictError('business_memory_changed', '所用记忆已变化，请读取最新事实后再安排。')
