"""Connection-pool helpers. Pool lifecycle is managed by app.py via lifespan."""
from typing import Annotated

from fastapi import Depends, Request
from psycopg_pool import AsyncConnectionPool


async def get_pool(request: Request) -> AsyncConnectionPool:
    return request.app.state.pool


PoolDep = Annotated[AsyncConnectionPool, Depends(get_pool)]
