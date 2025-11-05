#!/usr/bin/env python3
"""
ClientID 盈亏监控 API 测试脚本

测试 5 个核心接口:
1. GET /api/client-pnl/summary - 分页查询汇总数据
2. GET /api/client-pnl/accounts/{client_id} - 查询账户明细
3. POST /api/client-pnl/initialize - 初始化数据
4. POST /api/client-pnl/compare - 对比数据一致性
5. GET /api/client-pnl/status - 获取刷新状态
"""

import requests
import json
from datetime import datetime


BASE_URL = "http://localhost:8000"


def test_get_summary():
    """测试分页查询汇总数据"""
    print("\n=== 测试 1: 分页查询汇总数据 ===")
    
    # 基础查询
    response = requests.get(f"{BASE_URL}/api/client-pnl/summary", params={
        "page": 1,
        "page_size": 10,
    })
    print(f"状态码: {response.status_code}")
    data = response.json()
    print(f"返回记录数: {len(data.get('data', []))}")
    print(f"总记录数: {data.get('total')}")
    print(f"总页数: {data.get('total_pages')}")
    print(f"最后更新时间: {data.get('last_updated')}")
    
    # 排序查询
    print("\n--- 按总平仓盈亏降序排序 ---")
    response = requests.get(f"{BASE_URL}/api/client-pnl/summary", params={
        "page": 1,
        "page_size": 5,
        "sort_by": "total_closed_profit_usd",
        "sort_order": "desc",
    })
    data = response.json()
    for item in data.get('data', [])[:3]:
        print(f"ClientID: {item['client_id']}, 总平仓盈亏: {item['total_closed_profit_usd']}")
    
    # 搜索查询（假设有 client_id=12345）
    print("\n--- 搜索 ClientID ---")
    response = requests.get(f"{BASE_URL}/api/client-pnl/summary", params={
        "page": 1,
        "page_size": 10,
        "search": "12345",
    })
    data = response.json()
    print(f"搜索结果数: {data.get('total')}")


def test_get_accounts():
    """测试查询账户明细"""
    print("\n=== 测试 2: 查询账户明细 ===")
    
    # 先获取一个 client_id
    response = requests.get(f"{BASE_URL}/api/client-pnl/summary", params={
        "page": 1,
        "page_size": 1,
    })
    data = response.json()
    
    if data.get('data'):
        client_id = data['data'][0]['client_id']
        print(f"查询 ClientID: {client_id} 的账户明细")
        
        response = requests.get(f"{BASE_URL}/api/client-pnl/accounts/{client_id}")
        accounts_data = response.json()
        
        print(f"账户数: {len(accounts_data.get('accounts', []))}")
        for acc in accounts_data.get('accounts', [])[:3]:
            print(f"  - Login: {acc['login']}, Server: {acc['server']}, Balance: {acc['balance_usd']}")
    else:
        print("没有可用的 client_id 进行测试")


def test_get_status():
    """测试获取刷新状态"""
    print("\n=== 测试 3: 获取刷新状态 ===")
    
    response = requests.get(f"{BASE_URL}/api/client-pnl/status")
    print(f"状态码: {response.status_code}")
    data = response.json()
    print(f"客户总数: {data.get('total_clients')}")
    print(f"账户总数: {data.get('total_accounts')}")
    print(f"最后更新时间: {data.get('last_updated')}")


def test_compare():
    """测试对比数据一致性"""
    print("\n=== 测试 4: 对比数据一致性 ===")
    
    response = requests.post(f"{BASE_URL}/api/client-pnl/compare", json={
        "auto_fix": False,
    })
    print(f"状态码: {response.status_code}")
    data = response.json()
    print(f"缺失客户数: {data.get('total_missing')}")
    print(f"孤儿客户数: {data.get('total_orphan')}")
    print(f"差异详情:")
    for diff in data.get('differences', [])[:5]:
        print(f"  - {diff['status']}: ClientID={diff.get('client_id')}, {diff['description']}")


def test_initialize():
    """测试初始化数据（谨慎使用）"""
    print("\n=== 测试 5: 初始化数据 ===")
    print("⚠️ 注意: 初始化会触发大量数据刷新，仅在必要时使用")
    print("跳过实际初始化测试，避免影响现有数据")
    
    # 如果需要测试，取消注释以下代码
    # response = requests.post(f"{BASE_URL}/api/client-pnl/initialize", json={
    #     "force": False,
    # })
    # data = response.json()
    # print(f"初始化客户数: {data.get('total_clients')}")
    # print(f"初始化账户数: {data.get('total_accounts')}")
    # print(f"耗时（秒）: {data.get('duration_seconds')}")


if __name__ == "__main__":
    print(f"开始测试 ClientID 盈亏监控 API")
    print(f"基础 URL: {BASE_URL}")
    print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        test_get_summary()
        test_get_accounts()
        test_get_status()
        test_compare()
        test_initialize()
        
        print("\n✅ 所有测试完成")
    except requests.exceptions.ConnectionError:
        print("\n❌ 错误: 无法连接到后端服务")
        print("请确保后端服务已启动: uvicorn app.main:app --reload")
    except Exception as e:
        print(f"\n❌ 测试失败: {str(e)}")

