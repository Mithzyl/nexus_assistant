"""Retrieve published business operations and execute validated local HTTP calls.

OpenAPI is read from the current app, not scanned from source or duplicated in a
tool registry. New /api/v1 business routes participate automatically; publishers
may set x-assistant.enabled=false. Explicit user intent authorizes mutations; preview routes still prepare drafts. Authentication and business
version checks remain in the existing HTTP handlers.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote

import httpx
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, Field, model_validator


class OperationCall(BaseModel):
    """Accept an operation reference and typed JSON arguments, never a URL or credential."""

    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=1, max_length=180)
    path_parameters: dict[str, Any] = Field(default_factory=dict)
    query_parameters: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] = Field(default_factory=dict)
    summary: str = Field(default="执行这项操作", min_length=1, max_length=300)


class OperationDecision(BaseModel):
    """Describe exactly one next action, clarification, catalogue query or final answer."""

    model_config = ConfigDict(extra="forbid")
    decision: Literal["call", "answer", "clarify", "ask_user", "search"]
    call: OperationCall | None = None
    answer: str | None = Field(default=None, max_length=6000)
    question: str | None = Field(default=None, max_length=2000)
    search_query: str | None = Field(default=None, max_length=300)
    choices: list[str] = Field(default_factory=list, max_length=6)
    cards: list[dict[str, Any]] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def require_action_fields(self):
        """Refuse incomplete or contradictory model decisions before any dispatch."""
        required = {"call": self.call, "answer": self.answer, "clarify": self.question, "ask_user": self.question, "search": self.search_query}
        if not required[self.decision]:
            raise ValueError("当前决策缺少必要内容")
        if self.decision != "call" and self.call is not None:
            raise ValueError("非调用决策不能附带调用")
        if self.decision != "answer" and self.cards:
            raise ValueError("卡片只能随最终回答返回")
        if any(not choice.strip() or len(choice) > 160 for choice in self.choices):
            raise ValueError("选项内容无效")
        return self


def search_terms(text: str) -> set[str]:
    """Combine identifiers, words and Chinese bigrams without a business-specific registry."""
    words = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text.lower())
    terms = set(words)
    # Vocabulary bridges English OpenAPI identifiers and ordinary Chinese input;
    # these are search terms, never operation names or execution registrations.
    vocabulary = {'日程': 'schedule schedules calendar', '事项': 'matter matters', '安排': 'schedule', '会议': 'schedule',
                  '待办': 'todo task', '提醒': 'reminder', '通勤': 'commute',
                  '草稿': 'draft', '修改': 'update edit patch', '调整': 'update edit patch',
                  '补充': 'update edit', '确认': 'confirm', '查询': 'list get',
                  '删除': 'delete', '取消': 'cancel', '新增': 'create post'}
    for phrase, aliases in vocabulary.items():
        if phrase in text:
            terms.update(aliases.split())
    for word in words:
        if re.search(r"[\u4e00-\u9fff]", word):
            terms.update(word[i:i + 2] for i in range(len(word) - 1))
    return terms


@dataclass
class PublishedOperation:
    """Keep the actual route and its selected parameter definitions together."""

    operation_id: str
    method: str
    path: str
    definition: dict
    components: dict
    effect: str

    def model_description(self) -> dict:
        """Provide input schemas and response field names, excluding unrelated definitions."""
        return {"operation_id": self.operation_id, "method": self.method, "path": self.path,
                "effect": self.effect, **self.definition, "components": {"schemas": self.components}}


class OperationCatalog:
    """Build a bounded searchable catalogue from the app's current published OpenAPI."""

    def __init__(self, app):
        """Select business operations; authentication and recursive AI endpoints stay private."""
        self.document = app.openapi()
        self.operations: dict[str, PublishedOperation] = {}
        excluded = ("/api/v1/auth", "/api/v1/memory", "/api/v1/data-sources", "/api/v1/sync", "/api/v1/calendar-subscription", "/api/v1/calendar/")
        for path, item in self.document["paths"].items():
            if not path.startswith('/api/v1/') or path.startswith(excluded) or '/intake' in path or '/memory' in path or path.startswith('/api/v1/asks'):
                continue
            for method, schema in item.items():
                if method not in {"get", "post", "patch", "put", "delete"}:
                    continue
                options = schema.get('x-assistant', {})
                if options.get('enabled') is False:
                    continue
                oid = schema.get('operationId')
                if not oid or oid in self.operations:
                    raise ValueError('业务接口必须具有唯一 operationId')
                definition = {k: schema[k] for k in ('summary', 'description', 'tags', 'requestBody') if k in schema}
                definition['parameters'] = item.get('parameters', []) + schema.get('parameters', [])
                components: dict = {}
                self._collect_components(definition, components)
                responses = schema.get('responses', {})
                response_schema = next((v.get('content', {}).get('application/json', {}).get('schema', {}) for k, v in responses.items() if str(k).startswith('2')), {})
                if '$ref' in response_schema:
                    response_schema = self.document.get('components', {}).get('schemas', {}).get(response_schema['$ref'].split('/')[-1], {})
                definition['response_fields'] = list(response_schema.get('properties', {}))
                self.operations[oid] = PublishedOperation(oid, method.upper(), path, definition, components, 'read' if method == 'get' else 'preview' if options.get('effect') == 'preview' else 'write')

    def _collect_components(self, value, selected: dict) -> None:
        """Resolve only local schema references needed by one operation's arguments."""
        if isinstance(value, dict):
            ref = value.get('$ref')
            if ref:
                if not ref.startswith('#/components/schemas/'):
                    raise ValueError('接口只支持本地 OpenAPI schema 引用')
                name = ref.split('/')[-1]
                if name not in selected:
                    selected[name] = self.document['components']['schemas'][name]
                    self._collect_components(selected[name], selected)
            for key, child in value.items():
                if key != '$ref':
                    self._collect_components(child, selected)
        elif isinstance(value, list):
            for child in value:
                self._collect_components(child, selected)

    def search(self, query: str, limit: int = 5) -> list[PublishedOperation]:
        """Rank operation summaries and fields; fetch full definitions only for selected matches."""
        terms = search_terms(query)
        ranked = []
        for operation in self.operations.values():
            text = json.dumps(operation.definition, ensure_ascii=False) + ' ' + operation.path + ' ' + operation.operation_id
            # An operation's purpose must outweigh incidental matches in long
            # parameter descriptions (for example many endpoints mention version).
            headline = operation.definition.get('summary', '') + ' ' + operation.path + ' ' + operation.operation_id
            score = 8 * len(terms & search_terms(headline)) + len(terms & search_terms(text))
            ranked.append((score, operation.operation_id, operation))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        matches = [row[2] for row in ranked if row[0] > 0]
        # An unknown/new user can request another catalogue query; do not inject
        # the entire application's OpenAPI merely because matching was sparse.
        return matches[:min(limit, 8)]

    def validate_call(self, value: dict | OperationCall) -> tuple[PublishedOperation, OperationCall]:
        """Validate exact published fields and schemas before dispatching a local operation."""
        call = value if isinstance(value, OperationCall) else OperationCall.model_validate(value)
        if len(call.model_dump_json()) > 24000:
            raise ValueError('调用参数过长')
        operation = self.operations.get(call.operation_id)
        if operation is None:
            raise ValueError('调用的接口未发布或不可用于助手')
        for location, values in [('path', call.path_parameters), ('query', call.query_parameters), ('header', call.headers)]:
            parameters = {p['name']: p for p in operation.definition['parameters'] if p.get('in') == location}
            if set(values) - set(parameters):
                raise ValueError('调用包含接口未定义的参数')
            for name, parameter in parameters.items():
                if name not in values:
                    if parameter.get('required') and name.lower() != 'idempotency-key':
                        raise ValueError(f'缺少接口参数：{name}')
                    continue
                self._validate_schema(parameter.get('schema', {}), values[name], operation)
            if location == 'header' and any(k.lower() in {'authorization', 'cookie', 'host'} for k in values):
                raise ValueError('认证信息不能由模型提供')
        body = operation.definition.get('requestBody', {})
        schema = body.get('content', {}).get('application/json', {}).get('schema')
        if schema is not None:
            self._validate_schema(schema, call.body, operation)
        elif call.body:
            raise ValueError('接口不接受 JSON 请求体')
        for value in call.path_parameters.values():
            if not isinstance(value, (str, int)) or str(value) in {'.', '..'} or any(x in str(value) for x in '/\\?#%'):
                raise ValueError('路径参数无效')
        return operation, call

    @staticmethod
    def _validate_schema(schema: dict, value, operation: PublishedOperation) -> None:
        """Use the full JSON Schema validator, including referenced and conditional types."""
        validator = Draft202012Validator({**schema, 'components': {'schemas': operation.components}}, format_checker=FormatChecker())
        error = next(validator.iter_errors(value), None)
        if error:
            # Report location and rule, not a possibly sensitive raw input value.
            raise ValueError('参数不符合接口定义：' + '.'.join(map(str, error.path)) + ' (' + str(error.validator) + ')')


def result_shape(value, path: str = '', depth: int = 0) -> dict:
    """Expose bounded JSON field names and collection counts without business values."""
    if depth > 4:
        return {}
    if isinstance(value, dict):
        shape = {path: {'type': 'object', 'fields': list(value)[:40]}}
        for key, child in list(value.items())[:40]:
            if isinstance(child, (dict, list)):
                escaped = key.replace('~', '~0').replace('/', '~1')
                shape.update(result_shape(child, path + '/' + escaped, depth + 1))
            if len(shape) >= 48:
                break
        return dict(list(shape.items())[:48])
    if isinstance(value, list):
        fields = list(value[0])[:40] if value and isinstance(value[0], dict) else []
        return {path: {'type': 'array', 'count': len(value), 'item_fields': fields}}
    return {}


def read_result_pointer(value, pointer: str):
    """Read JSON Pointer locally; a position cannot choose among multiple records."""
    if not isinstance(pointer, str) or len(pointer) > 600 or (pointer and not pointer.startswith('/')):
        raise ValueError('结果引用路径无效。')
    for part in pointer.split('/')[1:] if pointer else []:
        key = part.replace('~1', '/').replace('~0', '~')
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and len(value) == 1 and key == '0':
            value = value[0]
        else:
            raise ValueError('结果引用不存在，或不能按位置选择多条记录。')
    return value


def _same_result_value(left, right) -> bool:
    """Compare scalar filters and equivalent timezone-aware ISO date-time instants."""
    if isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
        return False
    if type(left) is type(right) and left == right:
        return True
    if isinstance(left, str) and isinstance(right, str) and 'T' in left and 'T' in right:
        try:
            a = datetime.fromisoformat(left.replace('Z', '+00:00'))
            b = datetime.fromisoformat(right.replace('Z', '+00:00'))
            return a.tzinfo is not None and b.tzinfo is not None and a == b
        except ValueError:
            return False
    return False


def resolve_result_references(value, receipt: dict | None, depth: int = 0):
    """Resolve AI-authored references to the last successful response before schema validation.

    Filters must identify exactly one record. Overrides copy an existing object
    while changing explicit fields, so unchanged private fields stay local.
    """
    if depth > 12:
        raise ValueError('结果引用嵌套过深。')
    if isinstance(value, list):
        return [resolve_result_references(item, receipt, depth + 1) for item in value]
    if not isinstance(value, dict):
        return value
    if '$result' not in value:
        return {k: resolve_result_references(v, receipt, depth + 1) for k, v in value.items()}
    if set(value) - {'$result', 'where', 'field', 'overrides'} or not receipt or receipt.get('status') != 'succeeded':
        raise ValueError('没有可用的成功结果引用。')
    selected = read_result_pointer(receipt.get('data'), value['$result'])
    if isinstance(selected, list):
        conditions = value.get('where')
        if not isinstance(conditions, dict) or not conditions or len(conditions) > 8:
            raise ValueError('必须提供条件来核对原记录。')
        matches = [row for row in selected if isinstance(row, dict) and all(
            key in row and _same_result_value(row[key], expected) for key, expected in conditions.items())]
        if len(matches) != 1:
            raise ValueError('查询结果未唯一匹配目标，请补充可区分的信息。')
        selected = matches[0]
    elif 'where' in value:
        raise ValueError('筛选条件只能用于结果列表。')
    if 'field' in value:
        field = value['field']
        if not isinstance(field, str) or not isinstance(selected, dict) or field not in selected:
            raise ValueError('引用字段不存在。')
        selected = selected[field]
    selected = copy.deepcopy(selected)
    if 'overrides' in value:
        if not isinstance(selected, dict) or not isinstance(value['overrides'], dict):
            raise ValueError('只能替换已有对象中的指定字段。')
        selected.update(resolve_result_references(value['overrides'], receipt, depth + 1))
    return selected


def model_receipt(receipt: dict) -> dict:
    """Send status, reference IDs and result shape to AI, never business response values."""
    data = receipt.get('data')
    references = {}
    if isinstance(data, dict):
        for key, value in data.items():
            if (key.endswith('_id') or key in {'version', 'revision', 'status'}) and isinstance(value, (str, int, bool)):
                references[key] = value
        draft = data.get('draft')
        if isinstance(draft, dict):
            references['draft'] = {k: draft[k] for k in ('draft_id', 'version', 'status') if k in draft}
    return {k: receipt[k] for k in ('status', 'http_status', 'operation_id') if k in receipt} | {'references': references, 'result_shape': result_shape(data)}


async def execute_call(app, authorization: str, operation: PublishedOperation, call: OperationCall, execution_id: str) -> dict:
    """Dispatch in-process HTTP through original authentication/handlers without following redirects."""
    path = operation.path
    for key, value in call.path_parameters.items():
        path = path.replace('{' + key + '}', quote(str(value), safe=''))
    headers = {**call.headers, 'Authorization': authorization, 'Idempotency-Key': execution_id}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://assistant-internal', follow_redirects=False) as client:
        response = await client.request(operation.method, path, params=call.query_parameters, json=call.body if operation.method != 'GET' else None, headers=headers)
    if len(response.content) > 200000:
        return {'operation_id': call.operation_id, 'status': 'unknown', 'http_status': response.status_code, 'data': None, 'error': '结果过大，请在对应页面核对。'}
    try:
        data = response.json()
    except ValueError:
        data = None
    return {'operation_id': call.operation_id, 'status': 'succeeded' if 200 <= response.status_code < 300 else 'failed', 'http_status': response.status_code, 'data': data}
