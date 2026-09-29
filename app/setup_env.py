from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def check_python_version() -> None:
    if sys.version_info < (3, 11):
        print(f"Python 3.11+ required. Current version: {sys.version}")
        sys.exit(1)
    print(f"Python version: {sys.version}")


def install_requirements() -> None:
    requirements_path = Path(__file__).parent / "requirements.txt"
    if not requirements_path.exists():
        print("requirements.txt not found")
        sys.exit(1)
    print("Installing Python dependencies...")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r", str(requirements_path)]
    )
    print("Dependencies installed successfully.")


def install_playwright() -> None:
    print("Installing Playwright Chromium browser...")
    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    print("Playwright Chromium installed successfully.")


def create_directories() -> None:
    from config.settings import get_data_dir

    data_dir = get_data_dir()
    directories = [
        data_dir / "profile" / "experiences",
        data_dir / "profile" / "jobs",
        data_dir / "profile" / "resumes",
        data_dir / "profile" / "cover_letters",
        data_dir / "profile" / "job_descriptions",
        data_dir / "browser_profile",
        data_dir / "backups",
    ]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
    print(f"Data directories created at: {data_dir}")


def write_readme() -> None:
    from config.settings import get_data_dir

    readme_path = get_data_dir() / "profile" / "experiences" / "README.txt"
    if not readme_path.exists():
        readme_path.write_text(
            "HOW TO USE THIS FOLDER\n"
            "=====================\n\n"
            "Add .txt files describing your work experience, skills, and achievements.\n"
            "Write in plain language — the AI will read these to generate tailored\n"
            "resumes and cover letters for each job application.\n\n"
            "Tips:\n"
            "- One file per job/role works well, but any organization is fine\n"
            "- Include specific achievements with numbers where possible\n"
            "- Mention technologies, tools, and methodologies you've used\n"
            "- Describe projects you've led or contributed to\n"
            "- Include education, certifications, and relevant training\n\n"
            "Example files:\n"
            "  senior_engineer_acme.txt\n"
            "  skills_and_tools.txt\n"
            "  education.txt\n"
            "  projects_and_achievements.txt\n",
            encoding="utf-8",
        )
        print("README.txt created in experiences folder.")


def main() -> None:
    print("=" * 50)
    print("AutoApply Setup")
    print("=" * 50)

    check_python_version()
    install_requirements()
    install_playwright()
    create_directories()
    write_readme()

    print()
    print("=" * 50)
    print("Setup complete!")
    print()
    print("Next steps:")
    print("  1. Add your experience files to the experiences folder")
    print("  2. Run: python run.py")
    print("  3. Open http://localhost:5000 in your browser")
    print("=" * 50)


if __name__ == "__main__":
    main()
