"""分类表 —— 文档/解析结果的分类（分类管理页面的主数据）。

数据库：SQL Server（docRender）
DDL 以 IF NOT EXISTS 保证幂等，可重复执行。
改表名只需修改下方 TABLE.name。
"""

from __future__ import annotations

from app.db.tables.base import TableDef

TABLE = TableDef(
    key="category",
    name="Sup_EWP_DCR_Category",
    comment="分类表",
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
        code        NVARCHAR(64)      NOT NULL,                                              -- 分类编码，唯一（如 invoice、contract）
        name        NVARCHAR(128)     NOT NULL,                                              -- 分类名称
        sort_order  INT               NOT NULL CONSTRAINT DF_${TABLE_NAME}_sort_order DEFAULT (0), -- 排序号（越小越前）
        is_enabled  BIT               NOT NULL CONSTRAINT DF_${TABLE_NAME}_is_enabled DEFAULT (1),  -- 是否启用（1=启用，0=停用）
        remark      NVARCHAR(512)     NULL,                                                  -- 备注
        created_by        NVARCHAR(64) NOT NULL CONSTRAINT DF_${TABLE_NAME}_created_by DEFAULT SUSER_SNAME(), -- 创建人
        created_datetime  DATETIME     NOT NULL CONSTRAINT DF_${TABLE_NAME}_created_datetime DEFAULT SYSUTCDATETIME(), -- 创建时间（UTC）
        updated_by        NVARCHAR(64) NULL,                                                  -- 更新人
        updated_datetime  DATETIME     NULL,                                                  -- 最后更新时间（UTC）
        CONSTRAINT PK_${TABLE_NAME} PRIMARY KEY CLUSTERED (id),
        CONSTRAINT UQ_${TABLE_NAME}_code UNIQUE (code)
    );
END
GO
""",
)
