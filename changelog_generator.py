#!/usr/bin/env python3
"""
Context-Aware Changelog Generator
Analyzes git commits and PRs to create meaningful changelogs that explain 
why changes were made, not just what changed. Groups related changes intelligently.
"""

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from typing import Optional


# Conventional commit types
COMMIT_TYPES = {
    "feat": {"name": "Features", "icon": "✨", "priority": 1},
    "fix": {"name": "Bug Fixes", "icon": "🐛", "priority": 2},
    "docs": {"name": "Documentation", "icon": "📝", "priority": 4},
    "style": {"name": "Styles", "icon": "💄", "priority": 7},
    "refactor": {"name": "Code Refactoring", "icon": "♻️", "priority": 5},
    "perf": {"name": "Performance Improvements", "icon": "⚡", "priority": 3},
    "test": {"name": "Tests", "icon": "✅", "priority": 8},
    "build": {"name": "Builds", "icon": "📦", "priority": 6},
    "ci": {"name": "CI/CD", "icon": "🔧", "priority": 9},
    "chore": {"name": "Maintenance", "icon": "🔨", "priority": 10},
    "revert": {"name": "Reverts", "icon": "⏪", "priority": 11},
    "security": {"name": "Security", "icon": "🔒", "priority": 0},
}


def run_git_command(args: list, cwd: str = None) -> str:
    """Run a git command and return output."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        return ""


def get_commit_log(limit: int = 100, since: Optional[str] = None, until: Optional[str] = None, cwd: str = None) -> list:
    """Get commit history with detailed information."""
    # Get list of commit hashes first
    hash_args = ["rev-list"]
    if since:
        hash_args.append(f"--since={since}")
    if until:
        hash_args.append(f"--until={until}")
    hash_args.extend(["--reverse", "-n", str(limit), "HEAD"])
    
    hash_output = run_git_command(hash_args, cwd=cwd)
    if not hash_output:
        return []
    
    commit_hashes = [h.strip() for h in hash_output.split("\n") if h.strip()]
    
    commits = []
    for commit_hash in commit_hashes:
        # Get details for each commit
        show_args = ["show", "--quiet", "--pretty=format:%s%n%b%n%an%n%ae%n%ai", commit_hash]
        show_output = run_git_command(show_args, cwd=cwd)
        
        if not show_output:
            continue
        
        # Parse the output - lines are: subject, body, author, email, date
        lines = show_output.split("\n")
        
        # First line is subject
        subject = lines[0] if len(lines) > 0 else ""
        
        # Find where body ends (first empty line or next metadata)
        body_lines = []
        author = ""
        email = ""
        date = ""
        
        # Look for author line (starts after body)
        for i, line in enumerate(lines[1:], 1):
            if line == author and i < len(lines):
                continue
            # Try to detect author line by pattern
            if i >= 1 and i < len(lines):
                # Body ends, remaining lines are metadata
                # Find author, email, date
                remaining = lines[i:]
                if len(remaining) >= 3:
                    author = remaining[0]
                    email = remaining[1]
                    date = remaining[2]
                    body_lines = lines[1:i]
                break
        
        # If we didn't find metadata, try simpler parsing
        if not author and len(lines) >= 4:
            # Assume format: subject, body (rest), author, email, date
            subject = lines[0]
            # Last 3 lines are author, email, date
            author = lines[-3] if len(lines) >= 3 else ""
            email = lines[-2] if len(lines) >= 2 else ""
            date = lines[-1]
            # Everything in between is body
            body_lines = lines[1:-3] if len(lines) > 3 else []
        
        body = "\n".join(body_lines)
        
        commit = {
            "hash": commit_hash,
            "subject": subject,
            "body": body,
            "author": author,
            "email": email,
            "date": date,
            "parents": [],
            "type": "unknown",
            "scope": None,
            "breaking": False,
            "issue_refs": [],
            "files_changed": [],
        }
        
        # Parse conventional commit format
        parse_conventional_commit(commit)
        
        # Extract issue/PR references
        commit["issue_refs"] = extract_issue_references(commit["subject"] + " " + commit["body"])
        
        commits.append(commit)
    
    return commits


def parse_conventional_commit(commit: dict) -> None:
    """Parse conventional commit format (type(scope): subject)."""
    subject = commit["subject"]
    
    # Match conventional commit pattern
    pattern = r'^(\w+)(?:\(([^)]+)\))?(!)?:\s*(.+)$'
    match = re.match(pattern, subject)
    
    if match:
        commit["type"] = match.group(1).lower()
        commit["scope"] = match.group(2)
        commit["breaking"] = match.group(3) is not None
        commit["conventional_subject"] = match.group(4)
    else:
        # Try to infer type from subject
        commit["type"] = infer_commit_type(subject)
        commit["conventional_subject"] = subject


def infer_commit_type(subject: str) -> str:
    """Infer commit type from subject if not using conventional format."""
    subject_lower = subject.lower()
    
    if any(word in subject_lower for word in ["fix", "bug", "patch", "resolve"]):
        return "fix"
    elif any(word in subject_lower for word in ["feat", "add", "new", "implement"]):
        return "feat"
    elif any(word in subject_lower for word in ["doc", "readme", "comment"]):
        return "docs"
    elif any(word in subject_lower for word in ["refactor", "cleanup", "improve"]):
        return "refactor"
    elif any(word in subject_lower for word in ["perf", "optimize", "speed"]):
        return "perf"
    elif any(word in subject_lower for word in ["test", "coverage"]):
        return "test"
    elif any(word in subject_lower for word in ["security", "vulnerability", "CVE"]):
        return "security"
    elif any(word in subject_lower for word in ["build", "deps", "dependency"]):
        return "build"
    elif any(word in subject_lower for word in ["ci", "pipeline", "workflow"]):
        return "ci"
    else:
        return "chore"


def extract_issue_references(text: str) -> list:
    """Extract issue/PR numbers from text."""
    # Match #123, GH-123, closes #123, fixes #123, etc.
    patterns = [
        r'(?:closes?|fixes?|resolves?|refs?|see)\s+#(\d+)',
        r'GH[-#](\d+)',
        r'(?:PR|MR)[:\s#]?(\d+)',
    ]
    
    refs = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        refs.extend(matches)
    
    return list(set(refs))


def get_commit_diff(commit_hash: str) -> str:
    """Get the diff for a specific commit."""
    return run_git_command(["show", "--stat", "--format=", commit_hash])


def get_related_files(commit_hash: str) -> list:
    """Get list of files changed in a commit."""
    output = run_git_command(["show", "--name-only", "--pretty=format=", commit_hash])
    return [f.strip() for f in output.split("\n") if f.strip()]


def group_commits(commits: list) -> dict:
    """Group commits by type and detect related changes."""
    groups = defaultdict(list)
    
    for commit in commits:
        commit_type = commit.get("type", "chore")
        
        # Add file info
        commit["files_changed"] = get_related_files(commit["hash"])
        
        # Add diff summary
        commit["diff_summary"] = get_commit_diff(commit["hash"])
        
        groups[commit_type].append(commit)
    
    return dict(groups)


def analyze_change_context(commit: dict) -> str:
    """Analyze a commit to understand why the change was made."""
    context_parts = []
    
    # Check for breaking changes
    if commit.get("breaking"):
        context_parts.append("⚠️ **BREAKING CHANGE**")
    
    # Check for issue references
    if commit.get("issue_refs"):
        refs = ", ".join([f"#{ref}" for ref in commit["issue_refs"]])
        context_parts.append(f"Addresses: {refs}")
    
    # Analyze files changed to provide context
    files = commit.get("files_changed", [])
    if files:
        # Categorize changed files
        categories = categorize_files(files)
        if categories:
            context_parts.append(f"Modified: {', '.join(categories)}")
    
    return " | ".join(context_parts) if context_parts else ""


def categorize_files(files: list) -> list:
    """Categorize changed files."""
    categories = set()
    
    for file in files:
        if "test" in file.lower():
            categories.add("tests")
        elif "doc" in file.lower() or "readme" in file.lower():
            categories.add("docs")
        elif any(x in file.lower() for x in ["src/lib", "src/app"]):
            categories.add("source")
        elif "config" in file.lower():
            categories.add("config")
        elif "package" in file.lower() or "requirements" in file.lower():
            categories.add("dependencies")
    
    return list(categories)[:3]  # Limit to 3 categories


def generate_changelog_entry(commit: dict, include_why: bool = True) -> str:
    """Generate a changelog entry for a single commit."""
    lines = []
    
    # Subject (the what)
    subject = commit.get("conventional_subject") or commit.get("subject", "No description")
    lines.append(f"- {subject}")
    
    # Context (the why)
    if include_why:
        context = analyze_change_context(commit)
        if context:
            lines.append(f"  - *{context}*")
    
    # Add metadata
    date = commit.get("date", "")[:10]
    author = commit.get("author", "")
    lines.append(f"  - ({date}) - {author}")
    
    return "\n".join(lines)


def generate_markdown(groups: dict, title: str = "Changelog") -> str:
    """Generate markdown format changelog."""
    lines = []
    lines.append(f"# {title}")
    lines.append(f"\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    
    # Sort types by priority
    sorted_types = sorted(
        groups.keys(),
        key=lambda x: COMMIT_TYPES.get(x, {}).get("priority", 99)
    )
    
    for commit_type in sorted_types:
        commits = groups[commit_type]
        if not commits:
            continue
        
        type_info = COMMIT_TYPES.get(commit_type, {"name": commit_type.title(), "icon": "📌"})
        icon = type_info.get("icon", "📌")
        name = type_info.get("name", commit_type.title())
        
        lines.append(f"\n## {icon} {name}\n")
        
        for commit in commits:
            lines.append(generate_changelog_entry(commit))
    
    return "\n".join(lines)


def generate_json(groups: dict) -> str:
    """Generate JSON format output."""
    output = {
        "generated": datetime.now().isoformat(),
        "groups": {}
    }
    
    for commit_type, commits in groups.items():
        type_info = COMMIT_TYPES.get(commit_type, {"name": commit_type.title()})
        output["groups"][commit_type] = {
            "name": type_info.get("name", commit_type.title()),
            "icon": type_info.get("icon", "📌"),
            "commits": commits
        }
    
    return json.dumps(output, indent=2)


def generate_console(groups: dict) -> str:
    """Generate colored console output."""
    lines = []
    lines.append("\n" + "=" * 60)
    lines.append("📋 CONTEXT-AWARE CHANGELOG")
    lines.append("=" * 60)
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    
    # Sort types by priority
    sorted_types = sorted(
        groups.keys(),
        key=lambda x: COMMIT_TYPES.get(x, {}).get("priority", 99)
    )
    
    for commit_type in sorted_types:
        commits = groups[commit_type]
        if not commits:
            continue
        
        type_info = COMMIT_TYPES.get(commit_type, {"name": commit_type.title()})
        icon = type_info.get("icon", "📌")
        name = type_info.get("name", commit_type.title())
        
        lines.append(f"\n{icon} {name.upper()}")
        lines.append("-" * 40)
        
        for commit in commits:
            subject = commit.get("conventional_subject") or commit.get("subject", "No description")
            date = commit.get("date", "")[:10]
            lines.append(f"  • {subject}")
            
            context = analyze_change_context(commit)
            if context:
                lines.append(f"    → {context}")
            
            lines.append(f"    [{date}]")
    
    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


def try_github_pr_info(commit_hash: str) -> Optional[dict]:
    """Try to get PR information from GitHub CLI."""
    try:
        # Try to find PR associated with this commit
        result = subprocess.run(
            ["gh", "search", "commits", commit_hash, "--limit=1", "--json", "number,title,body"],
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode == 0 and result.stdout:
            data = json.loads(result.stdout)
            if data:
                return data[0]
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError):
        pass
    
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Context-Aware Changelog Generator - Creates meaningful changelogs that explain why changes were made"
    )
    parser.add_argument(
        "-n", "--limit",
        type=int,
        default=50,
        help="Number of commits to analyze (default: 50)"
    )
    parser.add_argument(
        "--since",
        type=str,
        help="Start date (e.g., '2024-01-01')"
    )
    parser.add_argument(
        "--until",
        type=str,
        help="End date (e.g., '2024-12-31')"
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "json", "console"],
        default="console",
        help="Output format (default: console)"
    )
    parser.add_argument(
        "--no-why",
        action="store_true",
        help="Don't include 'why' context in changelog"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output file (default: stdout)"
    )
    parser.add_argument(
        "--title",
        type=str,
        default="Changelog",
        help="Title for markdown output"
    )
    parser.add_argument(
        "-r", "--repo",
        type=str,
        default=".",
        help="Git repository path (default: current directory)"
    )
    
    args = parser.parse_args()
    
    # Determine the repo path
    repo_path = args.repo if args.repo != "." else None
    
    # Check if it's a git repository
    if not run_git_command(["rev-parse", "--git-dir"], cwd=repo_path):
        print(f"Error: Not a git repository: {repo_path or '.'}", file=sys.stderr)
        sys.exit(1)
    
    # Get commits
    commits = get_commit_log(
        limit=args.limit,
        since=args.since,
        until=args.until,
        cwd=repo_path
    )
    
    if not commits:
        print("No commits found in the specified range.")
        sys.exit(0)
    
    # Group commits
    groups = group_commits(commits)
    
    # Generate output
    if args.format == "markdown":
        output = generate_markdown(groups, title=args.title)
    elif args.format == "json":
        output = generate_json(groups)
    else:
        output = generate_console(groups)
    
    # Write output
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Changelog written to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
