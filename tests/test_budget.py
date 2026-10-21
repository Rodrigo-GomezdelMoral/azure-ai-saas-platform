import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED = ("docs/",)
MAX_FILES = 80
APPLICATION = ("app/", "ops/", "functions/")
SUPPORT = ("tests/", "load/")
LIMITS = {"application": 1200, "support": 600, "bicep": 600, "yaml": 500, "typescript": 700}
MAX_DEPTH, MAX_FUNCTION_LINES, MAX_COMMENTS = 3, 35, 24
FORBIDDEN_NAMES = {"utils.py", "helpers.py", "common.py", "misc.py"}
UNFINISHED = ("TO" + "DO", "FIX" + "ME")
AGENT_FRAMEWORK = "agent" + "_framework"
IMAGE = re.compile(r"^\s*(?:-\s*)?image:\s*['\"]?([^'\"\s]+)")


def tracked_files(*suffixes: str) -> list[Path]:
    command = ["git", "ls-files", "--cached", "--others", "--exclude-standard"]
    names = subprocess.run(command, capture_output=True, text=True, check=True, cwd=ROOT).stdout.split("\n")
    files = [ROOT / name for name in names if name and not name.startswith(EXCLUDED) and (ROOT / name).is_file()]
    return [f for f in files if f.suffix in suffixes] if suffixes else files


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def lines(paths: list[Path]) -> int:
    return sum(len(p.read_text(encoding="utf-8").splitlines()) for p in paths)


def test_file_and_depth_budget() -> None:
    files = tracked_files()
    assert len(files) <= MAX_FILES, f"{len(files)} tracked files"
    deepest = max(len(f.relative_to(ROOT).parts) - 1 for f in files)
    assert deepest <= MAX_DEPTH, f"directory depth {deepest}"
    assert not [f for f in files if f.name in FORBIDDEN_NAMES], "a grab-bag module exists"


def test_line_budgets() -> None:
    python = tracked_files(".py")
    application = [f for f in python if relative(f).startswith(APPLICATION)]
    support = [f for f in python if relative(f).startswith(SUPPORT)]
    assert lines(application) <= LIMITS["application"], f"{lines(application)} application Python lines"
    assert lines(support) <= LIMITS["support"], f"{lines(support)} support Python lines"
    assert lines(tracked_files(".bicep", ".bicepparam")) <= LIMITS["bicep"]
    manifests = [f for f in tracked_files(".yaml", ".yml") if relative(f).startswith("k8s/")]
    assert lines(manifests) <= LIMITS["yaml"], f"{lines(manifests)} YAML lines under k8s/"
    assert lines(tracked_files(".ts")) <= LIMITS["typescript"]


def test_python_craft() -> None:
    comments = 0
    for path in tracked_files(".py"):
        source = path.read_text(encoding="utf-8")
        comments += sum(1 for line in source.splitlines() if line.lstrip().startswith("#"))
        assert not any(marker in source for marker in UNFINISHED), f"{path.name} is unfinished"
        assert AGENT_FRAMEWORK not in source, f"{path.name} imports an agent framework"
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                length = (node.end_lineno or node.lineno) - node.lineno + 1
                assert length <= MAX_FUNCTION_LINES, f"{path.name}:{node.name} is {length} lines"
            if isinstance(node, ast.ExceptHandler):
                blind = node.type is None or (isinstance(node.type, ast.Name) and node.type.id == "Exception")
                assert not blind, f"{path.name}:{node.lineno} catches everything"
    assert comments <= MAX_COMMENTS, f"{comments} comment lines"


def test_images_are_pinned() -> None:
    sources = [f for f in tracked_files(".yaml", ".yml") if relative(f).startswith("k8s/")]
    sources += [f for f in tracked_files() if f.name == "Dockerfile"]
    for path in sources:
        for line in path.read_text(encoding="utf-8").splitlines():
            match = IMAGE.match(line) or re.match(r"^\s*FROM\s+(\S+)", line)
            if match:
                image = match.group(1)
                assert ":" in image.rsplit("/", 1)[-1] and not image.endswith(":latest"), f"{path.name}: {image}"


def test_dependencies_are_pinned() -> None:
    files = (ROOT / "requirements.txt", ROOT / "requirements-dev.txt", ROOT / "functions" / "requirements.txt")
    pins = [line for file in files if file.exists() for line in file.read_text(encoding="utf-8").splitlines()]
    for pin in (p for p in pins if p and not p.startswith(("-r", "#"))):
        assert "==" in pin, f"unpinned dependency {pin}"
