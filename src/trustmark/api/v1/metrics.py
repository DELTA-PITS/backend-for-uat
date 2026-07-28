from fastapi import APIRouter
from pydantic import BaseModel, Field


router = APIRouter()


class HealthResponse(BaseModel):
    status: str = Field(description="Current service status.", examples=["ok"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description="Returns the current service health status.",
    response_description="Health check result.",
)
def health() -> HealthResponse:
    return HealthResponse(status="ok")
