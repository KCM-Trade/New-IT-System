from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date

class IBReportRequest(BaseModel):
    start_date: date
    end_date: date
    groups: Optional[List[str]] = Field(default_factory=list)

class IBValue(BaseModel):
    range_val: float
    month_val: float

class IBReportRow(BaseModel):
    group: str
    user_name: str
    time_range: str
    deposit: IBValue
    withdrawal: IBValue
    ib_withdrawal: IBValue
    net_deposit: IBValue
    volume: IBValue
    adjustments: IBValue
    commission: IBValue
    ib_commission: IBValue
    swap: IBValue
    profit: IBValue
    new_clients: IBValue
    new_agents: IBValue
