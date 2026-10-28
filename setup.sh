#!/bin/sh
# Creates project/, a git repository with two branches, `a` and `b`, that differ in every file
# and in biome.json. Switching between them makes the file watcher receive a
# burst of thousands of events while Biome reloads its configuration.
set -e
N=${1:-5000}
rm -rf project && mkdir project && cd project
mkdir -p src
for i in $(seq 1 $N); do
  d=src/m$((i / 100)); mkdir -p $d
  prev=$((i - 1))
  {
    [ $i -gt 1 ] && echo "import { f$prev } from '../m$((prev / 100))/f$prev';"
    echo "export function f$i(x: number): number {"
    echo "  return x + $i;"
    echo "}"
  } > $d/f$i.ts
done
cat > biome.json <<'JSON'
{
  "root": true,
  "vcs": { "enabled": true, "clientKind": "git", "useIgnoreFile": true },
  "linter": { "rules": { "preset": "recommended", "suspicious": { "noImportCycles": "error" } } }
}
JSON
git init -q -b a
git add -A && git commit -qm "branch a"
git checkout -qb b
find src -name '*.ts' -exec sh -c 'echo "// changed" >> "$1"' _ {} \;
sed -i.bak 's/"error"/"warn"/' biome.json && rm biome.json.bak
git commit -qam "branch b"
git checkout -q a
echo "Created project/ with $N files on branches a and b."
