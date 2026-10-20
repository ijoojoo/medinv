import json
from importlib.resources import files
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi


def install_contract(app: FastAPI) -> None:
    """Expose only implemented operations, using the reviewed wire contract."""
    contract = json.loads(
        files("medinv.contracts").joinpath("core-openapi.json").read_text(encoding="utf-8")
    )

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        generated = get_openapi(title=app.title, version=app.version, routes=app.routes)
        for path, methods in generated["paths"].items():
            for method in methods:
                if path in contract["paths"]:
                    generated["paths"][path][method] = contract["paths"][path][method]
                    if path == "/api/v1/shipments/{id}/post":
                        generated["paths"][path][method]["x-permission"] = "sales.post"
                elif path.startswith("/api/"):
                    operation = generated["paths"][path][method]
                    headers = ["X-Context-Generation"]
                    if method in {"post", "patch", "delete"}:
                        headers += ["X-CSRF-Token", "Idempotency-Key", "Origin"]
                    operation.setdefault("parameters", []).extend(
                        {
                            "name": header,
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                        for header in headers
                    )
                    for code in (400, 401, 403, 404, 409, 422, 503):
                        operation["responses"][str(code)] = {
                            "description": "标准业务错误；敏感请求内容不会回显",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                }
                            },
                        }
        components = generated.setdefault("components", {})
        for group, values in contract["components"].items():
            components.setdefault(group, {}).update(values)
        generated["security"] = contract["security"]
        app.openapi_schema = generated
        return generated

    app.openapi = openapi  # type: ignore[method-assign]
