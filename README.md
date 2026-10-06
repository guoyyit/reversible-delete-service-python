# Reversible Delete Resource Service

带可逆删除（软删除）、保留期恢复与到期回收的资源管理服务。

## 运行

```bash
uv sync
uv run uvicorn main:app
```

## 测试

```bash
uv run pytest tests/ -q
```

## 概念

### 可逆删除

`DELETE /resources/{id}` 执行软删除：

- 被删除资源立即从查询（`GET`）、列表与计数（`count`）中消失，既有调用方的默认行为不变；
- 删除是级联的：父资源删除时，其全部存活后代一并软删除，并记录级联根；
- 名称唯一性只在存活资源间生效，删除后同名资源可立即重建；
- 重复删除返回确定的 `404`。

### 恢复

`POST /resources/{id}/restore` 在保留期内恢复资源：

- 恢复级联根会一并恢复被同次级联删除的后代，内容与关联关系与删除前完全一致；
- 独立删除的子资源不随父资源恢复而复活；
- 父仍被删除时直接恢复子资源返回 `409`（不产生孤儿数据）；
- 恢复时若同名存活资源已存在，返回确定的 `409`，不静默覆盖、不产生重复数据；
- 重复恢复返回 `409`；超过保留期返回 `410`。

### 保留期与回收

- 资源删除后进入保留期（默认 7 天），保留期内可恢复；
- 保留期边界含端点：`deleted_at + retention_seconds` 时刻仍可恢复，之后返回 `410`；
- 回收（物理清除）由后台线程周期执行，也可由管理员调用 `POST /admin/recycle` 主动触发；
- 回收**幂等且可中断重入**：重复执行结果一致；状态通过原子快照（临时文件 + rename）持久化，
  服务重启后自动加载快照并立即执行一次回收，不会留下半删除状态，也不会误删仍在保留期内的资源。

### 批量操作

`POST /resources/batch-delete`、`POST /resources/batch-restore` 接收 `{"ids": [...]}`：

- **原子语义**：全部校验通过才一次性生效；任一项失败则整体不生效，响应中带有
  `batch aborted` 说明，不会留下部分成功的中间状态；
- 同批包含父子资源时自动按依赖顺序处理。

### 权限与审计

- 身份通过请求头传递：`X-User-Id`（必需）、`X-User-Role: admin`（可选）；
- 删除/恢复仅资源拥有者或管理员可执行，越权返回 `403`；
- 已删除资源仅拥有者/管理员可通过 `include_deleted=true` 查看，不向无权者泄露存在性；
- 删除、恢复、回收的每次成功与失败（denied / conflict / not_found / expired）都写入审计日志，
  管理员可通过 `GET /audit` 查询；审计记录随快照持久化，重启不丢失。

### 并发一致性

所有读-改-写在单把可重入锁的临界区内完成，每次变更整体提交。
任一时刻读取到的都是单一的已删除/未删除状态，不存在短暂双写或字段级混合。

## 配置项（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RETENTION_SECONDS` | `604800`（7 天） | 保留期秒数；保留期内可恢复，到期后可被回收 |
| `STATE_FILE` | 空（纯内存） | 状态快照文件路径；设置后重启可恢复状态 |
| `RECYCLE_INTERVAL_SECONDS` | `60` | 后台回收间隔；`<=0` 表示不启动后台回收线程 |

## 接口一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/resources` | 创建资源（同名存活资源冲突返回 `409`） |
| `GET` | `/resources` | 列表，`?parent_id=`、`?include_deleted=` |
| `GET` | `/resources/{id}` | 查询，`?include_deleted=` |
| `DELETE` | `/resources/{id}` | 可逆删除（级联） |
| `POST` | `/resources/{id}/restore` | 保留期内恢复 |
| `POST` | `/resources/batch-delete` | 批量删除（原子） |
| `POST` | `/resources/batch-restore` | 批量恢复（原子） |
| `POST` | `/admin/recycle` | 手动触发回收（管理员） |
| `GET` | `/audit` | 审计日志（管理员） |
