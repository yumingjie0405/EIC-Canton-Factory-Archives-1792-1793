# `research_sessions` — 迁移候选（**未安装、未执行**）

本目录不是 `versions/`，Alembic 不会扫描、也不会执行这里的任何文件。

| 文件 | 作用 |
| --- | --- |
| `0001_research_sessions.ddl.sql` | 由 `app/models/research.py` 用 SQLAlchemy PostgreSQL 方言编译生成的 DDL，供人工审核 |
| `0001_research_sessions_revision.py.draft` | 同一份 DDL 的 Alembic revision 骨架，`down_revision` 为哨兵值，**故意无法被应用** |

## 唯一阻塞项

`backend/alembic_baseline/versions/` 目前没有任何 revision；现有数据库的完整 Schema 基线尚未通过审核（Stage 8 Phase-A 处于 `BLOCKED`）。此时把本候选放进 `versions/` 会产生一个 `down_revision = None` 的**第二个根 revision**，即"重复根 / 意外多 head"——正是必须避免的情形。

因此本候选**没有** `down_revision` 的真实目标；补齐基线 revision 之后才能按 `*.draft` 文件头部的清单安装。

## 同时生效的保护

- `app/models/research.py::ALEMBIC_MANAGED_TABLES = {"research_sessions"}`，`app/main.py` 的 `Base.metadata.create_all()` 会**跳过**该表，因此应用启动不会偷偷建表；
- 表在迁移安装前不存在，历史 API 会返回 `503` 并说明原因，不会伪装成"空历史"。

## 重算 DDL

DDL 由脚本生成，模型改动后请重新生成而不是手改：

```
cd backend
.\venv\Scripts\python.exe ...\generate_research_ddl.py
```

生成脚本位于本项目的审核产物目录（`eic_preflight_artifacts\S8_R2_history\generate_research_ddl.py`），逻辑等价于：

```python
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable, CreateIndex
from app.models.research import ResearchSession
print(CreateTable(ResearchSession.__table__).compile(dialect=postgresql.dialect()))
```
