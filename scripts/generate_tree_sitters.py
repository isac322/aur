import urllib.request
import json
import hashlib
import os
import subprocess

packages = [
    "tree-sitter-go",
    "tree-sitter-java",
    "tree-sitter-kotlin",
    "tree-sitter-scala",
    "tree-sitter-php",
    "tree-sitter-swift",
    "tree-sitter-lua",
    "tree-sitter-zig",
    "tree-sitter-powershell",
    "tree-sitter-elixir",
    "tree-sitter-objc",
    "tree-sitter-julia",
    "tree-sitter-verilog"
]

base_dir = "/home/bhyoo/projects/aur"

for pkg in packages:
    aur_name = f"python-{pkg}"
    pkg_dir = os.path.join(base_dir, aur_name)
    os.makedirs(pkg_dir, exist_ok=True)
    
    url = f"https://pypi.org/pypi/{pkg}/json"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
    
    version = data["info"]["version"]
    desc = data["info"]["summary"].replace('"', '\\"')
    pypi_url = data["info"]["home_page"] or data["info"]["project_url"]
    
    sdist_url = None
    for release in data["releases"][version]:
        if release["packagetype"] == "sdist":
            sdist_url = release["url"]
            break
            
    if not sdist_url:
        print(f"No sdist found for {pkg}")
        continue
        
    print(f"Downloading {pkg} v{version} to hash...")
    req = urllib.request.Request(sdist_url)
    with urllib.request.urlopen(req) as response:
        tar_data = response.read()
        sha256 = hashlib.sha256(tar_data).hexdigest()
        
    pypi_name_underscore = pkg.replace('-', '_')
    
    pkgbuild = f"""# Maintainer: Your Name <your.email@example.com>

_name={pkg}
pkgname={aur_name}
pkgver={version}
pkgrel=1
pkgdesc="{desc}"
arch=('x86_64' 'aarch64')
url="{pypi_url}"
license=('MIT')
depends=('python' 'python-tree-sitter')
makedepends=(
    'python-build'
    'python-installer'
    'python-wheel'
    'python-setuptools'
)
source=("${{pkgname}}-${{pkgver}}.tar.gz::{sdist_url}")
sha256sums=('{sha256}')

build() {{
    cd "${{_name}}-${{pkgver}}"
    python -m build --wheel --no-isolation
}}

package() {{
    cd "${{_name}}-${{pkgver}}"
    python -m installer --destdir="${{pkgdir}}" dist/*.whl
}}
"""
    
    with open(os.path.join(pkg_dir, "PKGBUILD"), "w") as f:
        f.write(pkgbuild)
        
    print(f"Generated {aur_name} PKGBUILD.")
    
    subprocess.run(["makepkg", "--printsrcinfo"], cwd=pkg_dir, stdout=open(os.path.join(pkg_dir, ".SRCINFO"), "w"))
