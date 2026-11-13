# 备份与恢复说明

> 对应需求：`docs/阶段二.md` 2.1 的 S2-10 与 14.7 的备份恢复项
>
> 脚本：`backend/scripts/backup.ps1`
>
> 更新日期：2026-11-13

---

## 一、备份范围

| 内容 | 路径 | 说明 |
| --- | --- | --- |
| 数据库 | `rag_campus` | 用户、部门、文档、版本、反馈、评估报告、审计与检索日志 |
| 原始文档 | `storage/` | 按知识库分目录保存的上传文件 |
| 索引 | `indexes/` | Chroma 向量库与 BM25 索引文件 |

原始文档与数据库是必需项。索引可以由原始文档重建，备份索引只用于缩短恢复时间。

---

## 二、执行备份

在项目根目录打开 PowerShell，先设置数据库口令，再执行脚本：

```powershell
$env:RAG_DB_PASSWORD = "数据库口令"
backend\scripts\backup.ps1
```

可覆盖的参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `-BackupRoot` | `backend/backups` | 备份根目录，每次执行生成时间戳子目录 |
| `-MysqlHost` | `127.0.0.1` | 数据库地址 |
| `-MysqlPort` | `3307` | 数据库端口 |
| `-MysqlUser` | `root` | 数据库账号 |
| `-MysqlDatabase` | `rag_campus` | 数据库名 |

示例：

```powershell
backend\scripts\backup.ps1 -BackupRoot D:\rag-backups -MysqlPort 3306
```

脚本会生成 `yyyyMMdd-HHmmss` 目录，包含 `rag_campus.sql`、`storage/`、`indexes/` 与 `RESTORE.txt`。未找到 `mysqldump` 时会跳过数据库导出并给出警告，此时需要手工备份 MySQL 数据目录。

`backend/backups/` 已加入 `.gitignore`，不会进入版本库。

---

## 三、恢复步骤

1. 停止后端服务，避免恢复过程中继续写入。
2. 将备份目录中的 `storage/` 与 `indexes/` 覆盖回项目根目录。
3. 还原数据库：

```powershell
$env:MYSQL_PWD = "数据库口令"
mysql -h 127.0.0.1 -P 3307 -u root rag_campus < backend\backups\20261113-120000\rag_campus.sql
Remove-Item Env:MYSQL_PWD
```

4. 启动后端，访问 `http://127.0.0.1:8004/health`。
5. 确认 `status` 为 `ok`，各知识库的 `db_chunks`、`chroma_chunks`、`bm25_chunks` 一致。
6. 若索引损坏或与数据库不一致，使用管理员账号在知识库页执行重建，或调用 `/api/v1/kb/{id}/rebuild`。

恢复顺序建议为数据库、原始文档、索引。只恢复数据库与原始文档时，重建索引即可恢复检索能力。

---

## 四、校验与演练

恢复后至少检查：

1. `/health` 返回 `ok`，`mismatches` 为 0。
2. 管理端能看到知识库与文档，版本状态与备份时一致。
3. 用户端可以正常问答并返回引用。
4. 预览接口可以按 ACL 打开原文。
5. 抽查一份文档的版本历史与分块数。

建议每季度演练一次完整恢复，演练记录包含备份目录、恢复耗时、健康检查结果与异常处理。

---

## 五、保留策略

1. 建议保留最近 7 个每日备份与 4 个每周备份。
2. 发布重要制度版本或执行迁移前，额外做一次手工备份。
3. 备份目录与生产数据不要放在同一块磁盘。
4. 备份文件包含制度原文与用户信息，需要限制访问权限，不得放入公开仓库或公开网盘。

---

## 六、注意事项

1. 脚本从环境变量 `RAG_DB_PASSWORD` 或 `MYSQL_PASSWORD` 读取口令，未设置时直接报错，不在脚本中保存明文口令。
2. `mysqldump` 使用 `--single-transaction`，对 InnoDB 表可在不锁表的情况下导出。
3. 恢复会覆盖现有数据库与文件，执行前确认目标环境与备份时间点。
4. `alembic downgrade` 不属于常规恢复手段，执行前必须先备份。
