"""Read-only installer/reference resources for source and wheel installations."""

from pathlib import Path


def resource_root() -> Path:
    """Use assets from this installed version, never an unrelated ~/.lope tree."""
    package = Path(__file__).resolve().parent
    for candidate in (package / "_assets", package.parent):
        if ((candidate / "install").is_file()
                and (candidate / "skills" / "lope" / "SKILL.md").is_file()
                and (candidate / "commands").is_dir()
                and (candidate / "docs" / "reference.md").is_file()):
            return candidate
    raise FileNotFoundError("Lope installation is missing bundled host resources; reinstall lope-agent")
