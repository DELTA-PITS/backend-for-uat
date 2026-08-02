import logging
import os
import tomllib
from pathlib import Path

from dynaconf import Dynaconf

os.environ["BASE_DIR"] = os.getenv(
    "BASE_DIR",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
)

settings = Dynaconf(
    settings_files=["conf/*settings.toml", "conf/.secrets.toml", "conf/*settings.json"],
    root_path=os.getenv("BASE_DIR"),
    environments=True,
    env_switcher="ENVIRONMENT",
    load_dotenv=True,
)


def get_env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        logging.warning("Invalid %s=%r. Falling back to %d.", name, value, default)
        return default


def project_details() -> dict:
    """Read project metadata from pyproject.toml.

    Returns:
        dict: A dictionary containing:
            - title (str): The project name.
            - description (str): The project description.
            - version (str): The project version.
    """
    base_dir = Path(os.getenv("BASE_DIR", Path(__file__).parent.parent.parent.parent))
    toml_path = base_dir / "pyproject.toml"

    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    project = data.get("project", {})
    tool = data.get("tool", {}).get("trustmark", {})
    return {
        "title": tool.get("title", project.get("name", "")),
        "description": project.get("description", ""),
        "version": project.get("version", "unknown"),
    }
