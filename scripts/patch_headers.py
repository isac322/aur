import os
import glob
import subprocess

def patch_pkgbuild(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    if "prepare() {" in content and "curl -sLo src/tree_sitter/parser.h" in content:
        return


    if "${_name//-/_}-${pkgver}" in content:
        cd_dir = "${_name//-/_}-${pkgver}"
    else:
        cd_dir = "${_name}-${pkgver}"

    prepare_block = f"""
prepare() {{
    cd "{cd_dir}"
    if [ ! -f src/tree_sitter/parser.h ]; then
        mkdir -p src/tree_sitter
        curl -sLo src/tree_sitter/parser.h https://raw.githubusercontent.com/tree-sitter/tree-sitter/v0.22.6/lib/src/parser.h
        curl -sLo src/tree_sitter/alloc.h https://raw.githubusercontent.com/tree-sitter/tree-sitter/v0.22.6/lib/src/alloc.h
        curl -sLo src/tree_sitter/array.h https://raw.githubusercontent.com/tree-sitter/tree-sitter/v0.22.6/lib/src/array.h
    fi
}}
"""

    if "build() {" in content:
        content = content.replace("build() {", prepare_block + "\nbuild() {")
        
        with open(filepath, 'w') as f:
            f.write(content)
        
        print(f"Patched {filepath}")
        
        pkgdir = os.path.dirname(filepath)
        subprocess.run(["makepkg", "--printsrcinfo"], cwd=pkgdir, stdout=open(os.path.join(pkgdir, ".SRCINFO"), "w"))

for d in glob.glob("/home/bhyoo/projects/aur/python-tree-sitter-*/"):
    pkgbuild = os.path.join(d, "PKGBUILD")
    if os.path.exists(pkgbuild):
        patch_pkgbuild(pkgbuild)
