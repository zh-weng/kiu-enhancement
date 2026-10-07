"""Bind an AppImage child to the exact process launched by this session."""
from pathlib import Path


def _record(pid: int, proc: Path) -> tuple[int, str]:
    # comm may contain spaces and parentheses; fields after its last ')' begin
    # at state (field 3), ppid is field 4, starttime is field 22.
    raw = (proc / str(pid) / "stat").read_text()
    fields = raw.rsplit(")", 1)[1].split()
    return int(fields[1]), fields[19]


def require_descendant(owner: int, launched: int, launch_identity: str,
                       proc=Path("/proc")) -> str:
    """Return the guest's starttime only for the original launcher or its child.

    AppImage runtimes can retain a parent while AppRun executes in a child.
    Matching executable names is insufficient: require the live parent chain
    and original launch PID/starttime, then pin the discovered child identity.
    """
    try:
        if launch_identity is None or _record(launched, proc)[1] != launch_identity:
            raise ValueError("launch process identity changed")
        candidate = owner
        seen = set()
        guest_identity = None
        while candidate > 0 and candidate not in seen:
            seen.add(candidate)
            parent, identity = _record(candidate, proc)
            if guest_identity is None:
                guest_identity = identity
            if candidate == launched:
                if identity != launch_identity:
                    raise ValueError("launch process identity changed")
                return guest_identity
            candidate = parent
    except (OSError, ValueError, IndexError) as exc:
        raise RuntimeError("Cannot verify the launched Azahar process lineage; refusing to attach") from exc
    raise RuntimeError("GDB port belongs to another process; refusing to attach")
