# Four-Category Negative Tests Consolidation Spec

## Why

metatest/ has 12 fragmented negative test files (from create-metatest-comprehensive-validation Task 19-21). Granularity too fine, missing "underlying logic" category. Consolidate into 4 clear, independently-runnable files and add new logic-error dimension.

## What Changes

- KEEP and AUGMENT test_negative_invalid_config.py: add empty_pool/self_loop/orphan/dup_edge/invalid_params/cycle (6 boundary cases)
- CREATE test_negative_runtime_errors.py: runtime anomalies (dup entry/TTL no position/formula error/module import/state corruption/concurrent access), 6+ cases
- CREATE test_negative_api_frontend.py: API/frontend (404/405/500/SSE disconnect/WebSocket error/config missing/XSS/invalid JSON body), 7+ cases
- CREATE test_negative_logic_errors.py (NEW): underlying logic (waterline hash/compile failure/call depth>3/unregistered role/decouple recovery/propagate unknown mode/filter spec malformed), 7+ cases
- DO NOT delete existing 12 fragmented files; new 4 files coexist as consolidated view
- All negative tests verify graceful handling not crash; pass rate target >= 70%

## Impact

- Affected specs: create-metatest-comprehensive-validation (parallel, untouched)
- Affected code: no production code; only metatest/test_negative_*.py test files
- Dependencies: reuse conftest.py fixtures (virtual_clock/fz_stocks/pool_engine/event_collector/pool_snapshot/fastapi_client/config_store/tick_table/compiled_pool/signal_collector)
- Test target APIs: core/execution_module.py, core/event_bus.py, core/runtime_mode_module.py, core/formula_module.py, app.py, api.py

## ADDED Requirements

### Requirement: Four independently-runnable negative test files

System SHALL provide 4 independently-runnable pytest files in metatest/ covering invalid-config/runtime-errors/api-frontend/logic-errors categories.

#### Scenario: Single file runs successfully
- WHEN executing python -m pytest metatest/test_negative_invalid_config.py -v
- THEN file recognized and cases execute without collection errors

#### Scenario: Exceptions handled gracefully
- WHEN system receives invalid config/runtime anomaly/malformed request/logic error
- THEN system handles via controlled exception (ValueError/KeyError/HTTPException) or graceful degradation, no uncaught crash

### Requirement: New underlying-logic negative test category

System SHALL provide test_negative_logic_errors.py verifying meta-pattern logic constraints: waterline hash, compile failure, call depth limit, role registration, decouple recovery, mode propagation, filter spec malformation.

#### Scenario: Call depth exceeds limit rejected
- WHEN module call depth exceeds three layers
- THEN system raises controlled exception or returns error flag, no infinite recursion

## MODIFIED Requirements

### Requirement: Negative test consolidated organization

Existing 12 fragmented files SHALL remain untouched; 4 new consolidated files added as category views, each focusing on one anomaly dimension with 5-8+ cases.
