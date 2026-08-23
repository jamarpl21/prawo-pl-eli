#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lightweight, dependency-free validator for the gibek-skills plugins (Claude Code + OpenAI Codex).

Checks every plugin's manifests, both marketplace catalogs, each skill's SKILL.md frontmatter,
and that each engine compiles. All declared versions must be identical (lockstep). No pip deps.
"""
import json
import re
import sys
import py_compile
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGINS = [  # (plugin, [engines]) — plugin może mieć kilka silników (np. eli.py + edzienniki.py)
    ("prawo-pl-eli", ["eli.py"]),
    ("prawo-pl-edzienniki", ["edzienniki.py"]),
    ("prawo-eu-eurlex", ["eurlex.py"]),
    ("prawo-pl-saos", ["saos.py"]),
    ("prawo-pl-cbosa", ["cbosa.py"]),
    ("prawo-pl-uodo", ["uodo.py"]),
    ("prawo-pl-rejestr-umow", ["rejestrumow.py"]),
]
errors = []
versions = {}  # source file -> declared version (all must match: lockstep)


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


for plugin, engine_names in PLUGINS:
    # Plugin manifests (Claude + Codex)
    for rel in (f"plugins/{plugin}/.claude-plugin/plugin.json", f"plugins/{plugin}/.codex-plugin/plugin.json"):
        d = load_json(rel)
        if isinstance(d, dict):
            for k in ("name", "description", "version"):
                if k not in d:
                    errors.append(f"{rel}: missing field '{k}'")
            if d.get("name") != plugin:
                errors.append(f"{rel}: name should be '{plugin}'")
            if "version" in d:
                versions[rel] = d["version"]

    # Shared SKILL.md frontmatter (open Agent Skills standard)
    skill_rel = f"plugins/{plugin}/skills/{plugin}/SKILL.md"
    skill = ROOT / skill_rel
    if not skill.exists():
        errors.append(f"{skill_rel}: missing")
    else:
        text = skill.read_text(encoding="utf-8")
        if not text.startswith("---"):
            errors.append(f"{skill_rel}: no YAML frontmatter")
        else:
            parts = text.split("---", 2)
            fm = parts[1] if len(parts) >= 3 else ""
            for k in ("name:", "description:", "version:"):
                if k not in fm:
                    errors.append(f"{skill_rel}: frontmatter missing '{k}'")
            m = re.search(r"^version:\s*(\S+)\s*$", fm, re.M)
            if m:
                versions[skill_rel] = m.group(1).strip("'\"")
            m = re.search(r"description: >-\n((?:  .*\n)+)", text)
            if m:
                desc = " ".join(l.strip() for l in m.group(1).splitlines())
                if len(desc) > 1024:
                    errors.append(f"{skill_rel}: description too long ({len(desc)} > 1024 chars)")

    # Engines compile
    for engine_name in engine_names:
        engine_rel = f"plugins/{plugin}/skills/{plugin}/scripts/{engine_name}"
        engine = ROOT / engine_rel
        if not engine.exists():
            errors.append(f"{engine_rel}: missing")
            continue
        try:
            py_compile.compile(str(engine), doraise=True)
        except py_compile.PyCompileError as e:
            errors.append(f"{engine_name}: syntax error ({e})")
        m = re.search(r"^__version__\s*=\s*[\"']([^\"']+)[\"']", engine.read_text(encoding="utf-8"), re.M)
        if not m:
            errors.append(f"{engine_name}: missing __version__")
        else:
            versions[engine_rel] = m.group(1)

# Marketplace catalogs (Claude + Codex) — must list every plugin with a version
for rel in (".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json"):
    d = load_json(rel)
    if isinstance(d, dict):
        if "name" not in d:
            errors.append(f"{rel}: missing 'name'")
        if not isinstance(d.get("plugins"), list) or not d.get("plugins"):
            errors.append(f"{rel}: 'plugins' must be a non-empty list")
        listed = {e.get("name"): e for e in d.get("plugins") or [] if isinstance(e, dict)}
        for plugin, _ in PLUGINS:
            entry = listed.get(plugin)
            if not entry:
                errors.append(f"{rel}: missing plugin entry '{plugin}'")
            elif "version" not in entry:
                errors.append(f"{rel}: plugin entry '{plugin}' missing 'version'")
            else:
                versions[f"{rel}#{plugin}"] = entry["version"]

# Single version everywhere (manifests, marketplaces, SKILL.md, engines) — lockstep
if len(set(versions.values())) > 1:
    listing = ", ".join(f"{src}={v}" for src, v in sorted(versions.items()))
    errors.append(f"version mismatch: {listing}")

if errors:
    print("VALIDATION FAILED:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print(f"OK: {len(PLUGINS)} plugins — manifests, marketplaces, SKILL.md frontmatter and engines "
      f"all valid (version {next(iter(versions.values()))}).")
