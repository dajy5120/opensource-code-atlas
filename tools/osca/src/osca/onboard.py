"""Onboarding a new project: `osca new` (local study repo) and `osca publish` (GitHub)."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from . import gitutil
from .project import load_project, write_sync
from .verify import CLAUDE_IMPORT, CLAUDE_MD

TEMPLATE = "gh:dajy5120/opensource-code-atlas"


class OnboardError(RuntimeError):
    pass


@dataclass
class NewProject:
    id: str
    name: str
    upstream: str
    anchor: str
    directory: Path
    branch: str = "main"
    license: str = ""
    terminology: list[str] = field(default_factory=lambda: ["general"])
    include: list[str] = field(default_factory=lambda: ["**"])
    exclude: list[str] = field(default_factory=lambda: ["**/tests/**", "**/benches/**"])
    template: str = TEMPLATE
    template_ref: str = "main"
    github_owner: str = "dajy5120"


def _run(cmd: list[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise OnboardError(f"{' '.join(cmd[:3])} … failed:\n{(proc.stderr or proc.stdout).strip()[-800:]}")
    return proc.stdout


def create(spec: NewProject, log=print) -> Path:
    d = spec.directory
    if d.exists() and any(d.iterdir()):
        raise OnboardError(f"{d} already exists and is not empty")
    log(f"clone     {spec.upstream}")
    _run(["git", "clone", "-q", "--no-checkout", spec.upstream, str(d)])
    gitutil.git(d, "remote", "rename", "origin", "upstream")
    commit = gitutil.try_rev_parse(d, spec.anchor)
    if not commit:
        raise OnboardError(f"anchor {spec.anchor!r} not found upstream")
    gitutil.git(d, "switch", "-q", "-c", "study/zh-CN", commit)
    local = [b for b in gitutil.git(d, "branch", "--format=%(refname:short)").split() if b != "study/zh-CN"]
    for b in local:  # the clone's default branch is not part of the model
        gitutil.git(d, "branch", "-q", "-D", b)

    log(f"template  {spec.template}@{spec.template_ref}")
    answers = {
        "project_id": spec.id,
        "project_name": spec.name,
        "upstream_url": spec.upstream,
        "upstream_branch": spec.branch,
        "license": spec.license,
        "terminology": spec.terminology,
        "scope_include": spec.include,
        "scope_exclude": spec.exclude,
        "github_owner": spec.github_owner,
    }
    data_args = [a for k, v in answers.items() for a in ("-d", f"{k}={json.dumps(v) if isinstance(v, list) else v}")]
    _run(["uvx", "copier", "copy", "--trust", "--defaults", "--vcs-ref", spec.template_ref, *data_args, spec.template, str(d)])

    # anchor + mirror branch + CLAUDE.md wiring (same as `osca init`)
    write_sync(d, {"anchor": {"upstream_commit": commit, "upstream_ref": spec.anchor,
                              "synced_at": date.today().isoformat()}, "history": []})
    gitutil.git(d, "branch", f"mirror/{spec.branch}", commit)
    claude = d / CLAUDE_MD
    if claude.exists():
        text = claude.read_text(encoding="utf-8")
        if CLAUDE_IMPORT not in text.splitlines():
            claude.write_text(f"{text}{'' if text.endswith(chr(10)) else chr(10)}\n{CLAUDE_IMPORT}\n", encoding="utf-8")
    else:
        claude.write_text(f"{CLAUDE_IMPORT}\n", encoding="utf-8")

    # force-add: upstream .gitignore may hide CLAUDE.md / .claude / .osca files
    gitutil.git(d, "add", "-A")
    gitutil.git(d, "add", "-f", ".osca", "osca", ".claude", CLAUDE_MD, ".github/workflows")
    gitutil.git(d, "commit", "-q", "-m", f"osca: bootstrap study branch at upstream {spec.anchor}\n\n"
                f"Generated from the opensource-code-atlas study-repo template.\nAnchor: {spec.anchor} ({commit})")
    load_project(d)  # sanity check
    log(f"study     {d} (study/zh-CN @ {spec.anchor}, mirror/{spec.branch})")
    return d


def registry_entry(spec: NewProject, categories: list[str], description: str, languages: list[str]) -> str:
    entry = {
        "id": spec.id,
        "name": spec.name,
        "description": description,
        "repository": {
            "upstream": re.sub(r"\.git$", "", spec.upstream),
            "study": f"https://github.com/{spec.github_owner}/osca-{spec.id}",
        },
        "license": spec.license,
        "languages": {"primary": languages[0], "secondary": languages[1:]},
        "categories": categories,
        "terminology": spec.terminology,
        "onboarded": date.today().isoformat(),
    }
    return yaml.safe_dump(entry, sort_keys=False, allow_unicode=True)


def publish(root: Path, repo: str, public: bool, description: str, log=print) -> None:
    """Create the GitHub repo with Actions off, push, disable upstream workflows, re-enable Actions."""
    project = load_project(root)
    _run(["gh", "repo", "create", repo, "--public" if public else "--private", "--description", description])
    log(f"created   https://github.com/{repo}")
    _run(["gh", "api", "-X", "PUT", f"repos/{repo}/actions/permissions", "-F", "enabled=false"])
    if "origin" not in gitutil.git(root, "remote").split():
        gitutil.git(root, "remote", "add", "origin", f"git@github.com:{repo}.git")
    gitutil.git(root, "push", "-q", "-u", "origin", project.study_branch, project.mirror_branch)
    log(f"pushed    {project.study_branch}, {project.mirror_branch}")
    _run(["gh", "api", "-X", "PATCH", f"repos/{repo}", "-f", f"default_branch={project.study_branch}",
          "-F", "allow_merge_commit=true"])
    flows = json.loads(_run(["gh", "workflow", "list", "--all", "--repo", repo, "--json", "id,path,state"]))
    n = 0
    for wf in flows:
        if not Path(wf["path"]).name.startswith("osca-") and wf["state"] == "active":
            _run(["gh", "workflow", "disable", str(wf["id"]), "--repo", repo])
            n += 1
    log(f"disabled  {n} upstream workflow(s)")
    _run(["gh", "api", "-X", "PUT", f"repos/{repo}/actions/permissions", "-F", "enabled=true", "-f", "allowed_actions=all"])
    # reading site (osca-site workflow) is published with GitHub Pages
    subprocess.run(["gh", "api", "-X", "POST", f"repos/{repo}/pages", "-f", "build_type=workflow"], capture_output=True)
    log(f"pages     https://{repo.split('/')[0]}.github.io/{repo.split('/')[1]}/")
    # the osca-sync workflow opens PRs with the workflow token
    _run(["gh", "api", "-X", "PUT", f"repos/{repo}/actions/permissions/workflow",
          "-f", "default_workflow_permissions=read", "-F", "can_approve_pull_request_reviews=true"])
    log("actions   re-enabled (osca-verify only)")
