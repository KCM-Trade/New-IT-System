# ETL 脚本 `sync_pnl_summary.py` 使用指南

本文档提供 `sync_pnl_summary.py` 脚本的详细使用说明，该脚本用于将 MT5 的交易数据从源 MySQL 数据库同步到 PostgreSQL 报表数据库。

## 1. 功能概述

该脚本是一个 ETL (Extract, Transform, Load) 工具，其主要功能是：

-   **Extract**: 从 MT5 的 `mt5_deals`, `mt5_positions`, 和 `mt5_users` 表中提取交易数据。
-   **Transform**: 在内存中对数据进行聚合计算，包括总交易数、买/卖单数、总手数、已平仓盈亏和浮动盈亏。
-   **Load**: 将计算好的聚合数据加载到 PostgreSQL 的 `pnl_summary` 表中，以便进行快速的报表查询。

脚本支持**全量 (full)** 和**增量 (incremental)** 两种同步模式。

## 2. 环境准备

在运行脚本之前，请确保完成以下准备工作：

### a. 安装依赖

脚本依赖于 `mysql-connector-python`, `psycopg2-binary`, 和 `python-dotenv`。通过 `requirements.txt` 文件进行安装：

```bash
pip install -r requirements.txt
```

### b. 配置环境变量

1.  在 `backend/data/` 目录下，根据模板创建一个名为 `.env` 的文件。
2.  **严禁将 `.env` 文件提交到版本控制系统 (Git) 中。**
3.  打开 `.env` 文件，填写你的 Azure MySQL 和 Azure PostgreSQL 的真实连接凭据：

    ```ini
    # .env file content

    # MySQL Connection Details
    MYSQL_HOST=your_azure_mysql_host.mysql.database.azure.com
    MYSQL_USER=your_mysql_user
    MYSQL_PASSWORD=your_mysql_password
    MYSQL_DATABASE=mt5_live
    MYSQL_SSL_CA=/path/to/your/DigiCertGlobalRootG2.crt.pem

    # PostgreSQL Connection Details
    POSTGRES_HOST=your_azure_postgres_host.postgres.database.azure.com
    POSTGRES_USER=your_postgres_user
    POSTGRES_PASSWORD=your_postgres_password
    POSTGRES_DBNAME=reporting_db
    ```

### c. 数据库表结构

确保你的 PostgreSQL (`reporting_db`) 中已经创建了以下两个表：

-   `pnl_summary`: 用于存储最终的报表数据。
-   `etl_watermarks`: 用于记录增量同步的进度（水位线）。

如果尚未创建，请参考 `mt5_database_schema_explained.md` 或之前的对话记录中的 `CREATE TABLE` 语句。

## 3. 脚本配置

### Volume 换算系数

脚本内部有一个名为 `VOLUME_DIVISORS` 的 Python 字典，用于管理不同交易品种手数的换算。这对于区分**标准账户**和**美分账户 (Cent Account)** 至关重要。

```python
# sync_pnl_summary.py
VOLUME_DIVISORS = {
    'XAUUSD.kcmc': 10000.0, # 美分账户，除以 10000
    'EURUSD': 100.0,      # 标准账户，除以 100
    '__default__': 100.0   # 未在此处定义的品种，默认使用 100
}
```

在处理新的交易品种前，请务必在此处添加或确认其正确的换算系数。

## 4. 如何运行脚本

该脚本通过命令行接收参数运行。

### a. 参数说明

-   `symbol` (必需): 你想要处理的交易品种名称，例如 `XAUUSD.kcmc`。
-   `--mode` (可选): 运行模式。接受 `full` 或 `incremental` 两个值。**默认为 `incremental`**。

### b. 运行模式

#### 首次全量加载 (`--mode full`)

当你第一次为一个新的 `symbol` 同步数据，或者当你想彻底清空旧数据并重新计算时，使用此模式。

**示例命令:**
```bash
python sync_pnl_summary.py XAUUSD.kcmc --mode full
```

**执行流程:**
1.  删除 PostgreSQL `pnl_summary` 表中所有关于 `XAUUSD.kcmc` 的记录。
2.  从 MySQL `mt5_deals` 表的**最开始**读取所有相关的平仓交易。
3.  计算汇总数据并插入到 `pnl_summary` 表。
4.  在 `etl_watermarks` 表中为 `XAUUSD.kcmc` 创建或更新水位线，记录已处理到的最大 `Deal` ID。

#### 增量更新 (`--mode incremental` 或 默认)

这是最常用的模式，用于定时任务和手动刷新，只同步最新的交易数据。

**示例命令:**
```bash
# --mode incremental 是默认值，可以省略
python sync_pnl_summary.py XAUUSD.kcmc
```

**执行流程:**
1.  从 `etl_watermarks` 表中读取 `XAUUSD.kcmc` 上次同步到的 `last_deal_id`。
2.  从 MySQL `mt5_deals` 表中**只查询** `Deal` ID 大于 `last_deal_id` 的新平仓交易。
3.  将计算出的**增量**数据与 `pnl_summary` 表中的现有数据进行合并更新 (`ON CONFLICT ... DO UPDATE`)。
4.  更新 `etl_watermarks` 表中的水位线到最新的 `Deal` ID。

## 5. 部署建议

### a. 定时任务 (Cron Job)

为了实现每10分钟自动更新，可以在你的服务器上设置一个 Cron Job。

打开 crontab 编辑器:
```bash
crontab -e
```

添加以下行（请根据你的项目实际路径修改）:
```cron
*/10 * * * * /usr/bin/python3 /opt/myproject/New-IT-System/backend/data/sync_pnl_summary.py XAUUSD.kcmc >> /var/log/pnl_sync.log 2>&1
```
这会每10分钟执行一次增量同步，并将日志输出到 `/var/log/pnl_sync.log` 文件中。

### b. 手动刷新 API

在你的后端 API 中，创建一个端点 (e.g., `POST /api/pnl-summary/refresh`)。当这个 API 被调用时，它应该在后台**异步**执行 `python sync_pnl_summary.py <symbol>` 命令，并立即返回响应，而不是等待脚本执行完毕。
