"""
env_manager.py - Environment variable & .env management for Priya API keys.
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional


def load_project_env(project_dir: Optional[str] = None) -> Dict[str, str]:
    """
    Load .env from the project directory into os.environ (if not already set).
    Returns a dictionary of loaded keys.
    """
    if project_dir is None:
        project_dir = os.getcwd()

    env_path = Path(project_dir) / ".env"
    loaded = {}
    if not env_path.is_file():
        return loaded

    try:
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            if k:
                loaded[k] = v
                if k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass

    return loaded


def save_api_key(project_dir: str, key_name: str, key_value: str) -> Path:
    """
    Save or update an API key in the project directory's .env file and update os.environ.
    """
    key_name = key_name.strip()
    key_value = key_value.strip()

    norm_dir = Path(project_dir).resolve()
    norm_dir.mkdir(parents=True, exist_ok=True)
    env_path = norm_dir / ".env"

    lines = []
    found = False

    if env_path.is_file():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            lines = []

    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{key_name}=") or stripped.startswith(f"{key_name} ="):
            new_lines.append(f"{key_name}={key_value}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"{key_name}={key_value}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    os.environ[key_name] = key_value
    return env_path


def get_api_keys_status(project_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Check if MISTRAL_API_KEY and GEMINI_API_KEY are present in os.environ or .env.
    """
    if project_dir is None:
        project_dir = os.getcwd()

    env_path = Path(project_dir) / ".env"
    has_env_file = env_path.is_file()

    mistral_val = os.environ.get("MISTRAL_API_KEY", "").strip()
    gemini_val = os.environ.get("GEMINI_API_KEY", "").strip()

    # If missing from os.environ, check .env directly
    if (not mistral_val or not gemini_val) and has_env_file:
        file_vars = load_project_env(project_dir)
        if not mistral_val:
            mistral_val = file_vars.get("MISTRAL_API_KEY", "").strip()
        if not gemini_val:
            gemini_val = file_vars.get("GEMINI_API_KEY", "").strip()

    return {
        "has_env_file": has_env_file,
        "env_path": str(env_path),
        "mistral": bool(mistral_val),
        "mistral_masked": (mistral_val[:4] + "…" + mistral_val[-4:]) if len(mistral_val) > 8 else ("Set" if mistral_val else "Not set"),
        "gemini": bool(gemini_val),
        "gemini_masked": (gemini_val[:4] + "…" + gemini_val[-4:]) if len(gemini_val) > 8 else ("Set" if gemini_val else "Not set"),
        "has_any_key": bool(mistral_val or gemini_val),
    }


def get_onboarding_instructions(status: Optional[Dict[str, Any]] = None) -> str:
    """
    Returns user-facing Markdown instructions explaining how to obtain and configure API keys.
    """
    if status is None:
        status = get_api_keys_status()

    mistral_icon = "✓ Configured" if status["mistral"] else "✗ Missing"
    gemini_icon = "✓ Configured" if status["gemini"] else "✗ Missing"
    env_icon = f"✓ Found ({status['env_path']})" if status["has_env_file"] else "✗ Not present"

    return f"""### Welcome to Priya! API Key Setup & Onboarding

Priya requires an API key for **Mistral AI** or **Google Gemini** (both offer free tiers):

| Provider | Status | Key Name |
| :--- | :--- | :--- |
| **Mistral AI** | `{mistral_icon}` | `MISTRAL_API_KEY` |
| **Google Gemini** | `{gemini_icon}` | `GEMINI_API_KEY` |
| **Project .env** | `{env_icon}` | `.env` in current directory |

---

#### 1. How to get a Mistral API Key:
1. Visit [console.mistral.ai](https://console.mistral.ai/)
2. Sign up or log into your Mistral account
3. Navigate to **API Keys** and click **Create new key**
4. Copy the key and configure it below or export `MISTRAL_API_KEY="your-key"`

#### 2. How to get a Google Gemini API Key:
1. Visit [aistudio.google.com](https://aistudio.google.com/)
2. Sign in with your Google account
3. Click **Get API key** → **Create API key**
4. Copy the key and configure it below or export `GEMINI_API_KEY="your-key"`
*(Free tier includes Gemini 3.7 Flash, 3.5 Flash, 3.1 Flash Lite, 3.8 Flash)*

---
Select an option below to enter/update your key or continue.
"""
