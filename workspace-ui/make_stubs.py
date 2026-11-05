# make_stubs.py — regenerate .stubs/npm files with better heuristics
from pathlib import Path
import os

repo_root = Path(".").resolve()

# packages to stub; extend if needed
packages = [
    "react",
    "lucide-react",
    "next",
    "next/font/google",
    "@vercel/analytics/next",
    "@base-ui/react/button",
    "class-variance-authority",
    "clsx",
]

# exported names to expose in stubs (add names you see unresolved)
export_names = [
    # hooks & runtime
    "useState", "useRef", "useCallback",
    # icons/components (capitalized)
    "Sparkles", "Maximize2", "Minimize2", "X", "Bot", "Send", "Check", "Copy", "Icon",
    "ButtonPrimitive", "Analytics", "Select",
    # other names observed
    "Waypoints", "ShieldCheck", "Loader2", "AlertTriangle", "FileCode2", "Clock", "LayoutGrid",
    "buttonVariants", "clsx",
]

stubs_root = repo_root / ".stubs" / "npm"
for pkg in packages:
    safe = pkg.replace("/", "__").replace("@", "")
    d = stubs_root / safe
    d.mkdir(parents=True, exist_ok=True)
    p = d / "index.ts"
    lines = []
    for n in sorted(set(export_names)):
        if not n:
            continue
        # heuristics: hooks (useX) => function, Components/icons (Uppercase) => function,
        # otherwise const placeholder.
        if n.startswith("use") and len(n) > 3 and n[3].isalpha():
            lines.append(f"export function {n}(..._args: any[]): any {{ return undefined; }}")
        elif n[0].isupper():
            lines.append(f"export function {n}(_props?: any): any {{ return null; }}")
        else:
            lines.append(f"export const {n}: any = undefined;")
    lines.append("\nexport default {} as any;")
    p.write_text("\n".join(lines), encoding="utf-8")
    print("wrote", p)
print("Done creating stubs under .stubs/npm. Now run: python check_repo.py")