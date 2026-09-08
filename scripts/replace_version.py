import os
import glob
import subprocess

for d in glob.glob("/home/bhyoo/projects/aur/python-tree-sitter-*/"):
    pkgbuild = os.path.join(d, "PKGBUILD")
    if os.path.exists(pkgbuild):
        with open(pkgbuild, 'r') as f:
            content = f.read()
            
        if 'v0.23.0' in content:
            content = content.replace('v0.23.0', 'v0.22.6')
            with open(pkgbuild, 'w') as f:
                f.write(content)
            print(f"Patched {pkgbuild}")
            subprocess.run(["makepkg", "--printsrcinfo"], cwd=d, stdout=open(os.path.join(d, ".SRCINFO"), "w"))
