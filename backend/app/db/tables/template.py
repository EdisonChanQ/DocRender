"""模板表 —— 每个分类下可创建多个模板（模板管理页面的主数据）。

数据库：SQL Server（docRender）
说明：不加 category_id 外键约束——业务要求"删除分类后模板失效而非被阻止/级联删除"，
孤儿引用由应用层校验（启用模板必须挂到有效分类）。
改表名只需修改下方 TABLE.name。
"""

from __future__ import annotations

from app.db.tables.base import TableDef

TABLE = TableDef(
    key="template",
    name="Sup_DCR_Template",
    comment="模板表",
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
        code        NVARCHAR(64)      NOT NULL,                                              -- 模板ID，自动生成（TPL+年月+序号），唯一
        category_id INT               NOT NULL,                                              -- 所属分类ID（关联分类表 id；分类被删后成为孤儿引用）
        name        NVARCHAR(128)     NOT NULL,                                              -- 模板名称
        dpi         INT               NULL,                                                  -- 模板块规格：分辨率（前端框选提交时落库）
        width       INT               NULL,                                                  -- 模板块规格：宽度（像素）
        height      INT               NULL,                                                  -- 模板块规格：高度（像素）
        ref_image   VARBINARY(MAX)    NULL,                                                  -- 参照范本图（前端框选裁出的支票 PNG，自动分切的匹配底片；列表查询不取此列）
        is_enabled  BIT               NOT NULL CONSTRAINT DF_${TABLE_NAME}_is_enabled DEFAULT (1),  -- 是否启用（所属分类被删除时自动置 0）
        created_by        NVARCHAR(64) NOT NULL CONSTRAINT DF_${TABLE_NAME}_created_by DEFAULT SUSER_SNAME(), -- 创建人
        created_datetime  DATETIME     NOT NULL CONSTRAINT DF_${TABLE_NAME}_created_datetime DEFAULT SYSUTCDATETIME(), -- 创建时间（UTC）
        updated_by        NVARCHAR(64) NULL,                                                  -- 更新人
        updated_datetime  DATETIME     NULL,                                                  -- 最后更新时间（UTC）
        CONSTRAINT PK_${TABLE_NAME} PRIMARY KEY CLUSTERED (id),
        CONSTRAINT UQ_${TABLE_NAME}_code UNIQUE (code)
    );
    CREATE INDEX IX_${TABLE_NAME}_category_id ON dbo.${TABLE_NAME} (category_id);
END
GO
""",
)
