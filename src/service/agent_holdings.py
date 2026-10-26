"""Narrow authenticated Agent routes, separate from the published OM API contract."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from src import config
from src.app.agent_holding_name_service import (
    AgentHoldingNameService, AgentNameError, authenticate, RECORD_PATTERN,
)

router = APIRouter(prefix='/api/v1/agent/holdings', tags=['agent-holdings'])
RecordId = Annotated[str, Path(pattern=RECORD_PATTERN, max_length=128)]


class NamePreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


class NameConfirmRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preview_id: str = Field(pattern=r'^[0-9a-f]{32}$', min_length=32, max_length=32)


def authorize(request: Request, record_id: RecordId):
    try:
        policy = config.get('agent_operations')
    except Exception:
        raise AgentNameError(503, 'AGENT_AUTH_UNAVAILABLE') from None
    grant = authenticate(policy, request.headers.get('authorization', ''), record_id)
    if request.query_params:
        raise AgentNameError(422, 'AGENT_PARAMETERS_INVALID')
    return grant


def operation_service(request: Request):
    service = request.app.state.agent_name_service
    if service is None:
        # Construct only after authorization. Durable state remains lazy.
        service = AgentHoldingNameService(storage=request.app.state.portfolio_service.storage)
    return service


def call_safely(call):
    try:
        return call()
    except AgentNameError:
        raise
    except Exception:
        raise AgentNameError(503, 'AGENT_OPERATION_UNAVAILABLE') from None


@router.post('/{record_id}/name-preview')
def preview(request: Request, record_id: RecordId, payload: NamePreviewRequest,
            grant=Depends(authorize)):
    return call_safely(lambda: operation_service(request).preview(grant))


@router.post('/{record_id}/name-confirm')
def confirm(request: Request, record_id: RecordId, payload: NameConfirmRequest,
            grant=Depends(authorize)):
    return call_safely(lambda: operation_service(request).confirm(grant, payload.preview_id))


@router.get('/{record_id}/name-operations/{preview_id}')
def status(request: Request, record_id: RecordId,
           preview_id: Annotated[str, Path(pattern=r'^[0-9a-f]{32}$', max_length=32)],
           grant=Depends(authorize)):
    return call_safely(lambda: operation_service(request).status(grant, preview_id))


async def error_response(request: Request, exc: AgentNameError):
    return JSONResponse(status_code=exc.status, content={'success': False, 'error_code': exc.code},
                        headers={'Cache-Control': 'no-store'})
