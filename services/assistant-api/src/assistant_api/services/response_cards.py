"""Validate declarative embedded cards and bind private business values locally.

AI optionally chooses text, fields, timelines and images. Assistant validates the
same input schema it publishes, resolves real tool results and converts display
values before validating browser-ready output. No executable content is accepted.
"""
from __future__ import annotations

import copy
import json
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, ValidationError, field_validator

from assistant_api.services.api_operations import read_result_pointer, resolve_result_references


class CardModel(BaseModel):
    """Reject unrecognised presentation fields throughout the card tree."""
    model_config = ConfigDict(extra='forbid')


def validate_media_url(value: str) -> str:
    """Accept bounded HTTPS and same-origin paths for images and ordinary links."""
    if len(value) > 2000 or any(c.isspace() or ord(c) < 32 for c in value) or '\\' in value:
        raise ValueError('图片或链接地址无效')
    parsed = urlsplit(value)
    if value.startswith('/') and not value.startswith('//'):
        return value
    if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password:
        return value
    raise ValueError('图片和链接只支持HTTPS或本站路径')


class CardImage(CardModel):
    """Describe one image without exposing HTML or browser credentials."""
    src: str
    alt: str = Field(min_length=1, max_length=160)
    caption: str | None = Field(default=None, max_length=300)
    _url = field_validator('src')(validate_media_url)


class CardField(CardModel):
    """Present a labelled scalar after server-side unit conversion and result binding."""
    label: str = Field(min_length=1, max_length=60)
    value: str | int | FiniteFloat | bool | None
    format: Literal['text', 'datetime', 'date', 'time', 'number'] = 'text'


class TextBlock(CardModel):
    """Render a short plain-text paragraph."""
    type: Literal['text']
    text: str = Field(min_length=1, max_length=2000)


class FieldsBlock(CardModel):
    """Render an ordered set of business-independent labels and values."""
    type: Literal['fields']
    items: list[CardField] = Field(min_length=1, max_length=24)


class ImageBlock(CardImage):
    """Render a full-width card image with accessible text."""
    type: Literal['image']


class CardLink(CardModel):
    """Offer a normal navigation link, never a write action or script."""
    label: str = Field(min_length=1, max_length=80)
    href: str
    _url = field_validator('href')(validate_media_url)


class TimelineItem(CardModel):
    """Describe one stop, appointment or step using reusable display fields."""
    title: str = Field(min_length=1, max_length=180)
    subtitle: str | None = Field(default=None, max_length=300)
    time: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=700)
    category: str | None = Field(default=None, max_length=40)
    image: CardImage | None = None
    fields: list[CardField] = Field(default_factory=list, max_length=12)
    link: CardLink | None = None
    connector: str | None = Field(default=None, max_length=180, description='从上一节点到当前节点的交通或衔接说明；第一项省略')


class TimelineBlock(CardModel):
    """Render a sequence of places or other ordered items without a travel adapter."""
    type: Literal['timeline']
    items: list[TimelineItem] = Field(min_length=1, max_length=30)


Block = Annotated[TextBlock | FieldsBlock | ImageBlock | TimelineBlock, Field(discriminator='type')]


class EmbeddedCard(CardModel):
    """Keep content model-authored while completion status is derived by Assistant."""
    kind: Literal['result', 'plan', 'info'] = 'info'
    title: str = Field(min_length=1, max_length=180)
    subtitle: str | None = Field(default=None, max_length=500)
    blocks: list[Block] = Field(min_length=1, max_length=12)
    status: Literal['completed', 'failed', 'unknown', 'suggestion', 'info'] = 'info'


STATUS_DISPLAY_LABELS = {
    'todo_status': {'open': '未完成', 'done': '已完成'},
    'schedule_status': {'scheduled': '已安排', 'cancelled': '已取消'},
    'reminder_status': {'scheduled': '待提醒', 'due': '已到提醒时间', 'acknowledged': '已确认', 'cancelled': '已取消'},
    'matter_status': {'active': '进行中', 'waiting': '等待中', 'archived': '已归档', 'deleted': '已删除'},
}


def format_card_status(value, display_format):
    """Translate a bound status using an explicit domain; retain null/unknown codes without claiming success."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('状态显示需要真实结果中的字符串状态值。')
    return STATUS_DISPLAY_LABELS[display_format].get(value, value)


def format_card_seconds(value, *, relative=False):
    """Convert a finite second count into explicit units, retaining the direction of reminder offsets."""
    if value is None:
        return None
    if isinstance(value,bool) or not isinstance(value,(int,float)):
        raise ValueError('秒数格式需要真实结果中的有限数字。')
    seconds=Decimal(str(value))
    if not seconds.is_finite():
        raise ValueError('秒数格式不支持无限值。')
    remaining=abs(seconds)
    parts=[]
    for size,unit in ((86400,'天'),(3600,'小时'),(60,'分钟')):
        count=int(remaining//size)
        if count:
            parts.append(f'{count}{unit}')
            remaining-=count*size
    if remaining or not parts:
        parts.append(format(remaining.normalize(),'f')+'秒')
    if relative and not seconds:
        return '开始时提醒'
    direction=('提前' if seconds<0 else '延后') if relative else ('-' if seconds<0 else '')
    return direction+''.join(parts)


def bound_field_name(binding):
    """Read the source field from a result/item pointer, without guessing its unit from an AI-authored label."""
    if not isinstance(binding,dict):
        return None
    field=binding.get('field') or binding.get('$result') or binding.get('$item')
    return field.rsplit('/',1)[-1].replace('~1','/').replace('~0','~') if isinstance(field,str) else None


def resolve_card_values(value, receipt: dict | None, item=None, depth: int = 0, results: dict | None = None):
    """Bind actual values and convert declared second units before validating browser-ready card fields."""
    if depth > 12:
        raise ValueError('卡片内容嵌套过深')
    if isinstance(value, list):
        return [resolve_card_values(v, receipt, item, depth + 1, results) for v in value]
    if not isinstance(value, dict):
        return value
    if '$each' in value:
        if set(value) != {'$each', 'template'} or not receipt or receipt.get('status') != 'succeeded':
            raise ValueError('卡片集合引用无效')
        rows = read_result_pointer(receipt.get('data'), value['$each'])
        if not isinstance(rows, list) or len(rows) > 30:
            raise ValueError('卡片集合必须是至多30项的列表')
        return [resolve_card_values(value['template'], receipt, row, depth + 1, results) for row in rows]
    if '$item' in value:
        if set(value) != {'$item'} or item is None:
            raise ValueError('卡片条目引用无效')
        return read_result_pointer(item, value['$item'])
    if '$result' in value:
        if 'where' in value:
            return resolve_result_references(value, receipt, results=results)
        if set(value) - {'$result', 'step', 'field'}:
            raise ValueError('展示引用只允许读取真实结果。')
        source = results.get(str(value['step'])) if 'step' in value and results else receipt
        if not source or source.get('status') != 'succeeded':
            raise ValueError('没有可展示的成功结果。')
        selected = read_result_pointer(source.get('data'), value['$result'])
        return read_result_pointer(selected, '/' + value['field']) if 'field' in value else selected
    resolved = {k: resolve_card_values(v, receipt, item, depth + 1, results) for k, v in value.items()}
    if 'label' in resolved and 'value' in resolved:
        content = resolved['value']
        field=bound_field_name(value.get('value'))
        display_format=resolved.get('format')
        if field in {'reminder_offset_seconds','schedule_offset_seconds'}:
            # These API fields are signed seconds, even when a model labels them
            # "提前分钟" and requests a raw number. Never present -900 as minutes.
            display_format='relative_seconds'
            resolved['label']='提醒'
        if display_format in STATUS_DISPLAY_LABELS:
            resolved['value'] = format_card_status(content, display_format)
            resolved['format'] = 'text'
        elif display_format in {'duration_seconds','relative_seconds'}:
            resolved['value']=format_card_seconds(content,relative=display_format=='relative_seconds')
            resolved['format']='text'
        elif resolved.get('format') == 'weekdays':
            if not isinstance(content, list) or not content or any(type(day) is not int or day not in range(1, 8) for day in content):
                raise ValueError('weekdays格式需要星期1至7的数组。')
            days = sorted(set(content)); names = ['周一','周二','周三','周四','周五','周六','周日']
            resolved['value'] = names[days[0]-1]+'至'+names[days[-1]-1] if len(days)>2 and days == list(range(days[0],days[-1]+1)) else '、'.join(names[day-1] for day in days)
            resolved['format'] = 'text'
        elif resolved.get('format') == 'list':
            if not isinstance(content, list) or len(content)>30 or any(not isinstance(v,(str,int,float,bool)) for v in content):
                raise ValueError('list格式需要至多30项的标量数组。')
            resolved['value'] = '、'.join(str(v) for v in content)
            resolved['format'] = 'text'
    return resolved


def validate_card_input(value, validator, index):
    """Validate the published model-input schema and identify the failing path without echoing values."""
    error = next(validator.iter_errors(value), None)
    if error is None:
        return
    pending, leaves = [error], []
    while pending:
        item = pending.pop()
        if item.context:
            pending.extend(item.context)
        else:
            leaves.append(item)
    error = max(leaves, key=lambda item: len(item.absolute_path))
    path = '.'.join(map(str, ['cards', index, *error.absolute_path]))
    requirement = (json.dumps(error.validator_value, ensure_ascii=False)[:200]
                   if error.validator in {'type', 'enum', 'const', 'required', 'minLength', 'maxLength', 'minItems', 'maxItems'}
                   else '按card_schema提供字段')
    hint = ('结果卡片的value/src必须使用$result或$item引用；不能填业务结果常量。'
            if isinstance(value, dict) and value.get('kind') == 'result' else
            'info/plan普通说明允许文字常量；状态显示格式必须引用真实状态。')
    raise ValueError(f'{path}: 不符合{error.validator}要求（{requirement}）。{hint}')


def prepare_cards(values: list[dict], receipt: dict | None, results: dict | None = None, *, index_offset: int = 0) -> list[dict]:
    """Validate the published input, bind real values and produce compatible output without modifying business data."""
    if len(values) > 6:
        raise ValueError('一次最多返回6张卡片')
    cards = []
    validator = Draft202012Validator(presentation_schema())
    for index, value in enumerate(values, start=index_offset):
        validate_card_input(value, validator, index)
        kind = value.get('kind', 'info')
        if kind == 'result':
            if not receipt:
                raise ValueError('尚无可核对的执行结果')
            data = receipt.get('data')
            if receipt.get('http_status') == 202 or (isinstance(data, dict) and data.get('applied') is False):
                raise ValueError('当前操作尚未完成保存')
        # Legacy model-authored top-level status was never authoritative. Ignore
        # it before resolving bindings, then derive the browser status below.
        resolved = resolve_card_values({key: item for key, item in value.items() if key != 'status'}, receipt, results=results)
        resolved['status'] = (receipt.get('status', 'unknown') if kind == 'result' else 'suggestion' if kind == 'plan' else 'info')
        if resolved['status'] == 'succeeded':
            resolved['status'] = 'completed'
        try:
            cards.append(EmbeddedCard.model_validate(resolved).model_dump(mode='json', exclude_none=True))
        except ValidationError as exc:
            error = exc.errors(include_input=False, include_url=False)[0]
            path = '.'.join(map(str, ['cards', index, *error['loc']]))
            raise ValueError(f'{path}: 引用解析后的值不符合展示字段类型（{error["type"]}）。') from exc
    return cards


def presentation_schema() -> dict:
    """Define model-authored cards once for both published instructions and actual input validation.

    Browser output remains EmbeddedCard. Input permits result/item bindings and
    collection templates; result-card fields/images require references, whereas
    advice literals remain valid. Resolved values still need output validation.
    """
    schema = EmbeddedCard.model_json_schema()
    definitions = schema['$defs']
    definitions['ResultBinding'] = {'type': 'object', 'properties': {
        '$result': {'type': 'string'}, 'step': {'type': 'integer', 'minimum': 0},
        'field': {'type': 'string'}, 'where': {'type': 'object', 'minProperties': 1}},
        'required': ['$result'], 'additionalProperties': False}
    definitions['ItemBinding'] = {'type': 'object', 'properties': {'$item': {'type': 'string'}},
        'required': ['$item'], 'additionalProperties': False}
    definitions['ValueBinding'] = {'oneOf': [{'$ref': '#/$defs/ResultBinding'}, {'$ref': '#/$defs/ItemBinding'}]}
    binding = {'$ref': '#/$defs/ValueBinding'}

    def allow_bindings(properties, names):
        """Keep scalar constraints while permitting a value resolved from a real response."""
        for name in names:
            properties[name] = {'anyOf': [properties[name], copy.deepcopy(binding)]}

    allow_bindings(schema['properties'], ['title', 'subtitle'])
    for name, fields in {
        'CardField': ['label', 'value'], 'CardImage': ['src', 'alt', 'caption'],
        'ImageBlock': ['src', 'alt', 'caption'], 'CardLink': ['label', 'href'],
        'TextBlock': ['text'], 'TimelineItem': ['title', 'subtitle', 'time', 'description', 'category', 'connector'],
    }.items():
        allow_bindings(definitions[name]['properties'], fields)
    field_format = definitions['CardField']['properties']['format']
    field_format['enum'].extend(['weekdays', 'list', 'duration_seconds', 'relative_seconds', *STATUS_DISPLAY_LABELS])
    field_format['description'] = ('duration_seconds转换真实秒数；relative_seconds转换带符号提醒偏移，不能将秒数标为分钟。'
        '状态格式只用于真实结果引用，未知值保留原码；固定映射：' + json.dumps(STATUS_DISPLAY_LABELS, ensure_ascii=False))
    scalar_value = copy.deepcopy(definitions['CardField']['properties']['value'])
    definitions['CardField']['properties']['value'] = {'anyOf': [scalar_value, {'type': 'array'}]}
    definitions['CardField']['allOf'] = [
        {'if': {'required': ['format'], 'properties': {'format': {'enum': list(STATUS_DISPLAY_LABELS)}}},
         'then': {'properties': {'value': binding}}},
        {'if': {'required': ['format'], 'properties': {'format': {'const': 'weekdays'}}},
         'then': {'properties': {'value': {'anyOf': [binding, {'type': 'array', 'minItems': 1,
             'items': {'type': 'integer', 'minimum': 1, 'maximum': 7}}]}}}},
        {'if': {'required': ['format'], 'properties': {'format': {'const': 'list'}}},
         'then': {'properties': {'value': {'anyOf': [binding, {'type': 'array', 'maxItems': 30,
             'items': {'type': ['string', 'number', 'boolean']}}]}}}},
        {'if': {'required': ['format'], 'properties': {'format': {'enum': ['duration_seconds', 'relative_seconds']}}},
         'then': {'properties': {'value': {'anyOf': [binding, {'type': ['number', 'null']}]}}}},
        {'if': {'not': {'required': ['format'], 'properties': {'format': {'enum': ['weekdays', 'list']}}}},
         'then': {'properties': {'value': scalar_value}}},
    ]
    schema['properties']['status'] = {'readOnly': True,
        'description': '由Assistant根据实际执行状态生成；省略即可，兼容旧格式的任何传入值会被忽略，不作状态依据。'}

    def collection_items(item_name):
        """Share array/template structure while retaining result-specific item constraints."""
        return {'anyOf': [
            {'type': 'array', 'items': {'$ref': '#/$defs/' + item_name}, 'minItems': 1, 'maxItems': 30},
            {'type': 'object', 'properties': {'$each': {'type': 'string'}, 'template': {'$ref': '#/$defs/' + item_name}},
             'required': ['$each', 'template'], 'additionalProperties': False}]}
    definitions['TimelineBlock']['properties']['items'] = collection_items('TimelineItem')
    for target, (original, properties) in {
        'ResultCardField': ('CardField', {'value': binding}),
        'ResultCardImage': ('CardImage', {'src': binding}),
        'ResultImageBlock': ('ImageBlock', {'src': binding}),
        'ResultFieldsBlock': ('FieldsBlock', {'items': {'items': {'$ref': '#/$defs/ResultCardField'}}}),
        'ResultTimelineItem': ('TimelineItem', {
            'fields': {'items': {'$ref': '#/$defs/ResultCardField'}},
            'image': {'anyOf': [{'$ref': '#/$defs/ResultCardImage'}, {'type': 'null'}]}}),
        'ResultTimelineBlock': ('TimelineBlock', {'items': collection_items('ResultTimelineItem')}),
    }.items():
        definitions[target] = {'allOf': [{'$ref': '#/$defs/' + original}, {'properties': properties}]}
    schema['allOf'] = [{'if': {'required': ['kind'], 'properties': {'kind': {'const': 'result'}}},
        'then': {'properties': {'blocks': {'items': {'oneOf': [
            {'$ref': '#/$defs/' + name} for name in ('TextBlock', 'ResultFieldsBlock', 'ResultImageBlock', 'ResultTimelineBlock')
        ]}}}}}]
    return schema
