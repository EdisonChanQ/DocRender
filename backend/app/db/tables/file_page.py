"""文件分页表 —— 每页一条记录；同时是 OCR/QR 文字提取任务的抢占单元。

- 页由所属文件任务的流水线实例顺序处理（split/normalize/rectify/match/slice），
  match 结果（template_id）与归一化产物记录在页行上。
- 登记产物后页 state=0 进入提取队列；OCR/QR 实例 claim 页 → 查该页切片
  （file_slice：块路径+坐标）→ 逐块解析 → 汇总 JSON 回写 result_json。
- 页终态聚合 job（等待OCR → 成功/失败）。租约超时自动回收。
state：0 待提取 / 1 处理中 / 2 成功 / 3 失败（页级提取状态）。
rel_path 均为相对路径，与路径表 code='default' 共享目录拼接。
"""

from __future__ import annotations

from app.db.tables.base import TableDef

TABLE = TableDef(
    key="file_page",
    name="Sup_EWP_DCR_FilePage",
    comment="文件分页表（提取抢占单元）",
    ddl="""
IF NOT EXISTS (
    SELECT 1
    FROM sys.tables
    WHERE schema_id = SCHEMA_ID('dbo') AND name = N'${TABLE_NAME}'
)
BEGIN
    CREATE TABLE dbo.${TABLE_NAME}
    (
        id          INT IDENTITY(1,1) NOT NULL,                                              -- 自增主键
        job_id      INT               NOT NULL,                                              -- 所属文件任务ID
        job_code    NVARCHAR(64)      NULL,                                                  -- 所属文件ID（冗余，排查/反查免 JOIN）
        page_index  INT               NOT NULL,                                              -- 页码（0 基）
        state       INT               NOT NULL CONSTRAINT DF_${TABLE_NAME}_state DEFAULT (0), -- 0待提取 1处理中 2成功 3失败
        orig_rel_path       NVARCHAR(512) NULL,                                              -- 原始页图相对路径
        normalized_rel_path NVARCHAR(512) NULL,                                              -- 归一化/纠正后页图相对路径
        skew        FLOAT             NULL,                                                  -- 检测到的倾斜角（度）
        width       INT               NULL,                                                  -- 页宽（像素）
        height      INT               NULL,                                                  -- 页高（像素）
        dpi         INT               NULL,                                                  -- 页分辨率
        template_id INT               NULL,                                                  -- 匹配到的模板ID（页级归属，切片反查此列）
        match_score FLOAT             NULL,                                                  -- 模板匹配度（ECC 相关系数 0~1，越高越可信）
        page_mode   NVARCHAR(16)      NULL,                                                  -- 模板类型 full_page 整页 / block 局部块
        slice_count INT               NULL,                                                  -- 该页切出的块数
        result_json NVARCHAR(MAX)     NULL,                                                  -- 提取结果：{"data":{field_key:{value,source,confidence}}}
        instance_id NVARCHAR(64)      NULL,                                                  -- 抢占实例（OCR/QR 服务）
        claimed_datetime  DATETIME    NULL,                                                  -- 抢占时间（UTC）
        lease_expires_datetime DATETIME NULL,                                                -- 租约到期（UTC）
        heartbeat_datetime DATETIME   NULL,                                                  -- 最后心跳（UTC）
        retry_count INT               NOT NULL CONSTRAINT DF_${TABLE_NAME}_retry DEFAULT (0), -- 已重试次数
        max_retry   INT               NOT NULL CONSTRAINT DF_${TABLE_NAME}_maxretry DEFAULT (3), -- 最大重试
        priority    INT               NOT NULL CONSTRAINT DF_${TABLE_NAME}_priority DEFAULT (0), -- 优先级
        error_msg   NVARCHAR(1024)    NULL,                                                  -- 失败原因
        created_by        NVARCHAR(64) NOT NULL CONSTRAINT DF_${TABLE_NAME}_created_by DEFAULT SUSER_SNAME(), -- 创建人
        created_datetime  DATETIME     NOT NULL CONSTRAINT DF_${TABLE_NAME}_created_datetime DEFAULT SYSUTCDATETIME(), -- 创建时间（UTC）
        updated_by        NVARCHAR(64) NULL,                                                  -- 更新人
        updated_datetime  DATETIME     NULL,                                                  -- 最后更新时间（UTC）
        CONSTRAINT PK_${TABLE_NAME} PRIMARY KEY CLUSTERED (id),
        CONSTRAINT UQ_${TABLE_NAME}_job_page UNIQUE (job_id, page_index)
    );
    CREATE INDEX IX_${TABLE_NAME}_job ON dbo.${TABLE_NAME} (job_id);
    CREATE INDEX IX_${TABLE_NAME}_claim ON dbo.${TABLE_NAME} (state, priority DESC, id);
END
GO
-- 增量补列（表已存在时上面的 IF NOT EXISTS 不会重建，这里兜底）
IF COL_LENGTH('dbo.${TABLE_NAME}', 'match_score') IS NULL
    ALTER TABLE dbo.${TABLE_NAME} ADD match_score FLOAT NULL;
GO
IF COL_LENGTH('dbo.${TABLE_NAME}', 'page_mode') IS NULL
    ALTER TABLE dbo.${TABLE_NAME} ADD page_mode NVARCHAR(16) NULL;
GO
""",
)
