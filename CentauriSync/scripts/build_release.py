# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations
import argparse
import ast
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
PLUGIN = "centauri_canvas_sync.py"
SMOKE = "centaurisync_test.py"
LICENSE_ID = "AGPL-3.0-only"
LICENSE_SHA256 = "57c8ff33c9c0cfc3ef00e650a1cc910d7ee479a8bc509f6c9209a7c2a11399d6"
SOURCES = {
    PLUGIN: ROOT / "centauri_canvas_sync" / PLUGIN,
    "filament_map.example.json": ROOT / "centauri_canvas_sync" / "filament_map.example.json",
    "install.ps1": ROOT / "install.ps1",
    "README.md": ROOT / "README.md",
    "CHANGELOG.md": ROOT / "CHANGELOG.md",
    "TEST-PLAN.md": ROOT / "TEST-PLAN.md",
    "LICENSE": REPO / "LICENSE",
    "NOTICE.md": REPO / "NOTICE.md",
}

def sha(data):
    return hashlib.sha256(data).hexdigest()


def source_bytes(path):
    # Stable on Windows and across checkout autocrlf policies.
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Expected a regular source file: {path.name}")
    return path.read_text(encoding="utf-8").encode("utf-8")


def constants(path):
    text = source_bytes(path).decode("utf-8")
    block = text.split("# /// script\n",1)[1].split("# ///",1)[0]
    meta = tomllib.loads("\n".join(line[2:] for line in block.splitlines()))
    vals = {n.targets[0].id: n.value.value for n in ast.parse(text).body
            if isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name)
            and isinstance(n.value,ast.Constant)}
    if f"# SPDX-License-Identifier: {LICENSE_ID}" not in text:
        raise ValueError("Source SPDX notice missing")
    compile(text,str(path),"exec")
    return meta["tool"]["orcaslicer"]["plugin"], vals


def build(out, target="generic"):
    if target not in {"generic", "windows-arm64"}:
        raise ValueError(f"Unknown target: {target}")
    main, vals = constants(SOURCES[PLUGIN])
    smoke_path = ROOT / "tests" / SMOKE
    smoke, fixture = constants(smoke_path)
    version, release_date = vals["PLUGIN_VERSION"], vals["RELEASE_DATE"]
    when = date.fromisoformat(release_date)
    status = vals["RELEASE_STATUS"]
    required = (
        re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version),
        status == "prerelease",
        main["version"] == smoke["version"] == fixture["VERSION"] == version,
        release_date == fixture["RELEASE_DATE"],
        main["id"] == "centauri-canvas-sync", smoke["id"] == "centaurisync-ui-smoke-test",
        main["homepage"] == smoke["homepage"] == vals["AUTHOR_URL"] == "https://amitkuzi.github.io/",
        f"{version} prerelease – {release_date}" in source_bytes(SOURCES["CHANGELOG.md"]).decode("utf-8"),
        vals["AGENT_ID"] == "elegoo-cc-canvas",
    )
    if not all(required):
        raise ValueError("Release metadata, package identities or changelog disagree")
    files = {name: source_bytes(path) for name,path in SOURCES.items()}
    if files["LICENSE"] != SOURCES["LICENSE"].read_bytes() or sha(files["LICENSE"]) != LICENSE_SHA256:
        raise ValueError("AGPL text does not match the verified upstream file byte-for-byte")
    manifest = {"product": main["name"], "version": version, "release_status": status,
                "release_date": release_date, "author_url": vals["AUTHOR_URL"],
                "source_repository": "https://github.com/amitkuzi/OrcaPlugin",
                "license": LICENSE_ID, "physical_printer_validated": False,
                "orca_slicer_ui_validated": False, "plugin_id": main["id"],
                "printer_agent_id": vals["AGENT_ID"], "installable_filename": PLUGIN,
                "zip_payload_sha256": {name: sha(data) for name,data in sorted(files.items())}}
    suffix = "prerelease"
    if target == "windows-arm64":
        guide = "WINDOWS-ARM64.md"
        files[guide] = source_bytes(ROOT / guide)
        manifest.update({
            "target_platform": "windows-arm64",
            "architecture_specific_binary": False,
            "distribution_status": "validation-candidate",
            "native_arm64_verified": False,
            "zip_payload_sha256": {name: sha(data) for name, data in sorted(files.items())},
        })
        suffix = "windows-arm64-validation-candidate"
    files["release.json"] = (json.dumps(manifest,indent=2,sort_keys=True)+"\n").encode()
    out.mkdir(parents=True,exist_ok=True)
    package = out / f"CentauriSync-v{version}-{suffix}.zip"
    with zipfile.ZipFile(package,"w") as z:
        for name,data in sorted(files.items()):
            info=zipfile.ZipInfo(name,(when.year,when.month,when.day,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o100644 << 16
            z.writestr(info,data)
    with zipfile.ZipFile(package) as z:
        if z.testzip() is not None or len(z.namelist()) != len(files) or set(z.namelist()) != set(files):
            raise ValueError("Invalid production ZIP inventory")
        for name,data in files.items():
            if z.read(name) != data: raise ValueError(f"ZIP member mismatch: {name}")
    payloads = {
        PLUGIN: files[PLUGIN], SMOKE: source_bytes(smoke_path), package.name: package.read_bytes(),
        "release.json": files["release.json"], "LICENSE": files["LICENSE"], "NOTICE.md": files["NOTICE.md"],
    }
    for name,data in payloads.items():
        if name != package.name: (out/name).write_bytes(data)
    (out/"SHA256SUMS.txt").write_text("".join(f"{sha(data)}  {name}\n" for name,data in sorted(payloads.items())),
                                   encoding="ascii",newline="\n")
    return package

if __name__=="__main__":
    parser=argparse.ArgumentParser(description="Build and verify the approved CentauriSync prerelease assets, without publishing.")
    parser.add_argument("--out",type=Path)
    parser.add_argument("--target", choices=("generic", "windows-arm64"), default="generic")
    args = parser.parse_args()
    default_folder = "prerelease-0.2.0" if args.target == "generic" else "windows-arm64-candidate"
    print("Built + verified:", build(args.out or ROOT / "dist" / default_folder, args.target))
