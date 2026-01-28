# systemd 部署与维护指南（FastAPI/Uvicorn）

适用场景：单台 Linux 服务器、希望服务常态化运行且开机自启。

## 1. 前置准备

- 项目目录：`/opt/myproject/New-IT-System/temp_folder`
- 虚拟环境：`/opt/myproject/New-IT-System/temp_folder/.venv`
- 启动命令（当前使用）：  
  `/opt/myproject/New-IT-System/temp_folder/.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8111`

## 2. 创建 systemd 服务

1) 新建服务文件：

```
sudo nano /etc/systemd/system/pnl-web.service
```

2) 写入内容：

```
[Unit]
Description=PNL Web Service
After=network.target

[Service]
Type=simple
User=kcm-trade
WorkingDirectory=/opt/myproject/New-IT-System/temp_folder
# Use venv Python to avoid global env
ExecStart=/opt/myproject/New-IT-System/temp_folder/.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8111
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

> `User=` 请替换为实际运行用户。

## 3. 启动与开机自启

```
sudo systemctl daemon-reload
sudo systemctl enable pnl-web
sudo systemctl start pnl-web
```

检查状态：

```
sudo systemctl status pnl-web
```

## 4. 日志查看

实时查看日志：

```
journalctl -u pnl-web -f
```

最近 200 行：

```
journalctl -u pnl-web -n 200
```

## 5. 常用维护命令

重启服务：

```
sudo systemctl restart pnl-web
```

停止服务：

```
sudo systemctl stop pnl-web
```

关闭开机自启：

```
sudo systemctl disable pnl-web
```

## 6. 常见问题排查

- **端口不可访问**：确认 `--host 0.0.0.0`；检查防火墙是否放行 `8111`  
- **服务启动失败**：查看 `journalctl -u pnl-web -f`  
- **找不到 Python 包**：确认 `ExecStart` 指向 `.venv` 的 `uvicorn`  

## 7. 失败告警（可选）

当服务崩溃时触发通知（示例：Webhook）。

1) 新建失败通知服务：

```
sudo nano /etc/systemd/system/pnl-web-fail.service
```

```
[Unit]
Description=PNL Web Fail Notify

[Service]
Type=oneshot
ExecStart=/usr/bin/curl -X POST https://你的webhook地址 -d "PNL Web crashed"
```

2) 在 `pnl-web.service` 里加入：

```
OnFailure=pnl-web-fail.service
```

3) 重新加载并重启：

```
sudo systemctl daemon-reload
sudo systemctl restart pnl-web
```

---

如需更完整的业务日志（请求参数、错误堆栈、耗时等），可在 `app.py` 中加入 `logging` 记录。
