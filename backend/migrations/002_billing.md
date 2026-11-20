# Schema v2 — 计件与封账

新增表（`src/models/billing.py`，由 SQLAlchemy metadata 创建）：

- `billing_control`：单行（id=1）。`enabled` 计费开关，`counter` 提交/封账共享序号，`backfilled` 历史登记标记。
- `billing_periods`：每月一行。`month`（北京时间 YYYY-MM 唯一）、`status`（closed/open）、`close_seq`、`closed_at`、`closed_by`、`closed_count`、`closed_checksum`、`note`。
- `billing_ingestions`：自然键 `sample_code + test_name` 唯一，记录首次成功入库所属运行、检测月与计费月。计费关闭时仍登记。
- `billing_lines`：每个自然键至多一行（唯一约束）。保存检测月、计费月、`adjusted`/`late`/`after_close`、来源运行与批次、归期原因、`submit_seq`。
- `billing_skips`：入库但未计件的留痕（重复自然键、恢复备份、历史登记、计费未启用）。

对既有库的增量补丁（`src/config/schema.py`，启动时幂等执行）：

```sql
ALTER TABLE runs ADD COLUMN IF NOT EXISTS submit_seq INTEGER NOT NULL DEFAULT 0;
```

`runs.submit_seq` 记录提交时共享序号；封账的 `close_seq` 与之比较决定并发归期。既有库中的历史运行 `submit_seq` 默认为 0，视作"早于任何封账"。
