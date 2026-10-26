"""Decrypt a Firefox profile's saved logins with the system NSS library.

    python3 -I firefox_logins.py <profile dir>          -> JSON array on stdout
    python3 -I firefox_logins.py <profile dir> --check  -> {"count", "needsPassword"}

Used by `ide-web-cred import firefox`. Output records are
{"origin", "username", "password", "realm"}; nothing is written to disk.
If the profile has a primary password, it is read from IDE_WEB_FF_PASSWORD
or prompted for on the terminal. NSS is opened read-only, so a running
Firefox is fine.
"""

import base64
import ctypes
import getpass
import json
import os
import sys


class SECItem(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint),
        ("data", ctypes.POINTER(ctypes.c_ubyte)),
        ("len", ctypes.c_uint),
    ]


def load_nss():
    for name in ("libnss3.so", "libnss3.so.0"):
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    sys.exit("libnss3 not found (install nss)")


def configure(nss):
    nss.NSS_Init.argtypes = [ctypes.c_char_p]
    nss.PK11_GetInternalKeySlot.restype = ctypes.c_void_p
    nss.PK11_NeedLogin.argtypes = [ctypes.c_void_p]
    nss.PK11_CheckUserPassword.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    nss.PK11_FreeSlot.argtypes = [ctypes.c_void_p]
    nss.PK11SDR_Decrypt.argtypes = [ctypes.POINTER(SECItem), ctypes.POINTER(SECItem), ctypes.c_void_p]
    nss.SECITEM_ZfreeItem.argtypes = [ctypes.POINTER(SECItem), ctypes.c_int]


def decrypt(nss, b64):
    raw = base64.b64decode(b64)
    buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    inp = SECItem(0, buf, len(raw))
    out = SECItem(0, None, 0)
    if nss.PK11SDR_Decrypt(ctypes.byref(inp), ctypes.byref(out), None) != 0:
        raise ValueError("decryption failed")
    try:
        return ctypes.string_at(out.data, out.len).decode("utf-8")
    finally:
        nss.SECITEM_ZfreeItem(ctypes.byref(out), 0)


def unlock(nss, slot):
    """Authenticate to the key slot; empty password first (no primary password)."""
    if nss.PK11_CheckUserPassword(slot, b"") == 0:
        return
    pw = os.environ.get("IDE_WEB_FF_PASSWORD")
    if pw is None:
        with open("/dev/tty", "r+") as tty:
            pw = getpass.getpass("Firefox primary password: ", stream=tty)
    if nss.PK11_CheckUserPassword(slot, pw.encode("utf-8")) != 0:
        sys.exit("wrong Firefox primary password")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    profile = os.path.abspath(sys.argv[1])
    check = "--check" in sys.argv[2:]
    with open(os.path.join(profile, "logins.json"), encoding="utf-8") as f:
        logins = json.load(f).get("logins", [])

    nss = load_nss()
    configure(nss)
    if nss.NSS_Init(("sql:" + profile).encode()) != 0:
        sys.exit(f"NSS could not open {profile}")
    slot = nss.PK11_GetInternalKeySlot()
    try:
        if check:
            needs = nss.PK11_CheckUserPassword(slot, b"") != 0
            print(json.dumps({"count": len(logins), "needsPassword": needs}))
            return
        unlock(nss, slot)
        records = []
        for entry in logins:
            try:
                records.append({
                    "origin": entry.get("hostname", ""),
                    "username": decrypt(nss, entry["encryptedUsername"]),
                    "password": decrypt(nss, entry["encryptedPassword"]),
                    "realm": entry.get("httpRealm") or "",
                })
            except (KeyError, ValueError):
                print(f"skipped an undecryptable entry for {entry.get('hostname', '?')}", file=sys.stderr)
        json.dump(records, sys.stdout)
    finally:
        nss.PK11_FreeSlot(slot)
        nss.NSS_Shutdown()


if __name__ == "__main__":
    main()
