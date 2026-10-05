# scripts/security/legacy_free_sql_demo.py
# 反面教材演示："让大模型自由生成 SQL"模式为什么危险 → 白名单 + 参数绑定为什么安全。
#
# 用内存 SQLite 完整复现两种实现，不接触任何真实数据库：
#   旧模式（参考代码 SmartVoyage 的 query_tickets(sql) 写法）：
#       sql = f"SELECT ... WHERE name LIKE '%{keyword}%'"   ← 字符串拼接 = 注入面
#   新模式（本项目器材 MCP 的写法）：
#       SELECT ... WHERE name LIKE ?  +  参数绑定（白名单模板固定）
#
# 运行：python scripts/security/legacy_free_sql_demo.py

import sqlite3
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


def seed(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE equipment (asset_id TEXT, name TEXT, department TEXT)")
    conn.executemany("INSERT INTO equipment VALUES (?, ?, ?)", [
        ("P001", "爱普生投影仪", "行政部"),
        ("N001", "联想笔记本", "研发部"),
        ("V001", "VR 头显", "设计部"),
    ])
    conn.commit()


def legacy_query(conn: sqlite3.Connection, keyword: str) -> list[tuple]:
    """旧模式：字符串拼接（等价于"模型生成 SQL 字符串直通"）。"""
    sql = f"SELECT asset_id, name, department FROM equipment WHERE name LIKE '%{keyword}%'"
    return conn.execute(sql).fetchall()


def hardened_query(conn: sqlite3.Connection, keyword: str) -> list[tuple]:
    """新模式：固定模板 + 参数绑定（本项目实现）。"""
    sql = "SELECT asset_id, name, department FROM equipment WHERE name LIKE ?"      # 模板固定
    return conn.execute(sql, (f"%{keyword}%",)).fetchall()                          # 参数绑定


def main() -> int:
    conn = sqlite3.connect(":memory:")
    seed(conn)

    attack = "%' OR '1'='1' --"       # 常见注入 payload：让 WHERE 恒真，拖全表
    print("=" * 72)
    print("场景：员工输入关键词：", attack)
    print("=" * 72)

    legacy_rows = legacy_query(conn, attack)
    print(f"\n[旧模式] 拼接 SQL 实际执行：")
    print(f"  SELECT ... WHERE name LIKE '%{attack}%'")
    print(f"  返回 {len(legacy_rows)} 行：{legacy_rows}")
    legacy_bad = len(legacy_rows) == 3
    print("  → " + ("[危险] 注入成功：全表 3 行被拖出！" if legacy_bad else "（payload 未生效？）"))

    hardened_rows = hardened_query(conn, attack)
    print(f"\n[新模式] 参数绑定实际执行：")
    print(f"  SELECT ... WHERE name LIKE ?   [参数 = %{attack}%]")
    print(f"  返回 {len(hardened_rows)} 行：{hardened_rows}")
    hardened_ok = len(hardened_rows) == 0
    print("  → " + ("[安全] 注入被消解：payload 被当成普通字符串，0 行命中" if hardened_ok else "（异常）"))

    # 正常查询两种模式都工作
    print(f"\n[正常输入] 关键词=投影仪：旧模式 {len(legacy_query(conn, '投影仪'))} 行 / "
          f"新模式 {len(hardened_query(conn, '投影仪'))} 行（功能等价）")

    ok = legacy_bad and hardened_ok
    print("\n" + ("[PASS] 演示成立：同样输入，旧模式被注入、新模式安全" if ok else "[FAIL] 演示异常"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
