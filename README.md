# 见山 · 检验结果迁移核对站

见山质量档案组13人用源批次、字段映射、后台对账运行完成旧字段检验结果的有证据交接。

## 场景与业务
上游实验室交付旧字段JSON检验记录；过去人工改字段名、复制到新表并凭总行数判断成功。痛点：非法数值默默丢失、重复委托号覆盖、备份无法证明能恢复。本站登记原始行，字段映射规定类型和必填，Python逐块转换并保留拒绝原行，真实PG目标表一次提交。下游质量复核人下载对账凭据，管理员用JSON逻辑备份恢复本次运行目标并核验。

五业务页：源批次、字段映射、迁移执行（可取消）、差异核对（拒绝区/目标行/备份）、计件封账；管理员另有审计和回传恢复。三核心实体batches/mappings/runs，另target_rows持久化目标行。全部实体按模型/schema/service/controller/routes/前端api/store/page贯穿。

## 启动与账号
```sh
cp .env.example .env
docker compose up -d --build
docker compose exec -T backend python -m scripts.seed_demo
```
前端 http://localhost:18248 ，后端 http://localhost:19248/docs ，PostgreSQL localhost:17748（migrate / migratepass / migrationcheck）。结束docker compose down -v --remove-orphans会清除本地数据。

| 角色 | 账号 | 权限 / 页面 | 典型动作 |
|---|---|---|---|
| admin | admin@migration.local | 全部、审计、恢复、封账/解封/计费开关 | 回传备份恢复目标并校验，月结封账 |
| operator | operator@migration.local | 创建/编辑/执行/取消/导出 | 配置映射并迁移 |
| viewer | viewer@migration.local | 只读、下载凭据/备份 | 复核拒绝区、真实目标表与每月计件归期 |

密码统一Migrate2026!；PBKDF2密码与8小时JWT。本地演示凭据与JWT密钥，部署前更换。

## 一天的交接剧本
1. 09:00 operator签入，查看见山日班检验结果12条正常记录。
2. 09:05 登记lab-results-20261117.json，委托号JS-20261117-001结果420.1MPa。
3. 09:10 新建shift-20261117批次，复制旧字段。
4. 09:15 字段映射：委托号→sample_code、检测项→test_name、结果→result_value、单位→unit、时间→measured_at，每块4行。
5. 09:20 提交shift-20261117对账，或运行旧字段异常样本查看重复/非法数字/空值/缺列拒绝。
6. 09:25 查看接受/拒绝与分块证据，读取真实PG目标表。
7. 09:30 下载reconciliation-<id>.json与backup-<id>.json，viewer复核。
8. 10:00 admin回传该备份，恢复并核对摘要，查看审计完成事件。
9. 月末 admin在「计件封账」页封账上月，逐笔核对检测月→计费月、来源运行与归期原因；十月跨月样本可演示北京时间边界与旧月补入。

seed含三账号、四批次（正常/异常/空输入/十月跨月）、四映射。固定数值、自然键upsert式补缺，不覆盖用户编辑；连续执行不新增。

## 计件与封账
财务按北京时间（UTC+8）的检测月份计件：检测时间统一转换到北京时间再取 `YYYY-MM`，无时区时间按北京时间解释，跨月以北京零点为准（如 `2026-10-31T16:30Z` 属于 11 月）。**同一委托同一检测项只计首次成功入库**；重复自然键、恢复备份、历史回填与计费关闭期间的入库只留痕不计费。计费开关启用前就完成的入库只登记、不补计。

**封账**冻结单月快照（笔数 + 校验摘要），管理员可解封后重新封账。封账与迁移并发时按**提交先后**归期：提交运行与封账都取 `billing_control` 同一行锁并推进同一序号，运行记 `submit_seq`、封账记 `close_seq`。`run.submit_seq < period.close_seq`（迁移先提交）时，即使 worker 在封账后才入库，项目仍保留检测月并标记 `after_close` 供月结复核；反之（封账先提交），检测月已封账的项目归入提交时首个未封账月，其他月份照常入库。旧月封账后才入库的项目同样记入首个未封账月，`billing_lines` 同时保留检测月、计费月、来源运行/批次与归期原因，管理员可用 `/billing/months/{month}/explain` 逐笔核对。

计费明细与目标行在同一数据库事务内提交：worker 先写目标行再写 `billing_ingestions`/`billing_lines`/`billing_skips`，任一步失败整体回滚，重试从头计一次；唯一约束保证丢回执后重投不重复计费。恢复备份只替换目标行并补登记缺失的首次入库记录，不新增明细。

## 迁移、校验、取消与恢复
字段类型为text/decimal/datetime。decimal使用Decimal精确转换为6位小数字符串，范围±1e9；无穷/NaN拒绝。空值/空串按required隔离（只有unit可非必填）；缺源字段隔离。时间统一UTC，无时区按Asia/Shanghai(+08:00)解释。自然键sample_code+test_name按规范JSON SHA-256计算；同次运行重复键只接受首条合法记录。每行与全部接受行用排序key/紧凑JSON/UTF-8算SHA-256。拒绝行保存原行和原因。

最多2000源行、每行20列、单元格500字符；每块1-500行。逐块更新进度；取消在块间检查，最终提交前锁定run再次检查，成功目标一次提交；取消/异常没有部分目标数据。后台任务使用简单Redis单消费者；进程崩溃可能留下running状态，需人工复核并新建任务，不自动重试。

下载备份为本工具目标行的逻辑JSON快照(version1)，含run_id/rows/row_hashes/checksum，不是pg_dump或物理备份，不含WAL/索引/权限。管理员恢复只允许该运行已保存checksum完全匹配的备份；先核验逐行/总体摘要、schema和自然键，再事务性替换该运行target_rows，回读重新计算摘要并保存recovery记录。篡改、错运行、未完成运行拒绝，恢复事务回滚；不能用任意新数据覆盖目标。

受控场景演示旧字段到新目标结构的逻辑迁移，数据库真实保存源/映射/目标/对账。不能连接远端数据库，不宣称生产异构迁移或灾备能力。

## 工程与开发
Python3.11/FastAPI/SQLAlchemy/psycopg/Redis/PostgreSQL16，原生HTML/CSS/JS，nginx1.27。exact五服务frontend/backend/worker/db/redis均healthcheck，数据库/Redis/共享产物命名卷；依赖健康后启动。请求追踪、全局错误、JWT授权和业务审计横切。

backend/src按models/schemas/services/controllers/routes/middlewares/config/constants/utils；backend/scripts/seed_demo.py；backend/migrations记录元数据版本。frontend/src按api/stores/pages/components/common/hooks/router/types/constants/utils；共享table/status/feedback；forms/poll hooks。角色/状态/类型枚举在backend/src/constants/enums.py与frontend/src/constants/enums.js。墨绿邮政凭单视觉，Noto Sans SC/Space Mono双字体来自Google Fonts，离线回退；桌面/平板/手机响应。

.env.example定义COMPOSE_PROJECT_NAME/三端口/JWT_SECRET/SEED_DEMO；DATABASE_URL/REDIS_URL由Compose注入。启动SEED_DEMO失败只记日志。本地开发pip install -r backend/requirements.txt，设数据库/Redis/JWT环境变量，在backend运行uvicorn src.main:app --reload；前端需/api代理，不能file://。测试/截图/日志放批次test-artifacts/gb-4109，源码不混入。MIT License。
