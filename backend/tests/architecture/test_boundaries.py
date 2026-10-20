import ast
from pathlib import Path


def test_domain_has_no_framework_or_infrastructure_imports():
    source = Path(__file__).parents[2] / "src/medinv"
    forbidden = ("sqlalchemy", "fastapi", "pydantic", "psycopg", "medinv.platform", "httpx")
    for file in source.glob("modules/*/domain/*.py"):
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            names = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else ([item.name for item in node.names] if isinstance(node, ast.Import) else [])
            )
            assert not any(name.startswith(forbidden) for name in names), file


def test_repositories_cannot_commit_or_create_connections():
    source = Path(__file__).parents[2] / "src/medinv"
    for file in source.glob("modules/*/infrastructure/*.py"):
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"commit", "rollback", "close", "create_engine"}, file
