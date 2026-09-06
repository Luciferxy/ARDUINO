"""Small, dependency-free framing helpers for the Arduino serial protocol."""


def frame_command(command_id: int | str, payload: str) -> str:
    payload = payload.strip()
    if not payload or "\n" in payload or "\r" in payload:
        raise ValueError("serial payload must be a non-empty single line")
    return f"CMD:{command_id}:{payload}\n"


def parse_ack(line: str) -> str | None:
    line = line.strip()
    if line.startswith("ACK:") and line[4:]:
        return line[4:]
    return None
