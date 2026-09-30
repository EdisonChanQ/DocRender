"""模板字段明细表 —— 记录模板范本图上的文字目标块（供提取结果按字段输出 JSON）。

字段块坐标以范本图像素为准（x/y/w/h），pad 为外扩像素（识别时上下文留量）。
field_type：text 块走 OCR 文字识别，qr 块走二维码解码——提取实例按类型分派工具。
一个模板（一张支票范本）下多个字段；改表名只需修改下方 TABLE.name。
"""

from __future__ import annotations

from app.db.tables.base import TableDef

TABLE = TableDef(
    key="template_field",
    name="Sup_DCR_Template_Field",
    comment="模板字段明细表",
    ddl="""
IF NOT EXISTS (
    SELECT 1
    FROM sys.tables
    WHERE schema_id = SCHEMA_ID('dbo') AND name = N'${TABLE_NAME}'
)
BEGIN
    CREATE TABLE dbo.${TABLE_NAME}
    (
        id          INT IDENTITY(1,1) NOT NULL,                                              -- 自增主键（字段ID）
        template_id INT               NOT NULL,                                              -- 所属模板ID
        field_key   NVARCHAR(64)      NOT NULL,                                              -- 字段命名（OCR JSON 输出键，如 payee/amount/date）
        label       NVARCHAR(128)     NOT NULL,                                              -- 字段说明（如 收款人、金额大写）
        field_type  NVARCHAR(16)      NOT NULL CONSTRAINT DF_${TABLE_NAME}_field_type DEFAULT (N'text'), -- 块类型：text 走 OCR，qr 走二维码
        x           INT               NOT NULL,                                              -- 块左上角 X（范本图像素）
        y           INT               NOT NULL,                                              -- 块左上角 Y（范本图像素）
        width       INT               NOT NULL,                                              -- 块宽（像素）
        height      INT               NOT NULL,                                              -- 块高（像素）
        pad         INT               NOT NULL CONSTRAINT DF_${TABLE_NAME}_pad DEFAULT (0),   -- 外扩像素（识别时四周留量）
        sort_order  INT               NOT NULL CONSTRAINT DF_${TABLE_NAME}_sort_order DEFAULT (0), -- 排序号
        ref_image   VARBINARY(MAX)    NULL,                                                  -- 字段块样本图（从模板范本裁出，OCR 按块匹配的底片；列表查询不取此列）
        created_by        NVARCHAR(64) NOT NULL CONSTRAINT DF_${TABLE_NAME}_created_by DEFAULT SUSER_SNAME(), -- 创建人
        created_datetime  DATETIME     NOT NULL CONSTRAINT DF_${TABLE_NAME}_created_datetime DEFAULT SYSUTCDATETIME(), -- 创建时间（UTC）
        updated_by        NVARCHAR(64) NULL,                                                  -- 更新人
        updated_datetime  DATETIME     NULL,                                                  -- 最后更新时间（UTC）
        CONSTRAINT PK_${TABLE_NAME} PRIMARY KEY CLUSTERED (id),
        CONSTRAINT UQ_${TABLE_NAME}_tpl_key UNIQUE (template_id, field_key)
    );
    CREATE INDEX IX_${TABLE_NAME}_template_id ON dbo.${TABLE_NAME} (template_id);
END
GO
""",
)
