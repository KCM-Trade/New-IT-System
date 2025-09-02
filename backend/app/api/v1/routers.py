from fastapi import APIRouter

from .routes.health import router as health_router
from .routes.aggregations import router as aggregations_router
from .routes.trade_summary import router as trade_summary_router
from .routes.open_positions import router as open_positions_router
from .routes.downloads import router as downloads_router


api_v1_router = APIRouter()
api_v1_router.include_router(health_router, tags=["health"])
api_v1_router.include_router(aggregations_router, tags=["aggregations"]) 
api_v1_router.include_router(trade_summary_router, tags=["trade-summary"]) 
api_v1_router.include_router(open_positions_router, tags=["open-positions"]) 
api_v1_router.include_router(downloads_router, tags=["downloads"]) 


