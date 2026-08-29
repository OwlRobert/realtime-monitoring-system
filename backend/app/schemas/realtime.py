from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models.data_record import DataSource


class RealtimeReading(BaseModel):
    """One generated data point, as broadcast over the WebSocket.

    Deliberately not a DataRecord: nothing is persisted in this step, so the
    payload carries no id, owner or database timestamps.
    """

    title: str
    value: float
    category: str
    timestamp: datetime
    source: Literal[DataSource.REALTIME] = DataSource.REALTIME
    is_anomaly: bool
