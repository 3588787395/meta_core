# 垃圾代码清理与静态检查 - Verification Checklist

- [x] Checkpoint 1: 根目录无临时测试脚本（test_*.py, _test*.py, _*.py等）
- [x] Checkpoint 2: tests目录仅保留正式测试文件和必要的fixture/helper
- [x] Checkpoint 3: core目录无node_entered_codes/node_exited_codes残留引用
- [x] Checkpoint 4: core目录无大段注释掉的代码块（已检查，现有#注释均为文档说明和分隔线）
- [x] Checkpoint 5: 确认无外部调用的死代码（DataBinder.stock_code）已删除
- [x] Checkpoint 6: 主要未使用import已清理（时间函数统一从domain导入）
- [~] Checkpoint 7: 跨业务模块无直接import
  - ✅ tick_bar_module不再从execution_module直接import
  - ✅ import_export_module不再重复定义_hms_to_seconds，改为从domain导入
  - ✅ runtime_mode_module时间函数从domain导入（仅EdgeState保留从execution_module导入，属架构遗留问题）
  - ℹ️ execution_module作为核心编排层依赖formula_module/screening_module属合理设计
- [x] Checkpoint 8: time_at等6个公共工具函数已迁移到core.domain，消除tick_bar_module从execution_module的import违规
- [x] Checkpoint 9: 核心计算使用numpy/pandas向量化（现有代码已采用向量化计算）
- [x] Checkpoint 10: 修改的函数有正确类型注解（新增的时间函数已添加类型注解）
- [~] Checkpoint 11: 模块导入验证通过（pytest未安装，通过Python import验证所有core模块可正常加载）
