# 数据源与公式计算 Fallback 审计

> 临时审计文档，用于梳理当前代码中的静默/显式降级点。

## 1. 数据源层 Fallback 点

| 文件 | 位置 | 降级行为 | 类型 | 建议修复 |
|------|------|----------|------|----------|
| `services/data_service.py` | `get_active_source()` L436-481 | 按 `default_chain` 顺序探测，首个就绪源即为当前源；`auto_fallback=false` 已关闭自动降级，但 `default_chain` 仍可能被误解为降级链 | 半显式 | 在文档/注释中明确 `default_chain` 仅为显式候选顺序；未命中时抛 `RuntimeError`（已符合） |
| `services/data_service.py` | `_get_provider()` L483-506 | provider 加载失败时使用 `_PlaceholderProvider`（永远返回 `is_ready=False`） | 显式降级 | 保留占位，但调用方必须处理不可用异常，禁止继续 fallback |
| `config/data_source_contract.json` | sources.* | `mock_fallback_allowed` 对非 mock 源均为 false；mock 为 `explicit_only` | 配置显式 | 已符合新架构，无需修改 |

## 2. 公式计算层 Fallback 点

| 文件 | 位置 | 降级行为 | 类型 | 建议修复 |
|------|------|----------|------|----------|
| `runtime/tdx_evaluators.py` | `eval_nset0_indicator()` L161-220 | 排名模式直接返回空列表；`formula_router` 不可用时返回空列表并报错 | 显式失败 | 已符合新架构（不降级） |
| `converters/tdx.py` | `_evaluate_condition()` L1828-1866 | TDX 条件评估失败时降级为 mock 过滤（透传全部股票） | **静默** | 移除 mock 降级，改为显式返回失败或空列表 |
| `converters/tdx.py` | `_evaluate_indicator_condition()` L1956-1998 | 公式计算失败降级到快照评估 | **静默** | 改为通过 `FormulaRouter` 路由；失败时显式报错 |
| `converters/tdx.py` | `_evaluate_change_pct_*()` L2533-2600 | 公式结果不可用时降级使用行情快照 change_pct | **静默** | 移除快照降级，公式无结果即视为不满足 |
| `converters/dzh.py` | `_evaluate_dzh_indicator_condition()` L2439-2498 | FormulaRouter 失败后降级到 `dzh_condition_fallback.json` 配置策略 | 配置驱动但仍是降级 | 保留配置驱动的显式降级，但策略必须显式声明，禁止默认透传 |

## 3. 候选股/板块解析 Fallback 点

| 文件 | 位置 | 降级行为 | 类型 | 建议修复 |
|------|------|----------|------|----------|
| `converters/tdx.py` | `_resolve_candidate_pool()` L1427-1521 | `CandidatePoolResolver` 失败后降级到原有直接加载逻辑 | **静默** | 改为显式失败，由调用方决定是否使用备选解析 |
| `converters/tdx.py` | `_get_stock_list_for_type_*()` L1583-1622 | 多次降级：`2` → `'50'` → `'5'` → `get_stock_list` / `resolve_market` | **静默链** | 移除链式降级，明确使用单一 API；失败时报错 |
| `converters/tdx.py` | `_get_sector_stocks()` L1643-1655 | `get_sector_stocks` 失败后降级到 `get_block_members` | **静默** | 改为显式失败 |

## 4. 行情快照 Fallback 点

| 文件 | 位置 | 降级行为 | 类型 | 建议修复 |
|------|------|----------|------|----------|
| `converters/tdx.py` | 多处 `if self.tq_adapter and self.tq_adapter.is_ready()` | 将 `tq_adapter` 可用性作为分支条件，缺失时回退到空结果或默认行为 | 半显式 | 通过 `MarketDataPort` / `DataQuery` 统一获取快照；无数据时显式报错 |
| `core/engine.py` | `_inject_bar_data_async()` / `_get_stock_price()`（参考 DESIGN.md） | 使用 `fallback_chain.json` 按条件选择 handler | 配置驱动 | 保留显式配置降级，但默认链尾必须显式声明 `pass_through` 含义 |

## 5. 最高风险 Top 5

1. **`converters/tdx.py` 条件评估失败降级为 mock 过滤** — 会错误地让全部股票通过，导致股票池状态污染。
2. **`converters/tdx.py` 公式计算失败降级到快照** — 用涨跌幅替代公式结果，逻辑不一致。
3. **`converters/tdx.py` 候选股解析链式降级** — 多次尝试不同 API，结果集不稳定。
4. **`converters/tdx.py` 板块成分股获取降级** — 可能拿到错误板块成分。
5. **`converters/dzh.py` 公式失败后走 fallback 配置** — 若配置默认透传，同样会导致状态污染。

## 6. 与新架构的对应关系

- 数据源不可用 → 通过 `DataSourceContract.probe_or_raise()` 显式抛错，**禁止**静默回退到 mock。
- K 线数据加载 → 统一走 `DataQuery.get_kline_series()`，不下载、不回退。
- 实时分钟合成 → 统一走 `Min1Aggregator.on_tick()`，热路径无 SQLite。
- 公式计算 → 统一走 `FormulaRouter.eval()` / `eval_batch()`，按复杂度显式路由到 HQChart 或 Python 引擎；失败时显式报错。
