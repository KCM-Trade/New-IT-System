import glob
import os
import subprocess
import sys
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

PORT = 8111
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

app = FastAPI()


@app.get("/")
def index():
    return FileResponse(os.path.join(DIRECTORY, "index.html"))


@app.get("/api/files")
def list_files():
    csv_files = glob.glob(
        os.path.join(DIRECTORY, "account_pnl_with_client_metrics_*.csv")
    )
    csv_files.sort(key=os.path.getctime, reverse=True)
    file_info = []
    for f in csv_files:
        name = os.path.basename(f)
        ctime = os.path.getctime(f)
        file_info.append(
            {
                "name": name,
                "date": datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return JSONResponse(file_info)


@app.get("/api/file/{filename}")
def get_file(filename: str):
    if not filename.startswith("account_pnl_with_client_metrics_") or not filename.endswith(".csv"):
        raise HTTPException(status_code=404, detail="File not found")
    file_path = os.path.join(DIRECTORY, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, media_type="text/csv")


@app.post("/api/run")
def run_query():
    result = run_fetch_script()
    return JSONResponse(result)


def run_fetch_script():
    script_path = os.path.join(DIRECTORY, "fetch_mysql_pnl.py")
    try:
        # Use the same Python interpreter as the server (venv-safe).
        completed = subprocess.run(
            [sys.executable, script_path],
            cwd=DIRECTORY,
            capture_output=True,
            text=True,
            timeout=600,
        )
        latest_csv = get_latest_csv()
        return {
            "success": completed.returncode == 0,
            "latest_file": latest_csv["name"] if latest_csv else None,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "latest_file": None,
            "stdout": "",
            "stderr": "Query timeout: fetch_mysql_pnl.py took too long.",
        }


def get_latest_csv():
    csv_files = glob.glob(
        os.path.join(DIRECTORY, "account_pnl_with_client_metrics_*.csv")
    )
    if not csv_files:
        return None
    csv_files.sort(key=os.path.getctime, reverse=True)
    latest = csv_files[0]
    return {
        "name": os.path.basename(latest),
        "date": datetime.fromtimestamp(os.path.getctime(latest)).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    }


if __name__ == "__main__":
    # Allow running via: python app.py
    uvicorn.run("app:app", host="0.0.0.0", port=PORT, reload=False)
