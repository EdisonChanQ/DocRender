"""文件任务注册表 —— 上传落盘 + 任务队列（多实例从表抢占，不扫共享目录）。

状态机（5 态）：0 待处理 / 1 处理中 / 2 成功 / 3 失败 / 4 已取消
step（处理中阶段，5 步）：split 分页 / normalize 归一化 / rectify 纠正 / match 匹配模板 / slice 切片
抢占：UPDATE TOP(1) WITH (READPAST, ROWLOCK) + 租约（lease_expires_datetime），
实例崩溃后租约超时由任意实例的 claim 顺带回收到期待处理。
文件只记相对路径（rel_path），运行时与路径表 code='default' 的共享目录拼接，方便迁移。
"""

from __future__ import annotations

from app.db.tables.base import TableDef

TABLE = TableDef(
    key="file_job",
    name="Sup_EWP_DCR_FileJob",
    comment="文件任务注册表",
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
        code        NVARCHAR(64)      NOT NULL,                                              -- 文件ID（JOB+时间+随机），对外唯一标识
        file_hash   NVARCHAR(64)      NOT NULL,                                              -- SHA-256（十六进制），注册去重
        file_name   NVARCHAR(255)     NOT NULL,                                              -- 原始文件名
        rel_path    NVARCHAR(512)     NOT NULL,                                              -- 相对路径（与路径表 upload 共享目录拼接）
        file_size   BIGINT            NOT NULL,                                              -- 字节数
        extension   NVARCHAR(16)      NULL,                                                  -- 扩展名（小写含点）
        category_id INT               NOT NULL,                                              -- 所属分类ID
        template_id INT               NULL,                                                  -- 实例强制模板ID（NULL=流水线自动匹配）
        state       INT               NOT NULL CONSTRAINT DF_${TABLE_NAME}_state DEFAULT (0), -- 0待处理 1处理中 2成功 3失败 4已取消
        step        NVARCHAR(32)      NULL,                                                  -- 当前阶段：split/normalize/rectify/match/slice
        instance_id NVARCHAR(64)      NULL,                                                  -- 抢占实例标识
        claimed_datetime  DATETIME          NULL,                                                  -- 抢占时间（UTC）
        lease_expires_datetime DATETIME     NULL,                                                  -- 租约到期（UTC，超时未心跳则回收）
        heartbeat_datetime DATETIME         NULL,                                                  -- 最后心跳（UTC）
        retry_count INT               NOT NULL CONSTRAINT DF_${TABLE_NAME}_retry DEFAULT (0), -- 已重试次数
        max_retry   INT               NOT NULL CONSTRAINT DF_${TABLE_NAME}_maxretry DEFAULT (3), -- 最大重试次数
        priority    INT               NOT NULL CONSTRAINT DF_${TABLE_NAME}_priority DEFAULT (0), -- 优先级（大者先）
        error_msg   NVARCHAR(1024)    NULL,                                                  -- 最近失败原因
        page_count  INT               NULL,                                                  -- 分页结果数
        output_rel_path NVARCHAR(512) NULL,                                                  -- 归一化/切片输出相对目录
        created_by        NVARCHAR(64) NOT NULL CONSTRAINT DF_${TABLE_NAME}_created_by DEFAULT SUSER_SNAME(), -- 创建人（注册者）
        created_datetime  DATETIME     NOT NULL CONSTRAINT DF_${TABLE_NAME}_created_datetime DEFAULT SYSUTCDATETIME(), -- 注册时间（UTC）
        updated_by        NVARCHAR(64) NULL,                                                  -- 更新人
        updated_datetime  DATETIME     NULL,                                                  -- 最后更新时间（UTC）
        CONSTRAINT PK_${TABLE_NAME} PRIMARY KEY CLUSTERED (id),
        CONSTRAINT UQ_${TABLE_NAME}_code UNIQUE (code)
    );
    CREATE INDEX IX_${TABLE_NAME}_state_priority ON dbo.${TABLE_NAME} (state, priority DESC, id);
    CREATE INDEX IX_${TABLE_NAME}_hash ON dbo.${TABLE_NAME} (file_hash);
    CREATE INDEX IX_${TABLE_NAME}_category ON dbo.${TABLE_NAME} (category_id);
END
GO
""",
)
