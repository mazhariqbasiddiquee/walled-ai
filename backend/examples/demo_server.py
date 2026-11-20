"""A tiny MCP server used to try out MCP Guard (and by the end-to-end test)."""

from mcp.server.mcpserver import MCPServer

server = MCPServer("demo")


@server.tool()
def echo(text: str) -> str:
    """Echo the text back."""
    return text


@server.tool()
def write_note(path: str, content: str) -> str:
    """Pretend to write a file (does not touch disk)."""
    return f"wrote {len(content)} bytes to {path}"


if __name__ == "__main__":
    server.run()
