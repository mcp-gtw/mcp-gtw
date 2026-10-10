from mcp.types import ListToolsResult

from mcpgtw.oauth.secured_tool import SecuredTool


class SecuredToolsResult(ListToolsResult):
    tools: list[SecuredTool]
