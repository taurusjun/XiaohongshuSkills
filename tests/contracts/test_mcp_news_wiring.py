"""契约：MCP server 的 news 访问经 services.news（不再各自直连 scripts.sqlite_db）。"""
import xhs_operations_server as srv       # mcp_servers 已在 conftest sys.path
from services import news


def test_mcp_uses_services_news():
    assert srv.query_news is news.query_news
    assert srv.update_news is news.update_news
