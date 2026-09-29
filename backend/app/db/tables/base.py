"""表结构定义基类。

约定：每张物理表对应 app/db/tables/ 下的一个 .py 文件（如 category.py），
文件内导出一个模块级常量 `TABLE = TableDef(...)`。
改表名 = 只改对应文件里的 `name` 字段，后端业务代码与迁移器自动跟随。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TableDef:
    key: str  # 逻辑名（稳定标识，用于代码引用与命令行选择），不随物理表名变化
    name: str  # 物理表名 —— 全项目唯一定义处
    ddl: str  # 建表 DDL，其中的 ${TABLE_NAME} 由迁移器注入 name
    comment: str = ""  # 用途说明（迁移日志展示用）
