#!/usr/bin/env python3
"""
比较 MySQL 和 PostgreSQL 中的 login 差异

使用方法:
    python compare_logins.py [MT5|MT4Live2]
    
    默认比较 MT5，可指定 MT4Live2 来比较 MT4Live2 的数据

# 比较 MT5（默认）
python compare_logins.py

# 比较 MT4Live2
python compare_logins.py MT4Live2

环境变量从 backend/.env 文件中加载
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import mysql.connector
import psycopg2
from typing import Set, Tuple

# 加载 .env 文件（从脚本所在目录的父目录查找）
script_dir = Path(__file__).parent
env_file = script_dir / '.env'
if env_file.exists():
    load_dotenv(env_file)
    print(f"已加载环境变量文件: {env_file}")
else:
    # 尝试从当前目录加载
    load_dotenv()
    print("使用当前目录的 .env 文件（如果存在）")

def get_mysql_config(server: str) -> dict:
    """根据服务器类型获取 MySQL 配置"""
    base_config = {
        'host': os.getenv('MYSQL_HOST', 'localhost'),
        'user': os.getenv('MYSQL_USER', 'root'),
        'password': os.getenv('MYSQL_PASSWORD', ''),
        'ssl_ca': os.getenv('MYSQL_SSL_CA'),
    }
    
    if server == 'MT4Live2':
        base_config['database'] = os.getenv('MYSQL_DATABASE_MT4LIVE2', 'mt4_live2')
    else:  # MT5
        base_config['database'] = os.getenv('MYSQL_DATABASE', 'mt5_live')
    
    return base_config


def get_pg_config(server: str) -> dict:
    """根据服务器类型获取 PostgreSQL 配置"""
    base_config = {
        'host': os.getenv('POSTGRES_HOST', 'localhost'),
        'port': int(os.getenv('POSTGRES_PORT', '5432')),
        'user': os.getenv('POSTGRES_USER', 'postgres'),
        'password': os.getenv('POSTGRES_PASSWORD', ''),
    }
    
    if server == 'MT4Live2':
        base_config['dbname'] = os.getenv('POSTGRES_DBNAME_MT5', 'MT5_ETL')  # MT4Live2 也用同一个 PG 数据库
    else:  # MT5
        base_config['dbname'] = os.getenv('POSTGRES_DBNAME_MT5', 'MT5_ETL')
    
    return base_config


def get_mysql_logins(server: str) -> Set[int]:
    """从 MySQL 获取所有 login"""
    config = get_mysql_config(server)
    # 过滤掉 None 值的配置项
    config = {k: v for k, v in config.items() if v is not None}
    
    conn = mysql.connector.connect(**config)
    cursor = conn.cursor()
    
    if server == 'MT4Live2':
        cursor.execute("SELECT Login FROM mt4_live2.mt4_users")
    else:  # MT5
        cursor.execute("SELECT login FROM mt5_live.mt5_users")
    
    logins = {row[0] for row in cursor.fetchall()}
    cursor.close()
    conn.close()
    return logins


def get_pg_logins(server: str) -> Set[int]:
    """从 PostgreSQL 获取所有 login"""
    config = get_pg_config(server)
    conn = psycopg2.connect(**config)
    cursor = conn.cursor()
    
    if server == 'MT4Live2':
        cursor.execute("SELECT DISTINCT login FROM public.pnl_user_summary_mt4live2")
    else:  # MT5
        cursor.execute("SELECT DISTINCT login FROM public.pnl_user_summary")
    
    logins = {row[0] for row in cursor.fetchall()}
    cursor.close()
    conn.close()
    return logins


def compare_logins(server: str):
    """比较指定服务器的 login"""
    mysql_config = get_mysql_config(server)
    pg_config = get_pg_config(server)
    
    # 显示连接信息（隐藏密码）
    print(f"\n{'='*60}")
    print(f"比较 {server} 服务器的 login")
    print(f"{'='*60}")
    print(f"MySQL 连接: {mysql_config['host']}:3306/{mysql_config['database']} (user: {mysql_config['user']})")
    print(f"PostgreSQL 连接: {pg_config['host']}:{pg_config['port']}/{pg_config['dbname']} (user: {pg_config['user']})\n")
    
    print("正在从 MySQL 获取 login 列表...")
    try:
        mysql_logins = get_mysql_logins(server)
        print(f"✅ MySQL 中共有 {len(mysql_logins)} 个 login")
    except Exception as e:
        print(f"❌ MySQL 连接失败: {e}")
        return
    
    print("正在从 PostgreSQL 获取 login 列表...")
    try:
        pg_logins = get_pg_logins(server)
        print(f"✅ PostgreSQL 中共有 {len(pg_logins)} 个 login")
    except Exception as e:
        print(f"❌ PostgreSQL 连接失败: {e}")
        return
    
    # 计算差异
    mysql_only = mysql_logins - pg_logins
    pg_only = pg_logins - mysql_logins
    both = mysql_logins & pg_logins
    
    print("\n" + "="*60)
    print(f"{server} 比较结果:")
    print("="*60)
    print(f"MySQL 独有: {len(mysql_only)} 个")
    print(f"PostgreSQL 独有: {len(pg_only)} 个")
    print(f"两者共有: {len(both)} 个")
    print("="*60)
    
    if mysql_only:
        print(f"\nMySQL 中有但 PostgreSQL 中没有的 login (前20个):")
        for login in sorted(list(mysql_only))[:20]:
            print(f"  {login}")
        if len(mysql_only) > 20:
            print(f"  ... 还有 {len(mysql_only) - 20} 个")
    
    if pg_only:
        print(f"\nPostgreSQL 中有但 MySQL 中没有的 login (前20个):")
        for login in sorted(list(pg_only))[:20]:
            print(f"  {login}")
        if len(pg_only) > 20:
            print(f"  ... 还有 {len(pg_only) - 20} 个")
    
    # 导出到文件（使用服务器名称作为文件名前缀）
    # 已注释：用户选择在终端查看结果，不生成文件
    # prefix = server.lower()
    # if mysql_only:
    #     filename = f'{prefix}_mysql_only_logins.txt'
    #     with open(filename, 'w') as f:
    #         for login in sorted(mysql_only):
    #             f.write(f"{login}\n")
    #     print(f"\n✅ MySQL 独有的 login 已导出到: {filename}")
    # 
    # if pg_only:
    #     filename = f'{prefix}_pg_only_logins.txt'
    #     with open(filename, 'w') as f:
    #         for login in sorted(pg_only):
    #             f.write(f"{login}\n")
    #     print(f"✅ PostgreSQL 独有的 login 已导出到: {filename}")
    # 
    # if both:
    #     filename = f'{prefix}_both_logins.txt'
    #     with open(filename, 'w') as f:
    #         for login in sorted(both):
    #             f.write(f"{login}\n")
    #     print(f"✅ 两者共有的 login 已导出到: {filename}")


def main():
    # 解析命令行参数
    server = 'MT5'  # 默认
    if len(sys.argv) > 1:
        arg = sys.argv[1].upper()
        if arg in ['MT5', 'MT4LIVE2']:
            server = 'MT5' if arg == 'MT5' else 'MT4Live2'
        else:
            print(f"❌ 无效的参数: {sys.argv[1]}")
            print("使用方法: python compare_logins.py [MT5|MT4Live2]")
            print("默认比较 MT5")
            sys.exit(1)
    
    compare_logins(server)


if __name__ == '__main__':
    main()

