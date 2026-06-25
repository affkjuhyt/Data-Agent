"""AST-based security validation for agent-authored Python (doc §1.3, §1.6).

String/regex matching can be defeated by obfuscation (e.g.
``getattr(__builtins__, 'ev'+'al')``). Parsing to an AST and walking the real
call/import nodes catches that, because it inspects program *structure*, not
surface text — the technique the doc calls semantic-level enforcement.

Two independent checks, both must pass:
  1. declared-permission whitelist — only ALLOWED_MODULES may be imported at
     all (data-analysis libraries only).
  2. blocked-call/module denylist — defense in depth for dangerous builtins
     that don't require an import (eval, exec, open, ...).
"""
import ast

ALLOWED_MODULES = {
    "pandas", "numpy", "plotly", "matplotlib",
    "json", "math", "datetime", "statistics",
}

BLOCKED_CALLS = {
    "eval", "exec", "compile", "__import__", "open", "input", "breakpoint",
    # Dynamic attribute access is how sandboxes get escaped without ever
    # importing a blocked module — e.g. getattr(__builtins__, 'ev'+'al').
    # The doc cites exactly that example as something AST validation catches;
    # it only does if getattr/setattr/vars/globals/locals are blocked too.
    "getattr", "setattr", "delattr", "vars", "globals", "locals",
}

BLOCKED_MODULES = {
    "os", "sys", "subprocess", "socket", "urllib", "requests", "ctypes", "importlib",
}

# Dunder attributes that chain from any ordinary object back to builtins/the
# class hierarchy (the classic `().__class__.__bases__[0].__subclasses__()`
# sandbox-escape family). Blocked regardless of which object they're
# accessed on, since the whole point is they work on *any* object.
BLOCKED_ATTRIBUTES = {
    "__globals__", "__builtins__", "__import__", "__subclasses__", "__bases__",
    "__mro__", "__code__", "__closure__", "__class__", "__base__", "__loader__",
}


class SecurityError(Exception):
    pass


class SecurityVisitor(ast.NodeVisitor):
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top in BLOCKED_MODULES:
                raise SecurityError(f"Import blocked: {alias.name}")
            if top not in ALLOWED_MODULES:
                raise SecurityError(f"Import not in whitelist: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        top = (node.module or "").split(".")[0]
        if top in BLOCKED_MODULES:
            raise SecurityError(f"Import blocked: {node.module}")
        if top not in ALLOWED_MODULES:
            raise SecurityError(f"Import not in whitelist: {node.module}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in BLOCKED_CALLS:
            raise SecurityError(f"Call blocked: {node.func.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in BLOCKED_ATTRIBUTES:
            raise SecurityError(f"Attribute access blocked: {node.attr}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in BLOCKED_ATTRIBUTES:
            raise SecurityError(f"Name access blocked: {node.id}")
        self.generic_visit(node)


def validate_python_code(code: str) -> None:
    """Raises SecurityError on the first violation found. Raises SyntaxError
    (uncaught — caller should treat as a validation failure too) on code that
    doesn't parse."""
    tree = ast.parse(code)
    SecurityVisitor().visit(tree)
