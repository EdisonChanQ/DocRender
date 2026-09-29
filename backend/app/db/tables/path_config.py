"""路径配置表 —— 存放项目文件存放的共享路径（上传、导出、模板等目录）。

数据库：SQL Server（docRender）
DDL 以 IF NOT EXISTS 保证幂等，可重复执行。
改表名只需修改下方 TABLE.name。
"""

from __future__ import annotations

from app.db.tables.base import TableDef

TABLE = TableDef(
    key="path_config",
    name="Sup_EWP_DCR_Path",
    comment="路径配置表",
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
        code        NVARCHAR(64)      NOT NULL,                                              -- 路径编码 / 用途标识，唯一（如 upload、export、template）
        name        NVARCHAR(128)     NOT NULL,                                              -- 路径显示名称
        path        NVARCHAR(1024)    NOT NULL,                                              -- 文件存放共享路径（UNC \\\\server\\share 或映射盘/本地路径）
        is_enabled  BIT               NOT NULL CONSTRAINT DF_${TABLE_NAME}_is_enabled DEFAULT (1),  -- 是否启用（1=启用，0=停用）
        remark      NVARCHAR(512)     NULL,                                                  -- 备注
        created_by        NVARCHAR(64)     NOT NULL CONSTRAINT DF_${TABLE_NAME}_created_by DEFAULT SUSER_SNAME(), -- 创建人
        created_datetime  DATETIME         NOT NULL CONSTRAINT DF_${TABLE_NAME}_created_datetime DEFAULT SYSUTCDATETIME(), -- 创建时间（UTC）
        updated_by        NVARCHAR(64)     NULL,                                                  -- 更新人
        updated_datetime  DATETIME         NULL,                                                  -- 最后更新时间（UTC）
        CONSTRAINT PK_${TABLE_NAME} PRIMARY KEY CLUSTERED (id),
        CONSTRAINT UQ_${TABLE_NAME}_code UNIQUE (code)
    );
END
GO
""",
)

# 示例数据（如需初始化默认路径，取消注释后执行）
# INSERT INTO dbo.Sup_EWP_DCR_Path (code, name, path, is_enabled, remark)
# VALUES
#     ('upload',   N'上传文件目录', N'\\fileserver\docRender\uploads',   1, N'解析上传的原始文件存放位置'),
#     ('parsed',   N'解析结果目录', N'\\fileserver\docRender\parsed',    1, N'解析出的文本 / 结果输出位置'),
#     ('template', N'模板文件目录', N'\\fileserver\docRender\templates', 1, N'报表 / 导出模板存放位置');
