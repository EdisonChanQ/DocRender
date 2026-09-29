# DocRender

文档解析与重排系统：上传 PDF / 图片 → 落盘共享目录 → 注册任务入队 → 流水线实例分页、配准、匹配模板、切块 → OCR / QR 提取字段 → 结果反查与展示。

- **后端**：FastAPI + SQLAlchemy（`backend/`），多实例并发安全的抢占式任务队列（READPAST + 租约）
- **前端**：React + Vite + TypeScript（`frontend/`）
- **数据库**：SQLite / MySQL / PostgreSQL / SQL Server 运行时可选，连接配置存 `backend/storage/database.json`

## 目录结构

```
backend/
  app/
    api/routes/       # 路由（file_job / file_page / file_slice / category / template ...）
    db/tables/        # 每张表一个 .py，导出 TableDef（物理表名唯一定义处）
    services/         # 业务服务（任务队列、页队列、渲染、模板匹配、OCR/QR）
    core/             # OCR / QR 解码等基础能力
  models/             # OCR 模型（onnx 目录不入库，见下）
  storage/            # 运行时数据（数据库配置、上传件）—— 不入库
  migrate.py          # 建表 / 增量补列
  worker.py           # 流水线消费端（claim → 分页 → 配准 → 切块 → 提取）
  run_dev.py          # 开发启动
frontend/
  src/
    api/              # 接口封装
    components/       # 通用组件
    pages/            # 页面
    hooks/            # 复用逻辑
```

## 快速开始

### 1. 后端

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
copy .env.example .env           # 按需修改

# 配置数据库连接后建表
python migrate.py

# 启动（默认 0.0.0.0:8000，API 前缀 /api）
python run_dev.py
```

### 2. 前端

```bash
cd frontend
npm install
npm run dev                       # 默认 http://localhost:5173
```

## OCR 模型（必须另行下载）

`backend/models/onnx/` 体积约 163MB，**未纳入版本库**。缺少模型时 OCR 提取不可用。

需要放置以下文件：

```
backend/models/onnx/PP-OCRv6/
  det/PP-OCRv6_det_medium.onnx     # 文本检测（约 60MB）
  det/PP-OCRv6_det_small.onnx      # 文本检测（轻量，约 9.5MB）
  rec/PP-OCRv6_rec_medium.onnx     # 文本识别（约 74MB）
  rec/PP-OCRv6_rec_small.onnx      # 文本识别（轻量，约 21MB）
```

模型配置见 `backend/models/config_v6_medium.yaml` / `config_v6_small.yaml` / `config_detect.yaml`。

## 任务与队列模型

**两级抢占**：

1. `claim_job` —— 整个文件任务（`state` 0 待处理 → 1 处理中），一个任务只被抢一次
2. `claim_page` —— 分页产出的每一页（页 `state` 0 待提取 → 1 处理中），一页多块时可并行提取

`job` 状态：`0 待处理 / 1 处理中 / 2 成功 / 3 失败 / 4 已取消 / 5 等待OCR`
`job.step`：`split / normalize / rectify / match / slice`

租约（`lease_expires_datetime`）超时自动回收：`retry + 1` 回队，超过 `max_retry` 置失败终态。

**落盘路径**（相对共享目录，`rel_path` 只存相对路径）：

```
jobs/{分类code}/{yyyyMM}/{yyyyMMdd}/{job_code}/
  ├── source/{原文件名}                 上传原件
  ├── pages/{job}.p{页}c{块}.png        配准通过的页 / 块产物
  ├── pages/{job}.p{页}c{块}/{字段}.png  该块的字段切片图
  └── reject/{job}.p{页}c{块}.png       相关度不足的页，隔离待审
```

## 关键约定

- **时间字段一律 `xxx_datetime`**（`created_datetime` / `updated_datetime` / `lease_expires_datetime`），不用 `_at`
- **路径只存相对路径** `rel_path`，与路径表 `code='default'` 的共享目录拼接
- 物理表名只在 `backend/app/db/tables/<key>.py` 的 `name` 字段定义，业务代码用 `TABLES[key].name` 引用
- 改表结构：改对应 `.py` 的 ddl/name 后重跑 `python migrate.py [key...]`

## 服务化（Windows）

`backend/service/` 提供 `install_service.bat` / `uninstall_service.bat`，将后端注册为 Windows 服务。

## 配置说明

- `WORKER_ENABLED=true` 时，流水线 worker 随后端自动后台挂载；设为 `false` 需自行运行 `python worker.py run`
- `DEBUG=true` 会启用 uvicorn reload，**文件一变就重启进程、PowerShell worker 一同被杀**，日常调试建议 `false`
- 数据库凭据等敏感配置在 `backend/storage/database.json`，已被 `.gitignore` 排除
