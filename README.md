# Biome file watcher deadlock (biomejs/biome#8240)

After a burst of file changes, such as a git branch switch, the Biome daemon
(`biome __run_server`) can get stuck at 100% CPU. From then on, its file
watcher no longer sees changes made outside the editor.

## Run

Needs macOS, Node.js, git and Python 3.

```sh
npm install
sh setup.sh            # creates project/ with 5,000 files on branches a and b
npx biome stop         # make sure no daemon is running
python3 repro.py 4     # 4 branch switches
```

To test another Biome build: `python3 repro.py 4 path/to/biome`.

`repro.py` starts `biome lsp-proxy` in `project/`, opens `src/m0/f2.ts`, and
switches branches with plain `git checkout`. Nothing is sent over LSP during
the switch. After each switch it measures the daemon's CPU use over 5 seconds
while it should be idle.

Before the first switch and after the last one, it checks whether the
watcher still sees file changes: it edits `src/m0/f1.ts` on disk so that it
imports `f2.ts`, and waits for `noImportCycles` to be reported on the open
`f2.ts`.

## Result

Biome 2.6.3, macOS, Apple Silicon:

```
before switching:      cpu=  0.0%  formatting request answered in 0.01s
                       watcher sees file changes: yes
after checkout b (#1): cpu= 99.4%  formatting request answered in 0.00s
after checkout a (#2): cpu= 99.6%  formatting request answered in 0.00s
                       watcher sees file changes: NO
```
