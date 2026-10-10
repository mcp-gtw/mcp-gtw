from mcp.types import Tool
from pydantic import Field


class SecuredTool(Tool):
    security_schemes: list[dict] = Field(alias="securitySchemes")
