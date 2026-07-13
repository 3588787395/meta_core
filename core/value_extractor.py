"""值提取与路径导航：表驱动的多源值解析。

从 MetaEngine 提取的独立 helper，消除 MetaEngine 的值提取职责。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ValueExtractor:
    """表驱动值提取器：按 value_extractors 表分派提取原语。

    依赖注入：
    - tables: MetaEngine.tables 配置表容器
    - engine: MetaEngine 引用（用于 self.x 路径导航和方法反射调用）
    """

    def __init__(self, tables: Dict[str, Any], engine: Any):
        self._tables = tables
        self._engine = engine

    def extract_value(self, spec: Dict[str, Any], ctx: Dict[str, Any], *,
                      edge: Optional[Dict] = None, args=None, kwargs=None, source=None) -> Any:
        """按 extractors[spec.type] 表行的 path/call/value/wrap 字段执行提取。

        1 个通用解释器替代 12 个 _extract_* 方法。type 优先于 source：
        _resolve_chain 的 candidate 用 type 字段表示提取器类型，用 source 字段承载待提取的数据对象；
        _build_action_inputs/_build_tracker 的 spec 用 source 字段表示提取器类型。
        """
        extractor_type = spec.get('type') or spec.get('source') or 'literal_value'
        extractors = self._tables.get('value_extractors', {}).get('extractors', {})
        cfg = extractors.get(extractor_type)
        if not cfg:
            return None

        tmpl_vars = {k: v for k, v in spec.items() if isinstance(v, (str, int, float))}

        if 'eid_from' in cfg:
            eid = ''
            roots = {'ctx': ctx, 'edge': edge if isinstance(edge, dict) else {}, 'self': self._engine, 'source': source}
            for eid_path in str(cfg['eid_from']).split('|'):
                parts = eid_path.split('.')
                obj = roots.get(parts[0])
                for p in parts[1:]:
                    obj = obj.get(p, '') if isinstance(obj, dict) else getattr(obj, p, '')
                if obj:
                    eid = obj
                    break
            tmpl_vars['eid'] = eid

        if 'value' in cfg:
            return spec.get('value')

        result = None

        if 'path' in cfg:
            path_tmpl = cfg['path']
            try:
                path_str = path_tmpl.format(**tmpl_vars) if isinstance(path_tmpl, str) else path_tmpl
            except (KeyError, IndexError):
                return spec.get('fallback')
            result = self._navigate_path(path_str, spec, ctx, edge, args, kwargs, source)
            if result is None:
                default = cfg.get('default')
                if default is not None:
                    return default
                return spec.get('fallback')

        elif 'call' in cfg:
            call_tmpl = cfg['call']
            try:
                call_str = call_tmpl.format(**tmpl_vars) if isinstance(call_tmpl, str) and '{' in call_tmpl else call_tmpl
            except (KeyError, IndexError):
                call_str = call_tmpl
            # 内联 _exec_call：支持 self.method(args_from_ctx) / fn()
            if call_str.startswith('self.'):
                method_name = call_str[5:]
                handler = getattr(self._engine, method_name, None)
                if handler:
                    call_args = [ctx.get(k) for k in spec.get('args_from_ctx', [])]
                    result = handler(*call_args)
            elif call_str == '{fn}' or 'fn' in spec:
                fn = spec.get('fn') or source
                result = fn() if callable(fn) else None

        wrap_name = spec.get('wrap')
        if wrap_name and result is not None:
            wrap_fn = globals().get(wrap_name) if isinstance(wrap_name, str) else wrap_name
            if wrap_fn:
                result = wrap_fn(result)

        return result

    def resolve_chain(self, candidates: List[Dict[str, Any]], *, default=None) -> Any:
        """通用多源解析链：按优先级依次尝试候选者，返回第一个非 None 结果。

        candidates: list of dict，每项描述一个候选来源：
          {"type": "dict_key", "source": dict_obj, "key": "xxx"}
          {"type": "source_attr", "source": obj, "attr": "xxx"}
          {"type": "callable_fn", "fn": callable}
          {"type": "literal_value", "value": xxx}
        """
        for c in candidates:
            try:
                val = self.extract_value(c, {}, source=c.get("source"))
                if val is not None:
                    return val
            except (KeyError, AttributeError, TypeError):
                continue
        return default

    def resolve_field(self, spec: Dict[str, Any], ctx: Dict[str, Any]) -> Any:
        """深度表驱动字段解析：遍历 chain 返回首个非 None/非 0 值，否则 default。

        路径解析支持三种形式：
        1. 纯嵌套：'a.b.c' → ctx['a']['b']['c']
        2. 直接 key：'quote.price' → ctx['quote.price']（key 含点号）
        3. 前缀 key + 子路径：'tracker.snapshot.exit_price' → ctx['tracker.snapshot']['exit_price']
        """
        for path in spec.get('chain', []):
            # 1. 纯嵌套取值
            val = self._get_nested(ctx, path)
            # 2. 直接 key 取值（key 含点号）
            if val is None:
                val = ctx.get(path)
            # 3. 前缀 key + 子路径
            if val is None:
                keys = path.split('.')
                for i in range(len(keys) - 1, 0, -1):
                    prefix = '.'.join(keys[:i])
                    if prefix in ctx:
                        cur = ctx[prefix]
                        for k in keys[i:]:
                            if isinstance(cur, dict):
                                cur = cur.get(k)
                            else:
                                cur = None
                                break
                        if cur is not None:
                            val = cur
                            break
            if val is not None and val != 0:
                return val
        return spec.get('default', 0)

    def _navigate_path(self, path_str: str, spec, ctx, edge, args, kwargs, source) -> Any:
        """按 path 字符串导航取值。支持 ctx.x / self.x / edge.x / source.x / args[i]"""
        if not path_str:
            return None

        if path_str.startswith('args['):
            idx_str = path_str[5:path_str.index(']')]
            try:
                idx = int(idx_str)
                if args and len(args) > idx:
                    return args[idx]
                if kwargs:
                    key = spec.get('path', '')
                    return kwargs.get(key, spec.get('fallback', 0)) if key else spec.get('fallback', 0)
                return spec.get('fallback', 0)
            except (ValueError, IndexError):
                return None

        parts = path_str.split('.', 1)
        root_name = parts[0]
        rest = parts[1] if len(parts) > 1 else ''

        roots = {'ctx': ctx, 'self': self._engine, 'edge': edge if isinstance(edge, dict) else {}, 'source': source}
        if root_name in roots:
            val = roots[root_name]
        elif isinstance(ctx, dict) and root_name in ctx:
            val = ctx[root_name]
        else:
            return None

        if rest:
            if '[' in rest:
                attr_part, key_part = rest.split('[', 1)
                key = key_part.rstrip(']')
                if attr_part:
                    val = getattr(val, attr_part, None) if root_name == 'self' else (val.get(attr_part) if isinstance(val, dict) else None)
                if val is None:
                    return None
                if isinstance(val, dict):
                    return val.get(key)
                return None
            else:
                for p in rest.split('.'):
                    if val is None:
                        return None
                    if isinstance(val, dict):
                        val = val.get(p)
                    else:
                        val = getattr(val, p, None)
        return val

    @staticmethod
    def _get_nested(obj: Dict[str, Any], path: str) -> Any:
        """点号路径取值：'tracker.snapshot.exit_price' → obj['tracker']['snapshot']['exit_price']"""
        keys = path.split('.')
        cur = obj
        for k in keys:
            if isinstance(cur, dict):
                cur = cur.get(k)
            else:
                return None
            if cur is None:
                return None
        return cur
