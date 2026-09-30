"""切片明细表 —— 页内切出的块（对应模板标注字段），产物 + 解析结果记录。

不是抢占单元：OCR/QR 提取抢占的是"页"行（见 file_page），页提取时查本表拿到
该页所有块的相对路径 + 坐标 + field_key，逐块拉图解析；complete 时**两处同更新**：
本行写 result_content/source/confidence（块级解析结果），页行汇总 result_json。
模板归属沿 page_id → 页行的 template_id 反查，切片不冗余存模板ID。
rel_path 为相对路径（页图同名子目录内的切片图，提取时落盘回写），
与路径表 code='default' 共享目录拼接。
"""

from __future__ import annotations

from app.db.tables.base import TableDef

TABLE = TableDef(
    key="file_slice",
    name="Sup_DCR_FileSlice",
    comment="切片明细表",
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
        job_id      INT               NOT NULL,                                              -- 所属文件任务ID（冗余，反查免 JOIN）
        page_id     INT               NOT NULL,                                              -- 所属分页ID
        slice_index INT               NOT NULL,                                              -- 块序号（页内 0 基）
        field_key   NVARCHAR(64)      NULL,                                                  -- 对应模板字段命名（流水线登记时带入，回写按此匹配）
        rel_path    NVARCHAR(512)     NULL,                                                  -- 切片图相对路径（页图同名子目录内，提取时落盘回写）
        x           INT               NOT NULL,                                              -- 块在页上的 X（像素）
        y           INT               NOT NULL,                                              -- 块在页上的 Y
        width       INT               NOT NULL,                                              -- 块宽
        height      INT               NOT NULL,                                              -- 块高
        result_content NVARCHAR(MAX)  NULL,                                                  -- 解析结果原文（OCR 文本 / QR 载荷）
        source      NVARCHAR(8)       NULL,                                                  -- 提取来源：OCR / QR / LLM（低置信兜底）
        confidence  FLOAT             NULL,                                                  -- 置信度 0~1（QR 无置信度为 NULL）
        created_by        NVARCHAR(64) NOT NULL CONSTRAINT DF_${TABLE_NAME}_created_by DEFAULT SUSER_SNAME(), -- 创建人
        created_datetime  DATETIME     NOT NULL CONSTRAINT DF_${TABLE_NAME}_created_datetime DEFAULT SYSUTCDATETIME(), -- 创建时间（UTC）
        updated_by        NVARCHAR(64) NULL,                                                  -- 更新人
        updated_datetime  DATETIME     NULL,                                                  -- 最后更新时间（UTC）
        CONSTRAINT PK_${TABLE_NAME} PRIMARY KEY CLUSTERED (id),
        CONSTRAINT UQ_${TABLE_NAME}_page_slice UNIQUE (page_id, slice_index)
    );
    CREATE INDEX IX_${TABLE_NAME}_job ON dbo.${TABLE_NAME} (job_id);
    CREATE INDEX IX_${TABLE_NAME}_page ON dbo.${TABLE_NAME} (page_id);
END
GO
""",
)
