import os

base_dir = "/home/bhyoo/projects/aur"

for pkg_dir in os.listdir(base_dir):
    if not pkg_dir.startswith("python-tree-sitter-"):
        continue
    
    pkgbuild_path = os.path.join(base_dir, pkg_dir, "PKGBUILD")
    if not os.path.exists(pkgbuild_path):
        continue
        
    with open(pkgbuild_path, "r") as f:
        content = f.read()

    content = content.replace('-t -t', '-t')

    with open(pkgbuild_path, "w") as f:
        f.write(content)
