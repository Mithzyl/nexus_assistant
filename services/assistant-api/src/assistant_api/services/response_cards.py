"""Validate declarative embedded cards and bind private business values locally.

AI chooses text, fields, timelines and images. Result bindings use the preceding
HTTP response, so its body is never copied into the model prompt. No HTML, code,
CSS or executable action is accepted in this presentation protocol.
"""
from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator

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
    """Present a labelled scalar, with an optional standard display format."""
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


def resolve_card_values(value, receipt: dict | None, item=None, depth: int = 0, results: dict | None = None):
    """Resolve result bindings and bounded collection templates without returning bodies to AI."""
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
        if resolved.get('format') == 'weekdays':
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


def validate_result_fields(value) -> None:
    """Require result-card field values and image URLs to be bound to actual HTTP output."""
    if isinstance(value, list):
        for entry in value:
            validate_result_fields(entry)
    elif isinstance(value, dict):
        if ('label' in value and 'value' in value) or 'src' in value:
            content = value.get('value') if 'value' in value else value['src']
            if not isinstance(content, dict) or not ({'$result', '$item'} & set(content)):
                raise ValueError('操作结果字段须引用真实执行结果')
        for child in value.values():
            validate_result_fields(child)


def prepare_cards(values: list[dict], receipt: dict | None, results: dict | None = None) -> list[dict]:
    """Bind cards to real results; pending or unapplied operations cannot claim completion."""
    if len(values) > 6:
        raise ValueError('一次最多返回6张卡片')
    cards = []
    for value in values:
        kind = value.get('kind', 'info')
        if kind == 'result':
            if not receipt:
                raise ValueError('尚无可核对的执行结果')
            data = receipt.get('data')
            if receipt.get('http_status') == 202 or (isinstance(data, dict) and data.get('applied') is False):
                raise ValueError('当前操作尚未完成保存')
            validate_result_fields(value.get('blocks', []))
        resolved = resolve_card_values(value, receipt, results=results)
        resolved['status'] = (receipt.get('status', 'unknown') if kind == 'result' else 'suggestion' if kind == 'plan' else 'info')
        if resolved['status'] == 'succeeded':
            resolved['status'] = 'completed'
        cards.append(EmbeddedCard.model_validate(resolved).model_dump(mode='json', exclude_none=True))
    return cards


def presentation_schema() -> dict:
    """Publish the current presentation schema as prompt context rather than a business registry."""
    schema = EmbeddedCard.model_json_schema()
    binding = {'type':'object', 'properties': {'$result':{'type':'string'}, 'step':{'type':'integer','minimum':0}, 'field':{'type':'string'}, 'where':{'type':'object'}}, 'required':['$result'], 'additionalProperties':False}
    item_binding = {'type':'object','properties':{'$item':{'type':'string'}},'required':['$item'],'additionalProperties':False}
    fields = schema['$defs']['CardField']['properties']
    fields['value']['anyOf'].extend([binding, item_binding])
    fields['format']['enum'].extend(['weekdays','list'])
    return schema
