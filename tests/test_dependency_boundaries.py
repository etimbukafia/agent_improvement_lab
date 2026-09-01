"""Protect the public import boundary of the core package."""

from __future__ import annotations

import subprocess
import sys


def test_core_import_does_not_load_examples_or_optional_runtime_packages() -> None:
    """Importing the core must not require optional runtimes or examples."""

    check = (
        "import sys; import enterprise_agent_improvement_lab; "
        "forbidden = ('enterprise_agent_improvement_lab.examples', "
        "'enterprise_agent_improvement_lab.integrations', "
        "'langgraph', 'pydantic_ai', 'pydantic_evals'); "
        "assert not any("
        "name == item or name.startswith(item + '.') for name in sys.modules for item in forbidden)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", check],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
