"""建表迁移入口。

用法：
    python migrate.py              # 执行全部已注册表的建表 DDL
    python migrate.py --list       # 仅列出已注册的表，不连接数据库
    python migrate.py category     # 只执行指定逻辑名的表（可传多个）
"""

import sys

from app.db.migrator import apply_tables, render_table
from app.db.tables import TABLES


def list_tables() -> int:
    print("已注册表结构（app/db/tables/）：")
    for table in TABLES.values():
        comment = f"  # {table.comment}" if table.comment else ""
        print(f"  [{table.key}] -> {table.name}{comment}")
    if not TABLES:
        print("  （空）")
    return 0


def main(argv: list[str]) -> int:
    if "--list" in argv:
        return list_tables()

    keys = [a for a in argv if not a.startswith("-")] or None
    results = apply_tables(keys)

    failed = 0
    for r in results:
        status = "OK" if r.ok else "FAIL"
        line = f"[{status}] {r.key} -> 表 {r.table}"
        if not r.ok:
            line += f" ：{r.message}"
            failed += 1
        print(line)

    print(f"\n完成：成功 {len(results) - failed} 个，失败 {failed} 个")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
