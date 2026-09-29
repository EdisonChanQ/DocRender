"""表结构注册中心。

自动发现本包下所有定义 `TABLE = TableDef(...)` 的模块，聚合为 TABLES（按逻辑名 key 索引）。
新增一张表 = 在本目录加一个 .py 文件，无需改其他任何地方。
"""

from __future__ import annotations

import importlib
import pkgutil

from app.db.tables.base import TableDef

_TABLES: dict[str, TableDef] = {}

for _mod_info in pkgutil.iter_modules(__path__):
    if _mod_info.name in ("base",):
        continue
    _mod = importlib.import_module(f"{__name__}.{_mod_info.name}")
    _table = getattr(_mod, "TABLE", None)
    if isinstance(_table, TableDef):
        if _table.key in _TABLES:
            raise RuntimeError(f"表逻辑名重复：{_table.key}")
        _TABLES[_table.key] = _table

TABLES: dict[str, TableDef] = dict(sorted(_TABLES.items()))

__all__ = ["TABLES", "TableDef"]
