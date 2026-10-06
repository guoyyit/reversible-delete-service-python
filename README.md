# Reversible Delete Service

资源管理服务，支持**可逆删除（软删除）**、**保留期内恢复**与**到期回收**。
基于 FastAPI + SQLite（标准库），所有多行状态变更都在单事务内完成。

## 快速开始

```bash
uv sync
uv run uvicorn main:app --reload
uv run pytest            # 运行测试
```

调用时在请求头携带调用方身份：`X-User-Id: <user>`。

## 核心语义

### 1. 可逆删除

`DELETE /resources/{id}` 不再物理删除，而是写入 `deleted_at` /
`deleted_by` / `delete_reason` / `delete_root_id`（单条 UPDATE 完成，
不存在字段级混合状态）。被删除的资源：

- 从 `GET /resources`、`GET /resources/{id}`、`GET /stats`、
  `GET /resources/{id}/children` 的默认结果中消失（404 / 不计数）；
- 通过 `?include_deleted=true` 仍可查询，用于回收站与恢复流程；
- 重复删除是幂等的：返回 200，删除时间戳保持不变。

`POST /resources/{id}/restore` 在保留期内恢复资源，内容、父子关联与
删除前完全一致；重复恢复同样幂等。

### 2. 唯一性可见范围

`(parent_id, name)` 的唯一性只约束**未删除**的资源（数据库部分唯一索引
保证）。删除后同名资源可以立即重建；恢复时若同名资源已存在，返回
**确定的 409 `name_conflict`**，不产生重复数据、不静默覆盖。

### 3. 父子级联

删除父资源会在**同一事务**内级联删除所有存活的子孙，并打上
`delete_root_id = 根id` 标记：

- 父删除期间，整个子树确定不可见；
- 恢复根节点会精确恢复当时级联删除的集合——不产生孤儿、不重复关联；
- 级联成员不能单独恢复（409 `cascade_member`，提示恢复根节点）；
- 在级联之前被独立删除的子孙保持删除状态，可独立恢复；
- 已删除的父节点下不能新建子资源（404）。

### 4. 批量操作

`POST /resources/batch-delete` 与 `POST /resources/batch-restore`：

- `atomic: true`（默认）：先校验全部条目，再在**单个事务**内应用。
  任一条目失败则整体回滚，返回 409 与每项失败原因——调用方能明确
  区分"整体失败（什么都没应用）"与成功；
- `atomic: false`：逐项独立提交，响应中 `succeeded` / `failed`
  明确列出部分成功明细，不留无法解释的中间状态。

### 5. 权限与审计

- 删除/恢复要求资源属主或管理员（`RDS_ADMIN_USERS`），否则 403；
- 缺少 `X-User-Id` 返回 401；
- 恢复仅在**保留期内**允许，过期返回 409 `retention_expired`；
- 删除、恢复、回收、被拒绝的尝试、恢复冲突都会写入审计表，
  管理员通过 `GET /audit` 查询。审计记录与业务写入同事务提交；
  冲突/拒绝类审计在独立事务中持久化，不随业务回滚丢失。

### 6. 保留期与回收

- 保留期由 `RDS_RETENTION_SECONDS` 配置（默认 30 天），自 `deleted_at`
  起算。`deleted_at + retention < now` 的资源可被回收；
- `POST /admin/recycle`（仅管理员）同步执行一轮回收；设置
  `RDS_RECYCLE_INTERVAL_SECONDS > 0` 可开启后台周期回收；
- 回收**幂等**：重复执行是 no-op；**可中断重入**：每批在独立事务中
  提交，中途崩溃只回滚当前批次，重启后重跑即可，不会留下半删除状态，
  也不会误删仍在保留期内的资源；
- 回收按拓扑顺序先删子孙再删父节点，整棵子树一并清除，不留孤儿。

### 7. 并发一致性

所有写操作经单连接 `RLock` 串行化，多行变更包裹在单事务中；任一时刻
读到的资源都处于单一的"已删除/未删除"状态，不会出现短暂双写或字段级
混合。`children()` 在同一把锁内完成父检查与子列表读取，关联结果是
同一时刻的快照。

## 配置项

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RDS_DB_PATH` | `:memory:` | SQLite 路径；设为文件路径以获得重启持久化 |
| `RDS_RETENTION_SECONDS` | `2592000`（30 天） | 删除后的可恢复保留期 |
| `RDS_ADMIN_USERS` | 空 | 逗号分隔的管理员用户列表 |
| `RDS_RECYCLE_BATCH_SIZE` | `100` | 每轮回收的单批事务大小 |
| `RDS_RECYCLE_INTERVAL_SECONDS` | `0`（关闭） | 后台回收间隔；0 表示仅手动触发 |

## API 一览

| 方法/路径 | 说明 |
| --- | --- |
| `GET /` | 既有健康检查（契约不变） |
| `POST /resources` | 创建（同名冲突 409） |
| `GET /resources` / `GET /resources/{id}` | 查询（`include_deleted` 可选） |
| `GET /resources/{id}/children` | 关联子资源快照 |
| `DELETE /resources/{id}?reason=` | 可逆删除（幂等） |
| `POST /resources/{id}/restore` | 保留期内恢复（幂等） |
| `POST /resources/batch-delete` / `batch-restore` | 批量操作（atomic 可选） |
| `GET /stats` | 计数（默认不含已删除） |
| `GET /audit` | 审计日志（管理员） |
| `POST /admin/recycle` | 触发一轮到期回收（管理员） |

## 测试

```bash
uv run pytest -q
```

覆盖：重复删除/恢复、同名重建与恢复冲突、级联可见性、批量原子/部分
成功、保留期边界、回收幂等与中断重入、并发删除/恢复/读取、重启恢复、
权限与审计。
