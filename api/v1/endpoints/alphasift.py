# -*- coding: utf-8 -*-
"""AlphaSift stock screening API routes."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.deps import get_config_dep
from api.v1.errors import api_error
from src.config import Config
from src.services.alphasift_service import AlphaSiftService
from src.services.task_queue import TaskStatus as QueueTaskStatus
from src.services.task_queue import get_task_queue

router = APIRouter()


class AlphaSiftScreenRequest(BaseModel):
    market: str = Field("cn", min_length=1, max_length=16)
    strategy: str = Field("dual_low", min_length=1, max_length=64)
    max_results: int = Field(20, ge=1, le=100)


class AlphaSiftStrategyResponse(BaseModel):
    id: str
    name: str = ""
    title: str = ""
    description: str = ""
    category: str = ""
    tag: str = ""
    tags: List[str] = Field(default_factory=list)
    market_scope: List[str] = Field(default_factory=list)
    market: str = ""


class AlphaSiftScreenAccepted(BaseModel):
    task_id: str
    trace_id: str
    status: str = "pending"
    message: str
    strategy: str
    market: str
    max_results: int


class AlphaSiftScreenTaskStatus(BaseModel):
    task_id: str
    trace_id: Optional[str] = None
    status: str
    progress: int = 0
    message: Optional[str] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


class AlphaSiftAiScoreRequest(BaseModel):
    screen_task_id: str = Field(..., min_length=8, max_length=128)


class AlphaSiftAiScoreAccepted(BaseModel):
    task_id: str
    trace_id: str
    status: str = "pending"
    message: str
    screen_task_id: str
    total_count: int


def _service(config: Config) -> AlphaSiftService:
    return AlphaSiftService(config=config)


def _screening_task_not_found(task_id: str) -> HTTPException:
    return api_error(
        404,
        "alphasift_screen_task_not_found",
        f"选股任务 {task_id} 不存在或已过期",
    )


@router.get("/status")
def alphasift_status(config: Config = Depends(get_config_dep)) -> Dict[str, Any]:
    return _service(config).status()


@router.get("/strategies")
def alphasift_strategies(
    request: Request,
    config: Config = Depends(get_config_dep),
) -> Dict[str, Any]:
    return _service(config).strategies()


@router.get("/hotspots")
def alphasift_hotspots(
    provider: str = Query("", max_length=32),
    top: int = Query(12, ge=1, le=50),
    refresh: bool = Query(False),
    include_details: bool = Query(False),
    config: Config = Depends(get_config_dep),
) -> Dict[str, Any]:
    refresh_value = refresh if isinstance(refresh, bool) else bool(getattr(refresh, "default", False))
    include_details_value = (
        include_details
        if isinstance(include_details, bool)
        else bool(getattr(include_details, "default", False))
    )
    return _service(config).hotspots(
        provider=provider,
        top=top,
        refresh=refresh_value,
        include_details=include_details_value,
    )


@router.get("/hotspots/{topic:path}")
def alphasift_hotspot_detail(
    topic: str,
    provider: str = Query("", max_length=32),
    refresh: bool = Query(False),
    config: Config = Depends(get_config_dep),
) -> Dict[str, Any]:
    refresh_value = refresh if isinstance(refresh, bool) else bool(getattr(refresh, "default", False))
    return _service(config).hotspot_detail(topic=topic, provider=provider, refresh=refresh_value)


@router.post("/install")
def alphasift_install(
    request: Request,
    config: Config = Depends(get_config_dep),
) -> Dict[str, Any]:
    return _service(config).install(request=request)


@router.post("/screen/tasks", status_code=202, response_model=AlphaSiftScreenAccepted)
def alphasift_start_screen_task(
    request: AlphaSiftScreenRequest,
    http_request: Request,
    config: Config = Depends(get_config_dep),
) -> AlphaSiftScreenAccepted:
    task_id = uuid.uuid4().hex
    task_queue = get_task_queue()

    def run_screen() -> Dict[str, Any]:
        task_queue.update_task_progress(
            task_id,
            20,
            "正在执行 AlphaSift 选股，外部数据源较慢时会持续后台运行",
        )
        result = _service(config).screen(
            strategy=request.strategy,
            market=request.market,
            max_results=request.max_results,
        )
        task_queue.update_task_progress(
            task_id,
            90,
            f"选股已完成，正在整理 {result.get('candidate_count', 0)} 条候选",
        )
        return result

    task = task_queue.submit_background_task(
        run_screen,
        stock_code="alphasift_screen",
        stock_name=f"{request.strategy} / {request.market}",
        report_type="alphasift_screen",
        message="AlphaSift 选股任务已提交",
        task_id=task_id,
        trace_id=task_id,
    )
    return AlphaSiftScreenAccepted(
        task_id=task.task_id,
        trace_id=task.trace_id or task.task_id,
        status=task.status.value if isinstance(task.status, QueueTaskStatus) else str(task.status),
        message=task.message or "AlphaSift 选股任务已提交",
        strategy=request.strategy,
        market=request.market,
        max_results=request.max_results,
    )


@router.get("/screen/tasks/{task_id}", response_model=AlphaSiftScreenTaskStatus)
def alphasift_screen_task_status(task_id: str) -> AlphaSiftScreenTaskStatus:
    task = get_task_queue().get_task(task_id)
    if task is None or task.report_type != "alphasift_screen":
        raise _screening_task_not_found(task_id)

    result = task.result if task.status == QueueTaskStatus.COMPLETED and isinstance(task.result, dict) else None
    return AlphaSiftScreenTaskStatus(
        task_id=task.task_id,
        trace_id=task.trace_id or task.task_id,
        status=task.status.value if isinstance(task.status, QueueTaskStatus) else str(task.status),
        progress=task.progress,
        message=task.message,
        error=task.error,
        result=result,
    )


@router.post("/screen/llm/tasks", status_code=202, response_model=AlphaSiftAiScoreAccepted)
def alphasift_start_ai_score_task(
    request: AlphaSiftAiScoreRequest,
    config: Config = Depends(get_config_dep),
) -> AlphaSiftAiScoreAccepted:
    task_queue = get_task_queue()
    screen_task = task_queue.get_task(request.screen_task_id)
    if screen_task is None or screen_task.report_type != "alphasift_screen":
        raise _screening_task_not_found(request.screen_task_id)
    if screen_task.status != QueueTaskStatus.COMPLETED or not isinstance(screen_task.result, dict):
        raise api_error(409, "alphasift_screen_not_completed", "量化选股任务尚未完成，暂时不能启动 AI 评分")

    screen_result = screen_task.result
    candidates = [item for item in screen_result.get("candidates") or [] if isinstance(item, dict)]
    if not candidates:
        raise api_error(422, "alphasift_ai_score_empty", "没有可供 AI 评分的候选股票")
    strategy = str(screen_result.get("strategy") or "")
    market = str(screen_result.get("market") or "")
    run_id = str(screen_result.get("run_id") or request.screen_task_id)
    task_id = uuid.uuid4().hex

    def publish_partial_result(payload: Dict[str, Any], progress: int, message: str) -> None:
        task_queue.update_task_partial_result(
            task_id,
            payload,
            progress=progress,
            message=message,
        )

    def run_ai_score() -> Dict[str, Any]:
        return _service(config).score_candidates_with_ai(
            candidates=candidates,
            strategy=strategy,
            market=market,
            run_id=run_id,
            progress_callback=publish_partial_result,
        )

    task = task_queue.submit_background_task(
        run_ai_score,
        stock_code=f"alphasift_ai_score:{request.screen_task_id}",
        stock_name=f"{strategy} / {market}",
        report_type="alphasift_ai_score",
        message=f"AI 将逐只评分 {len(candidates)} 只候选股票",
        task_id=task_id,
        trace_id=screen_task.trace_id or request.screen_task_id,
    )
    return AlphaSiftAiScoreAccepted(
        task_id=task.task_id,
        trace_id=task.trace_id or task.task_id,
        status=task.status.value if isinstance(task.status, QueueTaskStatus) else str(task.status),
        message=task.message or "AI 评分任务已提交",
        screen_task_id=request.screen_task_id,
        total_count=len(candidates),
    )


@router.get("/screen/llm/tasks/{task_id}", response_model=AlphaSiftScreenTaskStatus)
def alphasift_ai_score_task_status(task_id: str) -> AlphaSiftScreenTaskStatus:
    task = get_task_queue().get_task(task_id)
    if task is None or task.report_type != "alphasift_ai_score":
        raise api_error(404, "alphasift_ai_score_task_not_found", f"AI 评分任务 {task_id} 不存在或已过期")
    result = task.result if isinstance(task.result, dict) else None
    return AlphaSiftScreenTaskStatus(
        task_id=task.task_id,
        trace_id=task.trace_id or task.task_id,
        status=task.status.value if isinstance(task.status, QueueTaskStatus) else str(task.status),
        progress=task.progress,
        message=task.message,
        error=task.error,
        result=result,
    )


@router.post("/screen")
def alphasift_screen(
    request: AlphaSiftScreenRequest,
    http_request: Request,
    config: Config = Depends(get_config_dep),
) -> Dict[str, Any]:
    return _service(config).screen(
        strategy=request.strategy,
        market=request.market,
        max_results=request.max_results,
    )
