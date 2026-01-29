"""
Trace ID Middleware for FastAPI

Features:
- Generates unique UUID for each incoming request
- Stores in context variable for logging
- Returns X-Trace-ID header in response
- Logs request start/end with timing

Fresh grad note:
- Middleware wraps every request/response cycle
- X-Trace-ID header helps frontend correlate errors with backend logs
- When user reports an issue, ask for Trace ID to find all related logs
"""

import uuid
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging_config import trace_id_var, get_logger

logger = get_logger(__name__)


class TraceIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware that assigns a unique trace ID to each request.
    
    The trace ID is:
    1. Generated at request start
    2. Stored in context variable (accessible by all loggers)
    3. Included in response header (X-Trace-ID)
    4. Used to correlate all logs for a single request
    """
    
    async def dispatch(self, request: Request, call_next) -> Response:
        # Generate unique trace ID (short UUID for readability)
        # Format: req-xxxxxxxx (8 hex chars = 4 billion unique IDs)
        trace_id = f"req-{uuid.uuid4().hex[:8]}"
        
        # Store in context variable (thread-safe, accessible by all loggers)
        trace_id_var.set(trace_id)
        
        # Record start time for duration calculation
        start_time = time.perf_counter()
        
        # Get client IP (handle proxy headers)
        client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if not client_ip:
            client_ip = request.client.host if request.client else "unknown"
        
        # Log incoming request
        logger.info(
            f"Request started: {request.method} {request.url.path} "
            f"client={client_ip}"
        )
        
        try:
            # Process the request through the application
            response = await call_next(request)
            
            # Calculate request duration
            duration_ms = (time.perf_counter() - start_time) * 1000
            
            # Log response with timing
            logger.info(
                f"Request completed: status={response.status_code} "
                f"duration={duration_ms:.2f}ms"
            )
            
            # Add trace ID to response headers for frontend correlation
            response.headers["X-Trace-ID"] = trace_id
            return response
            
        except Exception as e:
            # Calculate duration even on error
            duration_ms = (time.perf_counter() - start_time) * 1000
            
            # Log unhandled exceptions with full stack trace
            logger.exception(
                f"Request failed: {request.method} {request.url.path} "
                f"duration={duration_ms:.2f}ms error={str(e)}"
            )
            raise
        finally:
            # Clear context variable to prevent leakage
            trace_id_var.set(None)
