"""MCP 服务的动作快照必须与桥接的动作目录一致。"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_ROOT = os.path.join(REPO_ROOT, "mcp", "python")
if MCP_ROOT not in sys.path:
    sys.path.insert(0, MCP_ROOT)

from flandora_mcp import catalog

from flandora import action_catalog


def test_snapshot_matches_server_catalog():
    truth = action_catalog.catalog_dict()
    assert catalog.CATEGORIES == truth["categories"]
    assert catalog.ACTIONS == truth["actions"]
