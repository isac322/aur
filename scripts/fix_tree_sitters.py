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

    if pkg_dir != "python-tree-sitter-php":
        content = content.replace('cd "${_name}-${pkgver}"', 'cd "${_name//-/_}-${pkgver}"')

    if 'install -Dm644 LICENSE' not in content and 'install -Dm644' not in content:
        content = content.replace(
            'python -m installer --destdir="${pkgdir}" dist/*.whl',
            'python -m installer --destdir="${pkgdir}" dist/*.whl\n    install -Dm644 LICENSE -t "${pkgdir}/usr/share/licenses/${pkgname}/"'
        )
        content = content.replace(
            'install -Dm644 LICENSE',
            'install -Dm644 LICENSE* -t'
        )

    with open(pkgbuild_path, "w") as f:
        f.write(content)
        
print("Fixed tree-sitter PKGBUILDs")
