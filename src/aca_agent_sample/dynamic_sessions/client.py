"""Dynamic Sessions transport and an offline, intentionally limited interpreter.

The fake is not an isolation boundary. It interprets a small AST allowlist and never
uses Python eval/exec on model code or accesses the host filesystem.
"""
from __future__ import annotations

import ast
import io
import operator
import posixpath
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from ..config import validate_https_url
from ..types import ToolExecutionResult
from .session_registry import validate_session_id

API_VERSION = "2025-10-02-preview"


class SessionStateLost(RuntimeError):
    error_kind = "state_lost"


class DynamicSessionsError(RuntimeError):
    def __init__(self, kind: str, message: str, status_code: int | None = None):
        super().__init__(message)
        self.error_kind = kind
        self.status_code = status_code


def _path(path: str) -> str:
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
        raise ValueError("Invalid session file path")
    relative = path.removeprefix("/mnt/data/")
    if path.startswith("/") and not path.startswith("/mnt/data/"):
        raise ValueError("Path must be under /mnt/data")
    pieces = relative.split("/")
    if any(not p or p in {".", ".."} for p in pieces):
        raise ValueError("Invalid session file path")
    if any(not all(c.isascii() and (c.isalnum() or c in "-_.") for c in p) for p in pieces):
        raise ValueError("Unsupported session file name")
    return "/".join(pieces)


class DynamicSessionsClient:
    def __init__(self, pool_endpoint: str, token_provider: Callable[[], str], *,
                 wire_profile: str = "containerapp-1.3.0b5", http_client: Any = None,
                 request_timeout: float = 65) -> None:
        validate_https_url(pool_endpoint, "pool_endpoint")
        if wire_profile not in {"containerapp-1.3.0b5", "learn-properties"}:
            raise ValueError("Unsupported wire profile")
        self._endpoint = pool_endpoint.rstrip("/")
        self._token_provider = token_provider
        self.wire_profile = wire_profile
        self._client = http_client or httpx.Client(timeout=request_timeout, follow_redirects=False)
        self._owns_client = http_client is None
        self._timeout = request_timeout

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _request(self, method: str, path: str, session_id: str, **kwargs: Any) -> httpx.Response:
        validate_session_id(session_id)
        try:
            token = self._token_provider()
        except Exception:
            raise DynamicSessionsError("auth", "Unable to acquire session token") from None
        try:
            response = self._client.request(method, self._endpoint + path,
                params={"api-version": API_VERSION, "identifier": session_id, **kwargs.pop("params", {})},
                headers={"Authorization": "Bearer " + token}, timeout=self._timeout, **kwargs)
        except httpx.TimeoutException:
            raise DynamicSessionsError("outcome_unknown" if method == "POST" else "timeout", "Request timed out") from None
        except httpx.RequestError:
            raise DynamicSessionsError("outcome_unknown" if method == "POST" else "execution_failed", "Session transport failed") from None
        if response.status_code >= 400 or response.status_code < 200 or response.status_code >= 300:
            code = response.status_code
            kind = "auth" if code == 401 else "forbidden" if code == 403 else "timeout" if code in {408, 504} and method != "POST" else "outcome_unknown" if code in {408, 500, 502, 503, 504} and method == "POST" else "execution_failed"
            raise DynamicSessionsError(kind, f"Session request failed (HTTP {code})", code)
        return response

    def execute_python(self, session_id: str, code: str, timeout: int = 60) -> ToolExecutionResult:
        if not isinstance(code, str) or not code.strip() or type(timeout) is not int or not 1 <= timeout <= 220:
            raise ValueError("Invalid code or timeout")
        body = {"codeInputType": "inline", "executionType": "synchronous", "code": code, "timeoutInSeconds": timeout}
        if self.wire_profile == "learn-properties":
            body = {"properties": body}
        return self._execute(session_id, body)

    def execute_shell(self, session_id: str, command: str, timeout: int = 60) -> ToolExecutionResult:
        if not isinstance(command, str) or not command.strip() or type(timeout) is not int or not 1 <= timeout <= 600:
            raise ValueError("Invalid command or timeout")
        return self._execute(session_id, {"shellCommand": command, "timeoutInSeconds": timeout})

    def _execute(self, session_id: str, body: dict[str, Any]) -> ToolExecutionResult:
        try:
            response = self._request("POST", "/executions", session_id, json=body)
            payload = response.json()
        except DynamicSessionsError as exc:
            return ToolExecutionResult("failed", stderr=str(exc), error_kind=exc.error_kind)
        except (ValueError, TypeError):
            return ToolExecutionResult("failed", stderr="Invalid execution response", error_kind="schema_mismatch")
        if not isinstance(payload, dict):
            return ToolExecutionResult("failed", stderr="Invalid execution response", error_kind="schema_mismatch")
        if self.wire_profile == "learn-properties" and isinstance(payload.get("properties"), dict):
            payload = payload["properties"]
        status = payload.get("status")
        result = payload.get("result")
        shell_exit = None
        if "shellCommand" in body and isinstance(status, str) and status.removeprefix("-").isascii() and status.removeprefix("-").isdecimal():
            shell_exit = int(status)
            status = "Succeeded" if shell_exit == 0 else "Failed"
        if status not in {"Succeeded", "Failed"} or not isinstance(result, dict):
            return ToolExecutionResult("failed", stderr="Unrecognized execution response", error_kind="schema_mismatch")
        stdout, stderr = result.get("stdout"), result.get("stderr")
        if not isinstance(stdout, str) or not isinstance(stderr, str):
            return ToolExecutionResult("failed", stderr="Unrecognized execution output", error_kind="schema_mismatch")
        exit_code = result.get("exitCode", shell_exit)
        if exit_code is not None and type(exit_code) is not int:
            return ToolExecutionResult("failed", stderr="Invalid exit code", error_kind="schema_mismatch")
        failed = status == "Failed" or (exit_code is not None and exit_code != 0)
        return ToolExecutionResult("failed" if failed else "succeeded", stdout=stdout, stderr=stderr,
                                   exit_code=exit_code, error_kind="execution_failed" if failed else None)

    def upload_file(self, session_id: str, filename: str, content: bytes) -> Any:
        """Upload a basename with its directory in the service path query."""
        filename = _path(filename)
        directory, basename = posixpath.split(filename)
        if not isinstance(content, bytes):
            raise TypeError("File content must be bytes")
        response = self._request("POST", "/files", session_id, params={"path": directory}, files={"file": (basename, content, "application/octet-stream")})
        return response.json() if response.content else None

    def list_files(self, session_id: str) -> Any:
        try:
            return self._request("GET", "/files", session_id).json()
        except ValueError:
            raise DynamicSessionsError("schema_mismatch", "Invalid file listing") from None

    def file_info(self, session_id: str, filename: str) -> Any:
        try:
            return self._request("GET", "/files/" + quote(posixpath.basename(_path(filename)), safe=""), session_id, params={"path": posixpath.dirname(_path(filename))}).json()
        except ValueError:
            raise DynamicSessionsError("schema_mismatch", "Invalid file metadata") from None

    def download_file(self, session_id: str, filename: str) -> bytes:
        return self._request("GET", "/files/" + quote(posixpath.basename(_path(filename)), safe="") + "/content", session_id, params={"path": posixpath.dirname(_path(filename))}).content


class _VirtualFile(io.StringIO):
    def __init__(self, files: dict[str, bytes], path: str, mode: str):
        self._files, self._path, self._mode = files, path, mode
        if mode not in {"r", "w", "a"}:
            raise ValueError("Fake supports text file modes r, w and a")
        if mode == "r" and path not in files:
            raise FileNotFoundError(path)
        super().__init__(files.get(path, b"").decode("utf-8") if mode != "w" else "")
        if mode == "a":
            self.seek(0, 2)

    def write(self, text: str) -> int:
        if self._mode == "r":
            raise ValueError("File is read-only")
        count = super().write(text)
        self._files[self._path] = self.getvalue().encode("utf-8")
        return count

    def close(self) -> None:
        if not self.closed and self._mode != "r":
            self._files[self._path] = self.getvalue().encode("utf-8")
        super().close()


class _Return(Exception):
    def __init__(self, value: Any): self.value = value


class _Interpreter:
    """Explicit AST subset: unsupported Python fails closed, not a sandbox."""
    _binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
               ast.Div: operator.truediv, ast.Mod: operator.mod}
    _compare = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
                ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
                ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
                ast.Is: operator.is_, ast.IsNot: operator.is_not}

    def __init__(self, namespace: dict[str, Any], files: dict[str, bytes]):
        self.ns, self.files, self.output = namespace, files, io.StringIO()
        self.builtins = {"print": self._print, "open": self._open, "exec": self._exec,
                         "len": len, "str": str, "int": int, "bool": bool,
                         "globals": lambda: dict(self.ns)}

    def _print(self, *args: Any) -> None: print(*args, file=self.output)
    def _open(self, path: str, mode: str = "r", **kwargs: Any) -> _VirtualFile:
        if kwargs and kwargs != {"encoding": "utf-8"}: raise ValueError("Unsupported open options")
        return _VirtualFile(self.files, _path(path), mode)
    def _exec(self, code: str) -> None: self.run(code)
    def run(self, code: str) -> None:
        if len(code) > 12000: raise ValueError("Code too long")
        for statement in ast.parse(code).body: self.statement(statement)

    def statement(self, node: ast.stmt) -> None:
        if isinstance(node, ast.Expr): self.expr(node.value)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            self.ns[node.targets[0].id] = self.expr(node.value)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and type(node.op) in self._binary:
            self.ns[node.target.id] = self._binary[type(node.op)](self.ns[node.target.id], self.expr(node.value))
        elif isinstance(node, ast.Assert):
            if not self.expr(node.test): raise AssertionError(self.expr(node.msg) if node.msg else "assertion failed")
        elif isinstance(node, ast.If):
            for part in node.body if self.expr(node.test) else node.orelse: self.statement(part)
        elif isinstance(node, ast.Return): raise _Return(self.expr(node.value) if node.value else None)
        elif isinstance(node, ast.FunctionDef) and not node.decorator_list and not node.args.defaults and not node.args.kwonlyargs and not node.args.vararg and not node.args.kwarg:
            self.ns[node.name] = ("function", node)
        elif isinstance(node, ast.Import) and len(node.names) == 1 and node.names[0].name == "os" and node.names[0].asname is None:
            self.ns["os"] = ("module", "os")
        elif isinstance(node, ast.Pass): pass
        else: raise ValueError("Unsupported offline Python statement: " + type(node).__name__)

    def expr(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Constant): return node.value
        if isinstance(node, ast.Name):
            if node.id in self.ns: return self.ns[node.id]
            if node.id in self.builtins: return self.builtins[node.id]
            raise NameError(node.id + " is not defined")
        if isinstance(node, ast.BinOp) and type(node.op) in self._binary: return self._binary[type(node.op)](self.expr(node.left), self.expr(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.USub)):
            value = self.expr(node.operand); return not value if isinstance(node.op, ast.Not) else -value
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in self._compare:
            return self._compare[type(node.ops[0])](self.expr(node.left), self.expr(node.comparators[0]))
        if isinstance(node, ast.Call):
            args = [self.expr(x) for x in node.args]
            kwargs = {x.arg: self.expr(x.value) for x in node.keywords if x.arg is not None}
            if len(kwargs) != len(node.keywords): raise ValueError("Unsupported keyword arguments")
            if isinstance(node.func, ast.Attribute):
                obj = self.expr(node.func.value)
                if obj == ("module", "os") and node.func.attr == "makedirs":
                    if len(args) != 1 or kwargs != {"exist_ok": True}: raise ValueError("Invalid makedirs")
                    _path(args[0]); return None
                if not isinstance(obj, _VirtualFile) or node.func.attr not in {"read", "write", "close"}:
                    raise ValueError("Unsupported method")
                return getattr(obj, node.func.attr)(*args, **kwargs)
            fn = self.expr(node.func)
            if isinstance(fn, tuple) and len(fn) == 2 and fn[0] == "function":
                definition = fn[1]; names = [arg.arg for arg in definition.args.args]
                if len(names) != len(args) or kwargs: raise ValueError("Invalid function arguments")
                original = self.ns.copy()
                try:
                    self.ns.update(zip(names, args))
                    for part in definition.body: self.statement(part)
                except _Return as result: return result.value
                finally: self.ns.clear(); self.ns.update(original)
                return None
            if fn not in self.builtins.values(): raise ValueError("Unsupported function")
            return fn(*args, **kwargs)
        raise ValueError("Unsupported offline Python expression: " + type(node).__name__)


class FakeDynamicSessionsClient:
    """Deterministic, per-ID Python subset and virtual files; never a local fallback."""
    def __init__(self) -> None:
        self._sessions: dict[str, tuple[dict[str, Any], dict[str, bytes]]] = {}

    def _session(self, session_id: str) -> tuple[dict[str, Any], dict[str, bytes]]:
        validate_session_id(session_id)
        return self._sessions.setdefault(session_id, ({}, {}))

    def expire_session(self, session_id: str) -> None:
        validate_session_id(session_id)
        self._sessions.pop(session_id, None)

    def execute_python(self, session_id: str, code: str, timeout: int = 60) -> ToolExecutionResult:
        if type(timeout) is not int or not 1 <= timeout <= 220: raise ValueError("Invalid timeout")
        namespace, files = self._session(session_id)
        interpreter = _Interpreter(namespace, files)
        try:
            interpreter.run(code)
            return ToolExecutionResult("succeeded", stdout=interpreter.output.getvalue(), exit_code=0)
        except Exception as exc:
            return ToolExecutionResult("failed", stdout=interpreter.output.getvalue(), stderr=f"{type(exc).__name__}: {exc}", exit_code=1, error_kind="execution_failed")

    def upload_file(self, session_id: str, filename: str, content: bytes) -> dict[str, Any]:
        if not isinstance(content, bytes): raise TypeError("File content must be bytes")
        name = _path(filename); self._session(session_id)[1][name] = content
        return {"name": name, "size": len(content)}

    def download_file(self, session_id: str, filename: str) -> bytes:
        name = _path(filename)
        try: return self._session(session_id)[1][name]
        except KeyError: raise DynamicSessionsError("execution_failed", "Session file not found", 404) from None

    def file_info(self, session_id: str, filename: str) -> dict[str, Any]:
        return {"name": _path(filename), "size": len(self.download_file(session_id, filename))}

    def list_files(self, session_id: str) -> list[dict[str, Any]]:
        return [{"name": name, "size": len(data)} for name, data in sorted(self._session(session_id)[1].items())]

    def execute_shell(self, session_id: str, command: str, timeout: int = 60) -> ToolExecutionResult:
        _, files = self._session(session_id)
        if command == "cd /mnt/data && pwd":
            return ToolExecutionResult("succeeded", stdout="/mnt/data\n", exit_code=0)
        if command == "cd /mnt/data && mkdir -p shell-demo && echo 'hello-from-shell-session' > shell-demo/note.txt && pwd":
            files["/mnt/data/shell-demo/note.txt"] = b"hello-from-shell-session\n"
            return ToolExecutionResult("succeeded", stdout="/mnt/data\n", exit_code=0)
        if command == "cd /mnt/data && cat shell-demo/note.txt && pwd":
            data = files.get("/mnt/data/shell-demo/note.txt", b"").decode("utf-8")
            return ToolExecutionResult("succeeded", stdout=f"{data}/mnt/data\n", exit_code=0)
        return ToolExecutionResult("failed", stderr="Unsupported offline shell command", exit_code=1, error_kind="execution_failed")

    def close(self) -> None: pass



