import io, uuid, base64, qrcode

def generate_license_key() -> str:
    raw = uuid.uuid4().bytes + uuid.uuid4().bytes
    b32 = base64.b32encode(raw).decode().rstrip("=")
    return "-".join([b32[i:i+5] for i in range(0, 25, 5)])

def make_qr_png(data: str) -> bytes:
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
