from fastapi import APIRouter, HTTPException, status
import logging
from app.services.clickhouse_service import clickhouse_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ib-report")

@router.get("/groups", status_code=status.HTTP_200_OK)
async def get_ib_groups():
    """
    获取 IB 报表所有的组别列表及其用户数统计。
    该接口具备 7 天的后端缓存，以减轻 ClickHouse 查询压力。
    """
    try:
        data = clickhouse_service.get_ib_groups()
        return data
    except Exception as e:
        logger.error(f"Failed to fetch IB groups: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取组别列表失败: {str(e)}"
        )
