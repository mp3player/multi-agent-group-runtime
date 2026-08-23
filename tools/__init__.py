"""Tool subsystem package.

- tools.decorator: @tool decorator + ToolFunction
- tools.registry:  ToolRegistry + ToolCallError
- tools.builtin:   built-in tools such as terminal
- tools.file_ops:  file tools for read/write/replace/list_dir
"""

from tools.decorator import ToolFunction, tool
from tools.audit import ToolAuditJsonlSink, ToolAuditLog, ToolAuditRecord
from tools.permissions import (
    ToolPermission,
    ToolPermissionDecision,
    ToolPermissionPolicy,
)
from tools.registry import ToolCallError, ToolRegistry
from tools.runtime import ToolRuntime
from tools.builtin import terminal
from tools.file_ops import read_file, write_file, str_replace, list_dir

__all__ = [
    "tool",
    "ToolFunction",
    "ToolRegistry",
    "ToolCallError",
    "ToolRuntime",
    "ToolPermission",
    "ToolPermissionDecision",
    "ToolPermissionPolicy",
    "ToolAuditJsonlSink",
    "ToolAuditLog",
    "ToolAuditRecord",
    "terminal",
    "read_file",
    "write_file",
    "str_replace",
    "list_dir",
]
