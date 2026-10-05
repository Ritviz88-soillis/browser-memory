"""The structural rule of this server, enforced:

  * orchestrator.py is the only application file that calls services;
  * services are independent — no service imports another service.

If one of these fails, move the cross-service step into the orchestrator and
pass the result into the service as an argument.
"""

import ast
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
APPLICATION_FILES = [
    path
    for path in SERVER.rglob("*.py")
    if not {".venv", "tests", "scripts", "__pycache__"} & set(path.relative_to(SERVER).parts)
]


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def imports_a_service(path: Path) -> bool:
    return any(m == "services" or m.startswith("services.") for m in imported_modules(path))


def test_services_do_not_import_each_other():
    offenders = [
        path.name for path in (SERVER / "services").glob("*.py") if imports_a_service(path)
    ]
    assert offenders == [], f"services must stay independent: {offenders}"


def test_only_the_orchestrator_calls_services():
    callers = sorted(
        str(path.relative_to(SERVER))
        for path in APPLICATION_FILES
        if path.parent.name != "services" and imports_a_service(path)
    )
    assert callers == ["orchestrator.py"], f"services may only be called from the orchestrator: {callers}"


def test_every_service_is_wired_into_the_orchestrator():
    used = {m.removeprefix("services.") for m in imported_modules(SERVER / "orchestrator.py")}
    services = {p.stem for p in (SERVER / "services").glob("*.py") if p.stem != "__init__"}
    assert services <= used, f"unused services: {sorted(services - used)}"
