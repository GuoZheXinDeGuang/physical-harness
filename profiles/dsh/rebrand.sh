#!/usr/bin/env bash
# physical-harness cockpit -- de-brand overlay for the MATERIALIZED dsh install.
#
# what this is:
#   dsh (@deepseek-ai/dsh) is run via `npx`, which materializes the package tree
#   under ~/.npm/_npx/<hash>/node_modules. its user-visible branding lives in a
#   handful of static assets and pre-built client bundles inside that tree. this
#   script patches THAT INSTALLED COPY in place so the console presents as the
#   lab's native "physical-harness" console instead of "DeepSeek Harness".
#
#   it is a DEPLOYMENT STEP, not a fork: dsh's source is never touched, no
#   package is renamed, and MIT/LICENSE/about attribution is left intact. only
#   the presentation chrome (tab title, sidebar wordmark + mark, hero glyph +
#   tagline, favicon, PWA manifest name, boot splash, runtime document.title)
#   is neutralized. model names/ids, the DeepSeek model adapter, and the
#   help/about prose that attributes the upstream project are deliberately left
#   alone (functional config + required attribution).
#
# re-apply story (READ THIS):
#   the patch lives on the npx cache, which is DEPLOY STATE, not repo state. it
#   is wiped/replaced whenever the dsh version changes or the npx cache is
#   cleared. therefore this script MUST be re-run after any dsh upgrade. it is
#   idempotent (safe to run any number of times) and scripts/cockpit runs it on
#   every launch, so operators never have to remember. if dsh changes a string
#   this script targets, it prints a "DRIFT" warning for that surface (upstream
#   moved -- update the patterns below) but still applies the rest.
#
# usage: profiles/dsh/rebrand.sh        (discovers the install automatically)
#        DSH_PKG=@deepseek-ai/dsh@X.Y.Z profiles/dsh/rebrand.sh   (cold cache)
set -o pipefail
set -u

# self-locate the repo so the workspace-seed step (end of file) knows which
# directory to register, without cockpit having to pass it in. this script lives
# at $REPO/profiles/dsh/rebrand.sh, so the repo root is three levels up.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO="$(cd "$HERE/../.." && pwd -P)"
export PH_WORKSPACE="${PH_WORKSPACE:-$REPO}"

# node is needed to syntax-check patched bundles (a broken client bundle would
# blank the brand slots and the whale FALLBACKS would render -- worse than not
# patching). source nvm so `node` is on PATH; degrade to skip-check if absent.
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
if [ -s "$NVM_DIR/nvm.sh" ]; then
  # shellcheck disable=SC1091
  . "$NVM_DIR/nvm.sh"
  nvm use 22 >/dev/null 2>&1 || true
fi

export DSH_PKG="${DSH_PKG:-@deepseek-ai/dsh@0.1.1-rc.2}"

python3 <<'PY'
import glob, os, re, subprocess, sys, shutil

HOME = os.path.expanduser("~")
PKG  = os.environ.get("DSH_PKG", "@deepseek-ai/dsh@0.1.1-rc.2")

def discover():
    pat = os.path.join(HOME, ".npm", "_npx", "*", "node_modules",
                       "@deepseek-ai", "dsh-web-frontend", "dist", "index.html")
    hits = glob.glob(pat)
    if not hits:
        return None
    # newest materialization wins
    hits.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    # node_modules = .../node_modules/@deepseek-ai/dsh-web-frontend/dist/index.html -> up 4
    dist = os.path.dirname(hits[0])
    nm = os.path.dirname(os.path.dirname(os.path.dirname(dist)))  # node_modules
    return nm

NM = discover()
if NM is None:
    # cold cache: force materialization without starting a server, then retry.
    print("rebrand: dsh not materialized yet; materializing %s ..." % PKG, file=sys.stderr)
    subprocess.run(["npx", "--yes", PKG, "--help"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    NM = discover()
if NM is None:
    print("rebrand: ERROR could not locate a materialized dsh install under "
          "~/.npm/_npx/*/node_modules (is npx/node available?)", file=sys.stderr)
    sys.exit(1)

print("rebrand: target install: %s" % NM)

drift = 0
node_ok = shutil.which("node") is not None
if not node_ok:
    print("rebrand: WARNING node not on PATH; skipping JS syntax validation", file=sys.stderr)

def p(*parts):
    return os.path.join(NM, "@deepseek-ai", *parts)

def find_asset(glob_rel):
    hits = glob.glob(os.path.join(NM, "@deepseek-ai", *glob_rel))
    return hits[0] if hits else None

def node_check_or_abort(path, s):
    """syntax-check `s` (content destined for `path`) as a script; abort on error.
    validates via a `.cjs` temp so node parses it as a plain script (these client
    bundles are script-style: `window.__ModuleLoader__.load({...})`, no top-level
    import/export). skipped if node is unavailable."""
    if not node_ok:
        return
    tmp = path + ".rebrandcheck.cjs"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(s)
    try:
        r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    finally:
        os.remove(tmp)
    if r.returncode != 0:
        print("  [ABORT] patched %s failed `node --check`:\n%s"
              % (os.path.basename(path), r.stderr), file=sys.stderr)
        sys.exit(2)

def patch_text(path, edits):
    """edits: list of (label, old, new). literal string swaps -- syntax-safe by
    construction (quoted-content in, quoted-content out), so no parse check."""
    global drift
    if path is None or not os.path.exists(path):
        print("  [skip] missing file for: %s" % (edits[0][0] if edits else path))
        drift += 1
        return
    with open(path, encoding="utf-8") as f:
        s = orig = f.read()
    for label, old, new in edits:
        if old in s:
            s = s.replace(old, new)
            print("  [patched] %s" % label)
        elif new in s:
            print("  [already] %s" % label)
        else:
            print("  [DRIFT] %s -- expected string not found (dsh version changed?)" % label)
            drift += 1
    if s != orig:
        with open(path, "w", encoding="utf-8") as f:
            f.write(s)

def patch_regex(path, label, pattern, repl, marker):
    """structural regex edit (jsx call swap) -- validate with node before commit."""
    global drift
    if path is None or not os.path.exists(path):
        print("  [skip] missing file for: %s" % label); drift += 1; return
    with open(path, encoding="utf-8") as f:
        s = orig = f.read()
    s2, n = re.subn(pattern, repl, s)
    if n:
        print("  [patched] %s (x%d)" % (label, n))
        s = s2
    elif marker in s:
        print("  [already] %s" % label)
    else:
        print("  [DRIFT] %s -- pattern not found (dsh version changed?)" % label)
        drift += 1
    if s != orig:
        node_check_or_abort(path, s)
        with open(path, "w", encoding="utf-8") as f:
            f.write(s)

TITLE = "physical-harness 控制台"

# 1) served HTML tab title (pre-React / no-JS / curl-visible)
print("index.html title:")
patch_text(p("dsh-web-frontend", "dist", "index.html"),
           [("tab title", "<title>DeepSeek Harness</title>", "<title>%s</title>" % TITLE)])

# 2) PWA manifest name (installed-app label)
print("manifest.webmanifest:")
patch_text(p("dsh-web-frontend", "dist", "manifest.webmanifest"),
           [("manifest name", '"name": "DeepSeek Harness"', '"name": "%s"' % TITLE),
            ("manifest short_name", '"short_name": "DSH"', '"short_name": "PH"')])

# 3) runtime document.title (React overrides the static <title> from this const)
print("renderer productTitle:")
patch_text(p("dsh-client-ui-renderer", "lib", "client.js"),
           [("runtime document.title",
             'const productTitle = "DeepSeek Harness"',
             'const productTitle = "%s"' % TITLE)])

# 4) hero headline + active-composer placeholder (native-feel copy). the
#    placeholder.hero string is what the composer shows in the blank
#    auto-selected session -- and thanks to the workspace seed (end of file) a
#    session IS open at boot, so this is the placeholder the operator actually
#    types over. leave the neutral "预览版/Preview" badge alone.
print("conversation hero headline + placeholder:")
patch_text(p("dsh-client-ui-conversation", "lib", "client.js"),
           [("hero headline zh", '"hero.headline": "探索未至之境"', '"hero.headline": "物理测试台"'),
            ("hero headline en", '"hero.headline": "Into the Unknown"', '"hero.headline": "physical-harness"'),
            ("hero placeholder zh", '"placeholder.hero": "描述你想要构建的内容"', '"placeholder.hero": "对物理测试台下达任务…"'),
            ("hero placeholder en", '"placeholder.hero": "Describe what you want to build"', '"placeholder.hero": "Command the physical-harness…"')])

# 5) boot splash wordmark (transient pre-React loading screen)
print("boot splash wordmark:")
asset = find_asset(["dsh-web-frontend", "dist", "assets", "index-*.js"])
patch_text(asset, [("boot splash", ',"HARNESS")', ',"physical-harness")')])

# 6) brand plugin occupants -> plain text marks. this is the single chokepoint
#    that fills sidebar.brand.mark, sidebar.brand.name and
#    conversation.hero.brand.mark; filling them keeps the whale FALLBACKS in the
#    sidebar/conversation bundles dormant.
print("brand plugin occupants:")
brand = p("dsh-client-ui-brand-official", "lib", "client.js")
MARK_REPL = ('react_jsx_runtime.jsx)("span", { className, style: { display: "inline-flex", '
             'alignItems: "center", justifyContent: "center", fontWeight: 800, '
             'fontSize: (typeof size === "number" ? size : 24) + "px", lineHeight: 1, '
             'letterSpacing: "-0.03em" }, children: "PH" })')
NAME_REPL = ('react_jsx_runtime.jsx)("span", { style: { fontWeight: 600, fontSize: "15px", '
             'letterSpacing: "0.06em" }, children: "physical-harness" })')
patch_regex(brand, "brand mark (whale -> PH)",
            r'react_jsx_runtime\.jsx\)\(\s*_deepseek_ai_dsh_client_ui_primitives\.FishLogo\s*,\s*\{[^{}]*\}\s*\)',
            MARK_REPL, marker='children: "PH"')
patch_regex(brand, "brand name (wordmark -> physical-harness)",
            r'react_jsx_runtime\.jsx\)\(\s*_deepseek_ai_dsh_client_ui_primitives\.BrandWordmark\s*,\s*\{[^{}]*\}\s*\)',
            NAME_REPL, marker='children: "physical-harness"')

# 7) favicon: whale -> neutral "PH" monogram (theme-aware, matches original box)
print("favicon.svg:")
fav = p("dsh-web-frontend", "dist", "favicon.svg")
FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" width="50" height="50" '
           'viewBox="0 0 50 50" fill="none">\n'
           '\t<style>@media (prefers-color-scheme: dark){ text{ fill:#fff; } }</style>\n'
           '\t<text x="25" y="34" text-anchor="middle" '
           'font-family="Inter, system-ui, -apple-system, sans-serif" '
           'font-size="24" font-weight="700" letter-spacing="-1" fill="#000">PH</text>\n'
           '</svg>\n')
if os.path.exists(fav):
    cur = open(fav, encoding="utf-8").read()
    if cur == FAVICON:
        print("  [already] favicon PH monogram")
    else:
        open(fav, "w", encoding="utf-8").write(FAVICON)
        print("  [patched] favicon PH monogram")
else:
    print("  [skip] favicon.svg missing"); drift += 1

print()
if drift:
    print("rebrand: DONE with %d DRIFT/skip warning(s) -- some surfaces may still "
          "show upstream branding; check the [DRIFT] lines above against the new "
          "dsh version." % drift)
else:
    print("rebrand: DONE -- all branding surfaces neutralized.")
# never fail the launch on drift: a partly-branded console still serves.
sys.exit(0)
PY

# --- workspace pre-selection --------------------------------------------------
# everything above patches the INSTALLED npx copy (presentation chrome). this
# last step writes USER DATA instead: one workspace record into $DSH_HOME so the
# console opens with the physical-harness workspace already selected and the
# composer live, rather than demanding a manual "选择工作区" pick first. the dsh
# client auto-connects the single most-recent workspace at boot and opens a
# blank session in it (startInitialSelection), so seeding one record is the
# whole unlock -- no client behaviour is patched. idempotent + schema-drift
# guarded; see profiles/dsh/seed_workspace.py (run it with --selftest to confirm
# the storage schema still matches after a dsh upgrade).
#
# MUST run before `dsh web` starts (cockpit runs this overlay pre-exec): the
# server holds the workspace unit open in memory and republishes the whole file
# on every mutation, so a seed written to a live server is overwritten.
echo "workspace seed:"
python3 "$HERE/seed_workspace.py" --workspace "$PH_WORKSPACE" --dsh-home "${DSH_HOME:-$HOME/.dsh}" \
  || echo "rebrand: WARNING workspace seed reported an issue; console still serves" >&2

exit 0
