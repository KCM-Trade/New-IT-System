# Client PnL 刷新日志说明

## 功能概述
- 记录 `/api/v1/etl/pnl-user-summary/refresh` 与 `/api/v1/etl/client-pnl/refresh` 的执行情况，便于排查异常。
- 日志以按天归档的 JSON 行格式写入，文件名形如 `client_pnl_refresh_YYYY-MM-DD.log`。
- 自动清理 15 天前的历史文件，防止磁盘堆积。

## 日志目录
- 代码默认写入仓库根目录下的 `backend/logs/client_pnl_refresh/`。
- 目录由 `_ensure_log_dir()` 自动创建；也可提前手动执行：
  ```bash
  mkdir -p /opt/myproject/New-IT-System/backend/logs/client_pnl_refresh
  ```

## 写入权限
- 确保运行 FastAPI 服务的系统用户对该目录拥有读写权限：
  ```bash
  chown <user>:<group> /opt/myproject/New-IT-System/backend/logs /opt/myproject/New-IT-System/backend/logs/client_pnl_refresh
  chmod 770 /opt/myproject/New-IT-System/backend/logs /opt/myproject/New-IT-System/backend/logs/client_pnl_refresh
  ```
- 若服务与部署脚本由同一用户执行，可保持默认权限；若使用容器或 systemd，请在部署脚本中同步设置。

## 记录内容
- 每条日志为一行 JSON，主要字段：
  - `timestamp`：UTC 时间戳。
  - `event_type`：`pnl_user_summary_refresh`、`client_pnl_refresh` 等事件名称。
  - `payload`：包含入参、后端返回值或异常信息。
- 可通过 `jq`/`grep` 等工具快速过滤：
  ```bash
  tail -n 200 client_pnl_refresh_2025-11-09.log | jq
  ```

## 保留策略
- `cleanup_old_logs()` 会在每次写入后执行，删除超过 15 天的日志文件。
- 如需调整，修改 `DEFAULT_KEEP_DAYS` 常量并重新部署即可。

## 常见排查步骤
- 日志写不进去：确认目录已存在、服务用户拥有写权限。
- 没有生成日志文件：检查接口是否被调用或是否抛出异常导致提前退出。
- 需要长期存档：可在运维层增加 logrotate 或同步上传到集中式日志系统。











