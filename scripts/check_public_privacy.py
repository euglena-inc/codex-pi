#!/usr/bin/env python3
"""Check tracked content or reachable history; never print matched values."""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


PATTERNS = {
    "provider credential": rb"(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}|github_pat_[A-Za-z0-9_]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|xox[baprs]-[A-Za-z0-9-]{15,})",
    "private key": rb"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----",
    "JWT": rb"eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}",
    "credential URL": rb"[a-z][a-z0-9+.-]*://[^\s/<>\"']+:[^\s/<>\"']+@",
    "personal home path": rb"/Users/(?!example(?:/|\b)|USER(?:/|\b)|<)[A-Za-z0-9._-]+/|/Volumes/[^\s\"']+/MacStorage/home/",
    "real desktop thread ID": rb"01[a-f0-9]{6}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}",
    "email address": rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
}
ALLOWED_EMAIL_DOMAINS = {b"example.com", b"example.org", b"example.invalid", b"local.invalid", b"users.noreply.github.com"}


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def inspect(label: str, data: bytes) -> list[str]:
    issues = []
    for kind, pattern in PATTERNS.items():
        matches = list(re.finditer(pattern, data))
        if kind == "email address":
            matches = [m for m in matches if m.group().split(b"@")[-1].lower() not in ALLOWED_EMAIL_DOMAINS
                       and not m.group().split(b"@")[-1].lower().endswith(b".invalid")]
        if matches:
            issues.append(f"{label}: {kind} ({len(matches)} matches)")
    if re.fullmatch(r"docs/validation/.*\.(json|log)", label):
        issues.append(f"{label}: private raw validation file")
    if re.search(r"(^|/)(\.env($|\.)|auth\.json$|id_rsa$|id_ed25519$)", label) and not label.endswith(".env.example"):
        issues.append(f"{label}: credential file name")
    return issues


def history_blobs():
    rows = git("rev-list", "--objects", "--all").splitlines()
    names = {}
    for row in rows:
        fields = row.split(b" ", 1)
        names[fields[0]] = fields[1].decode("utf-8", "replace") if len(fields) > 1 else "<git object>"
    proc = subprocess.Popen(["git", "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    raw, _ = proc.communicate(b"\n".join(names) + b"\n")
    if proc.returncode:
        raise RuntimeError("git object read failed")
    offset = 0
    while offset < len(raw):
        end = raw.index(b"\n", offset)
        oid, kind, size = raw[offset:end].split()
        start = end + 1
        length = int(size)
        if kind == b"blob":
            yield names[oid], raw[start:start + length]
        offset = start + length + 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    root = Path(git("rev-parse", "--show-toplevel").decode().strip())
    issues = []
    count = 0
    if args.history:
        records = history_blobs()
    else:
        records = ((p.decode(), (root / p.decode()).read_bytes()) for p in git("ls-files", "-z").split(b"\0") if p and (root / p.decode()).is_file())
    for label, data in records:
        count += 1
        issues.extend(inspect(label, data))
    if args.history:
        issues.extend(inspect("Git author/committer metadata", git("log", "--all", "--format=%an <%ae> | %cn <%ce>")))
        issues.extend(inspect("Git commit messages", git("log", "--all", "--format=%B")))
    for issue in sorted(set(issues)):
        print(issue)
    print(f"{'FAIL' if issues else 'PASS'}: {count} {'historical blobs' if args.history else 'tracked files'} checked; matched values withheld")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
