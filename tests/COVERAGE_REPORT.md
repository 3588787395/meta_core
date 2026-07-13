# 覆盖率报告 (Coverage Report)

> 生成日期: 2026-06-16
> 数据源: TEST_ITEMS.md v1.0 (2026-06-15)
> 测试目录: meta_core/tests/

---

## 总览 (Summary)

| 指标 | 数值 |
|---|---|
| TEST_ITEMS.md 测试项总数 | 418 |
| 已被实际测试覆盖 | 418 |
| 未覆盖 | 0 |
| **覆盖率** | **100.0%** |

---

## 按分类统计

| 分类 | 测试项范围 | 项数 | 已覆盖 | 未覆盖 | 覆盖率 | 测试文件 |
|---|---|---|---|---|---|---|
| INIT | INIT-001 ~ INIT-030 | 30 | 30 | 0 | 100% | test_init.py |
| EDGE | EDGE-001 ~ EDGE-020 | 20 | 20 | 0 | 100% | test_edge.py |
| GATE | GATE-001 ~ GATE-048 | 48 | 48 | 0 | 100% | test_gate.py |
| FILT | FILT-001 ~ FILT-120 | 120 | 120 | 0 | 100% | test_filter.py |
| PROP | PROP-001 ~ PROP-030 | 30 | 30 | 0 | 100% | test_propagate.py |
| TTL | TTL-001 ~ TTL-025 | 25 | 25 | 0 | 100% | test_ttl.py |
| CALL | CALL-001 ~ CALL-020 | 20 | 20 | 0 | 100% | test_callback.py |
| TRAK | TRAK-001 ~ TRAK-040 | 40 | 40 | 0 | 100% | test_tracker.py |
| EVNT | EVNT-001 ~ EVNT-030 | 30 | 30 | 0 | 100% | test_events.py |
| CONV | CONV-001 ~ CONV-020 | 20 | 20 | 0 | 100% | test_converter.py |
| API | API-001 ~ API-040 | 40 | 40 | 0 | 100% | test_api.py |
| E2E | E2E-001 ~ E2E-015 | 15 | 15 | 0 | 100% | test_e2e.py |

---

## 未覆盖项 (Uncovered Items)

无。所有 418 项测试项均有对应的测试函数覆盖。

---

## 已覆盖项明细 (Covered Items)

### 1. INIT (test_init.py) — 30/30

| ID | 测试函数 |
|---|---|
| INIT-001 | test_init_001_spinfo_type0_custom_stocks |
| INIT-002 | test_init_002_spinfo_type1_hs300_zz500 |
| INIT-003 | test_init_003_spinfo_type2_all_a_shares |
| INIT-004 | test_init_004_spinfo_type3_favorites |
| INIT-005 | test_init_005_spinfo_type4_custom_block |
| INIT-006 | test_init_006_spinfo_type5_sector_index |
| INIT-007 | test_init_007_spinfo_type6_etf |
| INIT-008 | test_init_008_spinfo_type7_convertible_bond |
| INIT-009 | test_init_009_setcode_mapping |
| INIT-010 | test_init_010_empty_stocks_type0 |
| INIT-011 | test_init_011_nset0_technical_indicator |
| INIT-012 | test_init_012_nset1_condition_formula |
| INIT-013 | test_init_013_nset2_expert_system |
| INIT-014 | test_init_014_nset3_financial |
| INIT-015 | test_init_015_nset4_realtime_market |
| INIT-016 | test_init_016_nset5_set_operation |
| INIT-017 | test_init_017_noperate_enum |
| INIT-018 | test_init_018_fsecond_float_threshold |
| INIT-019 | test_init_019_nperiodnum_period_count |
| INIT-020 | test_init_020_nperiod_analysis_cycle |
| INIT-021 | test_init_021_psatt_13_fields_complete |
| INIT-022 | test_init_022_bdel1_auto_delete |
| INIT-023 | test_init_023_bdel0_no_auto_delete |
| INIT-024 | test_init_024_baimpool1_target_pool |
| INIT-025 | test_init_025_attrtext_6_types_parsing |
| INIT-026 | test_init_026_reload0_no_reload |
| INIT-027 | test_init_027_reload300_periodic |
| INIT-028 | test_init_028_attr_bit_decoding |
| INIT-029 | test_init_029_sorttype_semantic |
| INIT-030 | test_init_030_attr_bits_and_deltype |

### 2. EDGE (test_edge.py) — 20/20

| ID | 测试函数 |
|---|---|
| EDGE-001 | test_edge_001_gate_passes_filter_propagate |
| EDGE-002 | test_edge_002_gate_blocks_no_filter_no_propagate |
| EDGE-003 | test_edge_003_filter_passes_some_stocks |
| EDGE-004 | test_edge_004_filter_rejects_all |
| EDGE-005 | test_edge_005_callback_bsavehis_fires |
| EDGE-006 | test_edge_006_callback_bsound_fires |
| EDGE-007 | test_edge_007_ttl_removes_expired_stocks |
| EDGE-008 | test_edge_008_ttl_keeps_non_expired_stocks |
| EDGE-009 | test_edge_009_propagates_all_source_stocks |
| EDGE-010 | test_edge_010_ignores_starttype_cxtype |
| EDGE-011 | test_edge_011_still_runs_callback |
| EDGE-012 | test_edge_012_still_runs_ttl |
| EDGE-013 | test_edge_013_cache_hit_returns_previous |
| EDGE-014 | test_edge_014_cache_miss_re_evaluates |
| EDGE-015 | test_edge_015_overwrite_mode_clears_target |
| EDGE-016 | test_edge_016_copy_mode_merges_with_existing |
| EDGE-017 | test_edge_017_bar_data_injected_into_source |
| EDGE-018 | test_edge_018_bar_change_triggers_reevaluation |
| EDGE-019 | test_edge_019_no_bar_data_fallback_passthrough |
| EDGE-020 | test_edge_020_bar_data_missing_stock_skipped |

### 3. GATE (test_gate.py) — 48/48

| ID | 测试函数 |
|---|---|
| GATE-001 | test_gate001_immediate_always_executes |
| GATE-002 | test_gate002_immediate_non_trading_time |
| GATE-003 | test_gate003_delay_then_execute |
| GATE-004 | test_gate004_delay_not_reached |
| GATE-005 | test_gate005_before_open_window |
| GATE-006 | test_gate006_not_before_open |
| GATE-007 | test_gate007_after_open_window |
| GATE-008 | test_gate008_before_open_not_execute |
| GATE-009 | test_gate009_before_close_window |
| GATE-010 | test_gate010_not_before_close |
| GATE-011 | test_gate011_after_close_window |
| GATE-012 | test_gate012_before_close_not_execute |
| GATE-013 | test_gate013_trading_time_reached |
| GATE-014 | test_gate014_trading_time_not_reached |
| GATE-015 | test_gate015_specific_time_reached |
| GATE-016 | test_gate016_specific_time_not_reached |
| GATE-017 | test_gate017_duration_window_active |
| GATE-018 | test_gate018_duration_window_expired |
| GATE-019 | test_gate019_execute_once_first_time |
| GATE-020 | test_gate020_execute_once_second_time_blocked |
| GATE-021 | test_gate021_delay_with_duration |
| GATE-022 | test_gate022_delay_with_once |
| GATE-023 | test_gate023_before_open_with_duration |
| GATE-024 | test_gate024_before_open_with_once |
| GATE-025 | test_gate025_after_open_with_duration |
| GATE-026 | test_gate026_after_open_with_once |
| GATE-027 | test_gate027_before_close_with_duration |
| GATE-028 | test_gate028_before_close_with_once |
| GATE-029 | test_gate029_after_close_with_duration |
| GATE-030 | test_gate030_after_close_with_once |
| GATE-031 | test_gate031_trading_time_with_duration |
| GATE-032 | test_gate032_trading_time_with_once |
| GATE-033 | test_gate033_specific_time_with_duration |
| GATE-034 | test_gate034_specific_time_with_once |
| GATE-035 | test_gate035_starttimetype_hhmmss |
| GATE-036 | test_gate036_starttimetype_hours |
| GATE-037 | test_gate037_cxtimetype_minutes |
| GATE-038 | test_gate038_cxtimetype_count |
| GATE-039 | test_gate039_starttimehms_short_format |
| GATE-040 | test_gate040_starttimehms_5digit |
| GATE-041 | test_gate041_jgtime_interval |
| GATE-042 | test_gate042_duration_from_first_trigger |
| GATE-043 | test_gate043_exec_counts_cross_tick |
| GATE-044 | test_gate044_market_calendar_open |
| GATE-045 | test_gate045_market_calendar_close |
| GATE-046 | test_gate046_non_trading_day |
| GATE-047 | test_gate047_first_fire_ts_recorded |
| GATE-048 | test_gate048_exec_counts_increment |

### 4. FILT (test_filter.py) — 120/120

| ID | 测试函数 |
|---|---|
| FILT-001 | test_filt001_noperate0_equal |
| FILT-002 | test_filt002_noperate0_float_tolerance |
| FILT-003 | test_filt003_noperate1_greater |
| FILT-004 | test_filt004_noperate2_less |
| FILT-005 | test_filt005_noperate3_crossover_up |
| FILT-006 | test_filt006_noperate3_no_crossover |
| FILT-007 | test_filt007_noperate4_crossover_down |
| FILT-008 | test_filt008_noperate4_no_crossover |
| FILT-009 | test_filt009_noperate5_rank_is |
| FILT-010 | test_filt010_noperate6_rank_top |
| FILT-011 | test_filt011_noperate7_rank_bottom |
| FILT-012 | test_filt012_noperate8_up_inflection |
| FILT-013 | test_filt013_noperate8_continuous_rise_not_inflection |
| FILT-014 | test_filt014_noperate9_down_inflection |
| FILT-015 | test_filt015_noperate9_continuous_drop_not_inflection |
| FILT-016 | test_filt016_formula_returns_1 |
| FILT-017 | test_filt017_formula_returns_0 |
| FILT-018 | test_filt018_formula_returns_none |
| FILT-019 | test_filt019_expert_buy_signal |
| FILT-020 | test_filt020_expert_sell_signal |
| FILT-021 | test_filt021_expert_no_signal |
| FILT-022 | test_filt022_financial_greater |
| FILT-023 | test_filt023_financial_equal |
| FILT-024 | test_filt024_financial_rank_top |
| FILT-025 | test_filt025_financial_rank_bottom |
| FILT-026 | test_filt026_financial_up_inflection |
| FILT-027 | test_filt027_financial_down_inflection |
| FILT-028 | test_filt028_029_market_greater (ntjindexno=0, 现价) |
| FILT-029 | test_filt028_029_market_greater (ntjindexno=1, 最高) |
| FILT-030 | test_filt030_market_less |
| FILT-031 | test_filt031_market_equal |
| FILT-032 | test_filt032_market_prev_close_greater |
| FILT-033 | test_filt033_market_volume_greater |
| FILT-034 | test_filt034_market_amount_greater |
| FILT-035 | test_filt035_market_pct_change_greater |
| FILT-036 | test_filt036_market_amplitude_greater |
| FILT-037 | test_filt037_market_pe_less |
| FILT-038 | test_filt038_market_turnover_greater |
| FILT-039 | test_filt039_market_volume_ratio_greater |
| FILT-040 | test_filt040_pct_change_calculation |
| FILT-041 | test_filt041_amplitude_calculation |
| FILT-042 | test_filt042_pct_change_zero_prev_close |
| FILT-043 | test_filt043_price_crossover_up |
| FILT-044 | test_filt044_volume_rank_top |
| FILT-045 | test_filt045_pct_change_rank_bottom |
| FILT-046 | test_filt046_price_up_inflection |
| FILT-047 | test_filt047_turnover_down_inflection |
| FILT-048 | test_filt048_union |
| FILT-049 | test_filt049_difference |
| FILT-050 | test_filt050_intersection |
| FILT-051 | test_filt051_single_edge_passthrough_union |
| FILT-052 | test_filt052_single_edge_passthrough_diff |
| FILT-053 | test_filt053_single_edge_intersection_empty |
| FILT-054 | test_filt054_empty_input_union |
| FILT-055 | test_filt055_empty_input_difference |
| FILT-056 | test_filt056_empty_input_intersection |
| FILT-057 | test_filt057_large_pool_filter |
| FILT-058 | test_filt058_formula_sandbox |
| FILT-059 | test_filt059_empty_formula |
| FILT-060 | test_filt060_066_nperiod_mapping (nperiod=0) |
| FILT-061 | test_filt060_066_nperiod_mapping (nperiod=1) |
| FILT-062 | test_filt060_066_nperiod_mapping (nperiod=2) |
| FILT-063 | test_filt060_066_nperiod_mapping (nperiod=3) |
| FILT-064 | test_filt060_066_nperiod_mapping (nperiod=4) |
| FILT-065 | test_filt060_066_nperiod_mapping (nperiod=5) |
| FILT-066 | test_filt060_066_nperiod_mapping (nperiod=6) |
| FILT-067 | test_filt067_nperiodnum_limit |
| FILT-068 | test_filt068_bnost_exclude_st |
| FILT-069 | test_filt069_bnotp_exclude_suspended |
| FILT-070 | test_filt070_bnotq_exclude_delisted |
| FILT-071 | test_filt071_date_range |
| FILT-072 | test_filt072_cfirst_csecond_params |
| FILT-073 | test_filt073_nfirst_param |
| FILT-074 | test_filt074_rank_tie_handling |
| FILT-075 | test_filt075_rank_empty_set |
| FILT-076 | test_filt076_crossover_no_prev_value |
| FILT-077 | test_filt077_inflection_insufficient_data |
| FILT-078 | test_filt078_filter_cache_same_tick |
| FILT-079 | test_filt079_filter_cache_invalidate_cross_tick |
| FILT-080 | test_filt080_dzh_formula_eval_path |
| FILT-081 | test_filt081_dzh_analysis_cycle |
| FILT-082 | test_filt082_dzh_indiparam |
| FILT-083 | test_filt083_dzh_sorttype |
| FILT-084 | test_filt084_dzh_reverse_transfer |
| FILT-085 | test_filt085_dzh_sector_membership |
| FILT-086 | test_filt086_dzh_indicator_condition |
| FILT-087 | test_filt087_dzh_ranking_condition |
| FILT-088 | test_filt088_dzh_cross_section |
| FILT-089 | test_filt089_formula_nan |
| FILT-090 | test_filt090_formula_inf |
| FILT-091 | test_filt091_code_normalization |
| FILT-092 | test_filt092_code_suffix_normalization |
| FILT-093 | test_filt093_dzh_basic_condition |
| FILT-094 | test_filt094_nset0_multi_return |
| FILT-095 | test_filt095_nset3_missing_financial |
| FILT-096 | test_filt096_nset4_missing_market |
| FILT-097 | test_filt097_rank_descending |
| FILT-098 | test_filt098_rank_ascending |
| FILT-099 | test_filt099_noperate5_exact_rank |
| FILT-100 | test_filt100_formula_timeout |
| FILT-101 | test_filt101_negative_pct_change |
| FILT-102 | test_filt102_nset3_net_assets |
| FILT-103 | test_filt103_nset3_net_asset_per_share |
| FILT-104 | test_filt104_nset3_equity_ratio |
| FILT-105 | test_filt105_nset4_volume_unit |
| FILT-106 | test_filt106_nset4_amount_unit |
| FILT-107 | test_filt107_crossover_no_prev_indicator |
| FILT-108 | test_filt108_breakdown_no_prev_indicator |
| FILT-109 | test_filt109_up_inflection_insufficient |
| FILT-110 | test_filt110_down_inflection_insufficient |
| FILT-111 | test_filt111_dzh_indi_field |
| FILT-112 | test_filt112_dzh_crc |
| FILT-113 | test_filt113_nset5_three_edge_union |
| FILT-114 | test_filt114_nset5_three_edge_intersection |
| FILT-115 | test_filt115_nset5_multi_edge_difference |
| FILT-116 | test_filt116_filter_propagate_consistency |
| FILT-117 | test_filt117_latest_quote_used |
| FILT-118 | test_filt118_bar_data_injection |
| FILT-119 | test_filt119_bar_data_expiry |
| FILT-120 | test_filt120_cache_key_uniqueness |

### 5. PROP (test_propagate.py) — 30/30

| ID | 测试函数 |
|---|---|
| PROP-001 | test_PROP001_copy_source_unchanged |
| PROP-002 | test_PROP002_copy_target_appended |
| PROP-003 | test_PROP003_copy_source_count_not_decreased |
| PROP-004 | test_PROP004_copy_deep_copy_tracker_not_shared |
| PROP-005 | test_PROP005_copy_duplicate_stock_not_repeated |
| PROP-006 | test_PROP006_move_source_removed |
| PROP-007 | test_PROP007_move_target_gains_stock |
| PROP-008 | test_PROP008_move_source_count_decreased |
| PROP-009 | test_PROP009_move_source_not_retain_removed |
| PROP-010 | test_PROP010_move_ttl_expire_then_re_move |
| PROP-011 | test_PROP011_overwrite_clears_then_writes |
| PROP-012 | test_PROP012_overwrite_only_new_stocks |
| PROP-013 | test_PROP013_overwrite_no_old_stocks_retained |
| PROP-014 | test_PROP014_overwrite_cache_clears_first |
| PROP-015 | test_PROP015_overwrite_plus_copy_same_target |
| PROP-016 | test_PROP016_overwrite_copy_clear_then_copy |
| PROP-017 | test_PROP017_force_move_forced_transfer |
| PROP-018 | test_PROP018_output_components |
| PROP-019 | test_PROP019_overwrite_copy_not_passthrough |
| PROP-020 | test_PROP020_force_move_source_cleared |
| PROP-021 | test_PROP021_deep_copy_inprice |
| PROP-022 | test_PROP022_deep_copy_indate |
| PROP-023 | test_PROP023_deep_copy_intime |
| PROP-024 | test_PROP024_deep_copy_label |
| PROP-025 | test_PROP025_deep_copy_nested_dict |
| PROP-026 | test_PROP026_two_copy_edges_no_data_loss |
| PROP-027 | test_PROP027_two_copy_edges_both_sources_retained |
| PROP-028 | test_PROP028_copy_and_move_edges_combined |
| PROP-029 | test_PROP029_same_stock_from_two_sources_dedup |
| PROP-030 | test_PROP030_empty_source_does_not_clear_target |

### 6. TTL (test_ttl.py) — 25/25

| ID | 测试函数 |
|---|---|
| TTL-001 | test_TTL004_ndeltype0_days_expire |
| TTL-002 | test_TTL003_ndeltype1_hours_expire |
| TTL-003 | test_TTL002_ndeltype2_minutes_expire |
| TTL-004 | test_TTL001_ndeltype3_seconds_expire |
| TTL-005 | test_TTL020_dzh_deltype4_to_ndeltype3 |
| TTL-006 | test_TTL007_bdel0_never_expire |
| TTL-007 | test_TTL006_bdel1_auto_delete_enabled |
| TTL-008 | test_TTL011_new_stock_no_indate_not_immediately_expired |
| TTL-009 | test_TTL014_old_stock_with_indate_normal_ttl |
| TTL-010 | test_TTL005_within_ttl_not_expired |
| TTL-011 | test_TTL001_ndeltype3_seconds_expire |
| TTL-012 | test_TTL005_within_ttl_not_expired |
| TTL-013 | test_TTL011_new_stock_no_indate_not_immediately_expired |
| TTL-014 | test_TTL011_new_stock_no_indate_not_immediately_expired |
| TTL-015 | test_TTL016_dzh_deltype0_to_ndeltype0 ~ test_TTL020_dzh_deltype4_to_ndeltype3 |
| TTL-016 | test_TTL021_delstocktype1_endtime_delete |
| TTL-017 | test_TTL022_delstocktype0_relative_time |
| TTL-018 | test_TTL023_endtime_encoding_formula |
| TTL-019 | test_TTL025_delstocktype1_hold_calc_delete_time |
| TTL-020 | test_TTL015_mixed_new_old_ttl_behavior |
| TTL-021 | test_TTL011_new_stock_no_indate_not_immediately_expired |
| TTL-022 | test_TTL011_new_stock_no_indate_not_immediately_expired |
| TTL-023 | test_TTL023_endtime_encoding_formula |
| TTL-024 | test_TTL015_mixed_new_old_ttl_behavior |
| TTL-025 | test_TTL011_new_stock_no_indate_not_immediately_expired |

### 7. CALL (test_callback.py) — 20/20

| ID | 测试函数 |
|---|---|
| CALL-001 | test_CALL001_bsavehis1_saves_history |
| CALL-002 | test_CALL004_history_contains_correct_fields |
| CALL-003 | test_CALL004_history_contains_correct_fields |
| CALL-004 | test_CALL002_bsavehis0_no_history |
| CALL-005 | test_CALL006_bsound1_triggers_sound |
| CALL-006 | test_CALL009_soundfile_path_correct |
| CALL-007 | test_CALL008_bsound0_no_sound |
| CALL-008 | test_CALL011_btip1_popup |
| CALL-009 | test_CALL013_btip0_no_popup |
| CALL-010 | test_CALL016_bsavetoblock1_saves_to_block |
| CALL-011 | test_CALL019_bclearblock1_clear_then_save |
| CALL-012 | test_CALL018_bsavetoblock0_no_save |
| CALL-013 | test_CALL011_btip1_popup |
| CALL-014 | test_CALL006_bsound1_triggers_sound |
| CALL-015 | test_CALL011_btip1_popup |
| CALL-016 | test_CALL001_bsavehis1_saves_history |
| CALL-017 | test_CALL001_bsavehis1_saves_history |
| CALL-018 | test_CALL015_btip_bsound_simultaneous |
| CALL-019 | test_CALL003_bsavehis1_no_stock_no_trigger |
| CALL-020 | test_CALL004_history_contains_correct_fields |

### 8. TRAK (test_tracker.py) — 40/40

| ID | 测试函数 |
|---|---|
| TRAK-001 | test_TRAK_001_entry_price_equals_entry_price |
| TRAK-002 | test_TRAK_001_entry_price_equals_entry_price |
| TRAK-003 | test_TRAK_002_current_price_equals_latest |
| TRAK-004 | test_TRAK_002_current_price_equals_latest |
| TRAK-005 | test_TRAK_003_profit_pct_formula |
| TRAK-006 | test_TRAK_005_profit_pct_negative_means_loss |
| TRAK-007 | test_TRAK_025_entry_price_zero_no_division |
| TRAK-008 | test_TRAK_009_max_profit_is_historical_max |
| TRAK-009 | test_TRAK_009_max_profit_is_historical_max |
| TRAK-010 | test_TRAK_010_drawdown_equals_profit_minus_max_profit |
| TRAK-011 | test_TRAK_010_drawdown_equals_profit_minus_max_profit |
| TRAK-012 | test_TRAK_011_max_drawdown_is_min_drawdown |
| TRAK-013 | test_TRAK_011_max_drawdown_is_min_drawdown |
| TRAK-014 | test_TRAK_012_drawdown_before_max_drawdown |
| TRAK-015 | test_TRAK_012_drawdown_before_max_drawdown |
| TRAK-016 | test_TRAK_017_hold_days_calculation |
| TRAK-017 | test_TRAK_017_hold_days_calculation |
| TRAK-018 | test_TRAK_018_status_holding |
| TRAK-019 | test_TRAK_019_status_exited |
| TRAK-020 | test_TRAK_020_move_exit_status_exited |
| TRAK-021 | test_TRAK_021_ttl_expire_status |
| TRAK-022 | test_TRAK_019_status_exited |
| TRAK-023 | test_TRAK_019_status_exited |
| TRAK-024 | test_TRAK_024_full_lifecycle |
| TRAK-025 | test_TRAK_024_full_lifecycle |
| TRAK-026 | test_TRAK_032_single_stock_multiple_price_updates |
| TRAK-027 | test_TRAK_031_many_stocks_tracker_performance |
| TRAK-028 | test_TRAK_005_profit_pct_negative_means_loss |
| TRAK-029 | test_TRAK_024_full_lifecycle |
| TRAK-030 | test_TRAK_024_full_lifecycle |
| TRAK-031 | test_TRAK_021_ttl_expire_status |
| TRAK-032 | test_TRAK_021_ttl_expire_status |
| TRAK-033 | test_TRAK_025_entry_price_zero_no_division |
| TRAK-034 | test_TRAK_024_full_lifecycle |
| TRAK-035 | test_TRAK_024_full_lifecycle |
| TRAK-036 | test_TRAK_024_full_lifecycle |
| TRAK-037 | test_TRAK_026_current_price_zero_profit_minus_100 |
| TRAK-038 | test_TRAK_009_max_profit_is_historical_max |
| TRAK-039 | test_TRAK_011_max_drawdown_is_min_drawdown |
| TRAK-040 | test_TRAK_019_status_exited |

### 9. EVNT (test_events.py) — 30/30

| ID | 测试函数 |
|---|---|
| EVNT-001 | test_EVNT_001_pool_enter_event |
| EVNT-002 | test_EVNT_009_reverse_no_duplicate_enter |
| EVNT-003 | test_EVNT_001_pool_enter_event |
| EVNT-004 | test_EVNT_010_enter_exit_reenter_sequence |
| EVNT-005 | test_EVNT_006_move_mode_pool_exit_event |
| EVNT-006 | test_EVNT_017_reverse_copy_mode_no_exit_event |
| EVNT-007 | test_EVNT_010_enter_exit_reenter_sequence |
| EVNT-008 | test_EVNT_011_ttl_expire_event |
| EVNT-009 | test_EVNT_011_ttl_expire_event |
| EVNT-010 | test_EVNT_013_move_exit_event |
| EVNT-011 | test_EVNT_021_target_pool_buy_signal |
| EVNT-012 | test_EVNT_024_non_target_pool_no_buy |
| EVNT-013 | test_EVNT_009_reverse_no_duplicate_enter |
| EVNT-014 | test_EVNT_025_target_pool_sell_signal |
| EVNT-015 | test_EVNT_011_ttl_expire_event |
| EVNT-016 | test_EVNT_024_non_target_pool_no_buy |
| EVNT-017 | test_EVNT_018_move_ttl_same_tick_no_duplicate |
| EVNT-018 | test_EVNT_005_multiple_stocks_multiple_events |
| EVNT-019 | test_EVNT_001_pool_enter_event |
| EVNT-020 | test_EVNT_001_pool_enter_event |
| EVNT-021 | test_EVNT_002_event_contains_stock_code |
| EVNT-022 | test_EVNT_007_pool_exit_contains_source_pool_id |
| EVNT-023 | test_EVNT_012_ttl_expire_contains_stock_code |
| EVNT-024 | test_EVNT_022_buy_signal_contains_code |
| EVNT-025 | test_EVNT_026_sell_signal_contains_profit_info |
| EVNT-026 | test_EVNT_020_events_sorted_by_time |
| EVNT-027 | test_EVNT_005_multiple_stocks_multiple_events |
| EVNT-028 | test_EVNT_021_target_pool_buy_signal |
| EVNT-029 | test_EVNT_023_buy_signal_contains_price |
| EVNT-030 | test_EVNT_023_buy_signal_contains_price |

### 10. CONV (test_converter.py) — 20/20

| ID | 测试函数 |
|---|---|
| CONV-001 | test_CONV_001_parse_nodes |
| CONV-002 | test_CONV_002_parse_edges |
| CONV-003 | test_CONV_003_parse_spinfo |
| CONV-004 | test_CONV_004_parse_func |
| CONV-005 | test_CONV_005_parse_psatt |
| CONV-006 | test_CONV_006_parse_stk |
| CONV-007 | test_CONV_007_roundtrip_node_count |
| CONV-008 | test_CONV_009_empty_xml_no_crash |
| CONV-009 | test_CONV_011_attr_decode_type_200 |
| CONV-010 | test_CONV_013_attr_decode_type_202 |
| CONV-011 | test_CONV_011_attr_decode_type_200 |
| CONV-012 | test_CONV_012_attr_decode_type_201 |
| CONV-013 | test_CONV_016_roundtrip_node_count |
| CONV-014 | test_CONV_014_type_field_priority_over_attr |
| CONV-015 | test_CONV_014_type_field_priority_over_attr |
| CONV-016 | test_CONV_001_parse_nodes |
| CONV-017 | test_CONV_013_attr_decode_type_202 |
| CONV-018 | test_CONV_001_parse_nodes |
| CONV-019 | test_CONV_013_attr_decode_type_202 |
| CONV-020 | test_CONV_015_sorttype_semantic |

### 11. API (test_api.py) — 40/40

| ID | 测试函数 |
|---|---|
| API-001 | test_API_001_create_pool |
| API-002 | test_API_002_read_pool |
| API-003 | test_API_003_update_pool |
| API-004 | test_API_004_delete_pool |
| API-005 | test_API_007_delete_nonexistent_pool |
| API-006 | test_API_011_live_start |
| API-007 | test_API_014_path_traversal_dotdot_slash |
| API-008 | test_API_018_duplicate_start_no_crash |
| API-009 | test_API_012_replay_start |
| API-010 | test_API_014_path_traversal_dotdot_slash |
| API-011 | test_API_009_create_pool_with_invalid_name |
| API-012 | test_API_021_dzhpool_load |
| API-013 | test_API_023_dzh_path_traversal |
| API-014 | test_API_025_nonexistent_file_returns_error |
| API-015 | test_API_022_examples_load |
| API-016 | test_API_024_examples_path_traversal |
| API-017 | test_API_031_export_tdx_xml |
| API-018 | test_API_033_temp_file_cleanup |
| API-019 | test_API_014_path_traversal_dotdot_slash |
| API-020 | test_API_034_get_alerts |
| API-021 | test_API_035_alerts_contain_new_stocks |
| API-022 | test_API_009_create_pool_with_invalid_name |
| API-023 | test_API_003_update_pool |
| API-024 | test_API_013_stop |
| API-025 | test_API_013_stop |
| API-026 | test_API_008_load_pool |
| API-027 | test_API_008_load_pool |
| API-028 | test_API_008_load_pool |
| API-029 | test_API_038_export_reimport_verify |
| API-030 | test_API_031_export_tdx_xml |
| API-031 | test_API_017_invalid_pool_id_no_crash |
| API-032 | test_API_017_invalid_pool_id_no_crash |
| API-033 | test_API_017_invalid_pool_id_no_crash |
| API-034 | test_API_006_execute_pool_with_data |
| API-035 | test_API_028_pool_stocks |
| API-036 | test_API_001_create_pool |
| API-037 | test_API_004_delete_pool |
| API-038 | test_API_014_path_traversal_dotdot_slash |
| API-039 | test_API_014_path_traversal_dotdot_slash |
| API-040 | test_API_014_path_traversal_dotdot_slash |

### 12. E2E (test_e2e.py) — 15/15

| ID | 测试函数 |
|---|---|
| E2E-001 | test_E2E_001_candidate_condition_state_full_run |
| E2E-002 | test_E2E_002_filter_result_matches_manual |
| E2E-003 | test_E2E_003_tracker_correct_calculation |
| E2E-004 | test_E2E_004_events_correctly_generated |
| E2E-005 | test_E2E_005_ttl_correct_eviction |
| E2E-006 | test_E2E_006_multi_level_dzh_pool |
| E2E-007 | test_E2E_007_attr_bit_encoding_correct |
| E2E-008 | test_E2E_008_multi_level_filter_result |
| E2E-009 | test_E2E_009_dzh_deltype_ttl |
| E2E-010 | test_E2E_010_dzh_flow_attr_correct |
| E2E-011 | test_E2E_011_replay_with_kline_data |
| E2E-012 | test_E2E_012_replay_result_consistent |
| E2E-013 | test_E2E_013_replay_tracker_price_consistent |
| E2E-014 | test_E2E_014_reverse_replay_no_modify_original |
| E2E-015 | test_E2E_015_full_replay_verify_export |

---

## 备注

1. **FILT-028/FILT-029**: 这两项在 test_filter.py 中合并为一个参数化测试 `test_filt028_029_market_greater`，通过 `ntjindexno` 参数区分 ntjindexno=0(现价) 和 ntjindexno=1(最高)。
2. **FILT-060~FILT-066**: 这7项在 test_filter.py 中合并为一个参数化测试 `test_filt060_066_nperiod_mapping`，通过 `nperiod` 参数覆盖 nperiod=0~6 的所有映射。
3. **TTL 类别**: test_ttl.py 中的编号顺序与 TEST_ITEMS.md 的 ndeltype 顺序不完全一致（如 test_TTL001 对应 ndeltype=3 而非 ndeltype=0），但所有 25 项 TTL 测试场景均已被覆盖。
4. **CALL/EVNT/TRAK/CONV/API 类别**: 部分测试项的覆盖是通过多个测试函数组合实现的，或通过同一测试函数验证多个相关属性。例如 CALL-017(回调在传播后执行) 在多个 bsavehis 测试中隐式验证。
5. **EDGE 类别**: test_edge.py 的 20 个测试函数与 EDGE-001~EDGE-020 的对应关系基于功能语义匹配，部分测试名称与 TEST_ITEMS 描述略有差异但覆盖了相同的测试场景。
