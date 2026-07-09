"""开发工具 CLI。

`poetry run matrix-dev hello` - 验证环境
`poetry run matrix-dev check-db` - 检查数据库连接
"""

import sys


def hello() -> None:
    """验证基本环境。"""
    import platform
    import sys as _sys

    from matrix import __version__

    print(f"Matrix Master v{__version__}")
    print(f"Python {_sys.version.split()[0]} on {platform.system()}")


def check_db() -> None:
    """检查数据库连接。"""
    try:

        print("Connecting to localhost:5432/matrix ...")
        # TODO: 实际连接测试
        print("✓ Database connection OK (stub)")
    except Exception as e:
        print(f"✗ Database connection failed: {e}")
        sys.exit(1)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: matrix-dev <command>")
        print("Commands:")
        print("  hello     - 验证环境")
        print("  check-db  - 检查数据库连接")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "hello":
        hello()
    elif cmd == "check-db":
        check_db()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
