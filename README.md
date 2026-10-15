# twinsweep

**Find duplicate files by content and clean them up safely.**

```bash
pipx install twinsweep
```

Duplicate photos from phone imports, `report (1).pdf` in Downloads, a backup
folder that is a near-copy of your documents: duplicates pile up on every
machine. twinsweep finds files with identical content, regardless of name or
location, shows how much space they waste, and helps you remove them without
risking the original.

```
$ twinsweep ~/Pictures ~/Backup --prefer ~/Pictures
Scanned 48211 files.

[1] 3 copies x 2.9 GiB  (reclaim 5.7 GiB)
   keep  ~/Pictures/2024/trip.mov
   dupe  ~/Backup/old-phone/IMG_4412.MOV
   dupe  ~/Downloads/trip (1).mov
...
Found 912 duplicate groups, 1404 redundant files, 38.2 GiB reclaimable.
```

## Why use it

- **Safe by default.** Without `--action`, twinsweep only reports; it never
  changes anything. Destructive actions ask for confirmation, and `--dry-run`
  previews every one. Right before touching a file, twinsweep checks that both
  the duplicate and the copy being kept still exist and are unchanged since
  the scan. Hardlink and symlink replacements go through a temporary name and
  an atomic rename, so a path is never left missing.
- **You decide which copy survives.** Pick a rule (`oldest`, `newest`,
  `shortest-path`, `longest-path`, `first`) and pin folders with `--prefer`.
  The results are deterministic: the same input always picks the same keeper.
- **Fast on big libraries.** twinsweep uses the staged pipeline proven by
  fdupes, jdupes and rmlint (size, then a quick head-and-tail hash, then a full
  hash), so most files are never fully read. Hashing runs in parallel, and a
  SQLite hash cache makes repeat scans of the same library almost instant.
- **No dependencies.** Pure Python 3.9+ standard library. Runs on macOS,
  Linux and Windows.
- **Readable or scriptable output.** A shareable HTML report, JSON and CSV
  output, a stable exit status, and a small Python API.

## Install

You need Python 3.9 or newer (`python3 --version`). twinsweep is on
[PyPI](https://pypi.org/project/twinsweep/). Installing it with
[pipx](https://pipx.pypa.io/) is recommended, because it keeps the tool in its
own environment and puts the `twinsweep` command on your PATH:

```bash
pipx install twinsweep            # or: pip install twinsweep
twinsweep --help
```

If you don't have pipx yet, install it with `brew install pipx` (macOS) or
`python3 -m pip install --user pipx`, then run `pipx ensurepath` and open a
new terminal.

**Optional: Trash support on Linux and Windows.** On macOS, `--action trash`
works out of the box. On Linux and Windows, install the extra:

```bash
pipx install 'twinsweep[trash]'
```

**Upgrade or remove:**

```bash
pipx upgrade twinsweep
pipx uninstall twinsweep
```

**From source.** To run the latest code without installing:

```bash
git clone https://github.com/tigranmk/twinsweep.git
cd twinsweep
python3 -m twinsweep ~/Downloads   # run in place, no install
pipx install .                     # or install the command from this checkout
```

## Usage

```bash
twinsweep PATH [PATH ...] [options]
```

### Common recipes

| Goal | Command |
| --- | --- |
| See what's duplicated (changes nothing) | `twinsweep ~/Pictures` |
| Only look at files of 1 MiB or more | `twinsweep ~ --min-size 1M` |
| Keep copies in `Photos`, preview trashing the rest | `twinsweep ~/Photos ~/Backup --prefer ~/Photos --action trash --dry-run` |
| Actually move the rest to the Trash | `twinsweep ~/Photos ~/Backup --prefer ~/Photos --action trash` |
| Quarantine duplicates in a folder for review | `twinsweep ~/Docs --action move --move-to ~/dupes-review` |
| Free space but keep every path working | `twinsweep ~/Music --action hardlink --yes` |
| Browsable report to open in a browser | `twinsweep ~/Pictures --format html -o report.html && open report.html` |
| Export a spreadsheet of duplicates | `twinsweep ~/Shared --format csv -o dupes.csv` |
| Feed results to another tool | `twinsweep ~/data --format json \| jq '.summary'` |
| Skip extra folders | `twinsweep ~/code -x '*.log' -x '*/build/*'` |
| Also scan `node_modules`, virtualenvs and similar | `twinsweep ~/code --include-managed` |

### Actions (`--action`)

| Action | What happens to each duplicate | Reversible? |
| --- | --- | --- |
| `report` *(default)* | nothing | n/a |
| `trash` | moved to the Trash/Recycle Bin | yes: on macOS, Finder's **Put Back** restores it |
| `move` | moved under `--move-to DIR`, keeping its folder layout | yes, move it back |
| `hardlink` | replaced by a hard link to the kept copy (same filesystem only) | the paths still work; contents are now shared |
| `symlink` | replaced by a symbolic link to the kept copy | the paths still work |
| `delete` | permanently removed | **no** |

Destructive actions prompt `Continue? [y/N]`. Add `--yes` to skip the prompt
(for scripts) or `--dry-run` to see exactly what would happen. When stdin
isn't a terminal and `--yes` isn't given, twinsweep refuses and changes
nothing.

**How `trash` works on macOS.** twinsweep asks Finder to trash the files,
sending them in batches, so each drive uses its own Trash and **Put Back**
works exactly as if you had deleted them in Finder. The first time, macOS may
ask whether your terminal may control Finder: click **OK**. If you decline,
or Finder isn't available (for example over SSH), twinsweep falls back to the
optional Send2Trash package, then to moving files into `~/.Trash`, and reports
which method it used for each file. To change your answer later, go to
System Settings → Privacy & Security → Automation. Set
`TWINSWEEP_NO_FINDER=1` to skip Finder entirely.

> **Tip:** a hard link means both paths share the *same* data. Editing one
> edits the "other". That suits read-only archives (photos, music, installers)
> but not files you edit independently.

### Choosing the copy to keep

`--keep RULE` (default `oldest`):

- `oldest` / `newest`: by modification time
- `shortest-path` / `longest-path`: e.g. prefer `~/Photos/a.jpg` over `~/Photos/old/backup/2019/a.jpg`
- `first`: the copy under the path listed first on the command line

`--prefer DIR` (repeatable, in priority order) beats the rule: if any copy is
inside a preferred directory, that copy is kept. Ties are always broken by path
name, so the results are reproducible.

### All options

```
filtering:
  --min-size SIZE       ignore files smaller than SIZE (default: 1, i.e. skip empty files)
  --max-size SIZE       ignore files larger than SIZE
  -x, --exclude GLOB    skip files/dirs matching GLOB (name or full path); repeatable
  --hidden              include hidden files and directories
  --follow-symlinks     follow symbolic links
  --include-managed     also scan tool-managed folders (node_modules, virtualenvs, ...)

choosing which copy to keep:
  --keep {oldest,newest,shortest-path,longest-path,first}
  --prefer DIR          always keep copies inside DIR when possible; repeatable

actions:
  --action {report,delete,trash,move,hardlink,symlink}
  --move-to DIR         destination for --action move
  -n, --dry-run         show what would happen, change nothing
  -y, --yes             do not ask for confirmation

output:
  --format {text,json,csv,html}
  -o, --output FILE     write the report to FILE instead of stdout
  --limit N             text format: show only the N biggest groups
  -q, --quiet           no progress output

performance and accuracy:
  --paranoid            confirm matches byte-by-byte after hashing
  -j, --workers N       parallel hashing threads
  --no-cache            do not read or write the hash cache
  --cache-path FILE     hash cache location (default: ~/.cache/twinsweep/hashes.sqlite3)
```

### Output formats (`--format`)

| Format | Best for |
| --- | --- |
| `text` *(default)* | reading in the terminal |
| `html` | browsing and sharing: a single offline page with totals, a search filter, collapsible groups, modification dates and clickable file links. Light and dark themes follow your system setting. |
| `json` | scripts and other tools (`jq`, Python, and so on) |
| `csv` | Excel, Numbers, Google Sheets |

Use `-o FILE` to save any format to a file. The HTML page has no external
assets, so you can email or archive it. File names are HTML-escaped, so a
strangely named file can't inject anything into the page.

Sizes accept binary units: `500`, `10k`, `1.5M`, `2G`, `2GiB`.

**Exit status:** `0` on success, `1` if the action was aborted or any file was
skipped or failed, `2` for usage errors.

### Behaviour worth knowing

- **Tool-managed folders are skipped by default.** npm, pip, CocoaPods and
  similar tools often store byte-identical copies on purpose. npm, for
  example, nests a copy of `source-map` inside every package that depends on
  it. Deleting any of those copies breaks the project, so reporting them only
  buries the duplicates you can act on. Skipped by name: `node_modules`,
  `bower_components`, `jspm_packages`, `__pycache__`, `site-packages`, `Pods`,
  `DerivedData`, and, with `--hidden`, `.git`, `.venv`, `.gradle`, `.next`,
  `.tox` and other tool caches. Python virtualenvs are also detected by their
  `pyvenv.cfg` marker, whatever the folder is called. Ambiguous names such as
  `build`, `dist` or `vendor` are *not* skipped, because they are often your
  own folders. twinsweep reports how many folders it skipped. Pass
  `--include-managed` to scan them anyway, or give such a folder as the path
  to scan it directly.
- Hidden files and folders (`.git`, `.DS_Store`, and so on) and empty files
  are skipped unless you pass `--hidden` or `--min-size 0`.
- Symlinks are not followed by default, so a link is never reported as a
  duplicate of its target.
- Existing hard links count as one file: they already take no extra space.
  Overlapping paths such as `~/a ~/a/b` never make a file a duplicate of
  itself.
- The cache is keyed by path, size and modification time. If any of these
  changes, the file is hashed again, so a stale cache can't give a wrong
  answer. Delete the cache file at any time to reset it.

## How it works

```
all files ──► group by size ──► quick hash (first+last 64 KiB) ──► full BLAKE2b hash ──► [--paranoid byte compare]
              (free: from stat)  (cheap, removes most candidates)    (only for survivors)
```

Each stage runs only on files that still have a possible twin, and any group
that shrinks to one file is dropped. Files of 128 KiB or less are hashed
completely in the quick stage, so they are never read twice. BLAKE2b (160-bit)
is in the Python standard library, is faster than SHA-256, and makes
accidental collisions vanishingly unlikely. Use `--paranoid` if you want
byte-level proof anyway.

### Project layout

```
twinsweep/
  scanner.py   walk directories, apply filters, collapse hard links
  hashing.py   partial/full BLAKE2b hashing and byte comparison
  cache.py     SQLite hash cache
  finder.py    the staged duplicate-detection pipeline
  selector.py  rules for choosing which copy to keep
  actions.py   delete/trash/move/hardlink/symlink, with pre-action checks
  report.py    text, JSON and CSV output
  html_report.py  self-contained HTML report
  cli.py       argument parsing and orchestration
tests/         unittest suite (stdlib only)
```

### Python API

```python
from twinsweep import scan, find_duplicates
from twinsweep.selector import choose_keeper

for group in find_duplicates(scan(["~/Pictures"], min_size=1024)):
    keep, dupes = choose_keeper(group, rule="oldest")
    print(keep.path, "<-", [d.path for d in dupes], group.wasted_bytes)
```

## Development

```bash
python3 -m unittest discover -s tests -v      # or: pytest
```

The tests build throwaway directory trees in a temp folder and cover the
scanner filters, hard-link handling, the hashing stages (including files that
differ only in the middle), cache invalidation, every keep rule, every action,
the changed-since-scan guards, and the CLI's confirmation and output formats.

## Credits and prior art

twinsweep is an original implementation (no code was copied), but its design
stands on the shoulders of these open-source projects:

| Project | License | What we learned from it |
| --- | --- | --- |
| [fdupes](https://github.com/adrianlopezroche/fdupes) | MIT | The classic size, then hash, then byte-compare pipeline; the idea of a byte-for-byte check |
| [jdupes](https://codeberg.org/jbruchon/jdupes) | MIT | Partial-hash pre-filtering; parameter-order keep rule (`--keep first` ≈ `-O`); hard-link actions |
| [rmlint](https://github.com/sahib/rmlint) | GPL-3.0 | Sampling both the head *and* tail of files; "originals" directories (`--prefer`); JSON/CSV output; dry-run-first workflow |
| [czkawka](https://github.com/qarmin/czkawka) | MIT | Persistent hash cache keyed by size and mtime for fast re-scans |
| [Send2Trash](https://github.com/arsenetar/send2trash) | BSD-3-Clause | Optional dependency used for cross-platform `--action trash` |

Built entirely on the Python standard library (PSF License): `hashlib`
(BLAKE2), `sqlite3`, `concurrent.futures`, `argparse`.

## License

[MIT](https://github.com/tigranmk/twinsweep/blob/main/LICENSE)
