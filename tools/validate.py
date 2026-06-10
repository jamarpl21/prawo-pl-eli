#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lightweight, dependency-free validator for the prawo-pl-eli plugin (Claude Code + OpenAI Codex).

Checks both plugin manifests, both marketplace catalogs, the shared Agent Skills SKILL.md
frontmatter, and that the eli.py engine compiles. Run locally or in CI (no pip deps).
"""
import json
import re
import sys
import py_compile
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
errors = []
versions = {}  # source file -> declared version (all must match)


def load_json(rel):
    p = ROOT / rel
    if not p.exists():
        errors.append(f"{rel}: missing")
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        errors.append(f"{rel}: invalid JSON ({e})")
        return None


# Plugin manifests (Claude + Codex)
for rel in ("plugins/prawo-pl-eli/.claude-plugin/plugin.json", "plugins/prawo-pl-eli/.codex-plugin/plugin.json"):
    d = load_json(rel)
    if isinstance(d, dict):
        for k in ("name", "description", "version"):
            if k not in d:
                errors.append(f"{rel}: missing field '{k}'")
        if d.get("name") != "prawo-pl-eli":
            errors.append(f"{rel}: name should be 'prawo-pl-eli'")
        if "version" in d:
            versions[rel] = d["version"]

# Marketplace catalogs (Claude + Codex)
for rel in (".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json"):
    d = load_json(rel)
    if isinstance(d, dict):
        if "name" not in d:
            errors.append(f"{rel}: missing 'name'")
        if not isinstance(d.get("plugins"), list) or not d.get("plugins"):
            errors.append(f"{rel}: 'plugins' must be a non-empty list")
        for entry in d.get("plugins") or []:
            if isinstance(entry, dict) and entry.get("name") == "prawo-pl-eli":
                if "version" not in entry:
                    errors.append(f"{rel}: plugin entry 'prawo-pl-eli' missing 'version'")
                else:
                    versions[rel] = entry["version"]

# Shared SKILL.md frontmatter (open Agent Skills standard)
skill = ROOT / "plugins/prawo-pl-eli/skills/prawo-pl-eli/SKILL.md"
if not skill.exists():
    errors.append("skills/prawo-pl-eli/SKILL.md: missing")
else:
    text = skill.read_text(encoding="utf-8")
    if not text.startswith("---"):
        errors.append("SKILL.md: no YAML frontmatter")
    else:
        parts = text.split("---", 2)
        fm = parts[1] if len(parts) >= 3 else ""
        for k in ("name:", "description:", "version:"):
            if k not in fm:
                errors.append(f"SKILL.md: frontmatter missing '{k}'")
        m = re.search(r"^version:\s*(\S+)\s*$", fm, re.M)
        if m:
            versions["SKILL.md"] = m.group(1).strip("'\"")

# Engine compiles
engine = ROOT / "plugins/prawo-pl-eli/skills/prawo-pl-eli/scripts/eli.py"
if not engine.exists():
    errors.append("skills/prawo-pl-eli/scripts/eli.py: missing")
else:
    try:
        py_compile.compile(str(engine), doraise=True)
    except py_compile.PyCompileError as e:
        errors.append(f"eli.py: syntax error ({e})")
    m = re.search(r"^__version__\s*=\s*[\"']([^\"']+)[\"']", engine.read_text(encoding="utf-8"), re.M)
    if not m:
        errors.append("eli.py: missing __version__")
    else:
        versions["eli.py"] = m.group(1)

# Single version everywhere (manifests, marketplaces, SKILL.md, engine)
if len(set(versions.values())) > 1:
    listing = ", ".join(f"{src}={v}" for src, v in sorted(versions.items()))
    errors.append(f"version mismatch: {listing}")

if errors:
    print("VALIDATION FAILED:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print(f"OK: Claude + Codex manifests, SKILL.md frontmatter, and eli.py all valid (version {next(iter(versions.values()))}).")
