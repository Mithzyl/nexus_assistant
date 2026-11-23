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
    headers: dict[str, Any] = Field(default_factory=dict)
    body: dict[str, Any] | None = None
    summary: str = Field(default="执行这项操作", min_length=1, max_length=300)


class OperationDecision(BaseModel):
    """Describe exactly one next action, clarification, catalogue query or final answer."""

    model_config = ConfigDict(extra="forbid")
    decision: Literal["call", "answer", "clarify", "ask_user", "search"]
    call: OperationCall | None = None
    answer: str | None = Field(default=None, max_length=6000)
    question: str | None = Field(default=None, max_length=2000)
    search_query: str | None = Field(default=None, max_length=300)
    search_offset: int = Field(default=0, ge=0, le=100000)
    choices: list[str] = Field(default_factory=list, max_length=6)
    pending_reply_disposition: Literal["answered", "new_topic"] | None = None
    notify: bool = True
    cards: list[dict[str, Any]] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def require_action_fields(self):
        """Require decision-specific fields; index searches may use an empty query to paginate all descriptions."""
        required = {"call": self.call, "answer": self.answer, "clarify": self.question, "ask_user": self.question, "search": self.search_query}
        if (self.decision == 'search' and self.search_query is None) or (self.decision != 'search' and not required[self.decision]):
            field = {'call': 'call', 'answer': 'answer', 'clarify': 'question', 'ask_user': 'question', 'search': 'search_query'}[self.decision]
            raise ValueError(f"decision={self.decision} 必须提供非空 {field}。")
        if self.decision != "call" and self.call is not None:
            raise ValueError("非调用决策不能附带调用")
        if self.decision not in {'answer','ask_user','clarify'} and self.cards:
            raise ValueError('卡片只能随回答或询问用户返回')
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
                  '删除': 'delete', '取消': 'cancel', '新增': 'create post', '创建': 'create post', '新建': 'create post',
                  '搜索': 'search query', '地点': 'place', '路线': 'route', '测算': 'estimate',
                  '记忆': 'memory', '习惯': 'memory preference', '偏好': 'memory preference', '记住': 'memory save'}
    for phrase, aliases in vocabulary.items():
        if phrase in text:
            terms.update(aliases.split())
    for word in words:
        if re.search(r"[\u4e00-\u9fff]", word):
            terms.update(word[i:i + 2] for i in range(len(word) - 1))
    return terms


OPERATION_URI_PREFIX = 'assistant://operations/'
INDEX_PAGE_SIZE = 128
DEFINITION_RESOLVER = 'resolve_operation_definitions'


@dataclass
class PublishedOperation:
    """Keep the actual route and its selected parameter definitions together."""

    operation_id: str
    method: str
    path: str
    definition: dict
    components: dict
    effect: str

    @property
    def uri(self) -> str:
        """Identify a local published definition; this URI is never fetched over the network."""
        return OPERATION_URI_PREFIX + quote(self.operation_id, safe='')

    def model_description(self, *, include_components: bool = True) -> dict:
        """Describe one operation, optionally leaving shared schemas to the enclosing model context."""
        description = {"operation_id": self.operation_id, "uri": self.uri, "method": self.method, "path": self.path,
                       "effect": self.effect, **self.definition}
        if include_components:
            description['components'] = {'schemas': self.components}
        return description


    def parameter_description(self) -> tuple[dict, dict]:
        """Publish call inputs and result field names without response schemas or repeated index metadata."""
        full = self.model_description(include_components=False)
        keys = {'operation_id', 'uri', 'method', 'path', 'effect', 'summary', 'description',
                'parameters', 'requestBody', 'response_fields'}
        description = {key: value for key, value in full.items() if key in keys}
        components = {}

        def collect(value):
            """Keep every transitive input reference, including shared constraints and oneOf rules."""
            if isinstance(value, dict):
                if reference := value.get('$ref'):
                    name = reference.rsplit('/', 1)[-1]
                    if name not in components:
                        components[name] = self.components[name]
                        collect(components[name])
                for key, item in value.items():
                    if key != '$ref':
                        collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)

        collect(description.get('parameters', []))
        collect(description.get('requestBody', {}))
        return {**description, 'definition_scope': 'parameters'}, components


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
                if options.get('keywords'):
                    definition['keywords'] = options['keywords']
                definition['parameters'] = copy.deepcopy(item.get('parameters', []) + schema.get('parameters', []))
                for parameter in definition['parameters']:
                    if parameter.get('in') == 'header' and parameter['name'] in options.get('required_headers', []):
                        parameter['required'] = True
                components: dict = {}
                responses = schema.get('responses', {})
                definition['responses'] = {
                    code: {key: value for key, value in response.items() if key in {'description', 'content', 'headers'}}
                    for code, response in responses.items()
                }
                self._collect_components(definition, components)
                response_schema = next((v.get('content', {}).get('application/json', {}).get('schema', {}) for k, v in responses.items() if str(k).startswith('2')), {})
                if '$ref' in response_schema:
                    response_schema = self.document.get('components', {}).get('schemas', {}).get(response_schema['$ref'].split('/')[-1], {})
                definition['response_fields'] = list(response_schema.get('properties', {}))
                self.operations[oid] = PublishedOperation(oid, method.upper(), path, definition, components, 'read' if method == 'get' or options.get('effect') == 'read' else 'preview' if options.get('effect') == 'preview' else 'write')
        # Publishers describe continuations at the existing HTTP route. Resolve
        # their paths against current OpenAPI instead of registering tool functions.
        by_route = {(op.method, op.path): op for op in self.operations.values()}
        for operation in self.operations.values():
            schema = self.document['paths'][operation.path][operation.method.lower()]
            links = []
            for link in schema.get('x-assistant', {}).get('next', []):
                target = by_route.get((link['method'].upper(), link['path']))
                if target is None:
                    raise ValueError('接口后续步骤未发布：' + link['path'])
                links.append({'operation_id': target.operation_id, 'purpose': link['purpose']})
            if links:
                operation.definition['next_operations'] = links

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

    def search(self, query: str, limit: int = INDEX_PAGE_SIZE) -> list[PublishedOperation]:
        """Match catalogue text without interpreting prohibitions, intent or permitted business actions.

        Only an Agent-supplied search uses this matcher. New tasks see an unfiltered
        compact index first; searches do not automatically load full definitions.
        """
        terms = search_terms(query)
        ranked = []
        for operation in self.operations.values():
            headline = operation.definition.get('summary', '') + ' ' + operation.path + ' ' + operation.operation_id
            keywords = ' '.join(operation.definition.get('keywords', []))
            score = len(terms & search_terms(keywords + ' ' + headline))
            ranked.append((score, operation.operation_id, operation))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        matches = [row[2] for row in ranked if row[0] > 0]
        return matches[:max(0, limit)]

    def index_page(self, *, query: str = '', offset: int = 0, limit: int = INDEX_PAGE_SIZE) -> dict:
        """Return a bounded, paginated first-level index without argument/response schemas."""
        if not 0 <= offset <= 100000 or not 1 <= limit <= INDEX_PAGE_SIZE:
            raise ValueError('索引分页参数无效。')
        operations = (self.search(query, limit=len(self.operations)) if query.strip()
                      else sorted(self.operations.values(), key=lambda item: item.operation_id))
        selected = operations[offset:offset + limit]
        return {'items': [{'uri': operation.uri,
                           'summary': operation.definition.get('summary', operation.operation_id)[:180],
                           'effect': operation.effect}
                          for operation in selected],
                'query': query, 'offset': offset, 'limit': limit, 'total': len(operations),
                'next_offset': offset + limit if offset + limit < len(operations) else None}

    def resolve_definitions(self, uris: list[str]) -> dict:
        """Resolve only exact local identifiers, collecting shared schemas once and enforcing a response budget."""
        from assistant_api.core.errors import InvalidRequestError
        if not 1 <= len(uris) <= 8:
            raise InvalidRequestError('operation_uri_invalid', '每次提供1至8个目录中的完整uri。')
        by_uri = {operation.uri: operation for operation in self.operations.values()}
        if any(uri not in by_uri for uri in uris):
            raise InvalidRequestError('operation_uri_invalid', 'uri不存在或未发布，请从接口索引选择；不会访问外部地址。')
        selected = [by_uri[uri] for uri in dict.fromkeys(uris)]
        result = {'api_operations': [operation.model_description(include_components=False) for operation in selected],
                  'components': {'schemas': {name: schema for operation in selected for name, schema in operation.components.items()}},
                  'resolved_uris': [operation.uri for operation in selected]}
        if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 150000:
            raise InvalidRequestError('operation_definitions_too_large', '定义总量过大，请减少本次uri数量后分别获取。')
        return result

    def with_next_operations(self, selected: list[PublishedOperation], *, previous: str | None = None) -> list[PublishedOperation]:
        """Retain legacy link inspection for existing callers; indexed Agent startup never uses this expansion."""
        result = []
        seen = set()
        if previous in self.operations:
            for link in self.operations[previous].definition.get('next_operations', []):
                op = self.operations[link['operation_id']]
                result.append(op)
                seen.add(op.operation_id)
        # Keep each independently relevant capability before expanding a broad
        # draft's links. Otherwise one draft family can occupy most of the limit.
        groups = [selected, [self.operations[link['operation_id']] for operation in selected
                            for link in operation.definition.get('next_operations', [])]]
        for group in groups:
            for op in group:
                if op.operation_id not in seen:
                    result.append(op)
                    seen.add(op.operation_id)
        return result[:8]

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
                self._validate_schema(parameter.get('schema', {}), values[name], operation, location=f'{location}.{name}')
            if location == 'header' and any(not isinstance(v, str) for v in values.values()):
                raise ValueError('HTTP头参数必须为字符串')
            if location == 'header' and any(k.lower() in {'authorization', 'cookie', 'host'} for k in values):
                raise ValueError('认证信息不能由模型提供')
        body = operation.definition.get('requestBody', {})
        schema = body.get('content', {}).get('application/json', {}).get('schema')
        if schema is not None:
            if call.body is not None or body.get('required'):
                self._validate_schema(schema, call.body, operation, location='body')
        elif call.body:
            raise ValueError('接口不接受 JSON 请求体')
        for value in call.path_parameters.values():
            if not isinstance(value, (str, int)) or str(value) in {'.', '..'} or any(x in str(value) for x in '/\\?#%'):
                raise ValueError('路径参数无效')
        return operation, call

    @staticmethod
    def _validate_schema(schema: dict, value, operation: PublishedOperation, *, location: str = 'body') -> None:
        """Report field paths and published requirements without echoing submitted values."""
        validator = Draft202012Validator({**schema, 'components': {'schemas': operation.components}}, format_checker=FormatChecker())
        error = next(validator.iter_errors(value), None)
        if error:
            path = '.'.join([location, *map(str, error.path)])
            rule = str(error.validator)
            expected = error.validator_value
            if rule == 'required' and isinstance(error.instance, dict):
                missing = [name for name in expected if name not in error.instance]
                detail = '缺少必填字段 ' + ', '.join(f'{path}.{name}' for name in missing[:5])
            elif rule == 'additionalProperties':
                detail = f'{path} 只允许接口定义的字段，请删除未声明字段'
            elif rule in {'type', 'enum', 'const', 'format', 'minimum', 'maximum', 'minLength', 'maxLength', 'minItems', 'maxItems', 'pattern'}:
                detail = f'{path} 要求 {rule}={json.dumps(expected, ensure_ascii=False)[:300]}'
            else:
                # Conditional schemas can contain large shared definitions. Name
                # the rule and refer to the offered schema, never dump the input.
                detail = f'{path} 不满足 {rule}，请按已提供的接口定义检查互斥字段及条件必填项'
            raise ValueError('参数不符合接口定义：' + detail)


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


def resolve_result_references(value, receipt: dict | None, depth: int = 0, results: dict | None = None):
    """Resolve AI-authored references to the last successful response before schema validation.

    Filters identify one record; pick projects input fields for complete PUTs,
    overrides changes specified fields, and as=string binds scalar HTTP headers.
    Existing private values stay local throughout these deterministic operations.
    """
    if depth > 12:
        raise ValueError('结果引用嵌套过深。')
    if isinstance(value, list):
        return [resolve_result_references(item, receipt, depth + 1, results) for item in value]
    if not isinstance(value, dict):
        return value
    if '$result' in value and 'step' in value:
        if not isinstance(value['step'], int) or not results or str(value['step']) not in results:
            raise ValueError('没有找到对应步骤的执行结果。')
        receipt = results[str(value['step'])]
        value = {k: v for k, v in value.items() if k != 'step'}
    if '$result' not in value:
        return {k: resolve_result_references(v, receipt, depth + 1, results) for k, v in value.items()}
    if set(value) - {'$result', 'where', 'field', 'overrides', 'pick', 'as'} or not receipt or receipt.get('status') != 'succeeded':
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
    if 'pick' in value:
        fields = value['pick']
        if (not isinstance(selected, dict) or not isinstance(fields, list) or len(fields) > 60
                or any(not isinstance(field, str) or field not in selected for field in fields)):
            raise ValueError('请选择原响应中存在的对象字段。')
        selected = {field: selected[field] for field in fields}
    if 'overrides' in value:
        if not isinstance(selected, dict) or not isinstance(value['overrides'], dict):
            raise ValueError('只能替换已有对象中的指定字段。')
        selected.update(resolve_result_references(value['overrides'], receipt, depth + 1, results))
    if 'as' in value:
        if value['as'] != 'string' or isinstance(selected, (dict, list)) or selected is None:
            raise ValueError('只有标量结果可以转换为字符串。')
        selected = str(selected).lower() if isinstance(selected, bool) else str(selected)
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
    projected = {k: receipt[k] for k in ('status', 'http_status', 'operation_id') if k in receipt}
    if isinstance(data, dict) and isinstance(data.get('applied'), bool):
        projected['applied'] = data['applied']
    if isinstance(data, dict) and isinstance(data.get('error'), dict) and isinstance(data['error'].get('code'), str):
        projected['error_code'] = data['error']['code']
    return projected | {'references': references, 'result_shape': result_shape(data)}


async def execute_call(app, authorization: str, operation: PublishedOperation, call: OperationCall, execution_id: str, *, execution_user_id: str | None = None, execution_run_id: str | None = None) -> dict:
    """Dispatch through real handlers with server-bound run provenance, never model-authored headers."""
    path = operation.path
    for key, value in call.path_parameters.items():
        path = path.replace('{' + key + '}', quote(str(value), safe=''))
    headers = {**call.headers, 'Authorization': authorization, 'Idempotency-Key': execution_id}
    transport_app = app
    if execution_user_id and not authorization:
        from functools import partial
        from assistant_api.services.execution_context import dispatch_local
        transport_app = partial(dispatch_local, app, user_id=execution_user_id, local_run_id=execution_run_id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=transport_app), base_url='http://assistant-internal', follow_redirects=False) as client:
        response = await client.request(operation.method, path, params=call.query_parameters, json=call.body if operation.method != 'GET' else None, headers=headers)
    if len(response.content) > 200000:
        return {'operation_id': call.operation_id, 'status': 'unknown', 'http_status': response.status_code, 'data': None, 'error': '结果过大，请在对应页面核对。'}
    try:
        data = response.json()
    except ValueError:
        data = None
    return {'operation_id': call.operation_id, 'status': 'succeeded' if 200 <= response.status_code < 300 else 'failed', 'http_status': response.status_code, 'data': data}
